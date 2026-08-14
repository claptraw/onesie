from pathlib import Path, PurePosixPath

import pytest

from onesie_navidrome.config import FilesystemConfig, NavidromeConfig, PathMappingConfig
from onesie_navidrome.errors import SafetyError
from onesie_navidrome.pathing import PathMapper


def mapper(root: Path) -> PathMapper:
    return PathMapper(
        NavidromeConfig(
            url="http://n",
            username="u",
            password="p",
            client="Onesie",
            api_version="1.16.1",
            server_music_root=PurePosixPath("/music"),
            verify_tls=True,
            request_timeout=20,
            page_size=500,
            trigger_scan=False,
        ),
        FilesystemConfig(
            music_root=root,
            path_mappings=(PathMappingConfig(PurePosixPath("/music"), root),),
            allowed_extensions=frozenset({".flac"}),
            sidecars=(".lrc",),
            prune_empty_dirs=False,
        ),
    )


def test_maps_real_absolute_path(tmp_path):
    root = tmp_path / "music"
    audio = root / "Artist" / "Album" / "Song.flac"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"audio")
    mapped = mapper(root).map_song({"id": "1", "path": "/music/Artist/Album/Song.flac"})
    assert mapped.path == audio
    assert mapped.key == "/music/Artist/Album/Song.flac"


def test_rejects_synthetic_relative_path(tmp_path):
    root = tmp_path / "music"
    root.mkdir()
    with pytest.raises(SafetyError, match="Report Real Path"):
        mapper(root).map_song({"id": "1", "path": "Artist/Album/Song.flac"})


def test_rejects_traversal(tmp_path):
    root = tmp_path / "music"
    root.mkdir()
    with pytest.raises(SafetyError):
        mapper(root).map_song({"id": "1", "path": "/music/../escape.flac"})


def test_rejects_symlink_component(tmp_path):
    root = tmp_path / "music"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "Song.flac").write_bytes(b"audio")
    (root / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(SafetyError, match="Symlinked"):
        mapper(root).map_song({"id": "1", "path": "/music/link/Song.flac"})


def test_multiple_root_mapping_uses_matching_local_root(tmp_path):
    main = tmp_path / "main"
    archive = tmp_path / "archive"
    main.mkdir()
    audio = archive / "Artist" / "Song.flac"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"audio")
    fs = FilesystemConfig(
        music_root=main,
        path_mappings=(
            PathMappingConfig(PurePosixPath("/music-main"), main),
            PathMappingConfig(PurePosixPath("/music-archive"), archive),
        ),
        allowed_extensions=frozenset({".flac"}),
        sidecars=(".lrc",),
        prune_empty_dirs=False,
    )
    nav = NavidromeConfig(
        url="http://n", username="u", password="p", client="Onesie", api_version="1.16.1",
        server_music_root=PurePosixPath("/music-main"), verify_tls=True, request_timeout=20, page_size=500, trigger_scan=False
    )
    mapped = PathMapper(nav, fs).map_song({"id": "2", "path": "/music-archive/Artist/Song.flac"})
    assert mapped.path == audio
    assert mapped.key == "/music-archive/Artist/Song.flac"
