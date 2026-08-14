from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

import pytest

from onesie_navidrome.audit import iso_z
from onesie_navidrome.config import (
    BeetsConfig,
    Config,
    FilesystemConfig,
    PathMappingConfig,
    NavidromeConfig,
    NotificationConfig,
    PolicyConfig,
    RuntimeConfig,
)
from onesie_navidrome.engine import OnesieEngine
from onesie_navidrome.errors import SafetyError
from onesie_navidrome.state import STATE_VERSION, StateStore


def make_config(root: Path, state: Path, *, dry_run=False, max_delete=20) -> Config:
    return Config(
        navidrome=NavidromeConfig(
            url="http://navidrome",
            username="user",
            password="pass",
            client="Onesie",
            api_version="1.16.1",
            server_music_root=PurePosixPath("/music"),
            verify_tls=True,
            request_timeout=20,
            page_size=500,
            trigger_scan=True,
        ),
        policy=PolicyConfig(
            delete_rating=1,
            grace_period_seconds=7 * 86400,
            max_deletions_per_run=max_delete,
            dry_run=dry_run,
            strict_validation=True,
        ),
        filesystem=FilesystemConfig(
            music_root=root,
            path_mappings=(PathMappingConfig(PurePosixPath("/music"), root),),
            allowed_extensions=frozenset({".flac"}),
            sidecars=(".lrc",),
            prune_empty_dirs=False,
        ),
        backend="filesystem",
        beets=BeetsConfig(executable="beet", config_file=None),
        notifications=NotificationConfig(
            enabled=False,
            apprise_config=None,
            tag="",
            notify_on_noop=False,
            notify_on_dry_run=False,
        ),
        runtime=RuntimeConfig(state_file=state, audit_log=state.with_name("audit.jsonl")),
    )


class FakeNavidrome:
    def __init__(self, songs):
        self.songs = {s["id"]: dict(s) for s in songs}
        self.scan_started = False

    def ping(self):
        return None

    def all_songs(self):
        return [dict(s) for s in self.songs.values()]

    def get_song(self, song_id):
        return dict(self.songs[song_id])

    def start_scan(self):
        self.scan_started = True


def prime_old_state(path: Path, key: str, song_id: str):
    old = datetime.now(timezone.utc) - timedelta(days=8)
    StateStore(path).save(
        {
            "version": STATE_VERSION,
            "tracks": {
                key: {
                    "first_seen": iso_z(old),
                    "last_seen": iso_z(old),
                    "navidrome_id": song_id,
                    "artist": "Artist",
                    "album": "Album",
                    "title": "Song",
                }
            },
        }
    )


def test_filesystem_full_cycle_removes_audio_and_lrc(tmp_path: Path):
    root = tmp_path / "music"
    audio = root / "Artist" / "Album" / "Song.flac"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"audio")
    lyric = audio.with_suffix(".lrc")
    lyric.write_text("lyrics", encoding="utf-8")
    state = tmp_path / "state.json"
    prime_old_state(state, "/music/Artist/Album/Song.flac", "n1")
    song = {
        "id": "n1",
        "path": "/music/Artist/Album/Song.flac",
        "userRating": 1,
        "artist": "Artist",
        "album": "Album",
        "title": "Song",
    }
    app = OnesieEngine(make_config(root, state))
    app.navidrome = FakeNavidrome([song])
    assert app.run_once() == 0
    assert not audio.exists()
    assert not lyric.exists()
    assert StateStore(state).load()["tracks"] == {}
    assert app.navidrome.scan_started


def test_rating_change_cancels_queue_without_deletion(tmp_path: Path):
    root = tmp_path / "music"
    root.mkdir()
    audio = root / "Song.flac"
    audio.write_bytes(b"audio")
    state = tmp_path / "state.json"
    prime_old_state(state, "/music/Song.flac", "n1")
    song = {"id": "n1", "path": "/music/Song.flac", "userRating": 4, "title": "Song"}
    app = OnesieEngine(make_config(root, state))
    app.navidrome = FakeNavidrome([song])
    assert app.run_once() == 0
    assert audio.exists()
    assert StateStore(state).load()["tracks"] == {}


def test_live_rating_recheck_blocks_deletion(tmp_path: Path):
    root = tmp_path / "music"
    root.mkdir()
    audio = root / "Song.flac"
    audio.write_bytes(b"audio")
    state = tmp_path / "state.json"
    prime_old_state(state, "/music/Song.flac", "n1")
    song = {"id": "n1", "path": "/music/Song.flac", "userRating": 1, "title": "Song"}
    fake = FakeNavidrome([song])
    app = OnesieEngine(make_config(root, state))
    app.navidrome = fake
    original_all = fake.all_songs

    def all_then_change():
        rows = original_all()
        fake.songs["n1"]["userRating"] = 4
        return rows

    fake.all_songs = all_then_change
    assert app.run_once() == 2
    assert audio.exists()
    assert "/music/Song.flac" in StateStore(state).load()["tracks"]


def test_batch_guard_is_all_or_nothing(tmp_path: Path):
    root = tmp_path / "music"
    root.mkdir()
    state = tmp_path / "state.json"
    songs = []
    tracks = {}
    old = datetime.now(timezone.utc) - timedelta(days=8)
    for i in (1, 2):
        path = root / f"Song{i}.flac"
        path.write_bytes(b"audio")
        songs.append({"id": f"n{i}", "path": f"/music/Song{i}.flac", "userRating": 1, "title": f"Song{i}"})
        tracks[f"/music/Song{i}.flac"] = {
            "first_seen": iso_z(old),
            "last_seen": iso_z(old),
            "navidrome_id": f"n{i}",
            "artist": "",
            "album": "",
            "title": f"Song{i}",
        }
    StateStore(state).save({"version": STATE_VERSION, "tracks": tracks})
    app = OnesieEngine(make_config(root, state, max_delete=1))
    app.navidrome = FakeNavidrome(songs)
    with pytest.raises(SafetyError, match="exceeding"):
        app.run_once()
    assert (root / "Song1.flac").exists()
    assert (root / "Song2.flac").exists()


def test_new_marker_starts_grace_period(tmp_path: Path):
    root = tmp_path / "music"
    root.mkdir()
    audio = root / "Song.flac"
    audio.write_bytes(b"audio")
    state = tmp_path / "state.json"
    song = {"id": "n1", "path": "/music/Song.flac", "userRating": 1, "title": "Song"}
    app = OnesieEngine(make_config(root, state))
    app.navidrome = FakeNavidrome([song])
    assert app.run_once() == 0
    assert audio.exists()
    record = StateStore(state).load()["tracks"]["/music/Song.flac"]
    assert record["navidrome_id"] == "n1"


def test_force_dry_run_overrides_live_delete(tmp_path: Path):
    root = tmp_path / "music"
    root.mkdir()
    audio = root / "Song.flac"
    audio.write_bytes(b"audio")
    state = tmp_path / "state.json"
    prime_old_state(state, "/music/Song.flac", "n1")
    song = {"id": "n1", "path": "/music/Song.flac", "userRating": 1, "title": "Song"}
    app = OnesieEngine(make_config(root, state, dry_run=False))
    app.navidrome = FakeNavidrome([song])
    assert app.run_once(force_dry_run=True) == 0
    assert audio.exists()
