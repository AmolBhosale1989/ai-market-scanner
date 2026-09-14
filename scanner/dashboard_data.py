from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from time import monotonic
from typing import Callable, Iterable

import requests


@dataclass(frozen=True)
class DashboardFetchSettings:
    connect_timeout_seconds: float = 2.5
    read_timeout_seconds: float = 5.0
    max_workers: int = 16


def fetch_remote_bundle(
    remote_base: str,
    filenames: Iterable[str],
    settings: DashboardFetchSettings | None = None,
    fetcher: Callable[..., object] = requests.get,
) -> tuple[dict[str, str], dict[str, object]]:
    """Fetch dashboard artifacts concurrently without failing the whole page.

    Each artifact is independently optional. A missing or slow file is recorded
    in telemetry and can fall back to the local output directory in ``app.py``.
    """
    settings = settings or DashboardFetchSettings()
    names = tuple(dict.fromkeys(str(name) for name in filenames if str(name)))
    started = monotonic()
    payloads: dict[str, str] = {}
    failures: dict[str, str] = {}

    def fetch(name: str) -> tuple[str, str]:
        response = fetcher(
            f"{remote_base.rstrip('/')}/{name}",
            timeout=(settings.connect_timeout_seconds, settings.read_timeout_seconds),
        )
        response.raise_for_status()
        return name, str(response.text)

    worker_count = max(1, min(settings.max_workers, len(names) or 1))
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="dashboard-data") as executor:
        futures = {executor.submit(fetch, name): name for name in names}
        for future in as_completed(futures):
            name = futures[future]
            try:
                key, text = future.result()
                payloads[key] = text
            except Exception as exc:  # individual remote artifacts are non-fatal
                failures[name] = type(exc).__name__

    telemetry: dict[str, object] = {
        "settings": asdict(settings),
        "requested_files": len(names),
        "remote_files_loaded": len(payloads),
        "remote_files_failed": len(failures),
        "failed_files": sorted(failures),
        "elapsed_seconds": round(monotonic() - started, 3),
    }
    return payloads, telemetry


def local_text(local_dir: Path, name: str) -> str | None:
    path = Path(local_dir) / name
    try:
        return path.read_text()
    except (OSError, UnicodeError):
        return None

