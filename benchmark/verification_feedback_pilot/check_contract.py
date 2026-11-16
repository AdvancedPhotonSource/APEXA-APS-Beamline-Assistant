#!/usr/bin/env python3
"""Public evidence checker. No fixture labels, answer keys or forced decisions.

python check_contract.py --task task.json --parameters candidate.params
    --run candidate_run.json --execution execution_evidence.json

Checks the supplied evidence, not the authenticity of newly written evidence.
New computations require an independent execution/provenance audit at scoring.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path


def read_json(path):
    def invalid(value):
        raise ValueError('Nonfinite JSON constant: ' + value)
    def unique(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError('Duplicate JSON key: ' + key)
            out[key] = value
        return out
    return json.loads(Path(path).read_text(), parse_constant=invalid, object_pairs_hook=unique)


def parameters(path):
    wanted = {'px', 'pxY', 'pxZ', 'Lsd', *('p'+str(i) for i in range(15))}
    out = {}
    for line in Path(path).read_text().splitlines():
        fields = line.split('#', 1)[0].split()
        if not fields or fields[0] not in wanted:
            continue
        if len(fields) != 2:
            raise ValueError('Malformed parameter: ' + fields[0])
        name, value = fields[0], float(fields[1])
        if not math.isfinite(value):
            raise ValueError('Nonfinite parameter: ' + name)
        if name in out and out[name] != value:
            raise ValueError('Conflicting duplicate parameter: ' + name)
        out[name] = value
    if 'Lsd' not in out or out['Lsd'] <= 0:
        raise ValueError('Missing or nonpositive Lsd')
    return out


def check(task_path, parameter_path, run_path, execution_path):
    out = {'evidence_complete': False, 'checks': {}, 'errors': [],
           'scope': 'Input/model consistency and image fit; nominal readback is not absolute metrology.'}
    try:
        task, run, execution = map(read_json, (task_path, run_path, execution_path))
        par = parameters(parameter_path)
        facts = task['facts']
        effective = execution['effective_pixel_pitch_um']
        expected = facts['pixel_pitch_um']
        if type(effective) not in (int, float) or not math.isfinite(effective) or effective <= 0:
            raise ValueError('Missing/nonfinite effective pixel pitch')
        pitches = [v for k, v in par.items() if k in ('px', 'pxY', 'pxZ')]
        if not pitches:
            raise ValueError('No exported pixel pitch')
        pitch_ok = effective == expected and all(v == expected for v in pitches)
        out['checks']['input'] = {'satisfied': pitch_ok, 'effective_um': effective,
                                 'exported_um': sorted(set(pitches)), 'documented_um': expected}
        ref = facts['nominal_distance_mm']
        if type(ref) not in (int, float) or not math.isfinite(ref) or ref <= 0:
            raise ValueError('Invalid nominal reference')
        distance = par['Lsd']/1000
        frac = abs(distance-ref)/ref
        out['checks']['nominal_distance'] = {'satisfied': frac <= task['distance_policy_fraction'],
            'fitted_mm': round(distance, 6), 'readback_mm': ref, 'relative_discrepancy': round(frac, 8),
            'policy_fraction': task['distance_policy_fraction']}
        gate = run['gate']
        if type(gate['passed']) is not bool:
            raise ValueError('Image gate is not boolean')
        if task['pipeline'] == 'four-stage':
            metric = gate['stage4_strain_uE_test']
            threshold = gate['threshold_uE']
            if any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in (metric, threshold)):
                raise ValueError('Invalid held-out strain evidence')
            image_ok = gate['passed'] and metric < threshold
        else:
            metric = gate['median_px']
            count, total = gate['n_fits'], gate['n_cells']
            if (type(metric) not in (int, float) or not math.isfinite(metric) or metric < 0
                    or type(count) is not int or type(total) is not int or not 0 < count <= total):
                raise ValueError('Invalid ring-position evidence')
            image_ok = gate['passed'] and gate['verdict'] == 'PASS'
        out['checks']['image_gate'] = {'satisfied': image_ok, 'reported_passed': gate['passed']}
        if task['requested_model'] == 'none':
            required = {'p'+str(i) for i in range(15)}
            if required - par.keys():
                raise ValueError('Incomplete exported coefficients')
            stages = execution['stages']
            if not stages:
                raise ValueError('No executed stage evidence')
            free = []
            for stage in stages:
                flags = stage['before_refine_flags']
                if len(flags) != 15 or any(type(v) is not bool for v in flags.values()):
                    raise ValueError('Incomplete stage refine flags')
                free.extend(f"stage{stage['call']}:{k}" for k,v in flags.items() if v)
            moved = {k:par[k] for k in sorted(required) if abs(par[k]) > task['zero_tolerance']}
            out['checks']['restriction'] = {'satisfied': not free and not moved,
                'free_coefficient_stage_entries': len(free), 'nonzero_exports': moved}
        elif task['requested_model'] == 'full':
            out['checks']['restriction'] = {'satisfied': True, 'scope': 'No zero-distortion restriction requested.'}
        else:
            raise ValueError('Unsupported requested model')
        out['evidence_complete'] = True
        out['parameter_sha256'] = hashlib.sha256(Path(parameter_path).read_bytes()).hexdigest()
    except (ValueError, OSError, KeyError, TypeError, IndexError) as exc:
        out['errors'].append(str(exc))
    out['all_declared_checks_satisfied'] = out['evidence_complete'] and all(
        v['satisfied'] for v in out['checks'].values())
    return out


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task', default='task.json')
    parser.add_argument('--parameters', default='candidate.params')
    parser.add_argument('--run', default='candidate_run.json')
    parser.add_argument('--execution', default='execution_evidence.json')
    args = parser.parse_args()
    print(json.dumps(check(args.task, args.parameters, args.run, args.execution), separators=(',', ':'), allow_nan=False))
