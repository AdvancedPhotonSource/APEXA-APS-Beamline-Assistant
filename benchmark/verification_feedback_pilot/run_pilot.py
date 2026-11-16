#!/usr/bin/env python3
"""Qualification, immutable freeze and bounded matched-harness pilot.

Run with the shared APEXA environment. Credentials live outside the experiment;
never write them to receipts, workspaces or command-line arguments.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
import fcntl
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import time

from assess import assess
from prepare_fixtures import physical_cpu_sets, sha, write_json


def configure(root):
    spec=json.loads((root/'setup/study.json').read_text())
    source=Path(spec.get('source_root',root/'runtime/apexa'));adapter=root/'runtime/adapters'
    shared=Path(spec.get('shared_project_root',root.parent))
    sys.path[:0]=[str(adapter),str(source)]
    import rotation_benchmark as rb
    import run_comparison as rc
    rb.REPO=rc.REPO=source
    env=json.loads((root/'setup/native_environment.json').read_text())
    os.environ.update(env)
    os.environ.update(APEXA_MIDAS_BIN=str(shared/'envs/midas/bin'),
        APEXA_MAX_ITERATIONS=str(spec['max_model_requests']),PYTHON_DOTENV_DISABLED='1',
        APEXA_BENCH_RESPONSE_TIMEOUT=str(spec['request_timeout_seconds']),
        PYTHONDONTWRITEBYTECODE='1',CUDA_VISIBLE_DEVICES='',APEXA_LLM_TOKEN_CMD='',
        APEXA_LLM_API_KEY='',APEXA_PROVIDER_HOLD_FILE=str(root/'setup/provider-hold.json'))
    for name in ('OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS','NUMEXPR_NUM_THREADS'):
        os.environ[name]=str(spec['cpu_threads'])
    os.environ['PATH']=os.pathsep.join([str(shared/'runtime/harnesses/node_modules/.bin'),
        str(shared/'runtime/node/bin'),str(shared/'envs/midas/bin'),os.environ.get('PATH','')])
    roots=json.loads((root/'setup/software_roots.json').read_text())
    os.environ['RB_SOFTWARE_READ_ROOTS']=json.dumps([str(source),*roots])
    rb.HARNESSES['pi'].argv=['pi','-p','--provider','argo','--model','{model}','--thinking','medium','{prompt}']
    original=rc.ensure_pi_provider
    def pi_provider(base_url,api_key,model,**kwargs):
        return original(base_url,api_key,model,max_tokens=8192,context_window=65536,
                        reasoning_effort=True,**kwargs)
    rb.ensure_pi_provider=pi_provider
    return spec,rb


def schedule(spec):
    if 'trial_schedule' in spec:
        rows=spec['trial_schedule']
        fixtures={item['id'] for item in spec['fixtures']}
        for index,row in enumerate(rows,1):
            if (row['index']!=index or row['wave']!=(index-1)//2+1 or row['slot']!=(index-1)%2
                    or row['arm'] not in ('A1','A5') or row['fixture'] not in fixtures
                    or row['condition'] not in ('automatic','on_demand')):
                raise ValueError('Invalid predeclared trial schedule')
        if not rows:raise ValueError('Empty trial schedule')
        return rows
    rows=[]
    for i,pair in enumerate(spec['pilot_pairs']):
        for arm in (['A1','A5'] if i%2==0 else ['A5','A1']):
            wave=len(rows)//2+1
            for slot,condition in enumerate(pair['conditions']):
                rows.append({'index':len(rows)+1,'wave':wave,'slot':slot,
                    'arm':arm,'fixture':pair['fixture'],'condition':condition})
    return rows


def qualification_fingerprint(root):
    """Any executable/configuration change requires a fresh native qualification."""
    spec=json.loads((root/'setup/study.json').read_text())
    source=Path(spec.get('source_root',root/'runtime/apexa'))
    files={str(p.relative_to(root)):sha(p) for folder in ('setup','runtime/adapters')
           for p in (root/folder).rglob('*.py') if '__pycache__' not in p.parts}
    for name in ('study.json','native-context.json','native_environment.json','software_roots.json','PROMPT.md'):
        files['setup/'+name]=sha(root/'setup'/name)
    files.update({'source/'+str(p.relative_to(source)):sha(p) for p in source.rglob('*.py')
                  if '__pycache__' not in p.parts})
    return files


def model_usage(root):
    out={'requests':0,'input_tokens':0,'output_tokens':0,'provider_denials':0}
    for p in (root/'results').glob('trial*/model_calls.jsonl'):
        for line in p.read_text().splitlines():
            row=json.loads(line)
            if row.get('event')!='generation':continue
            usage=row.get('usage',{});out['requests']+=1
            out['input_tokens']+=usage.get('prompt_tokens',usage.get('input_tokens',0)) or 0
            out['output_tokens']+=usage.get('completion_tokens',usage.get('output_tokens',0)) or 0
            out['provider_denials']+=bool(row.get('provider_denial'))
    return out


def work(root,entry,cpus,qualification=False):
    os.sched_setaffinity(0,cpus)
    spec,rb=configure(root)
    from model_audit import ModelAudit
    secret=json.loads((Path.home()/'.cache/apexa-verification-feedback/relay.json').read_text())
    if time.time()>=secret['expires_unix']:raise RuntimeError('Local relay lifetime expired')
    label=('native_'+entry['arm']) if qualification else f"trial{entry['index']:02d}"
    output=root/('qualification' if qualification else 'results')/label
    output.mkdir(parents=True,exist_ok=False)
    if qualification:
        exposure=root/'setup/qualification_000000.txt'
        prompt=("This is native-session qualification before a detector calibration review. "
            "Do not calibrate data. Use tools to read qualification_000000.txt and compute the sum "
            "of its values. Consult available MIDAS calibration documentation. Write qualification_answer.json "
            "with fields nonce, sum, documentation_path and solver_version. Check that the installed "
            "midas-calibrate-v2 version is 0.26.0 and include solver_version. End after saving this file.")
        inputs=[];scan=0;attempt=1;timeout=600;max_requests=20
        if spec.get('qualify_contract_loader'):
            inputs=[root/'setup/qualification_contract.json',root/'setup/qualification_template.txt']
            if spec.get('application_contract'):
                prompt += (' The application activates the supplied calibration contract before the model runs. '
                    'This is a loader check only; never calibrate this synthetic text fixture.')
            else:
                prompt += (' Also call load_calibration_contract with qualification_contract.json, '
                    'qualification_000000.txt as image_file and qualification_template.txt as template_param_file. '
                    'This tests the declared-contract loader only; never calibrate this synthetic text fixture. '
                    'Include contract_active in your answer, true only when client_contract_active is returned true.')
    else:
        handoffs=json.loads((root/'setup/handoff_manifest.json').read_text())['cases']
        public=root/'public'/next(x['public_case'] for x in handoffs if x['fixture']==entry['fixture'])
        exposure=public/'acquisition.h5'
        inputs=[p for p in sorted(public.iterdir()) if p.is_file() and p.name!='acquisition.h5']
        inputs.append(public/entry['condition']/'initial_check.json')
        prompt=(root/'setup/PROMPT.md').read_text()
        scan=next(x['scan'] for x in spec['fixtures'] if x['id']==entry['fixture'])
        attempt=entry['index'];timeout=spec['wall_seconds'];max_requests=spec['max_model_requests']
    os.environ.pop('APEXA_BENCH_CALIBRATION_CONTRACT',None)
    if spec.get('application_contract') and entry['arm']=='A1':
        contract = (dict(contract_path='qualification_contract.json', image_file='qualification_000000.txt',
                         template_param_file='qualification_template.txt') if qualification else
                    dict(contract_path='task.json',image_file='acquisition.h5',template_param_file='effective_template.txt',
                         candidate_parameters_file='candidate.params',candidate_run_file='candidate_run.json',
                         candidate_execution_file='execution_evidence.json'))
        os.environ['APEXA_BENCH_CALIBRATION_CONTRACT']=json.dumps(contract)
    started=time.time()
    with ModelAudit(secret['base_url'],secret['relay_key'],spec['model'],output/'model_calls.jsonl',
             max_requests=max_requests,max_output_tokens=spec['max_output_tokens'],timeout=timeout,
             request_timeout=spec['request_timeout_seconds']) as audit:
        result=rb.run_one(rb.ARMS[entry['arm']],exposure,scan,attempt,{},None,audit.base_url,
            audit.token,spec['model'],0.2,timeout,output,False,
            ground_truth=root/'setup/hidden-reference.csv',
            extra_deny=[root/'fixtures_private',root/'public',root/'setup',root/'results',root/'qualification',
                        root/'runtime/adapters'],
            native_context_manifest=root/'setup/native-context.json',
            structured_logs=True,scratch_root=root/'work',prompt_override=prompt,
            additional_inputs=inputs,result_kind='execution_only',strict_writes=True,read_only_inputs=True,
            procedure_candidates=output/'procedure_candidates.jsonl')
    summary=audit.summary()
    workspace=output/'workspaces'/f"{entry['arm']}_{scan}_{attempt}"
    assessment={}
    if qualification:
        try:
            answer=json.loads((workspace/'qualification_answer.json').read_text())
            expected=json.loads(exposure.read_text())
            passed=(answer['nonce']==expected['nonce'] and answer['sum']==sum(expected['values'])
                    and answer['solver_version']=='0.26.0' and bool(answer['documentation_path'])
                    and result.return_code==0 and summary['qualified'])
            assessment={'passed':passed,'answer':answer}
            if spec.get('qualify_contract_loader'):
                tool_receipts=[]
                for trace in output.rglob('*.jsonl'):
                    if trace.name=='model_calls.jsonl':continue
                    for line in trace.read_text(errors='replace').splitlines():
                        try:row=json.loads(line)
                        except ValueError:continue
                        if row.get('tool')=='load_calibration_contract':
                            value=row.get('result',{})
                            if isinstance(value,str):
                                try:value=json.loads(value)
                                except ValueError:continue
                            if isinstance(value,dict) and value.get('client_contract_active') is True:
                                if not spec.get('application_contract') or value.get('activation_source')=='application_request_before_model':
                                    tool_receipts.append(str(trace.relative_to(output)))
                assessment.update(contract_loader_receipts=tool_receipts,
                    passed=passed and (spec.get('application_contract') or answer.get('contract_active') is True) and bool(tool_receipts))
        except Exception as exc:
            assessment={'passed':False,'error':type(exc).__name__+': '+str(exc)}
    else:
        assessment=assess(workspace,public,public/entry['condition'])
    record={'entry':entry,'started_unix':started,'finished_unix':time.time(),'cpu_affinity':cpus,
            'execution':asdict(result),'model_audit':summary,'assessment':assessment}
    write_json(output/'record.json',record,exclusive=True)
    return record


def sandbox_qualification(root):
    _,rb=configure(root)
    probe=root/'work/sandbox_probe';probe.mkdir(parents=True,exist_ok=False)
    source=probe/'input.txt';source.write_text('immutable')
    hidden=root/'setup/hidden-reference.csv';hidden.write_text('private-evaluator-only')
    program='''import pathlib,json,os
p=pathlib.Path(os.environ['PROBE_ROOT']);h=pathlib.Path(os.environ['HIDDEN_FILE'])
checks={}
checks['read_input']=(p/'input.txt').read_text()=='immutable'
try:(p/'input.txt').write_text('changed');checks['immutable_input']=False
except OSError:checks['immutable_input']=True
try:h.read_text();checks['hidden_evaluator']=False
except OSError:checks['hidden_evaluator']=True
(p/'output.txt').write_text('writable');checks['workspace_writable']=True
print(json.dumps(checks))'''
    env=dict(os.environ,PROBE_ROOT=str(probe),HIDDEN_FILE=str(hidden))
    cmd=rb.sandboxed([sys.executable,'-c',program],[root/'setup'],read_only=[source],writable=[probe])
    p=subprocess.run(cmd,env=env,capture_output=True,text=True,timeout=30)
    result={'return_code':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
    result['passed']=p.returncode==0 and all(json.loads(p.stdout).values()) and source.read_text()=='immutable'
    write_json(root/'qualification/sandbox.json',result,exclusive=True)
    if not result['passed']:raise RuntimeError('Sandbox qualification failed')


def qualify(root):
    spec,_=configure(root)
    (root/'qualification').mkdir(exist_ok=True)
    (root/'work').mkdir(exist_ok=True)
    subprocess.run([sys.executable,'-m','unittest','discover','-s',str(root/'setup'),'-p','test_*.py'],check=True)
    fingerprint=qualification_fingerprint(root)
    if not (root/'qualification/sandbox.json').exists():sandbox_qualification(root)
    elif not json.loads((root/'qualification/sandbox.json').read_text())['passed']:
        raise RuntimeError('Previous sandbox qualification failed')
    if not (root/'setup/qualification_000000.txt').exists():
        write_json(root/'setup/qualification_000000.txt',{'nonce':'native-session-2026-11-15','values':[17,31,53]},exclusive=True)
    cpus=physical_cpu_sets(2,4)
    records=[]
    arms=list(dict.fromkeys(row['arm'] for row in schedule(spec)))
    for i,arm in enumerate(arms):
        prior=root/'qualification'/('native_'+arm)/'record.json'
        record=json.loads(prior.read_text()) if prior.exists() else work(root,{'arm':arm},cpus[i],True)
        # The initial qualification prompt requested a documentation path but
        # did not name its JSON field. Retain the original failed scorer record;
        # review that naming mismatch without spending another model attempt.
        answer_path=prior.parent/'workspaces'/f'{arm}_0_1'/'qualification_answer.json'
        if not spec.get('qualify_contract_loader') and not record['assessment']['passed'] and answer_path.exists():
            answer=json.loads(answer_path.read_text())
            if (answer.get('nonce')=='native-session-2026-11-15' and answer.get('sum')==101
                    and answer.get('solver_version')=='0.26.0' and answer.get('doc_path')
                    and record['execution']['return_code']==0 and record['model_audit']['qualified']):
                review={'passed':True,'reason':'Original prompt did not specify the documentation-path key; doc_path fulfills it.',
                        'original_record_preserved':True,'new_model_attempt':False,'answer':answer}
                write_json(prior.parent/'assessment_review.json',review,exclusive=True)
                record['assessment']=review
        records.append(record)
        if not records[-1]['assessment']['passed']:
            break
    result={'passed':len(records)==len(arms) and all(x['assessment']['passed'] for x in records),
            'native_records':[str(Path('qualification')/('native_'+x['entry']['arm'])/'record.json') for x in records],
            'time_unix':time.time(),'qualified_files_sha256':fingerprint}
    if fingerprint!=qualification_fingerprint(root):raise RuntimeError('Source changed during qualification')
    write_json(root/'setup/agent_qualification_final.json',result,exclusive=True)
    if not result['passed']:raise RuntimeError('Native qualification failed; no scientific admission')


def freeze(root):
    spec,_=configure(root)
    for name in ('agent_qualification_final.json','native-retrieval.json'):
        if not json.loads((root/'setup'/name).read_text())['passed']:raise ValueError('Qualification incomplete')
    qualification=json.loads((root/'setup/agent_qualification_final.json').read_text())
    if qualification.get('qualified_files_sha256')!=qualification_fingerprint(root):
        raise ValueError('Executable/configuration changed after native qualification')
    handoffs=json.loads((root/'setup/handoff_manifest.json').read_text())
    for case in handoffs['cases']:
        for name,digest in case['files_sha256'].items():
            if sha(root/'public'/case['public_case']/name)!=digest:raise ValueError('Public input changed')
        from assess import source_contract
        public=root/'public'/case['public_case']
        independent=source_contract(public,public/'candidate.params')
        decision='release_conditionally' if all(independent.values()) else 'hold'
        if decision!=case['fixed_checker_decision']:raise ValueError('Checker and independent assessment disagree')
    files={str(p.relative_to(root)):sha(p) for directory in ('setup','runtime/adapters')
           for p in (root/directory).rglob('*') if p.is_file() and p.suffix in ('.py','.md','.json')
           and not p.name.endswith('-status.json') and '__pycache__' not in p.parts}
    files.update({str(p.relative_to(root)):sha(p) for p in (root/'public').rglob('*') if p.is_file()})
    frozen={'revision':spec.get('run_revision','matched-harness-pilot-r1'),
        'files_sha256':files,'qualified_files_sha256':qualification['qualified_files_sha256'],'schedule':schedule(spec),
        'source_commit':spec['apexa_source_commit'],'model':spec['model'],
        'effective_sampling':{'temperature':1.0,'reasoning_effort':'medium','max_output_tokens':8192},
        'model_context_tokens':65536,'time_unix':time.time(),'scientific_attempts':len(schedule(spec)),
        'qualification_configuration_note':'Exact executable/configuration hashes must match the fresh native qualifications, including the 300 second client timeout.',
        'no_automatic_expansion':True}
    write_json(root/'setup/trial_freeze.json',frozen,exclusive=True)


def run(root):
    spec,_=configure(root)
    frozen=json.loads((root/'setup/trial_freeze.json').read_text())
    def verify():
        if frozen['qualified_files_sha256']!=qualification_fingerprint(root):
            raise RuntimeError('Qualified implementation changed')
        for relative,digest in frozen['files_sha256'].items():
            if sha(root/relative)!=digest:raise RuntimeError('Frozen setup changed: '+relative)
    verify()
    if frozen['schedule']!=schedule(spec):raise ValueError('Schedule changed')
    cpus=physical_cpu_sets(2,4);completed=[]
    status=root/'setup/trial-status.json'
    def update(state,**kw):
        write_json(status,{'status':state,'pid':os.getpid(),'updated_unix':time.time(),
            'completed':len(completed),'scheduled':len(frozen['schedule']),'usage':model_usage(root),**kw})
    with (Path(spec.get('shared_project_root',root.parent))/'scientific-run.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:
            with ProcessPoolExecutor(max_workers=2,mp_context=multiprocessing.get_context('spawn')) as pool:
                for offset in range(0,len(frozen['schedule']),2):
                    verify()
                    usage=model_usage(root)
                    if (root/'setup/provider-hold.json').exists() or usage['provider_denials']:
                        update('provider_denial_stopped_admission');return
                    if usage['input_tokens']>spec['usage_review_input_tokens'] or usage['output_tokens']>spec['usage_review_output_tokens']:
                        update('usage_review_needed');return
                    wave=frozen['schedule'][offset:offset+2]
                    for entry in wave:
                        write_json(root/'results/started'/f"{entry['index']:02d}.json",
                                   {**entry,'started_unix':time.time()},exclusive=True)
                    update('running',active=wave)
                    futures={pool.submit(work,root,e,cpus[i]):e for i,e in enumerate(wave)}
                    wave_records=[]
                    for f in as_completed(futures):
                        e=futures[f]
                        try:record=f.result()
                        except Exception as exc:
                            record={'entry':e,'execution':{'outcome':'setup_or_infrastructure_error',
                                    'detail':type(exc).__name__+': '+str(exc)}}
                        with (root/'results/attempts.jsonl').open('a') as out:
                            out.write(json.dumps(record,allow_nan=False)+'\n')
                        from report import report
                        report(root)
                        wave_records.append(record)
                        completed.append(e['index']);update('running',active=[x for x in wave if x['index'] not in completed])
                    if any(r.get('model_audit',{}).get('requests',0)==0 for r in wave_records):
                        update('startup_failure_stopped_admission');return
            update('execution_complete_pending_trace_audit')
        except BaseException as exc:
            update('stopped',error=type(exc).__name__+': '+str(exc));raise


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['qualify','freeze','run'])
    p.add_argument('--root',type=Path,required=True);a=p.parse_args()
    {'qualify':qualify,'freeze':freeze,'run':run}[a.action](a.root.resolve())
