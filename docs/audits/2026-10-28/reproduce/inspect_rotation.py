from pathlib import Path
import json,csv,collections,sys
import h5py,numpy as np
root=Path('/Users/b324240/Box/01-Research/01-APEXA/ai_tune_rot_series')
summary=root/'APEXA-Analysis/rotation_series_calibration_summary.csv'
rows=list(csv.DictReader(summary.open()))
sys.path.insert(0,'/Users/b324240/Git/beamline-assistant-dev/benchmark/harness_comparison')
from rotation_benchmark import load_truth
truth=load_truth(summary,value_col='tz_deg')
counts=collections.Counter();table=[]
for r in rows:
 scan=int(r['scan']); passed=r['gate_passed'].lower()=='true'; bounds=[b for b in r['bounds'].split(';') if b]; nonphase=[b for b in bounds if not b.startswith('phi')];err=abs(float(r['tz_deg'])-truth[scan]['predicted_tz'])
 counts['total']+=1;counts['gate_passed']+=passed;counts['any_bounds']+=bool(bounds);counts['nonphase_bounds']+=bool(nonphase);counts['passed_with_any_bounds']+=passed and bool(bounds);counts['passed_with_nonphase_bounds']+=passed and bool(nonphase);counts['passed_and_tz_error_above_0.1']+=passed and err>0.1;counts['failed_and_tz_error_above_0.1']+=(not passed) and err>0.1
 table.append(dict(scan=scan,calibrant=r['calibrant'],passed=passed,tz=float(r['tz_deg']),loo_reference=truth[scan]['predicted_tz'],abs_error_deg=err,bounds=bounds,nonphase_bounds=nonphase))
print('SUMMARY',json.dumps(dict(counts)));print('GROUPS',dict(collections.Counter(r['calibrant'] for r in rows)))
raw=list((root/'data').rglob('*.h5'));print('RAW',len(raw))
p=sorted(raw)[0]
meta=[]
with h5py.File(p,'r') as f:
 def visit(n,o):
  if not isinstance(o,h5py.Dataset):return
  row={'path':n,'shape':o.shape,'dtype':str(o.dtype)}
  if o.size<=10:
   try:
    val=o[()];row['value']=str(val)[:220]
   except Exception as e:row['error']=str(e)
  meta.append(row)
 f.visititems(visit)
print('H5_FILE',p.name)
for r in meta:print(json.dumps(r))
# Inspect existing summaries only; do not run any calibration.
control=root/'APEXA-Analysis/distortion_sensitivity_tol10/distortion_sensitivity_summary.csv'
control_info={}
if control.exists():
 cr=list(csv.DictReader(control.open()));control_info={'rows':len(cr),'columns':list(cr[0]) if cr else []};print('CONTROL',json.dumps(control_info));print('CONTROL_FIRST',json.dumps(cr[:1]))
Path('/tmp/apexa-audit-20261028/rotation-inspection.json').write_text(json.dumps({'root':str(root),'counts':dict(counts),'rows':table,'raw_h5_count':len(raw),'sample_h5':str(p),'h5_metadata':meta,'control':control_info},indent=2))
