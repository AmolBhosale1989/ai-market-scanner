# Isolated PostgreSQL 18 contract execution

Authorized scope: validation/lineage-db-contracts only, based on frozen commit
25c2c8bfca2cb648bf265e0baed5ffe25a6fec69. No PR, merge, Render change, production
connection or external alert send is part of this job.

## Native-upload packaging adaptation

The reviewed local execution bundle contains a binary lineage_db_gate.zip.
For native GitHub text uploads this branch stores the seven unchanged source
files needed by its original 18 PostgreSQL contracts under payload/lineage_db_gate.
prepare_bundle.py validates every source byte hash and the exact test inventory
before copying them to the private temporary directory. The SQL, fixture,
18-test module, persistence adapter and three imported lineage modules are NOT
edited. The PostgreSQL runner, requirements and test runner are unchanged. The workflow
adds an always-run evidence-printing step for connector log access. Preparation/
transport differs, with payload_manifest.json and this note added. Archived prior logs and unrelated unit-test files are not
copied into this runtime payload.

The original archive was SHA-256 verified locally before extraction. CI verifies
its unpacked source files, NOT the binary archive itself; the source archive
hash in the manifests is a reference, not a claim of CI archive verification.

## Evidence and limits

The workflow creates disposable PostgreSQL 18 with network none, disabled TCP
and a private Unix socket; Python executes on the runner host and is NOT network
air-gapped. The runner wipes known production connection/Telegram variables and
verifies a random cluster identity before loading destructive synthetic fixtures.
No production secrets are referenced. Runtime image/dependency versions, all
18 test names/results and container logs are retained even on failure.

PASS requires 18/18 without errors, failures or skips. The reduced fixture does
not certify full repository migrations, production roles, dispatcher recovery,
producer integration, real trading profitability or continuous live freshness.
Original contract_manifest.current_execution_status describes the authoring
baseline only. Use the CI run and its evidence JSON for execution status.
