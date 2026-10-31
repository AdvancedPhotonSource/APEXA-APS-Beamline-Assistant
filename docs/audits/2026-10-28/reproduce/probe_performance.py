import os,sys,time,asyncio,json,statistics
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
import numpy as np
sys.path.insert(0,'/Users/b324240/Git/beamline-assistant-dev')
os.environ['APEXA_NETWORK']='data';os.environ['APEXA_OFFLINE']='1'
import beamline_core_server as core
import apexa_compute as C
from apexa_viewer_data import frame_payload
rows=[]
active=0;peak=0

def blocking_stub(*a,**k):
 global active,peak
 active+=1;peak=max(peak,active);time.sleep(0.15);active-=1
 return SimpleNamespace(returncode=0,stdout='audit',stderr='')
async def batch_probe():
 global peak
 plan=C.ExecutionPlan(n_workers=4,threads_per_worker=1)
 async def one(i):return json.loads(await core.run_command('echo audit'))
 t=time.perf_counter()
 with patch.object(core.subprocess,'run',blocking_stub):
  await C.run_batch_async(range(4),one,plan,ok=lambda r:r.get('success'),progress=False)
 rows.append({'probe':'actual_async_shell_tool_with_stubbed_subprocess','workers_requested':4,'peak_concurrent_subprocess_stubs':peak,'elapsed_s':round(time.perf_counter()-t,4),'serial_service_time_s':0.6,'note':'No shell was executed. Same no-await blocking pattern occurs in midas_auto_calibrate.'})
asyncio.run(batch_probe())
p=C.plan(4,'calibration',resources=C.Resources(cores=32,mem_total_gb=64.,mem_free_gb=0.1),profile=C.TaskProfile('calibration',rss_gb=7.,threads_per_worker=4))
rows.append({'probe':'insufficient_memory_admission','free_ram_gb':0.1,'profile_rss_gb':7.,'plan':p.as_dict()})
rng=np.random.default_rng(123);frame=rng.poisson(200,size=(2880,2880)).astype(np.float32)
# Warm imports once. Values here measure in-memory image handling only.
frame_payload(frame[:16,:16])
for with_profile in (False,True):
 times=[];out=None
 for _ in range(3):
  t=time.perf_counter();out=frame_payload(frame,**({'center_x':1440.,'center_y':1440.} if with_profile else {}));times.append(time.perf_counter()-t)
 rows.append({'probe':'in_memory_2880_frame_payload','radial_profile':with_profile,'median_s':round(statistics.median(times),4),'runs_s':[round(t,4) for t in times],'preview_chars':len(out['preview']),'input_mb':frame.nbytes/1e6,'note':'Local synthetic microbenchmark; no disk, browser, network, or numerical engine time.'})
t=time.perf_counter()
for _ in range(8):
 valid=frame[np.isfinite(frame)];float(valid.min());float(valid.max());float(valid.mean())
rows.append({'probe':'eight_global_image_statistics_passes','elapsed_s':round(time.perf_counter()-t,4),'note':'Replicates full-array statistics pattern in WS context builder.'})
Path('/tmp/apexa-audit-20261028/performance-probes.json').write_text(json.dumps(rows,indent=2))
for row in rows:print(json.dumps(row))
