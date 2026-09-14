import time

from scanner.dashboard_data import DashboardFetchSettings, fetch_remote_bundle


class Response:
    def __init__(self, text, fail=False):
        self.text = text
        self.fail = fail

    def raise_for_status(self):
        if self.fail:
            raise RuntimeError("missing")


def test_bundle_fetches_concurrently_and_isolates_failures():
    def fetcher(url, timeout):
        time.sleep(0.04)
        return Response(url.rsplit("/", 1)[-1], fail=url.endswith("bad.csv"))

    started = time.monotonic()
    payloads, health = fetch_remote_bundle(
        "https://example.test/data",
        ["one.csv", "two.csv", "bad.csv", "one.csv"],
        settings=DashboardFetchSettings(max_workers=3),
        fetcher=fetcher,
    )

    assert time.monotonic() - started < 0.11
    assert payloads == {"one.csv": "one.csv", "two.csv": "two.csv"}
    assert health["requested_files"] == 3
    assert health["remote_files_loaded"] == 2
    assert health["failed_files"] == ["bad.csv"]

