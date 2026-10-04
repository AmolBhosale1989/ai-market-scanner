"""Run the unchanged 18 contracts, with a verified ephemeral PG18 identity.

No arbitrary DSN, TCP endpoint, external sender, or production credential is used.
The parent shell creates the isolated database and private authorization marker.
"""
from __future__ import annotations
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
import unittest
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent


def flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flatten(item)
        else:
            yield item


def main() -> int:
    if len(sys.argv) != 3:
        raise RuntimeError('usage: run_contract_suite.py EXTRACTED_PACKAGE NEW_EVIDENCE_DIRECTORY')
    package, evidence = map(lambda x: Path(x).resolve(), sys.argv[1:])
    evidence.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((ROOT / 'contract_manifest.json').read_text())
    test_file = package / 'tests/postgres_contracts.py'
    if hashlib.sha256(test_file.read_bytes()).hexdigest() != manifest['test_module_sha256']:
        raise RuntimeError('TEST_BYTES_CHANGED')
    if hashlib.sha256((package / 'sql/lineage_gate_DRAFT.sql').read_bytes()).hexdigest() != manifest['schema_sha256']:
        raise RuntimeError('SCHEMA_BYTES_CHANGED')
    socket = Path(os.environ.get('LINEAGE_TEST_SOCKET', '/not-authorized')).resolve()
    nonce = os.environ.get('LINEAGE_CLUSTER_ID', '')
    if (not str(socket).startswith('/tmp/market-hunt-lineage-') or socket.name != 'socket'
            or not nonce.startswith('market-hunt-contracts-')
            or not (socket.parent / 'LOCAL_ONLY_AUTHORIZATION').is_file()
            or (socket.parent / 'LOCAL_ONLY_AUTHORIZATION').read_text().strip() != nonce):
        raise RuntimeError('ISOLATED_CLUSTER_AUTHORIZATION_MISSING')
    import psycopg
    with psycopg.connect(host=str(socket), port=55482, dbname='lineage_gate_test', user='lineage_test',
                         connect_timeout=3, options='-c statement_timeout=5000 -c timezone=UTC') as db:
        row = db.execute("""SELECT version(),current_setting('server_version_num')::int,
           current_database(),current_user,inet_server_addr()::text,
           current_setting('listen_addresses'),current_setting('cluster_name'),
           current_setting('fsync'),current_setting('synchronous_commit'),
           current_setting('transaction_isolation'),pg_current_snapshot()::text""").fetchone()
        if (row[1] // 10000 != 18 or row[2:4] != ('lineage_gate_test', 'lineage_test')
                or row[4] is not None or row[5] != '' or row[6] != nonce
                or row[7:9] != ('on', 'on')):
            raise RuntimeError('POSTGRES_RUNTIME_IDENTITY_MISMATCH')
        identity = dict(zip(('version','server_version_num','database','db_user','tcp_address',
                            'listen_addresses','cluster_name','fsync','synchronous_commit',
                            'isolation','initial_pg_snapshot'),row))
        identity.update(psycopg_version=psycopg.__version__, python=sys.version,
                        baseline_commit=manifest['baseline_commit'],
                        ci_head_sha=os.environ.get('GITHUB_SHA'),
                        checked_at_utc=datetime.now(timezone.utc).isoformat())
    (evidence / 'postgres_identity.json').write_text(json.dumps(identity,indent=2)+'\n')
    spec = importlib.util.spec_from_file_location('reviewed_postgres_contracts', test_file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    names = sorted(t._testMethodName for t in flatten(suite))
    if names != manifest['expected_test_names']:
        raise RuntimeError('RUNTIME_TEST_INVENTORY_MISMATCH')
    started = time.monotonic()
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    passed = result.wasSuccessful() and result.testsRun == 18 and not result.skipped
    summary = dict(status='PASS' if passed else 'FAIL', test_cases_reported_run=result.testsRun,
                   failures=len(result.failures), errors=len(result.errors), skipped=len(result.skipped),
                   elapsed_seconds=round(time.monotonic()-started,6),
                   failure_ids=[t.id() for t,_ in result.failures],
                   error_ids=[t.id() for t,_ in result.errors],
                   schema_scope=manifest['schema_scope'],
                   production_validation=False, test_names=names)
    (evidence / 'postgres_contract_result.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2))
    return 0 if passed else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f'EXECUTION_GATE_FAILED:{type(exc).__name__}:{exc}',file=sys.stderr)
        raise
