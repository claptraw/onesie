from pathlib import Path

from onesie_navidrome.cli import main


def test_init_writes_config(tmp_path: Path):
    output = tmp_path / "onesie.yaml"
    assert main(["init", "--output", str(output)]) == 0
    assert "delete_rating: 1" in output.read_text(encoding="utf-8")


def test_init_refuses_overwrite(tmp_path: Path):
    output = tmp_path / "onesie.yaml"
    output.write_text("existing", encoding="utf-8")
    assert main(["init", "--output", str(output)]) == 2
    assert output.read_text(encoding="utf-8") == "existing"
