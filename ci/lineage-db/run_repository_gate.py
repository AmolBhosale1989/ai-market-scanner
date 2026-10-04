"""Full-schema compatibility and application-entry requirements, isolated only.

Assembles a candidate from the exact frozen application tree plus the unchanged
reviewed SQL as a ninth migration. This is NOT a consolidated producer/outbox
implementation. Diagnostic workflows are outside that assembled product tree;
its original production boundary audit remains unchanged and must pass.
"""
from __future__ import annotations
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parent
BASE='25c2c8bfca2cb648bf265e0baed5ffe25a6fec69'


def write_json(path, value):
    path.write_text(json.dumps(value,indent=2,default=str)+'\n')


def command(args,cwd,env,log,timeout=360):
    with log.open('w') as stream:
        try:
            run=subprocess.run(args,cwd=cwd,env=env,stdout=stream,stderr=subprocess.STDOUT,timeout=timeout,check=False)
            return run.returncode
        except subprocess.TimeoutExpired:
            stream.write('\nSUBPROCESS_TIMEOUT\n')
            return 124


class EvidenceResult(unittest.TextTestResult):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.records=[]
    def startTest(self,test):
        self.at=time.monotonic()
        super().startTest(test)
    def record(self,test,status,detail=None):
        self.records.append(dict(test=test.id(),status=status,seconds=round(time.monotonic()-self.at,6),detail=detail,evidence=getattr(test,"evidence",None)))
    def addSuccess(self,test):
        super().addSuccess(test);self.record(test,'PASS')
    def addFailure(self,test,err):
        super().addFailure(test,err);self.record(test,'FAIL',self._exc_info_to_string(err,test))
    def addError(self,test,err):
        super().addError(test,err);self.record(test,'ERROR',self._exc_info_to_string(err,test))
    def addSkip(self,test,reason):
        super().addSkip(test,reason);self.record(test,'SKIP',reason)


def main():
    package,evidence=map(lambda s:Path(s).resolve(),sys.argv[1:])
    nonce=os.environ['LINEAGE_CLUSTER_ID']
    socket=Path(os.environ['LINEAGE_TEST_SOCKET']).resolve()
    if (not str(socket).startswith('/tmp/market-hunt-lineage-') or socket.name!='socket'
        or (socket.parent/'LOCAL_ONLY_AUTHORIZATION').read_text().strip()!=nonce):
        raise RuntimeError('ISOLATED_AUTHORIZATION_MISMATCH')
    import psycopg
    info=dict(host=str(socket),port=55482,user='lineage_test',dbname='lineage_gate_test',sslmode='disable',connect_timeout=3)
    with psycopg.connect(**info) as db:
        row=db.execute("SELECT current_database(),current_user,inet_server_addr(),current_setting('cluster_name'),current_setting('server_version_num')::int").fetchone()
        if row[:4]!=('lineage_gate_test','lineage_test',None,nonce) or row[4]//10000!=18:
            raise RuntimeError('WRONG_DATABASE')
        db.execute('DROP SCHEMA IF EXISTS lineage_draft CASCADE; DROP SCHEMA public CASCADE; CREATE SCHEMA public')
    temp=Path(tempfile.mkdtemp(prefix='repository-',dir=socket.parent))
    archive=temp/'source.tar'
    with archive.open('wb') as stream:
        subprocess.run(['git','archive',BASE],stdout=stream,check=True,timeout=20)
    source=temp/'source';source.mkdir()
    with tarfile.open(archive) as tar:
        tar.extractall(source,filter='data')
    migration=package/'sql/lineage_gate_DRAFT.sql'
    raw=migration.read_bytes()
    manifest=json.loads((ROOT/'contract_manifest.json').read_text())
    if hashlib.sha256(raw).hexdigest()!=manifest['schema_sha256']:
        raise RuntimeError('REVIEWED_SQL_CHANGED')
    (source/'sql/009_lineage_gate_DRAFT.sql').write_bytes(raw)
    hardening=(ROOT/'hardening/010_authority_and_fencing.sql').read_bytes()
    (source/'sql/010_authority_and_fencing.sql').write_bytes(hardening)
    provenance=dict(ci_head_sha=os.environ.get('GITHUB_SHA'),application_baseline=BASE,
        scope='frozen application + original 009 + candidate 010 authority; candidate adapter/dispatcher exercised only in integration tests, not live producers',
        hardening_sql_sha256=hashlib.sha256(hardening).hexdigest(),
        hardening_modules={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'hardening').glob('*.py')},
        candidate_migration_sha256=hashlib.sha256(raw).hexdigest(),
        source_tar_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        source_export='git archive of frozen BASE; diagnostic CI packaging not included in product-tree boundary audit',
        integration_tests_sha256=hashlib.sha256((ROOT/'repository_contracts.py').read_bytes()).hexdigest())
    write_json(evidence/'repository_provenance.json',provenance)
    env=dict(os.environ,DATABASE_URL=psycopg.conninfo.make_conninfo(**info),PGSSLMODE='disable',PYTHONPATH=str(source))
    for key in ('PRODUCTION_RUN_ID','WAREHOUSE_CONSUMER_SNAPSHOT','TELEGRAM_BOT_TOKEN','TELEGRAM_CHAT_ID'):
        env.pop(key,None)
    statuses={}
    statuses['full_migrations']=command([sys.executable,'-m','scanner.control_plane','migrate'],source,env,evidence/'repository_migrations.log')
    statuses['repository_boundary_audit']=command([sys.executable,'-m','scanner.production_audit'],source,env,evidence/'repository_audit.log')
    junit=evidence/'repository_pytest.xml'
    statuses['full_repository_tests']=command([sys.executable,'-m','pytest','-q','tests','--junitxml='+str(junit)],source,env,evidence/'repository_pytest.log',480)
    counts={}
    if junit.exists():
        suites=ET.parse(junit).getroot().findall('testsuite')
        counts={key:sum(int(s.get(key,0)) for s in suites) for key in ('tests','failures','errors','skipped')}
    write_json(evidence/'repository_suite_result.json',dict(exit_codes=statuses,pytest=counts,**provenance))
    if statuses['full_migrations']:
        return 1
    os.environ.update(DATABASE_URL=env['DATABASE_URL'],PGSSLMODE='disable',
        LINEAGE_VERIFIED_PACKAGE=str(package),LINEAGE_REPOSITORY_SOURCE=str(source))
    sys.path.insert(0,str(source))
    spec=importlib.util.spec_from_file_location('repository_contracts',ROOT/'repository_contracts.py')
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(module.RepositoryContracts)
    expected=suite.countTestCases()
    with (evidence/'repository_contracts.log').open('w') as stream:
        result=unittest.TextTestRunner(stream=stream,verbosity=2,resultclass=EvidenceResult).run(suite)
    ok=result.wasSuccessful() and result.testsRun==expected==44 and not result.skipped
    summary=dict(status='PASS' if ok else 'FAIL',tests=result.testsRun,failures=len(result.failures),
        errors=len(result.errors),skipped=len(result.skipped),test_results=result.records,production_validation=False)
    write_json(evidence/'repository_contract_result.json',summary)
    print(json.dumps(summary,indent=2))
    full_ok=all(value==0 for value in statuses.values()) and counts.get('tests',0)==708 and counts.get('skipped',0)==0
    return 0 if ok and full_ok else 1


if __name__=='__main__':
    raise SystemExit(main())
