"""Execute the unchanged 18-test draft on a new, network-isolated PG18 container.

No production DSN, existing container, or remote database is accepted. The SQL
fixture is destructive ONLY inside the newly created throwaway database. This
runner is not a publication, deployment, or complete production integration test.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent
BRANCH = "validation/lineage-pg18-contracts-20261004"
REPOSITORY = "AmolBhosale1989/ai-market-scanner"
IMAGE = "postgres:18"


def run(command: list[str], *, timeout: int = 30, check: bool = True,
        env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=timeout, env=env)
    if check and result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}): {command[0]}\n{result.stdout}")
    return result


def verify_inputs() -> None:
    expected = json.loads((ROOT / "contract_inputs.sha256.json").read_text())
    for relative, digest in expected.items():
        path = (ROOT / relative).resolve()
        if not path.is_relative_to(ROOT) or not path.is_file():
            raise RuntimeError(f"Contract input missing or invalid: {relative}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != digest:
            raise RuntimeError(f"Contract input changed: {relative}; expected={digest}; actual={actual}")
    print(f"LINEAGE_CONTRACT_INPUT_HASHES_VERIFIED files={len(expected)}", flush=True)


def main() -> int:
    if os.environ.get("GITHUB_ACTIONS") == "true":
        if (os.environ.get("GITHUB_REPOSITORY") != REPOSITORY or
                os.environ.get("GITHUB_HEAD_REF") != BRANCH):
            raise RuntimeError("REFUSED: not the explicitly isolated validation PR")
    elif os.environ.get("LINEAGE_LOCAL_DOCKER_TEST") != "1":
        raise RuntimeError("REFUSED: local execution requires LINEAGE_LOCAL_DOCKER_TEST=1")
    if not shutil.which("docker"):
        raise RuntimeError("NOT RUN: Docker is required; do not substitute an existing database")
    verify_inputs()
    import psycopg

    # The image tag is resolved once, logged, and the immutable image ID is run.
    # Pulling uses runner network access; the actual database has --network none.
    pulled = run(["docker", "pull", IMAGE], timeout=150)
    print(pulled.stdout, flush=True)
    image = json.loads(run(["docker", "image", "inspect", IMAGE]).stdout)[0]
    image_id = image["Id"]
    print("LINEAGE_PG18_IMAGE " + json.dumps({"id": image_id,
          "digests": image.get("RepoDigests", [])}), flush=True)
    clean_env = {k: os.environ[k] for k in (
        "PATH", "HOME", "LANG", "LC_ALL", "LD_LIBRARY_PATH", "VIRTUAL_ENV"
    ) if k in os.environ}
    clean_env["PYTHONUNBUFFERED"] = "1"
    clean_env["PYTHONNOUSERSITE"] = "1"
    container_id = None
    with tempfile.TemporaryDirectory(prefix="market-hunt-lineage-", dir="/tmp") as directory:
        private = Path(directory)
        private.chmod(0o700)
        socket = private / "socket"
        socket.mkdir(mode=0o777)
        socket.chmod(0o777)  # Container UID can write; parent remains private on host.
        (private / "LOCAL_ONLY_AUTHORIZATION").write_text("THROWAWAY PG18 ONLY\n")
        clean_env["LINEAGE_TEST_SOCKET"] = str(socket)
        try:
            container_id = run([
                "docker", "run", "--detach", "--rm", "--network", "none",
                "--memory", "512m", "--cpus", "1",
                "--tmpfs", "/var/lib/postgresql:rw,nosuid,size=256m",
                "--mount", f"type=bind,source={socket},target=/socket",
                "--env", "POSTGRES_HOST_AUTH_METHOD=trust",
                "--env", "POSTGRES_USER=lineage_test",
                "--env", "POSTGRES_DB=lineage_gate_test",
                image_id, "postgres", "-c", "listen_addresses=",
                "-c", "port=55482", "-c", "unix_socket_directories=/socket",
                "-c", "unix_socket_permissions=0777",
            ], timeout=40).stdout.strip()
            if not re.fullmatch(r"[0-9a-f]{64}", container_id):
                raise RuntimeError("Invalid new container identity")
            inspected = json.loads(run(["docker", "inspect", container_id]).stdout)[0]
            if (inspected["HostConfig"]["NetworkMode"] != "none" or
                    inspected["HostConfig"].get("PortBindings")):
                raise RuntimeError("REFUSED: database network isolation missing")
            deadline = time.monotonic() + 60
            metadata = None
            while time.monotonic() < deadline:
                try:
                    # Explicit socket identity; no DATABASE_URL or PG* defaults.
                    with psycopg.connect(host=str(socket), port=55482,
                            dbname="lineage_gate_test", user="lineage_test", password="",
                            connect_timeout=2,
                            options="-c statement_timeout=5000 -c timezone=UTC") as conn:
                        row = conn.execute("SELECT version(),current_setting('server_version_num'),"
                            "inet_server_addr() IS NULL,current_database(),current_user,"
                            "current_setting('listen_addresses')").fetchone()
                        if int(row[1]) // 10000 != 18 or row[2:] != (
                                True, "lineage_gate_test", "lineage_test", ""):
                            raise RuntimeError("REFUSED: server version or local database identity mismatch")
                        metadata = {"version": row[0], "server_version_num": row[1],
                                    "unix_socket_only": row[2], "database": row[3],
                                    "user": row[4], "listen_addresses": row[5],
                                    "driver_version": psycopg.__version__}
                    break
                except psycopg.OperationalError:
                    time.sleep(0.5)
            if metadata is None:
                raise RuntimeError("NOT RUN: new PG18 container did not become ready within 60 seconds")
            print("LINEAGE_PG18_SERVER " + json.dumps(metadata), flush=True)
            print("LINEAGE_PG18_CONTRACTS_BEGIN expected_tests=18", flush=True)
            result = run([sys.executable, str(ROOT / "tests" / "postgres_contracts.py")],
                         timeout=180, check=False, env=clean_env)
            print(result.stdout, flush=True)
            ran = re.search(r"Ran (\d+) tests? in ([\d.]+)s", result.stdout)
            success = (result.returncode == 0 and ran is not None and int(ran[1]) == 18
                       and re.search(r"^OK$", result.stdout, re.MULTILINE) is not None)
            verdict = {"status": "PASS" if success else "FAIL",
                       "tests_run": int(ran[1]) if ran else 0,
                       "test_seconds": float(ran[2]) if ran else None,
                       "exit_code": result.returncode, "server": metadata,
                       "image_id": image_id, "production_changes": False,
                       "schema_scope": "reduced_synthetic_fixture_not_full_migrations"}
            print("LINEAGE_PG18_CONTRACT_RESULT " + json.dumps(verdict, sort_keys=True), flush=True)
            summary = os.environ.get("GITHUB_STEP_SUMMARY")
            if summary:
                with open(summary, "a", encoding="utf-8") as output:
                    output.write("\n## Isolated PostgreSQL 18 lineage contracts\n\n```json\n"
                                 + json.dumps(verdict, indent=2) + "\n```\n")
            return 0 if success else 1
        finally:
            if container_id and re.fullmatch(r"[0-9a-f]{64}", container_id):
                try:
                    logs = run(["docker", "logs", container_id], check=False, timeout=10)
                    print("LINEAGE_PG18_SERVER_LOG_TAIL\n" + "\n".join(logs.stdout.splitlines()[-35:]), flush=True)
                finally:
                    removed = run(["docker", "rm", "--force", "--volumes", container_id],
                                  check=False, timeout=20)
                    print(f"LINEAGE_PG18_CLEANUP exit_code={removed.returncode}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
