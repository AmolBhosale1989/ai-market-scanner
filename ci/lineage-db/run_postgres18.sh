#!/usr/bin/env bash
# Run only in an authorized development/CI host. Never target an existing DB.
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "$0")" && pwd)"
EVIDENCE="${ROOT}/evidence"
mkdir -p "$EVIDENCE"
if [ "$(id -u)" = 0 ]; then
  echo 'REFUSED: execute as an unprivileged host user with authorized Docker access.' >&2
  exit 2
fi
command -v docker >/dev/null || { echo 'NOT RUN: Docker is required.' >&2; exit 2; }
command -v timeout >/dev/null
python -c 'import psycopg, pandas' || { echo 'NOT RUN: install the test requirements.' >&2; exit 2; }
# Child clients inherit no production DB/GitHub/Telegram connection environment.
while IFS='=' read -r key _; do
  case "$key" in PG*|DATABASE_URL|TELEGRAM_*|RENDER_API_KEY|GH_TOKEN|GITHUB_TOKEN) unset "$key";; esac
done < <(env)
TMP="$(mktemp -d /tmp/market-hunt-lineage-XXXXXXXX)"
chmod 700 "$TMP"
mkdir "$TMP/socket"
# The parent is private (0700). The container sees ONLY the socket subdirectory.
chmod 777 "$TMP/socket"
NONCE="market-hunt-contracts-$(python -c 'import uuid; print(uuid.uuid4().hex)')"
printf '%s\n' "$NONCE" > "$TMP/LOCAL_ONLY_AUTHORIZATION"
CONTAINER=""
cleanup() {
  local status=$?
  trap - EXIT
  if [ -n "$CONTAINER" ]; then
    timeout 10 docker logs "$CONTAINER" > "$EVIDENCE/postgres_server.log" 2>&1 || true
    timeout 15 docker rm -f -v "$CONTAINER" > "$EVIDENCE/container_cleanup.log" 2>&1 || true
  fi
  rm -rf -- "$TMP"
  printf '{"shell_exit_code":%s}\n' "$status" > "$EVIDENCE/execution_exit.json"
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
python "$ROOT/prepare_bundle.py" "$TMP/reviewed" > "$EVIDENCE/prepared_package.txt"
# Pull is outside the isolated server container. Record and run the exact image ID.
timeout 180 docker pull postgres:18 > "$EVIDENCE/image_pull.log" 2>&1
timeout 10 docker image inspect postgres:18 > "$EVIDENCE/postgres_image.json"
IMAGE_ID="$(docker image inspect postgres:18 --format '{{.Id}}')"
# Keep the image initializer's default socket as well as the private test socket.
CONTAINER="$(docker run -d --name "$NONCE" --network none --memory 768m --cpus 2 \
  --pids-limit 256 --security-opt no-new-privileges=true \
  --tmpfs /var/lib/postgresql:rw,size=512m \
  --mount "type=bind,source=$TMP/socket,target=/lineage-socket" \
  -e POSTGRES_USER=lineage_test -e POSTGRES_DB=lineage_gate_test \
  -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_INITDB_ARGS='--encoding=UTF8 --locale=C' \
  -e PGDATA=/var/lib/postgresql/lineage-data \
  "$IMAGE_ID" postgres -p 55482 -c listen_addresses='' \
  -c 'unix_socket_directories=/var/run/postgresql,/lineage-socket' -c unix_socket_permissions=0777 \
  -c "cluster_name=$NONCE" -c fsync=on -c synchronous_commit=on)"
ready=0
for _ in $(seq 1 45); do
  if timeout 3 docker exec "$CONTAINER" sh -c \
      'test "$(cat /proc/1/comm)" = postgres && pg_isready -h /lineage-socket -p 55482 -U lineage_test -d lineage_gate_test' \
      >/dev/null 2>&1; then ready=1; break; fi
  sleep 1
done
if [ "$ready" != 1 ]; then echo 'POSTGRES_STARTUP_FAILED' >&2; exit 1; fi
export LINEAGE_TEST_SOCKET="$TMP/socket" LINEAGE_CLUSTER_ID="$NONCE"
python -m pip freeze > "$EVIDENCE/python_freeze.txt"
# Original 18 contracts remain byte-for-byte unchanged.
timeout --kill-after=10s 180 python "$ROOT/run_contract_suite.py" \
  "$TMP/reviewed/lineage_db_gate" "$EVIDENCE" 2>&1 | tee "$EVIDENCE/postgres_contracts.txt"
if [[ "${LINEAGE_FULL_REPOSITORY:-0}" == 1 ]]; then
  timeout --kill-after=10s 600 python "$ROOT/run_repository_gate.py" \
    "$TMP/reviewed/lineage_db_gate" "$EVIDENCE" 2>&1 | tee "$EVIDENCE/repository_execution.log"
fi
