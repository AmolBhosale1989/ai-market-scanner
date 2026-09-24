from contextlib import contextmanager
import pytest
from scanner import dashboard_data as module
from scanner.control_plane import _hash


def setup_db(monkeypatch, rows):
    calls = []
    class Cursor:
        def execute(self, sql, params):
            calls.append(params)
        def fetchall(self):
            return rows
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
    class Connection:
        def cursor(self):
            return Cursor()
    @contextmanager
    def connect():
        yield Connection()
    monkeypatch.setattr(module, "_connect", connect)
    return calls


def test_batch_preserves_order_empty_and_missing(monkeypatch):
    records = [{"ticker": "B"}, {"ticker": "A"}]
    calls = setup_db(monkeypatch, [
        ("prices", _hash(records), 2, records[0]),
        ("prices", _hash(records), 2, records[1]),
        ("empty", _hash([]), 0, None),
    ])
    result = module.read_dashboard_datasets(["prices", "empty", "missing"], run_id="fixed")
    assert result["prices"][0].to_dict("records") == records
    assert result["empty"][1] == "postgresql"
    assert result["missing"][1] == "blocked"
    assert len(calls) == 1
    assert calls[0][0] == "fixed"


@pytest.mark.parametrize("digest,count", [("tampered", 1), (_hash([{"ticker":"A"}]), 2)])
def test_corruption_blocks_batch(monkeypatch, digest, count):
    setup_db(monkeypatch, [("prices", digest, count, {"ticker":"A"})])
    with pytest.raises(RuntimeError, match="DATASET_CORRUPT"):
        module.read_dashboard_datasets(["prices"], run_id="fixed")
