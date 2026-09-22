from __future__ import annotations

import copy
import sys

import pandas as pd
import pytest

import scanner.control_plane as real


@pytest.fixture(autouse=True)
def memory_control_plane(monkeypatch, request):
    if request.node.get_closest_marker("postgres_integration"):
        return None
    datasets: dict[tuple[str, str], pd.DataFrame] = {}
    states: dict[tuple[str, str], object] = {}
    events: dict[str, dict[str, dict]] = {}
    run_id = "00000000-0000-0000-0000-000000000001"

    def current_run_id(required=True):
        return run_id

    def write_dataset(name, frame, **kwargs):
        rid = kwargs.get("run_id") or run_id
        datasets[(rid, name)] = frame.copy()
        return {"dataset_version_id": len(datasets), "row_count": len(frame), "content_hash": "test"}

    def read_dataset(name, *, run_id=None, published_mode=None, required=True):
        key = (run_id or run_id_value(), name)
        frame = datasets.get(key)
        if frame is None:
            if required:
                raise RuntimeError(f"CONTROL_PLANE_DATASET_UNAVAILABLE: {name}")
            return pd.DataFrame()
        return frame.copy()

    def run_id_value():
        return run_id

    def append_state(namespace, key, payload, **kwargs):
        states[(str(namespace), str(key))] = copy.deepcopy(payload)
        return 1

    def read_state(namespace, key, default=None):
        return copy.deepcopy(states.get((str(namespace), str(key)), default))

    def append_events(namespace, rows, *, key_field="event_id", **kwargs):
        bucket = events.setdefault(str(namespace), {})
        inserted = 0
        for row in rows:
            item = copy.deepcopy(dict(row))
            key = str(item[key_field])
            if key not in bucket:
                bucket[key] = item
                inserted += 1
        return inserted

    def read_events(namespace, **kwargs):
        return copy.deepcopy(list(events.get(str(namespace), {}).values()))

    def write_record(name, payload, **kwargs):
        return write_dataset(name, pd.DataFrame([payload]), **kwargs)

    def read_record(name, **kwargs):
        frame = read_dataset(name, **kwargs)
        return frame.iloc[-1].to_dict() if not frame.empty else {}

    replacements = {
        "current_run_id": current_run_id,
        "write_dataset": write_dataset,
        "read_dataset": read_dataset,
        "append_state": append_state,
        "read_state": read_state,
        "append_events": append_events,
        "read_events": read_events,
        "write_record": write_record,
        "read_record": read_record,
    }
    for name, value in replacements.items():
        monkeypatch.setattr(real, name, value)
    for module_name, module in list(sys.modules.items()):
        if not module_name.startswith("scanner") or module is None:
            continue
        for name, value in replacements.items():
            if hasattr(module, name):
                monkeypatch.setattr(module, name, value)

    return {"datasets": datasets, "states": states, "events": events, "run_id": run_id}
