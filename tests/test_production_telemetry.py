import pytest

import scanner.production_telemetry as telemetry


def test_publication_gate_forwards_complete_required_dataset_set(monkeypatch):
    captured = {}

    def publish(mode, required):
        captured.update(mode=mode, required=tuple(required))
        return {"pipeline_run_id": "run-1", "datasets": list(required)}

    monkeypatch.setattr(telemetry, "publish", publish)
    result = telemetry.finalize()
    assert result["pipeline_run_id"] == "run-1"
    assert captured["mode"] == "production"
    assert set(captured["required"]) == set(telemetry.REQUIRED_DATASETS)
    assert {"all_candidates", "intraday_live", "v4_shadow_observations", "v9_readiness"}.issubset(captured["required"])


def test_publication_failure_propagates(monkeypatch):
    monkeypatch.setattr(telemetry, "publish", lambda *_: (_ for _ in ()).throw(RuntimeError("blocked")))
    with pytest.raises(RuntimeError, match="blocked"):
        telemetry.finalize()


def test_failure_telemetry_marks_run_without_file_fallback(monkeypatch):
    marked = []
    monkeypatch.setattr(telemetry, "fail_run", marked.append)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    result = telemetry.record_failure("warehouse", "failed")
    assert result["status"] == "FAILED"
    assert marked == ["warehouse: failed"]
