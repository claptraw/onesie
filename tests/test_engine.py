from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

import pytest

import onesie_navidrome.engine as engine_module
from onesie_navidrome.audit import iso_z, parse_time
from onesie_navidrome.config import (
    BeetsConfig,
    Config,
    FilesystemConfig,
    NavidromeConfig,
    NotificationConfig,
    PathMappingConfig,
    PolicyConfig,
    RuntimeConfig,
)
from onesie_navidrome.engine import OnesieEngine
from onesie_navidrome.errors import SafetyError
from onesie_navidrome.state import STATE_VERSION, StateStore


def make_config(
    root: Path,
    state: Path,
    *,
    dry_run=False,
    max_delete=20,
    prune=False,
    notifications=False,
) -> Config:
    return Config(
        navidrome=NavidromeConfig(
            url="http://navidrome",
            username="user",
            password="pass",
            client="onesie",
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
            prune_empty_dirs=prune,
            cleanup_files=("cover.jpg", "cover.webp", "cover.mp4"),
        ),
        backend="filesystem",
        beets=BeetsConfig(executable="beet", config_file=None),
        notifications=NotificationConfig(
            enabled=notifications,
            apprise_config=None,
            tag="",
            notify_on_noop=False,
            notify_on_dry_run=False,
            notify_before_deletion=True,
            warning_before_deletion_seconds=2 * 86400,
            warning_retry_interval_seconds=12 * 3600,
            final_warning_window_seconds=12 * 3600,
            warning_failure_postpone_seconds=86400,
            notify_after_deletion=True,
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


class FakeNotifier:
    def __init__(self, results=None):
        self.results = list(results or [])
        self.calls = []

    def send(self, title, body, kind="info"):
        self.calls.append((title, body, kind))
        if self.results:
            return self.results.pop(0)
        return True


def prime_state_at(path: Path, key: str, song_id: str, first_seen: datetime):
    StateStore(path).save(
        {
            "version": STATE_VERSION,
            "tracks": {
                key: {
                    "first_seen": iso_z(first_seen),
                    "last_seen": iso_z(first_seen),
                    "navidrome_id": song_id,
                    "artist": "Artist",
                    "album": "Album",
                    "title": "Song",
                }
            },
        }
    )


def prime_old_state(path: Path, key: str, song_id: str):
    prime_state_at(path, key, song_id, datetime.now(timezone.utc) - timedelta(days=8))


def song(path="/music/Artist/Album/Song.flac", song_id="n1", rating=1):
    return {
        "id": song_id,
        "path": path,
        "userRating": rating,
        "artist": "Artist",
        "album": "Album",
        "title": "Song",
    }


def test_filesystem_full_cycle_removes_audio_and_lrc(tmp_path: Path):
    root = tmp_path / "music"
    audio = root / "Artist" / "Album" / "Song.flac"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"audio")
    lyric = audio.with_suffix(".lrc")
    lyric.write_text("lyrics", encoding="utf-8")
    state = tmp_path / "state.json"
    prime_old_state(state, "/music/Artist/Album/Song.flac", "n1")
    app = OnesieEngine(make_config(root, state))
    app.navidrome = FakeNavidrome([song()])
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
    current = {"id": "n1", "path": "/music/Song.flac", "userRating": 4, "title": "Song"}
    app = OnesieEngine(make_config(root, state))
    app.navidrome = FakeNavidrome([current])
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
    current = {"id": "n1", "path": "/music/Song.flac", "userRating": 1, "title": "Song"}
    fake = FakeNavidrome([current])
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
    current = {"id": "n1", "path": "/music/Song.flac", "userRating": 1, "title": "Song"}
    app = OnesieEngine(make_config(root, state))
    app.navidrome = FakeNavidrome([current])
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
    current = {"id": "n1", "path": "/music/Song.flac", "userRating": 1, "title": "Song"}
    app = OnesieEngine(make_config(root, state, dry_run=False))
    app.navidrome = FakeNavidrome([current])
    assert app.run_once(force_dry_run=True) == 0
    assert audio.exists()


def test_cleanup_removes_allowed_covers_and_newly_empty_parents(tmp_path: Path):
    root = tmp_path / "music"
    album = root / "Artist" / "Album"
    album.mkdir(parents=True)
    audio = album / "Song.flac"
    audio.write_bytes(b"audio")
    audio.with_suffix(".lrc").write_text("lyrics", encoding="utf-8")
    for name in ("cover.jpg", "cover.webp", "cover.mp4"):
        (album / name).write_bytes(b"cover")
    state = tmp_path / "state.json"
    prime_old_state(state, "/music/Artist/Album/Song.flac", "n1")
    app = OnesieEngine(make_config(root, state, prune=True))
    app.navidrome = FakeNavidrome([song()])
    assert app.run_once() == 0
    assert not (root / "Artist").exists()
    assert root.exists()


def test_cleanup_is_blocked_by_unrelated_subdirectory(tmp_path: Path):
    root = tmp_path / "music"
    album = root / "Artist" / "Album"
    extras = album / "Extras"
    extras.mkdir(parents=True)
    (extras / "booklet.pdf").write_bytes(b"pdf")
    audio = album / "Song.flac"
    audio.write_bytes(b"audio")
    audio.with_suffix(".lrc").write_text("lyrics", encoding="utf-8")
    cover = album / "cover.jpg"
    cover.write_bytes(b"cover")
    state = tmp_path / "state.json"
    prime_old_state(state, "/music/Artist/Album/Song.flac", "n1")
    app = OnesieEngine(make_config(root, state, prune=True))
    app.navidrome = FakeNavidrome([song()])
    assert app.run_once() == 0
    assert cover.exists()
    assert (extras / "booklet.pdf").exists()
    assert album.exists()


def test_dry_run_lists_audio_sidecar_cover_and_directories(tmp_path: Path, caplog):
    root = tmp_path / "music"
    album = root / "Artist" / "Album"
    album.mkdir(parents=True)
    audio = album / "Song.flac"
    audio.write_bytes(b"audio")
    audio.with_suffix(".lrc").write_text("lyrics", encoding="utf-8")
    (album / "cover.jpg").write_bytes(b"cover")
    state = tmp_path / "state.json"
    prime_old_state(state, "/music/Artist/Album/Song.flac", "n1")
    app = OnesieEngine(make_config(root, state, prune=True, dry_run=True))
    app.navidrome = FakeNavidrome([song()])
    with caplog.at_level("INFO"):
        assert app.run_once() == 0
    text = caplog.text
    assert "would remove music file" in text
    assert "would remove sidecar file" in text
    assert "would remove cleanup file" in text
    assert "would remove directory" in text
    assert audio.exists()
    assert (album / "cover.jpg").exists()


def test_warning_retries_and_final_window_postpones_then_allows_delete(tmp_path: Path, monkeypatch):
    root = tmp_path / "music"
    album = root / "Artist" / "Album"
    album.mkdir(parents=True)
    audio = album / "Song.flac"
    audio.write_bytes(b"audio")
    state = tmp_path / "state.json"
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    prime_state_at(state, "/music/Artist/Album/Song.flac", "n1", t0)
    app = OnesieEngine(make_config(root, state, notifications=True))
    app.navidrome = FakeNavidrome([song()])
    notifier = FakeNotifier([False, False, False, False, True, True])
    app.notifier = notifier

    def run_at(moment):
        monkeypatch.setattr(engine_module, "utcnow", lambda: moment)
        return app.run_once()

    # Day 5: first warning fails.
    assert run_at(t0 + timedelta(days=5)) == 0
    record = StateStore(state).load()["tracks"]["/music/Artist/Album/Song.flac"]
    assert record["warning_attempts"] == 1
    assert "deletion_deferred_until" not in record

    # Before the 12-hour retry interval, no second attempt is made.
    assert run_at(t0 + timedelta(days=5, hours=6)) == 0
    assert len(notifier.calls) == 1

    # 12-hour retries continue.
    assert run_at(t0 + timedelta(days=5, hours=12)) == 0
    assert run_at(t0 + timedelta(days=6)) == 0

    # Failure exactly 12 hours before day-7 deletion postpones by 24 hours.
    assert run_at(t0 + timedelta(days=6, hours=12)) == 0
    record = StateStore(state).load()["tracks"]["/music/Artist/Album/Song.flac"]
    assert parse_time(record["deletion_deferred_until"]) == t0 + timedelta(days=8)
    assert audio.exists()

    # Next 12-hour retry succeeds. Deletion remains scheduled for day 8.
    assert run_at(t0 + timedelta(days=7)) == 0
    record = StateStore(state).load()["tracks"]["/music/Artist/Album/Song.flac"]
    assert record.get("warning_sent_at")
    assert audio.exists()

    # Day 8: deletion is now allowed and success notification lists the song.
    assert run_at(t0 + timedelta(days=8)) == 0
    assert not audio.exists()
    assert notifier.calls[-1][0] == "onesie: songs successfully deleted"
    assert "Artist — Song (Album)" in notifier.calls[-1][1]


def test_existing_alpha1_eligible_track_gets_warning_before_delete(tmp_path: Path, monkeypatch):
    root = tmp_path / "music"
    album = root / "Artist" / "Album"
    album.mkdir(parents=True)
    audio = album / "Song.flac"
    audio.write_bytes(b"audio")
    state = tmp_path / "state.json"
    now = datetime(2026, 1, 10, tzinfo=timezone.utc)
    prime_state_at(state, "/music/Artist/Album/Song.flac", "n1", now - timedelta(days=8))
    app = OnesieEngine(make_config(root, state, notifications=True))
    app.navidrome = FakeNavidrome([song()])
    notifier = FakeNotifier([True])
    app.notifier = notifier
    monkeypatch.setattr(engine_module, "utcnow", lambda: now)
    assert app.run_once() == 0
    assert audio.exists()
    record = StateStore(state).load()["tracks"]["/music/Artist/Album/Song.flac"]
    assert record.get("warning_sent_at")
    assert parse_time(record["deletion_deferred_until"]) == now + timedelta(days=1)


def test_cleanup_is_blocked_by_unrelated_file_or_orphan_lrc(tmp_path: Path):
    root = tmp_path / "music"
    album = root / "Artist" / "Album"
    album.mkdir(parents=True)
    audio = album / "Song.flac"
    audio.write_bytes(b"audio")
    audio.with_suffix(".lrc").write_text("lyrics", encoding="utf-8")
    cover = album / "cover.jpg"
    cover.write_bytes(b"cover")
    orphan = album / "Other Song.lrc"
    orphan.write_text("orphan", encoding="utf-8")
    state = tmp_path / "state.json"
    prime_old_state(state, "/music/Artist/Album/Song.flac", "n1")
    app = OnesieEngine(make_config(root, state, prune=True))
    app.navidrome = FakeNavidrome([song()])
    assert app.run_once() == 0
    assert cover.exists()
    assert orphan.exists()
    assert album.exists()


def test_batch_dry_run_sees_album_empty_only_after_all_planned_tracks(tmp_path: Path, caplog):
    root = tmp_path / "music"
    album = root / "Artist" / "Album"
    album.mkdir(parents=True)
    state = tmp_path / "state.json"
    songs = []
    tracks = {}
    old = datetime.now(timezone.utc) - timedelta(days=8)
    for index in (1, 2):
        audio = album / f"Song{index}.flac"
        audio.write_bytes(b"audio")
        audio.with_suffix(".lrc").write_text("lyrics", encoding="utf-8")
        key = f"/music/Artist/Album/Song{index}.flac"
        songs.append({
            "id": f"n{index}",
            "path": key,
            "userRating": 1,
            "artist": "Artist",
            "album": "Album",
            "title": f"Song{index}",
        })
        tracks[key] = {
            "first_seen": iso_z(old),
            "last_seen": iso_z(old),
            "navidrome_id": f"n{index}",
            "artist": "Artist",
            "album": "Album",
            "title": f"Song{index}",
        }
    (album / "cover.jpg").write_bytes(b"cover")
    StateStore(state).save({"version": STATE_VERSION, "tracks": tracks})
    app = OnesieEngine(make_config(root, state, prune=True, dry_run=True))
    app.navidrome = FakeNavidrome(songs)
    with caplog.at_level("INFO"):
        assert app.run_once() == 0
    assert caplog.text.count("would remove music file") == 2
    assert "would remove cleanup file" in caplog.text
    assert str(album) in caplog.text
    assert (album / "cover.jpg").exists()
