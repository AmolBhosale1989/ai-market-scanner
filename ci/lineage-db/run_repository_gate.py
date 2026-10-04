"""Execute the checked-out application's actual migrations and modules, not an overlay."""
from __future__ import annotations
import hashlib
import importlib
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
MIGRATIONS=('V0042__lineage_and_transactional_outbox.sql','V0043__lineage_role_grants.sql')

def write_json(path,value):
    path.write_text(json.dumps(value,indent=2,default=str)+'\n')

def command(args,cwd,env,log,timeout=480):
    with log.open('w') as stream:
        try:return subprocess.run(args,cwd=cwd,env=env,stdout=stream,stderr=subprocess.STDOUT,timeout=timeout,check=False).returncode
        except subprocess.TimeoutExpired:
            stream.write('\nSUBPROCESS_TIMEOUT\n');return 124

class EvidenceResult(unittest.TextTestResult):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self.records=[]
    def startTest(self,test):
        self.at=time.monotonic();super().startTest(test)
    def record(self,test,status,detail=None):
        self.records.append(dict(test=test.id(),status=status,seconds=round(time.monotonic()-self.at,6),
            detail=detail,evidence=getattr(test,'evidence',None)))
    def addSuccess(self,test):super().addSuccess(test);self.record(test,'PASS')
    def addFailure(self,test,err):super().addFailure(test,err);self.record(test,'FAIL',self._exc_info_to_string(err,test))
    def addError(self,test,err):super().addError(test,err);self.record(test,'ERROR',self._exc_info_to_string(err,test))
    def addSkip(self,test,reason):super().addSkip(test,reason);self.record(test,'SKIP',reason)


