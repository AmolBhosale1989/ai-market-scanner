"""Test-only bridge to a separate, freshly created PostgreSQL 18 container.

The existing repository CI's PostgreSQL 16 service is NOT touched by this test.
No scanner runtime files, production migration registration, workflow definitions,
repository settings, secrets, or deployment configuration are changed.
"""
from pathlib import Path
import os
import subprocess
import sys

import pytest


@pytest.mark.postgres_integration
def test_isolated_postgresql18_lineage_contracts(capfd):
    if (os.environ.get("GITHUB_ACTIONS") != "true" and
            os.environ.get("LINEAGE_LOCAL_DOCKER_TEST") != "1"):
        pytest.skip("Isolated PG18 Docker gate requires CI or explicit local opt-in")
    root = Path(__file__).resolve().parents[1]
    runner = root / "validation" / "lineage_pg18" / "run_docker_pg18.py"
    result = subprocess.run([sys.executable, str(runner)], cwd=root,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, timeout=540)
    # Preserve real server identity and all eighteen results in the workflow log,
    # including on successful runs (pytest otherwise captures successful output).
    with capfd.disabled():
        print("\n=== ISOLATED POSTGRESQL 18 CONTRACT GATE ===", flush=True)
        print(result.stdout, flush=True)
    assert result.returncode == 0, "PG18 contract gate failed; see uncaptured server/test log"
