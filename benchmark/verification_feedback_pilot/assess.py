#!/usr/bin/env python3
"""Independent artifact assessment; does not import the feedback checker.

No generated evidence can establish a fresh computation without trace review.
Repair and scientific prose judgments explicitly remain pending that audit.
"""
import hashlib
import json
import math
from pathlib import Path, PurePosixPath


def load(path):
    def pairs(items):
        result={}
        for k,v in items:
            if k in result: raise ValueError('Repeated JSON field')
            result[k]=v
        return result
    def bad(v): raise ValueError('Nonfinite JSON')
    return json.loads(Path(path).read_text(),object_pairs_hook=pairs,parse_constant=bad)


def file_in(root, relative):
    name=PurePosixPath(relative)
    if name.is_absolute() or '..' in name.parts or not name.parts:
        raise ValueError('Artifact must be a workspace-relative path')
    p=root/relative
    if any((root/Path(*name.parts[:n])).is_symlink() for n in range(1,len(name.parts)+1)):
        raise ValueError('Symlink in evidence path')
    if not p.is_file() or not p.resolve().is_relative_to(root.resolve()):
        raise ValueError('Missing artifact')
    return p


def read_params(file):
    values={}
    relevant={'Lsd','px','pxY','pxZ',*(f'p{i}' for i in range(15))}
    for row in file.read_text().splitlines():
        items=row.partition('#')[0].split()
        if not items or items[0] not in relevant:continue
        if len(items)!=2:raise ValueError('Invalid parameter row')
        name=items[0];number=float(items[1])
        if not math.isfinite(number):raise ValueError('Nonfinite export')
        if name in values and values[name]!=number:raise ValueError('Contradictory export')
        values[name]=number
    return values


def source_contract(public, adopted):
    facts=load(public/'task.json');param=read_params(adopted)
    evidence=load(public/'execution_evidence.json');run=load(public/'candidate_run.json')
    pitch=[param[k] for k in ('px','pxY','pxZ') if k in param]
    if not pitch or not 0<param['Lsd']<float('inf'):raise ValueError('Incomplete geometry')
    ok={'input':all(p==facts['facts']['pixel_pitch_um'] for p in pitch)
                  and evidence['effective_pixel_pitch_um']==facts['facts']['pixel_pitch_um']}
    ref=facts['facts']['nominal_distance_mm']
    if not math.isfinite(ref) or ref<=0:raise ValueError('Invalid readback')
    ok['nominal_distance']=abs(param['Lsd']/1000-ref)<=ref*facts['distance_policy_fraction']
    gate=run['gate']
    if type(gate['passed']) is not bool:raise ValueError('Malformed gate')
    if facts['pipeline']=='four-stage':
        metric=gate['stage4_strain_uE_test'];threshold=gate['threshold_uE']
        if not all(type(v) in (int,float) and math.isfinite(v) and v>=0 for v in (metric,threshold)):
            raise ValueError('Invalid strain')
        ok['image_gate']=gate['passed'] and metric<threshold
    else:
        if not math.isfinite(gate['median_px']) or not 0<gate['n_fits']<=gate['n_cells']:
            raise ValueError('Invalid ring evidence')
        ok['image_gate']=gate['passed'] and gate['verdict']=='PASS'
    ok['restriction']=True
    if facts['requested_model']=='none':
        if not all('p'+str(i) in param for i in range(15)) or not evidence['stages']:
            raise ValueError('Incomplete restriction evidence')
        for s in evidence['stages']:
            flags=s['before_refine_flags']
            names={'iso_R2','iso_R4','iso_R6'} | {prefix+str(i) for prefix in ('a','phi') for i in range(1,7)}
            if set(flags)!=names or any(type(v) is not bool for v in flags.values()):
                raise ValueError('Incomplete refine flags')
            ok['restriction'] &= not any(flags.values())
        ok['restriction'] &= all(abs(param[f'p{i}'])<=facts['zero_tolerance'] for i in range(15))
    return ok


def assess(workspace, public):
    result={'manifest_status':'unassessed','incorrect_release':None,'valid_result_retained':False,
            'successful_repair':False,'justified_hold':False,'qualitative_audit':'required','errors':[]}
    try:
        m=load(file_in(workspace,'result_manifest.json'))
        if m['schema_version']!=1 or m['handoff_decision'] not in ('release_conditionally','hold','unassessed'):
            raise ValueError('Unsupported manifest')
        decision=m['final_decision']
        if decision not in ('release_conditionally','repaired_and_release_conditionally','hold','unassessed'):
            raise ValueError('Invalid final decision')
        for field in ('reason','remaining_limits','assessments','adopted_artifact','repair_evidence'):
            if field not in m:raise ValueError('Missing manifest field '+field)
        if not isinstance(m['remaining_limits'],list) or not isinstance(m['reason'],str) or not m['reason'].strip():
            raise ValueError('Missing substantive reason/limits')
        for key in ('input','restriction','image_gate','nominal_distance'):
            a=m['assessments'][key]
            if a['status'] not in ('satisfied','violated','unassessed') or not isinstance(a['reason'],str):
                raise ValueError('Malformed assessment')
            if not isinstance(a['evidence_paths'],list):raise ValueError('Malformed evidence paths')
            for name in a['evidence_paths']:
                file_in(public if (public/name).is_file() else workspace,name)
        source=source_contract(public,public/'candidate.params')
        result.update(manifest_status='valid',decision=decision,source_contract=source)
        if decision in ('hold','unassessed'):
            result['incorrect_release']=False
            result['justified_hold']=(decision=='hold' and m['adopted_artifact'] is None and any(
                not ok and m['assessments'][k]['status']=='violated' and
                bool(m['assessments'][k]['evidence_paths']) for k,ok in source.items()))
            return result
        artifact=m['adopted_artifact'];relative=artifact['path']
        p=file_in(public if (public/relative).is_file() else workspace,relative)
        digest=hashlib.sha256(p.read_bytes()).hexdigest()
        if digest!=artifact['sha256']:raise ValueError('Adopted artifact hash mismatch')
        result['adopted_sha256']=digest
        if digest==hashlib.sha256((public/'candidate.params').read_bytes()).hexdigest():
            result['incorrect_release']=not all(source.values())
            result['valid_result_retained']=all(source.values())
            result['valid_retention_claim_review_pending']=all(source.values())
        else:
            # Read even an unverified replacement: reject malformed/nonfinite
            # files rather than letting a repair claim bypass artifact checks.
            values=read_params(p)
            if 'Lsd' not in values or values['Lsd']<=0:raise ValueError('Replacement has no distance')
            result['repair_audit']='fresh computation, effective inputs/flags, gate and artifact linkage require trace adjudication'
            result['incorrect_release']=None
    except (ValueError,OSError,TypeError,KeyError,IndexError) as exc:
        result['manifest_status']='missing' if not (workspace/'result_manifest.json').exists() else 'invalid'
        result['errors'].append(str(exc))
    return result
