"""Offline audit reproductions. No real tool, model, shell, or hardware execution."""
import os,sys,json,asyncio,tempfile
from pathlib import Path
from unittest.mock import patch
ROOT=Path('/Users/b324240/Git/beamline-assistant-dev')
sys.path.insert(0,str(ROOT))
os.environ['APEXA_NETWORK']='data'
os.environ['APEXA_OFFLINE']='1'
os.environ['APEXA_HOME']='/tmp/apexa-audit-20261028/state'
os.environ['APEXA_SESSION_DIR']='/tmp/apexa-audit-20261028/state/sessions'
import epics_motor_server as motor
from beamline_core_server import is_command_allowed
from argo_mcp_client import _is_delete_command
from apexa_ledger import ToolLedger
from _idempotency import idempotent
rows=[]
def record(name,**kw):rows.append(dict(probe=name,**kw))
async def run():
 calls=[]
 with patch.object(motor,'_resolve_motor',lambda p,m:m),patch.object(motor,'_get_float',lambda *a,**k:(False,0.0)),patch.object(motor,'_caget',lambda *a,**k:(False,'unavailable')),patch.object(motor,'_caput',lambda pv,value,**k:(calls.append((pv,value)) is None,'stubbed')):
  result=json.loads(await motor.move_motor_absolute('m1',10.0,prefix='AUDIT',wait=False))
  record('motor_failed_readbacks',write_calls=calls,result=result)
 calls=[]
 with patch.object(motor,'_resolve_motor',lambda p,m:m),patch.object(motor,'_get_float',lambda p,m,f,**k:(True,{'RBV':0.,'HLM':100.,'LLM':-100.,'HLS':0.,'LLS':0.}.get(f,0.))),patch.object(motor,'_caget',lambda *a,**k:(True,'mm')),patch.object(motor,'_caput',lambda pv,value,**k:(calls.append((pv,value)) is None,'stubbed')):
  result=json.loads(await motor.move_motor_absolute('m1',float('nan'),prefix='AUDIT',wait=False))
  record('motor_nonfinite_target',write_count=len(calls),target_serialized=str(calls[0][1]) if calls else '',status=result.get('status'))
 commands=['caput AUDIT:m1 10', 'python3 -c "import os; os.remove(\'audit-marker\')"']
 for cmd in commands:record('shell_policy_only_no_execution',command=cmd,allowed=is_command_allowed(cmd),deletion_confirmation_requested=_is_delete_command(cmd))
 for result in [json.dumps({'error':'tool failed'}),json.dumps({'success':False,'return_code':1}),json.dumps({'status':'failed'}),json.dumps({'completed':False,'timed_out':True})]:
  ledger=ToolLedger();ledger.dispatch('x','stub');ledger.complete('x',result)
  record('ledger_failure_status',input=result,inferred_status=ledger.entries[0].status)
 ledger=ToolLedger();ledger.dispatch('x','list_directory');ledger.complete('x','{"files": []}')
 record('ledger_numeric_claim',violations=ledger.check_final_answer('Calibration completed successfully. Refined distance is 902345 micrometers.'))
 with tempfile.TemporaryDirectory(prefix='apexa-audit-probes-') as td:
  base=Path(td);src=base/'input.txt';src.write_text('first');outdir=base/'out';outdir.mkdir();count=[]
  @idempotent('stub_calibrate',anchor=lambda kw:str(outdir),salient=lambda kw:{'image_file':kw['image_file']})
  async def fake(image_file):
   count.append(1);val=Path(image_file).read_text();(outdir/'result.txt').write_text(val);return json.dumps({'status':'success','value':val})
  one=json.loads(await fake(str(src)));src.write_text('second');two=json.loads(await fake(str(src)))
  record('idempotency_changed_input_same_path',executions=len(count),first=one,second=two)
asyncio.run(run())
# HTTP tests do not enter app lifespan: no MCP subprocesses start.
import numpy as np
import web_server as web
from fastapi.testclient import TestClient
class FakeClient:
 sessions={};available_models={};selected_model='audit';_web_thread_lock=asyncio.Lock()
 async def execute_tool_call(self,*a,**k):return json.dumps({'error':'Tool not found'})
 async def run_query(self,*a,**k):return 'No calibration ran; the engine is unavailable.'
fake=FakeClient()
with tempfile.TemporaryDirectory(prefix='apexa-audit-http-') as td:
 root=Path(td);uploads=root/'uploads';uploads.mkdir()
 with patch.object(web,'mcp_client',fake),patch.object(web,'upload_dir',uploads),patch.object(web,'image_paths',{'probe':str(root/'input.tif')}),patch.object(web,'image_cache',{'probe':np.zeros((2,2))}):
  c=TestClient(web.app)
  for url,data in [('/api/analysis/phases',{'file_id':'probe','elements':'Au'}),('/api/analysis/peaks',{'file_id':'probe'}),('/api/calibrate',{'file_id':'probe'})]:
   r=c.post(url,data=data);record('http_error_contract',route=url,http_status=r.status_code,result=r.json())
  r=c.get('/api/status');record('empty_mcp_health',connected=r.json().get('mcp_client_connected'),servers=r.json().get('connected_servers'))
  r=c.post('/api/viewer/load_calibration',files={'file':('../escaped.txt',b'Lsd 900000\nBC 1 1\nWavelength 0.2\n','text/plain')})
  record('upload_parent_path',route='/api/viewer/load_calibration',http_status=r.status_code,wrote_outside_upload_root=(root/'escaped.txt').exists())
  r=c.get('/api/browse',params={'path':td});record('unauthenticated_browse',http_status=r.status_code)
  with c.websocket_connect('/ws',headers={'origin':'https://untrusted.invalid'}) as ws:
   ws.send_text('not json');r=ws.receive_json();record('cross_origin_websocket',accepted=True,response_type=r.get('type'))
# Pure benchmark classifier adversarial case; no harness run.
sys.path.insert(0,str(ROOT/'benchmark/harness_comparison'))
from rotation_benchmark import claimed_success
record('benchmark_contradictory_acceptance',text='The initial attempt succeeded numerically, but validation failed. Do not use this geometry.',claim=claimed_success('The initial attempt succeeded numerically, but validation failed. Do not use this geometry.',3.0))
Path('/tmp/apexa-audit-20261028/probes.json').write_text(json.dumps(rows,indent=2,allow_nan=False))
for row in rows:print(json.dumps(row,allow_nan=False))
