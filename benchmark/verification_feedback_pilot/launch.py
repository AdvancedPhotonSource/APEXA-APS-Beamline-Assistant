#!/usr/bin/env python3
"""Admit the frozen pilot after numerical qualification, never an expansion."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from prepare_fixtures import write_json


def main(root):
    state_file=root/'setup/launch-status.json'
    def state(status,**extra):
        write_json(state_file,{'status':status,'pid':os.getpid(),'updated_unix':time.time(),**extra})
    try:
        deadline=time.monotonic()+5400
        while True:
            preparation=json.loads((root/'setup/preparation-status.json').read_text())
            if preparation['status']=='fixtures_qualified_agent_setup_required':break
            if preparation['status']=='fixture_qualification_failed':
                state('stopped_fixture_qualification_failed');return 1
            if time.monotonic()>deadline:
                state('stopped_preparation_deadline');return 1
            state('waiting_for_numerical_preparation',completed_fixtures=preparation['completed'])
            time.sleep(15)
        state('validating_setup')
        spec=json.loads((root/'setup/study.json').read_text())
        source=Path(spec.get('source_root',root/'runtime/apexa'))
        env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHON_DOTENV_DISABLED='1')
        phases=[('assessment_tests',[sys.executable,'-m','unittest','discover','-s',str(root/'setup'),'-p','test_*.py'])]
        if not (root/'setup/agent_qualification_final.json').exists():
            phases.append(('agent_qualification',[sys.executable,str(root/'setup/run_pilot.py'),'qualify','--root',str(root)]))
        if not spec.get('reuse_qualified_inputs',False):
            phases.append(('public_handoffs',[sys.executable,str(root/'setup/handoffs.py'),'--root',str(root)]))
        phases += [
            ('freeze',[sys.executable,str(root/'setup/run_pilot.py'),'freeze','--root',str(root)]),
            ('pilot',[sys.executable,str(root/'setup/run_pilot.py'),'run','--root',str(root)])]
        for phase,command in phases:
            state('running',phase=phase)
            with (root/'setup'/f'{phase}.log').open('x') as log:
                child=subprocess.Popen(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
                state('running',phase=phase,child_pid=child.pid)
                code=child.wait()
            if code:raise RuntimeError(f'{phase} exited {code}; no automatic retry')
        terminal=json.loads((root/'setup/trial-status.json').read_text())
        state(terminal['status'],completed=terminal['completed'],scheduled=terminal['scheduled'])
        return 0
    except Exception as exc:
        state('stopped',error=type(exc).__name__+': '+str(exc));raise


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True)
    raise SystemExit(main(p.parse_args().root.resolve()))