def main():
    _,evidence=map(lambda x:Path(x).resolve(),sys.argv[1:])
    nonce=os.environ['LINEAGE_CLUSTER_ID'];socket=Path(os.environ['LINEAGE_TEST_SOCKET']).resolve()
    if not str(socket).startswith('/tmp/market-hunt-lineage-') or socket.name!='socket' or (socket.parent/'LOCAL_ONLY_AUTHORIZATION').read_text().strip()!=nonce:
        raise RuntimeError('ISOLATED_AUTHORIZATION_MISMATCH')
    import psycopg
    info=dict(host=str(socket),port=55482,user='lineage_test',dbname='lineage_gate_test',sslmode='disable',connect_timeout=3)
    with psycopg.connect(**info) as db:
        row=db.execute("SELECT current_database(),current_user,inet_server_addr(),current_setting('cluster_name'),current_setting('server_version_num')::int").fetchone()
        if row[:4]!=('lineage_gate_test','lineage_test',None,nonce) or row[4]//10000!=18:raise RuntimeError('WRONG_DATABASE')
        db.execute('DROP SCHEMA IF EXISTS lineage_draft CASCADE; DROP SCHEMA IF EXISTS lineage CASCADE; DROP SCHEMA public CASCADE; CREATE SCHEMA public')
    head=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
    if head!=os.environ['GITHUB_SHA']:raise RuntimeError('CHECKOUT_SHA_MISMATCH')
    temp=Path(tempfile.mkdtemp(prefix='repository-',dir=socket.parent));archive=temp/'source.tar'
    with archive.open('wb') as out:subprocess.run(['git','archive',head],stdout=out,check=True,timeout=30)
    source=temp/'source';source.mkdir()
    with tarfile.open(archive) as tar:tar.extractall(source,filter='data')
    names=sorted(p.name for p in (source/'sql').glob('*.sql'))
    if len(names)!=10 or names[-2:]!=list(MIGRATIONS):raise RuntimeError('ACTUAL_MIGRATION_INVENTORY_MISMATCH')
    files=[p for folder in ('sql','scanner','tests/integration') for p in (source/folder).rglob('*') if p.is_file()]
    provenance=dict(ci_head_sha=head,application_commit=head,baseline_ancestor=BASE,
        scope='actual tracked sql/ and scanner/ plus integration tests; no runtime source overlay',
        file_hashes={str(p.relative_to(source)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files})
    write_json(evidence/'repository_provenance.json',provenance)
    env=dict(os.environ,DATABASE_URL=psycopg.conninfo.make_conninfo(**info),PGSSLMODE='disable',PYTHONPATH=str(source))
    for key in ('PRODUCTION_RUN_ID','WAREHOUSE_CONSUMER_SNAPSHOT','TELEGRAM_BOT_TOKEN','TELEGRAM_CHAT_ID'):env.pop(key,None)
    statuses={}
    statuses['full_migrations']=command([sys.executable,'-m','scanner.control_plane','migrate'],source,env,evidence/'repository_migrations.log')
    if statuses['full_migrations']:
        write_json(evidence/'repository_suite_result.json',dict(status='MIGRATION_FAILED',exit_codes=statuses,pytest=None,application_commit=head));return 1
    with psycopg.connect(**info) as db:
        ledger=db.execute('SELECT migration_name,checksum,applied_at FROM public.schema_migration ORDER BY migration_name').fetchall()
        for name,digest,_ in ledger:
            if digest!=hashlib.sha256((source/'sql'/name).read_bytes()).hexdigest():raise RuntimeError('APPLIED_CHECKSUM_MISMATCH')
        pk=db.execute("SELECT c.relkind,pg_get_constraintdef(k.oid) FROM pg_class c JOIN pg_constraint k ON k.conrelid=c.oid AND k.contype='p' WHERE c.oid='public.market_observation'::regclass").fetchone()
        roles=db.execute("SELECT rolname,rolsuper,rolcreatedb,rolcreaterole,rolcanlogin FROM pg_roles WHERE rolname IN ('scanner_writer','dispatcher_worker') ORDER BY rolname").fetchall()
        ledger_doc=dict(migrations=[dict(name=n,checksum=d,applied_at=t) for n,d,t in ledger],market_observation_key=pk,roles=roles)
    write_json(evidence/'migration_ledger.json',ledger_doc)
    with (evidence/'migration_verification.log').open('w') as out:
        for name,digest,at in ledger:out.write(f'VERIFIED_APPLIED {name} sha256={digest} applied_at={at.isoformat()}\n')
    statuses['repository_boundary_audit']=command([sys.executable,'-m','scanner.production_audit'],source,env,evidence/'repository_audit.log')
    junit=evidence/'repository_pytest.xml'
    statuses['full_repository_tests']=command([sys.executable,'-m','pytest','-q','tests','--ignore=tests/integration','--junitxml='+str(junit)],source,env,evidence/'repository_pytest.log')
    counts={}
    if junit.exists():
        suites=ET.parse(junit).getroot().findall('testsuite')
        counts={key:sum(int(s.get(key,0)) for s in suites) for key in ('tests','failures','errors','skipped')}
    write_json(evidence/'repository_suite_result.json',dict(exit_codes=statuses,pytest=counts,application_commit=head))
    os.environ.update(DATABASE_URL=env['DATABASE_URL'],PGSSLMODE='disable',LINEAGE_REPOSITORY_SOURCE=str(source))
    sys.path.insert(0,str(source))
    module=importlib.import_module('tests.integration.test_lineage_contracts')
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(module.RepositoryContracts)
    boundary=importlib.import_module('tests.integration.test_completed_bar_boundary')
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(boundary.CompletedBarContracts))
    expected=suite.countTestCases()
    with (evidence/'repository_contracts.log').open('w') as stream:
        result=unittest.TextTestRunner(stream=stream,verbosity=2,resultclass=EvidenceResult).run(suite)
    ok=result.wasSuccessful() and result.testsRun==expected==57 and not result.skipped
    summary=dict(status='PASS' if ok else 'FAIL',tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),
        skipped=len(result.skipped),test_results=result.records,application_commit=head,production_validation=False,
        scope='native application modules, partitioned DB and nonsuperuser test logins; no live cutover or external sends')
    write_json(evidence/'repository_contract_result.json',summary);print(json.dumps(summary,indent=2))
    return 0 if ok and all(v==0 for v in statuses.values()) and counts==dict(tests=708,failures=0,errors=0,skipped=0) else 1

if __name__=='__main__':raise SystemExit(main())
