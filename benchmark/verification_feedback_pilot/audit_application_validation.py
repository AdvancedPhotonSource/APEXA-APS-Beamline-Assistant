"""Audit the four internal application checks without modifying their records.

This is an engineering regression on preserved handoffs, not a paper experiment.
Numerical checks parse artifacts and compare native receipts with live traces.
Free-form agent claims require separate reading; they are not certified here.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import time


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parameters(path):
    keys = {'px', 'Lsd', 'Wavelength', 'SpaceGroup', 'LatticeConstant',
            *(f'p{i}' for i in range(15))}
    result = {}
    for line in path.read_text().splitlines():
        fields = line.split('#', 1)[0].split()
        if not fields or fields[0] not in keys:
            continue
        values = [float(v) for v in fields[1:]]
        if not values or not all(map(math.isfinite, values)):
            raise ValueError('Nonfinite or missing parameter')
        if fields[0] in result and result[fields[0]] != values:
            raise ValueError('Conflicting parameter')
        result[fields[0]] = values
    return result


def audit(root):
    spec = read(root/'setup/study.json')
    expected = ['scale_valid', 'restriction_valid', 'scale_fault', 'restriction_fault']
    if (spec.get('paper_experiment') is not False or not spec.get('application_contract')
            or [e['fixture'] for e in spec['trial_schedule']] != expected):
        raise ValueError('Wrong cohort for this internal validation audit')
    terminal = read(root/'setup/trial-status.json')
    if terminal['completed'] != 4:
        raise ValueError('All four attempts must finish before audit')
    handoffs = {c['fixture']: root/'public'/c['public_case']
                for c in read(root/'setup/handoff_manifest.json')['cases']}
    rows = []
    for entry in spec['trial_schedule']:
        trial = root/'results'/f"trial{entry['index']:02d}"
        record_path = trial/'record.json'
        record = read(record_path)
        manifests = list((trial/'workspaces').glob('*/result_manifest.json'))
        row = {'trial': entry['index'], 'fixture': entry['fixture'],
               'requests': record['model_audit']['requests'],
               'elapsed_seconds': record['execution']['elapsed_s'],
               'original_schema_status': record['assessment']['manifest_status']}
        if len(manifests) != 1:
            row.update(passed=False, error='Missing or ambiguous manifest')
            rows.append(row)
            continue
        manifest_path = manifests[0]
        workspace = manifest_path.parent
        manifest = read(manifest_path)
        public = handoffs[entry['fixture']]
        task = read(public/'task.json')
        facts = task['facts']
        evidence = [record_path, manifest_path, public/'task.json', public/'candidate_run.json']
        events = []
        for trace in sorted((trial/'traces').glob('*.tools.jsonl')):
            evidence.append(trace)
            for line in trace.read_text().splitlines():
                event = json.loads(line)
                value = event.get('result', {})
                if isinstance(value, str):
                    try:
                        value = json.loads(value)
                    except ValueError:
                        value = {}
                events.append((event, value if isinstance(value, dict) else {}))
        verification = manifest.get('contract_verification', {})
        initial_valid = entry['fixture'].endswith('_valid')
        decisions = manifest.get('handoff_decision'), manifest.get('final_decision')
        checks = {
            'execution_completed': record['execution']['outcome'] == 'execution_completed',
            'qualified_transport': record['model_audit']['qualified'],
            'task_schema': row['original_schema_status'] == 'valid',
            'application_activation': bool(events) and events[0][0].get('tool') == 'load_calibration_contract'
                and events[0][1].get('client_contract_active') is True
                and events[0][1].get('activation_source') == 'application_request_before_model',
            'activation_saved': verification.get('activation_source') == 'application_request_before_model',
            'contract_hash': verification.get('contract_sha256') == sha(public/'task.json'),
            'original_decision': decisions[0] == ('release_conditionally' if initial_valid else 'hold'),
            'scope_limits': bool(manifest.get('remaining_limits')),
        }
        repairs = [(e, v) for e, v in events if e.get('tool') == 'midas_auto_calibrate'
                   and v.get('status') == 'success']
        refused = [(e, v or {'raw_result': e.get('result')}) for e, v in events
                   if e.get('tool') == 'finalize_result' and (v.get('status') in ('error', 'refused')
                   or isinstance(e.get('result'), str) and e['result'].startswith('Error'))]
        row.update(handoff_decision=decisions[0], final_decision=decisions[1],
                   finalizer_refusals=[{'arguments': e.get('arguments'), 'result': v} for e, v in refused],
                   authoritative_summary=manifest.get('summary'),
                   agent_proposed_summary=verification.get('agent_proposed_summary'),
                   agent_proposed_result=manifest.get('agent_proposed_result'),
                   raw_claim_audit='Not certified by numerical checks; read saved final chat and proposals separately.')
        if decisions[1] == 'hold':
            checks.update(justified_hold=record['assessment'].get('justified_hold') is True,
                          no_artifact_released=manifest.get('adopted_artifact') is None,
                          no_false_repair_claim=not repairs and manifest.get('repair_evidence') is None,
                          faulty_original=not initial_valid)
            row['outcome'] = 'faulty handoff held without repair'
        else:
            adopted = manifest['adopted_artifact']
            artifact = workspace/adopted['path']
            if not artifact.is_file() and adopted['path'] == 'candidate.params':
                artifact = public/'candidate.params'
            evidence.append(artifact)
            checks['artifact_hash'] = sha(artifact) == adopted['sha256']
            par = parameters(artifact)
            if repairs:
                if len(repairs) != 1:
                    raise ValueError('Unexpected multiple repairs; inspect the whole trace')
                _, live = repairs[0]
                receipt = workspace/manifest['repair_evidence']['execution_record_path']
                evidence.append(receipt)
                saved = read(receipt)
                native, effective = saved['native_result'], saved['effective_arguments']
                checks.update(
                    repaired_decision=decisions[1] == 'repaired_and_release_conditionally',
                    receipt_hash=sha(receipt) == verification['execution_receipt_sha256'],
                    live_artifact_hash=adopted['sha256'] == saved['artifact_sha256'] == live['artifact_sha256'],
                    receipt_matches_trace=all(native.get(k) == live.get(k) for k in
                        ('frame_selection', 'phase_provenance', 'gate', 'distortion_verification', 'constraint_execution')),
                    effective_matches_trace=effective == live['contract_execution']['effective_arguments'],
                    effective_pitch=effective['px_um'] == facts['pixel_pitch_um'],
                    effective_pipeline=effective['calibration_pipeline'] == task['pipeline'],
                    effective_model=effective['refine_distortion'] == task['requested_model'],
                    original_pixel_array=native['frame_selection'].get('oriented_frame_sha256') ==
                        read(public/'candidate_run.json')['frame_selection']['oriented_frame_sha256'])
                row['corrected_arguments'] = saved['corrected_arguments']
                if task['requested_model'] == 'none':
                    stages = native['constraint_execution']
                    fitted = native['distortion_verification']['checked_values']
                    checks.update(stage2_flags=bool(stages) and all(len(s['after_thaw_refine_flags']) == 15
                        and all(v is False for v in s['after_thaw_refine_flags'].values()) for s in stages),
                        fitted_coefficients=len(fitted) == 15 and all(abs(v) <= task['zero_tolerance'] for v in fitted.values()))
                row['outcome'] = 'fresh native repair'
            else:
                native = read(public/'candidate_run.json')
                checks.update(valid_retained=record['assessment'].get('valid_result_retained') is True,
                              retained_decision=initial_valid and decisions[1] == 'release_conditionally')
                row['outcome'] = 'valid handoff retained'
            frame, gate = native['frame_selection'], native['gate']
            reported = verification['reported_values']
            distance = par['Lsd'][0]/1000
            discrepancy = abs(distance-facts['nominal_distance_mm'])/facts['nominal_distance_mm']
            nonzero = sum(abs(par[f'p{i}'][0]) > task['zero_tolerance'] for i in range(15))
            transform = facts['image_transform']
            if isinstance(transform, int):
                transform = [transform]
            checks.update(
                selected_frames=frame['frame_indices'] == facts['frames'],
                reduction=frame['frame_reduce'] == facts['frame_reduction'],
                detector_dataset=frame['data_loc'].strip('/') == facts['data_loc'].strip('/'),
                image_transform=native['im_trans_applied'] == transform,
                pitch=par['px'] == [facts['pixel_pitch_um']],
                fitted_phase=par['SpaceGroup'] == [facts['space_group']] and len(par['LatticeConstant']) == 6
                    and all(abs(a-b) <= 1e-6 for a, b in zip(par['LatticeConstant'],[facts['lattice_angstrom']]*3+[90]*3)),
                wavelength=abs(par['Wavelength'][0]-12.398419843320026/facts['energy_kev']) <= 5.1e-7,
                distance_policy=discrepancy <= task['distance_policy_fraction'],
                reported_distance=math.isclose(reported['fitted_distance_mm'], distance, abs_tol=1e-9),
                reported_discrepancy=math.isclose(reported['relative_distance_discrepancy'], discrepancy, abs_tol=1e-12),
                reported_model=reported['requested_model'] == task['requested_model'],
                reported_coefficients=reported['nonzero_distortion_exports'] == nonzero,
                reported_frames=reported['frame_indices'] == facts['frames'] and reported['frame_reduction'] == facts['frame_reduction'],
                coefficient_claim=f'{nonzero}/15 exported distortion coefficients are nonzero' in manifest['summary'],
                agent_proposal_preserved=isinstance(manifest.get('agent_proposed_result'), dict))
            if task['pipeline'] == 'four-stage':
                checks.update(held_out_gate=gate['passed'] is True and 0 <= gate['stage4_strain_uE_test'] < gate['threshold_uE'],
                    reported_gate=reported['image_gate_kind'] == 'held_out_strain',
                    gate_claim='Held-out strain' in manifest['summary'] and 'Ring-position gate' not in manifest['summary'])
            else:
                checks.update(ring_gate=gate['passed'] is True and gate['verdict'] == 'PASS'
                    and math.isfinite(gate['median_px']) and 0 < gate['n_fits'] <= gate['n_cells'],
                    reported_gate=reported['image_gate_kind'] == 'ring_position',
                    gate_claim='Ring-position gate PASS' in manifest['summary'])
            if task['requested_model'] == 'none':
                checks['zero_exports'] = nonzero == 0
            row.update(fitted_distance_mm=distance, nonzero_distortion_exports=nonzero)
        row.update(checks=checks, passed=all(checks.values()),
                   evidence_sha256={str(p.relative_to(root)):sha(p) for p in evidence})
        rows.append(row)
    result = dict(scope='Four internal application checks; no new paper experiment or system ranking.',
                  reviewed_unix=time.time(), audit_code_sha256=sha(Path(__file__)),
                  all_checks_passed=all(r['passed'] for r in rows), trials=rows,
                  requests=sum(r['requests'] for r in rows), provider_denials=terminal['usage']['provider_denials'],
                  limits=['Reused handoffs, one attempt each; engineering evidence only.',
                          'Application enforcement and generated summaries do not demonstrate improved model reasoning.',
                          'Holds are not repairs. Free-form chat and proposals remain separately auditable.',
                          'Supplied acquisition facts are not independent physical ground truth.'])
    with (root/'results/trace_audit.json').open('x') as f:
        json.dump(result, f, indent=2, allow_nan=False)
        f.write('\n')
    lines = ['# Internal application validation', '',
             'This is software validation on the four preserved handoffs. It is excluded from the CVPR main paper and supplement, and is not pooled with earlier attempts.', '',
             'The original records and deterministic trial scores remain unchanged. '
             'This audit independently checks artifacts, native execution receipts, application activation, '
             'original/final decisions and reported numerical values. Free-form chat requires separate reading.', '',
             '| Handoff | Saved outcome | Audit |', '|---|---|---|']
    for row in rows:
        failures = [k for k,v in row.get('checks',{}).items() if not v]
        verdict = 'Checks passed' if row['passed'] else ', '.join(failures) or row.get('error','Audit failed')
        lines.append(f"| {row['fixture']} | {row.get('outcome','No audited outcome')} | {verdict} |")
    lines += ['', 'An accepted repair must match the declared frames, reduction, transform, pitch, '
              'phase, model, gate, artifact hash and native receipt. A hold is not a repair. '
              'The application-generated summary is assessed separately from the retained agent proposal.', '',
              'See [trace_audit.json](trace_audit.json) for every check, refusal, proposal and evidence hash. '
              'The raw final answers remain under each trial’s `traces` directory.', '',
              'No harness ranking, unseen-data result, improved-model-reasoning claim or independent physical ground truth follows from this check.', '']
    with (root/'results/AUDIT.md').open('x') as f:
        f.write('\n'.join(lines))
    print(json.dumps({k:result[k] for k in ('all_checks_passed','requests','provider_denials')}))
    for row in rows:
        print(row['trial'], row.get('outcome'), 'failed checks:',[k for k,v in row.get('checks',{}).items() if not v])
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, required=True)
    audit(p.parse_args().root.resolve())
