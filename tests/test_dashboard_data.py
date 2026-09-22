from pathlib import Path


def test_dashboard_has_only_postgres_publication_inputs():
    text = (Path(__file__).resolve().parents[1] / "app.py").read_text()
    assert "read_dataset" in text
    assert "publication_info" in text
    assert "run_id=PUBLICATION_RUN_ID" in text
    assert "published_mode=PUBLICATION_MODE" not in text
    assert "raw.githubusercontent.com" not in text
    assert "fetch_remote_bundle" not in text
