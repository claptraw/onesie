from pathlib import Path
from types import SimpleNamespace

import pytest

from onesie_navidrome.backends.beets_cli import BeetsCliBackend
from onesie_navidrome.config import BeetsConfig
from onesie_navidrome.errors import SafetyError
from onesie_navidrome.models import MappedSong


def test_beets_backend_requires_exact_path(monkeypatch, tmp_path: Path):
    audio = tmp_path / "Artist" / "Song.flac"
    audio.parent.mkdir()
    audio.write_bytes(b"audio")
    backend = BeetsCliBackend(BeetsConfig(executable="beet", config_file=None), (tmp_path,))
    monkeypatch.setattr("onesie_navidrome.backends.beets_cli.shutil.which", lambda _: "/usr/bin/beet")

    def fake_run(args):
        if args == ["version"]:
            return SimpleNamespace(returncode=0, stdout="beets 2.x", stderr="")
        if args[:2] == ["ls", "-f"]:
            return SimpleNamespace(returncode=0, stdout=f"42\t{audio}\n", stderr="")
        raise AssertionError(args)

    monkeypatch.setattr(backend, "_run", fake_run)
    backend.preflight()
    target = backend.prepare(MappedSong("Artist/Song.flac", audio, {"id": "n"}))
    assert target.backend_ref == "42"


def test_beets_backend_rejects_fuzzy_only_match(monkeypatch, tmp_path: Path):
    audio = tmp_path / "Song.flac"
    other = tmp_path / "Song (copy).flac"
    audio.write_bytes(b"audio")
    backend = BeetsCliBackend(BeetsConfig(executable="beet", config_file=None), (tmp_path,))
    monkeypatch.setattr(
        backend,
        "_run",
        lambda args: SimpleNamespace(returncode=0, stdout=f"1\t{other}\n", stderr=""),
    )
    with pytest.raises(SafetyError, match="No exact Beets item"):
        backend.prepare(MappedSong("Song.flac", audio, {"id": "n"}))


def test_beets_backend_resolves_relative_inventory_path(monkeypatch, tmp_path: Path):
    audio = tmp_path / "Artist" / "Album" / "Song.flac"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"audio")
    backend = BeetsCliBackend(BeetsConfig(executable="beet", config_file=None), (tmp_path,))
    monkeypatch.setattr(
        backend,
        "_run",
        lambda args: SimpleNamespace(returncode=0, stdout="9\tArtist/Album/Song.flac\n", stderr=""),
    )
    target = backend.prepare(MappedSong("Artist/Album/Song.flac", audio, {"id": "n"}))
    assert target.backend_ref == "9"


def test_beets_delete_uses_exact_id(monkeypatch, tmp_path: Path):
    audio = tmp_path / "Song.flac"
    audio.write_bytes(b"audio")
    backend = BeetsCliBackend(BeetsConfig(executable="beet", config_file=None), (tmp_path,))
    calls = []

    def fake_run(args):
        calls.append(args)
        if args[:2] == ["ls", "-f"]:
            return SimpleNamespace(returncode=0, stdout=f"77\t{audio}\n", stderr="")
        if args == ["remove", "-d", "-f", "id:77"]:
            audio.unlink()
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(args)

    monkeypatch.setattr(backend, "_run", fake_run)
    target = backend.prepare(MappedSong("/music/Song.flac", audio, {"id": "n"}))
    result = backend.delete(target)
    assert result.deleted is True
    assert not audio.exists()
    assert ["remove", "-d", "-f", "id:77"] in calls
