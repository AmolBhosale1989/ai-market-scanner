"""Copy byte-verified original contract sources from text-only GitHub transport.

Only packaging differs from the reviewed ZIP runner. SQL, fixture, tests and
imported implementation files are checked against their original byte hashes.
"""
from __future__ import annotations
import ast
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent


def main() -> None:
    if len(sys.argv) != 2:
        raise RuntimeError('usage: prepare_bundle.py NEW_DESTINATION')
    destination = Path(sys.argv[1]).resolve()
    if destination.exists():
        raise RuntimeError('DESTINATION_MUST_BE_NEW')
    original = json.loads((ROOT / 'contract_manifest.json').read_text())
    manifest = json.loads((ROOT / 'payload_manifest.json').read_text())
    if manifest['source_archive_sha256'] != original['original_bundle_sha256']:
        raise RuntimeError('SOURCE_ARCHIVE_REFERENCE_MISMATCH')
    source = ROOT / 'payload' / 'lineage_db_gate'
    files = manifest['files']
    inventory = {str(p.relative_to(source)) for p in source.rglob('*') if p.is_file()}
    if inventory != set(files):
        raise RuntimeError('PAYLOAD_FILE_INVENTORY_MISMATCH')
    verified = {}
    for name, expected in files.items():
        path = Path(name)
        if path.is_absolute() or '..' in path.parts or (source / path).is_symlink():
            raise RuntimeError('UNSAFE_PAYLOAD_PATH')
        data = (source / path).read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise RuntimeError('PAYLOAD_BYTES_CHANGED:' + name)
        verified[name] = data
    for name, field in [('sql/lineage_gate_DRAFT.sql', 'schema_sha256'),
                        ('tests/postgres_contracts.py', 'test_module_sha256')]:
        if files[name] != original[field]:
            raise RuntimeError('ORIGINAL_CONTRACT_HASH_MISMATCH')
    tree = ast.parse(verified['tests/postgres_contracts.py'])
    names = sorted(node.name for node in ast.walk(tree)
                   if isinstance(node, ast.FunctionDef) and node.name.startswith('test_'))
    if names != original['expected_test_names'] or len(names) != 18:
        raise RuntimeError('ORIGINAL_TEST_INVENTORY_MISMATCH')
    for name, data in verified.items():
        target = destination / 'lineage_db_gate' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    print(json.dumps({'status': 'VERIFIED', 'transport': 'unpacked_sources',
                      'source_archive_sha256_reference': manifest['source_archive_sha256'],
                      'archive_bytes_reverified_in_ci': False,
                      'verified_source_files': files, 'original_test_count': len(names)}, indent=2))


if __name__ == '__main__':
    main()
