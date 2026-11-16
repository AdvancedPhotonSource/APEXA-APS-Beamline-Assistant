#!/usr/bin/env python3
"""Build neutral, hashed public handoffs only after every fixture qualifies."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from check_contract import check


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, value):
    with Path(path).open('x') as f:
        json.dump(value, f, indent=2, allow_nan=False)
        f.write('\n')


def build(root):
    setup = root/'setup'
    spec = json.loads((setup/'study.json').read_text())
    prep = json.loads((setup/'preparation_manifest.json').read_text())
    if prep.get('qualification', {}).get('passed') is not True:
        raise ValueError('Four qualified fixtures required')
    receipts = []
    for number, entry in enumerate(spec['fixtures'], 1):
        private = root/'fixtures_private'/entry['id']
        qual = json.loads((private/'qualification.json').read_text())
        for name, digest in qual['files_sha256'].items():
            if sha(private/name) != digest:
                raise ValueError('Fixture changed after qualification')
        out = root/'public'/f'case{number:02d}'
        out.mkdir(parents=True, exist_ok=False)
        inp = prep['inputs'][str(entry['scan'])]
        subprocess.run(['cp', '--reflink=auto', inp['path'], str(out/'acquisition.h5')], check=True)
        shutil.copyfile(private/'fit/paramstest_v2.txt', out/'candidate.params')
        shutil.copyfile(private/'template.txt', out/'effective_template.txt')
        shutil.copyfile(setup/'check_contract.py', out/'check_contract.py')
        original = json.loads((private/'runner.stdout.json').read_text())
        public_run = {k:original[k] for k in ('engine', 'capabilities', 'frame_selection',
                     'phase_provenance', 'lsd', 'gate', 'im_trans_applied') if k in original}
        if 'frame_selection' in public_run:
            public_run['frame_selection']['source'] = 'acquisition.h5'
        dump(out/'candidate_run.json', public_run)
        executed = json.loads((private/'execution.json').read_text())
        public_execution = {'effective_pixel_pitch_um': entry['pixel_pitch_um'],
            'process_return_code': executed['process']['return_code'],
            'runner_source_sha256': executed['identity']['runner_source_sha256'],
            'executed_runner_sha256': executed['identity']['executed_runner_sha256'],
            'stages': executed['stages'], 'candidate_sha256': sha(out/'candidate.params')}
        dump(out/'execution_evidence.json', public_execution)
        task = {'schema_version': 1, 'facts': {'pixel_pitch_um': spec['documented_pixel_pitch_um'],
                    'nominal_distance_mm': inp['distance_readback_mm'],
                    'distance_readback_field': spec['readback_path'],
                    'calibrant': entry['calibrant'], 'lattice_angstrom': entry['lattice_angstrom'],
                    'space_group':225, 'energy_kev':spec['energy_kev'],
                    'image_transform':2, 'data_loc':'exchange/data',
                    'frames':list(range(1,21)), 'frame_reduction':'mean'},
                'requested_model':entry['model'], 'pipeline':entry['pipeline'],
                'distance_policy_fraction':spec['distance_discrepancy_fraction'],
                'zero_tolerance':spec['zero_coefficient_tolerance'],
                'readback_scope':'Nominal instrument reference, not independently calibrated absolute truth.'}
        dump(out/'task.json', task)
        report = check(out/'task.json', out/'candidate.params', out/'candidate_run.json', out/'execution_evidence.json')
        compact = json.dumps(report, separators=(',', ':'), allow_nan=False)
        if len(compact) > 2000 or not report['evidence_complete']:
            raise ValueError('Initial report incomplete or over 2000 characters')
        for condition in ('automatic','on_demand'):
            receipt_dir = out/condition
            receipt_dir.mkdir()
            body = compact if condition == 'automatic' else json.dumps({
                'automatic_check_performed':False,
                'checker':'python check_contract.py is available on demand; assess the same source evidence.'},separators=(',',':'))
            (receipt_dir/'initial_check.json').write_text(body+'\n')
        hashes = {p.name:sha(p) for p in out.iterdir() if p.is_file()}
        for p in out.rglob('*'):
            if p.is_file(): p.chmod(0o444)
        receipts.append({'fixture':entry['id'], 'public_case':out.name, 'files_sha256':hashes,
            'feedback':{c:sha(out/c/'initial_check.json') for c in ('automatic','on_demand')},
            'automatic_report_characters':len(compact),
            'automatic_report_tokens':None,
            'token_count_note':'Measured with the served model tokenizer at trial freeze, if locally available; never label a character estimate as tokens.',
            'fixed_checker_decision':'release_conditionally' if report['all_declared_checks_satisfied'] else 'hold'})
    dump(setup/'handoff_manifest.json', {'cases':receipts,'paired_source_inputs_identical':True,
        'public_generator_labels_removed':True,'model_calls':0})


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True)
    build(p.parse_args().root.resolve())
