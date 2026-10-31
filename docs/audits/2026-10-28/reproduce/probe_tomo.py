import sys,json,tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
sys.path.insert(0,'/Users/b324240/Git/beamline-assistant-dev')
import _tomo_runner as T
rows=[]
with tempfile.TemporaryDirectory(prefix='apexa-tomo-audit-') as td:
 calls=[];angles=[]
 def norm(p,f,d):calls.append('normalize');return np.ones_like(p)*0.5
 def log(p):calls.append('minus_log');return -np.log(p)
 def phase(p,**kw):calls.append('retrieve_phase');return p
 def recon(p,theta,**kw):angles.append(theta.tolist());return np.arange(27,dtype=float).reshape(3,3,3)
 stub=SimpleNamespace(normalize=norm,minus_log=log,prep=SimpleNamespace(phase=SimpleNamespace(retrieve_phase=phase)),recon=recon,circ_mask=lambda r,**kw:r,write_center=lambda *a,**k:None,find_center_vo=lambda p:1.)
 args=SimpleNamespace(pixel_size_um=150.,handedness='left',data=td,pattern='*.tif',output_dir=td,omega_start=0.,omega_step=1.,phase_retrieval=True,propagation_mm=100.,energy_kev=63.314,paganin_alpha=1e-3,rotation_center=None,center_sweep=[0.,2.,1.],algorithm='gridrec',threshold=None)
 with patch.dict(sys.modules,{'tomopy':stub}),patch.object(T,'_list_frames',lambda *a:['stub']),patch.object(T,'_read_stack',lambda *a:np.ones((5,3,3))),patch.object(T,'detect_flat_dark_layout',lambda *a:{'confident':True,'dark':[0],'flat':[1],'proj':[2,3,4]}):
  result=T.cmd_reconstruct(args);rows.append({'probe':'tomo_pipeline_order','calls':calls.copy()});rows.append({'probe':'provisional_center_status','source':result['rotation_center_source'],'status':result['status']})
  args.handedness='right';T.cmd_reconstruct(args);rows.append({'probe':'tomo_handedness_angles','angles_same':angles[0]==angles[1],'angles_left':angles[0],'angles_right':angles[1],'note':'Declaration is recorded but does not change angles; convention consistency needs qualification.'})
  stub.recon=lambda *a,**k:np.array([[[1.,float('inf')],[2.,3.]]]);args.center_sweep=None;args.rotation_center=1.
  result=T.cmd_reconstruct(args);rows.append({'probe':'tomo_infinite_voxel','status':result['status'],'max_is_infinite':bool(np.isinf(result['recon_stats']['max']))})
Path('/tmp/apexa-audit-20261028/tomo-probes.json').write_text(json.dumps(rows,indent=2))
for r in rows:print(json.dumps(r))
