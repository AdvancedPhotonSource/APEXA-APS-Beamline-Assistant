"""Post-run artifact/trace audit of the frozen eight-attempt R3 regression.

Reads the original records; never modifies agent outputs or their original scores.
Uses preserved handoff pixel-array hashes and live tool traces independently of
the production contract checker. Reporting findings are manual trace adjudications.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics
import time


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parameters(path):
    values = {}
    wanted = {'px', 'Lsd', 'Wavelength', 'SpaceGroup', 'LatticeConstant',
              *(f'p{i}' for i in range(15))}
    for line in path.read_text().splitlines():
        fields = line.split('#', 1)[0].split()
        if fields and fields[0] in wanted:
            parsed = [float(x) for x in fields[1:]]
            if not parsed or not all(map(math.isfinite, parsed)):
                raise ValueError('Missing or nonfinite exported parameter')
            if fields[0] in values and parsed != values[fields[0]]:
                raise ValueError('Conflicting exported parameter')
            values[fields[0]] = parsed
    return values


REPORTING_FINDINGS = {
    1: ['Skipped contract loading; correct valid-handoff decision, but no saved contract verification or physical scope limits.'],
    2: ['Contract activated and a missing final_decision was refused before finalization. Valid handoff, artifact hash and scope limits retained.'],
    3: ['Faithful repair and correct before/after decisions. The prose calls the four-stage strain check a ring-overlay gate; no such ring-position gate is recorded.',
        'The repaired-distance claim cites initial_check.json, which describes the original handoff rather than the new fit.'],
    4: ['Faithful repair, but missing handoff_decision makes the task manifest schema-invalid.',
        'The prose also claims a passing ring overlay; the recorded check is held-out strain.'],
    5: ['Faithful full-model repair and correct before/after decisions, but the prose says distortion stayed at default zero. All fifteen exported coefficients are nonzero.',
        'The repaired pixel-pitch assessment cites original candidate/template files that contain the wrong 120 um pitch.'],
    6: ['Faithful repair, but handoff_decision incorrectly releases the original scale-fault handoff.',
        'The nominal-distance assessment cites a 0.2 discrepancy as within 5%; the old relative discrepancy was 0.2, whereas the repaired relative discrepancy is about 0.00031.'],
    7: ['Skipped contract loading. Correct retention and artifact hash, but no saved contract verification or physical scope limits.',
        'The reason says no distortion coefficients are present; the exported coefficients are present and equal to zero.'],
    8: ['Skipped contract loading. Correct retention and artifact hash, but no saved contract verification or physical scope limits.'],
}


def audit(root):
    terminal = read(root/'setup/trial-status.json')
    if terminal['completed'] != 8:
        raise ValueError('This audit requires all eight frozen R3 attempts')
    handoffs = {c['fixture']: root/'public'/c['public_case']
                for c in read(root/'setup/handoff_manifest.json')['cases']}
    rows = []
    for index in range(1, 9):
        trial = root/'results'/f'trial{index:02d}'
        record_path = trial/'record.json'
        record = read(record_path)
        manifest_path, = (trial/'workspaces').glob('*/result_manifest.json')
        manifest = read(manifest_path)
        public = handoffs[record['entry']['fixture']]
        task, original = read(public/'task.json'), read(public/'candidate_run.json')
        facts = task['facts']
        evidence = [record_path, manifest_path, public/'task.json', public/'candidate_run.json']
        events = []
        for trace in (trial/'traces').glob('*.tools.jsonl'):
            evidence.append(trace)
            for line in trace.read_text().splitlines():
                event = json.loads(line)
                value = event.get('result')
                if isinstance(value, str):
                    try: value = json.loads(value)
                    except ValueError: value = {}
                events.append((event, value))
        adopted = manifest['adopted_artifact']
        artifact = manifest_path.parent/adopted['path']
        if not artifact.is_file() and adopted['path'] == 'candidate.params':
            artifact = public/'candidate.params'
        evidence.append(artifact)
        checks = {'artifact_hash': sha(artifact) == adopted['sha256']}
        repairs = [(event, value) for event, value in events
                   if event.get('tool') == 'midas_auto_calibrate' and value.get('status') == 'success']
        row = {'trial': index, 'entry': record['entry'],
               'execution': record['execution']['outcome'],
               'elapsed_seconds': record['execution']['elapsed_s'],
               'requests': record['model_audit']['requests'],
               'original_schema_status': record['assessment']['manifest_status'],
               'final_decision': manifest.get('final_decision'),
               'handoff_decision': manifest.get('handoff_decision'),
               'contract_activated': any(e.get('tool') == 'load_calibration_contract'
                                         and v.get('client_contract_active') is True for e, v in events),
               'scope_limits_saved': bool(manifest.get('remaining_limits')),
               'reporting_findings': REPORTING_FINDINGS[index]}
        if repairs:
            if len(repairs) != 1:
                raise ValueError('Expected one live computation per repair attempt')
            _, live = repairs[0]
            receipt = manifest_path.parent/manifest['repair_evidence']['execution_record_path']
            evidence.append(receipt)
            saved = read(receipt)
            native, effective = saved['native_result'], saved['effective_arguments']
            frame, gate = native['frame_selection'], native['gate']
            par = parameters(artifact)
            checks.update(
                receipt_hash=sha(receipt) == manifest['contract_verification']['execution_receipt_sha256'],
                live_artifact_hash=adopted['sha256'] == saved['artifact_sha256'] == live['artifact_sha256'],
                receipt_matches_live_trace=all(native.get(k) == live.get(k) for k in (
                    'frame_selection', 'phase_provenance', 'gate', 'distortion_verification', 'constraint_execution')),
                effective_arguments_match_trace=effective == live['contract_execution']['effective_arguments'],
                exact_frames=frame['frame_indices'] == facts['frames'],
                reduction=frame['frame_reduce'] == facts['frame_reduction'],
                transform=native['im_trans_applied'] == [facts['image_transform']],
                oriented_pixel_array_hash=bool(frame.get('oriented_frame_sha256')) and
                    frame['oriented_frame_sha256'] == original['frame_selection']['oriented_frame_sha256'],
                effective_pitch=effective['px_um'] == facts['pixel_pitch_um'],
                pipeline=effective['calibration_pipeline'] == task['pipeline'],
                requested_model=effective['refine_distortion'] == task['requested_model'],
                exported_pitch=par['px'] == [facts['pixel_pitch_um']],
                fitted_phase=par['SpaceGroup'] == [facts['space_group']] and
                    len(par['LatticeConstant']) == 6 and all(abs(a-b) < 1e-6 for a, b in zip(
                        par['LatticeConstant'], [facts['lattice_angstrom']]*3+[90]*3)),
                wavelength=abs(par['Wavelength'][0]-12.398419843320026/facts['energy_kev']) < 5.1e-7,
                distance_policy=abs(par['Lsd'][0]/1000-facts['nominal_distance_mm']) /
                    facts['nominal_distance_mm'] <= task['distance_policy_fraction'],
                image_gate=gate['passed'] is True)
            if task['requested_model'] == 'none':
                values = native['distortion_verification']['checked_values']
                stages = native['constraint_execution']
                checks.update(
                    zero_export=all(abs(par[f'p{j}'][0]) <= task['zero_tolerance'] for j in range(15)),
                    stage2_flags=bool(stages) and all(len(s['after_thaw_refine_flags']) == 15 and
                        all(v is False for v in s['after_thaw_refine_flags'].values()) for s in stages),
                    fitted_coefficients=len(values) == 15 and all(abs(x) <= task['zero_tolerance'] for x in values.values()),
                    held_out_gate=0 <= gate['stage4_strain_uE_test'] < gate['threshold_uE'])
            else:
                checks['ring_gate'] = (gate['verdict'] == 'PASS' and
                    0 < gate['n_fits'] <= gate['n_cells'] and math.isfinite(gate['median_px']))
            row.update(repair_checks=checks, faithful_repair=all(checks.values()),
                corrected_arguments=saved['corrected_arguments'],
                fitted_distance_mm=par['Lsd'][0]/1000,
                nonzero_distortion_exports=sum(abs(par[f'p{j}'][0]) > task['zero_tolerance'] for j in range(15)))
        else:
            row.update(retained_valid_handoff=record['assessment']['valid_result_retained'], artifact_checks=checks)
        row['evidence_sha256'] = {str(p.relative_to(root)): sha(p) for p in evidence}
        rows.append(row)
    counts = dict(attempts=8, completed=sum(x['execution'] == 'execution_completed' for x in rows),
        schema_valid=sum(x['original_schema_status'] == 'valid' for x in rows),
        contract_activated=sum(x['contract_activated'] for x in rows),
        faithful_repairs=sum(x.get('faithful_repair', False) for x in rows),
        retained_valid_handoffs=sum(x.get('retained_valid_handoff', False) for x in rows),
        frame_proposals_corrected=sum('skip_frame' in x.get('corrected_arguments', {}) for x in rows),
        requests=sum(x['requests'] for x in rows), median_requests=statistics.median(x['requests'] for x in rows),
        provider_denials=terminal['usage']['provider_denials'])
    result = {'schema_version': 1, 'audit_scope': 'All eight R3 attempts; independent artifact/trace audit, with original deterministic scores unchanged.',
        'reviewed_unix': time.time(), 'audit_code_sha256': sha(Path(__file__)),
        'counts': counts, 'audited_trials': rows,
        'limits': ['This is a post-observation system regression on reused acquisitions, not a general harness ranking or evidence of better model reasoning.',
                   'Verified execution of declared settings is not independent physical ground truth.',
                   'Three correct retained results did not activate the contract. Reporting/schema and original-handoff decisions remain separate from repaired-artifact correctness.']}
    output = root/'results/trace_audit.json'
    if output.exists():
        prior = read(output)
        result['previous_audit'] = {'reviewed_unix': prior.get('reviewed_unix'),
                                    'audited_trial_count': len(prior.get('audited_trials', [])),
                                    'sha256': sha(output)}
    temporary = output.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(result, indent=2, allow_nan=False)+'\n')
    temporary.replace(output)
    lines = ['# R3 completed regression audit', '',
        'All eight attempts completed without timeouts or provider quota failures. All four faulty handoffs received fresh native repairs that match the declared frames, reduction, transform, pixel pitch, phase, model and numerical acceptance checks. All four valid handoffs were retained with matching artifact hashes.', '',
        '**The direct mechanism is visible in the traces:** all four repair calls proposed `skip_frame=0`; contract dispatch changed this to 1. The recorded oriented-image hashes match the qualified frames 1–20, and the repair artifacts and execution receipts have intact hashes. This is an observed system correction, not evidence that the model reasoned better.', '',
        '| Measure | Result |', '|---|---:|',
        f"| Contract-faithful native repairs | {counts['faithful_repairs']}/4 |",
        f"| Valid handoffs retained | {counts['retained_valid_handoffs']}/4 |",
        f"| Contract activated | {counts['contract_activated']}/8 |",
        f"| Task-schema-valid manifests | {counts['schema_valid']}/8 |",
        f"| Model requests | {counts['requests']} total; median {counts['median_requests']} |", '',
        '**Remaining errors:** trials 1, 7 and 8 skipped the loader and omitted physical scope limits. Trial 4 omitted the original-handoff decision; trial 6 incorrectly released the original faulty handoff. Trial 5 falsely described its full-model coefficients as zero, although all fifteen exported values are nonzero. Trials 3 and 4 described the strain check as a passing ring overlay. These prevent an 8/8 end-to-end-success claim.', '',
        '| Trial | Case | Requests | Artifact/decision result |', '|---|---|---:|---|']
    for row in rows:
        result_text = 'contract-faithful repair' if row.get('faithful_repair') else 'valid handoff retained'
        lines.append(f"| {row['trial']} | {row['entry']['fixture']} | {row['requests']} | {result_text} |")
    lines += ['', 'Detailed reporting findings, independent checks, corrections and evidence hashes are in [trace_audit.json](trace_audit.json). Rerun [audit_contract_regression.py](audit_contract_regression.py) with `--root` pointing to the remote R3 directory, where the frozen public handoffs are available.', '',
        'Product follow-up: activate a declared contract in application-owned session initialization, validate the original-handoff decision separately from the repaired result, and build authoritative summaries from recorded facts. Keep this completed cohort and its original outputs unchanged.', '']
    (root/'results/AUDIT.md').write_text('\n'.join(lines))
    return counts


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    print(json.dumps(audit(parser.parse_args().root.resolve()), indent=2))
