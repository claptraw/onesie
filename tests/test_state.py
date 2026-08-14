from pathlib import Path

from onesie_navidrome.state import STATE_VERSION, StateStore


def test_atomic_state_roundtrip_and_permissions(tmp_path: Path):
    path = tmp_path / "state.json"
    store = StateStore(path)
    data = {"version": STATE_VERSION, "tracks": {"A.flac": {"first_seen": "2026-08-01T00:00:00Z"}}}
    store.save(data)
    assert store.load() == data
    assert path.stat().st_mode & 0o777 == 0o600
