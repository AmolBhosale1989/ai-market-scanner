from pathlib import Path

from scanner.v4_cutover import _csv


def test_csv_treats_empty_evidence_file_as_no_evidence(tmp_path: Path):
    path = tmp_path / "v4_outcomes.csv"
    path.write_text("")

    assert _csv(path).empty


def test_csv_treats_missing_evidence_file_as_no_evidence(tmp_path: Path):
    assert _csv(tmp_path / "missing.csv").empty
