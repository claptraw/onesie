from pathlib import Path

import pytest

from onesie_navidrome.config import Config
from onesie_navidrome.errors import ConfigError


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "onesie.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_minimal_filesystem_config(tmp_path, monkeypatch):
    monkeypatch.setenv("ONESIE_NAVIDROME_PASSWORD", "secret")
    root = tmp_path / "music"
    root.mkdir()
    cfg = Config.load(
        write(
            tmp_path,
            f"""
navidrome:
  url: http://navidrome:4533
  username: user
  server_music_root: /music
filesystem:
  music_root: {root}
""",
        )
    )
    assert cfg.backend == "filesystem"
    assert cfg.policy.delete_rating == 1
    assert cfg.policy.grace_period_seconds == 7 * 86400
    assert cfg.policy.dry_run is True


def test_password_env_indirection(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_ND_PASSWORD", "secret2")
    root = tmp_path / "music"
    root.mkdir()
    cfg = Config.load(
        write(
            tmp_path,
            f"""
navidrome:
  url: http://navidrome
  username: user
  password_env: MY_ND_PASSWORD
filesystem:
  music_root: {root}
""",
        )
    )
    assert cfg.navidrome.password == "secret2"


def test_rejects_unknown_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("ONESIE_NAVIDROME_PASSWORD", "secret")
    root = tmp_path / "music"
    root.mkdir()
    with pytest.raises(ConfigError):
        Config.load(
            write(
                tmp_path,
                f"""
navidrome:
  url: http://navidrome
  username: user
filesystem:
  music_root: {root}
delete:
  backend: magic
""",
            )
        )


def test_delete_rating_must_be_1_to_5(tmp_path, monkeypatch):
    monkeypatch.setenv("ONESIE_NAVIDROME_PASSWORD", "secret")
    root = tmp_path / "music"
    root.mkdir()
    with pytest.raises(ConfigError):
        Config.load(
            write(
                tmp_path,
                f"""
navidrome:
  url: http://navidrome
  username: user
filesystem:
  music_root: {root}
policy:
  delete_rating: 6
""",
            )
        )


def test_multiple_path_mappings_parse(tmp_path, monkeypatch):
    monkeypatch.setenv("ONESIE_NAVIDROME_PASSWORD", "secret")
    one = tmp_path / "one"
    two = tmp_path / "two"
    one.mkdir()
    two.mkdir()
    cfg = Config.load(
        write(
            tmp_path,
            f"""
navidrome:
  url: http://navidrome
  username: user
filesystem:
  path_mappings:
    - server_root: /music-one
      local_root: {one}
    - server_root: /music-two
      local_root: {two}
""",
        )
    )
    assert len(cfg.filesystem.path_mappings) == 2
    assert cfg.filesystem.path_mappings[1].local_root == two
