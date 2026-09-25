import os
import sys
import time
import pytest
from scanner.worker_supervisor import supervise


def test_initial_lease_failure_never_starts_child(tmp_path):
    marker = tmp_path / 'started'
    def lost():
        raise RuntimeError('PIPELINE_LEASE_LOST')
    with pytest.raises(RuntimeError, match='LEASE_LOST'):
        supervise([sys.executable,'-c',f'open({str(marker)!r},"w").write("bad")'],lost)
    assert not marker.exists()


def test_heartbeat_failure_kills_descendant_before_write(tmp_path):
    marker = tmp_path / 'rogue-write'
    ready = tmp_path / 'ready'
    grandchild = f'import time; time.sleep(0.8); open({str(marker)!r},"w").write("bad")'
    worker = f'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",{grandchild!r}]); open({str(ready)!r},"w").write("ready"); time.sleep(10)'
    checks=[]
    def renew():
        checks.append(1)
        if ready.exists():
            raise ConnectionError('heartbeat database disconnected')
    with pytest.raises(ConnectionError):
        supervise([sys.executable,'-c',worker],renew,heartbeat_seconds=0.05)
    time.sleep(1)
    assert ready.exists()
    assert len(checks)>1
    assert not marker.exists()


def test_normal_completion_preserves_exit_code():
    assert supervise([sys.executable,'-c','raise SystemExit(7)'],lambda:None,heartbeat_seconds=0.1)==7
