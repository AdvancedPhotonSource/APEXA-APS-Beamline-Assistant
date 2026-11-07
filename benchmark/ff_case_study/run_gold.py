"""Freeze raw evidence, recalibrate and invoke APEXA's typed gold FF workflow."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

DATA = Path('/gdata/dm/20ID/HT_HEDM/2026-2/nfdev_jul26/data/varexD')
STEM = 'Au_FF_box_1p1x0p025_att6'
GOLD = DATA / STEM / f'{STEM}_001181.vrx.h5'
CALIB = {tag: DATA / 'cali' / f'Ceria_0723_63keV_900mm_100x100_2p0s_aero_{angle}_{scan:06d}.vrx.h5'
         for tag, angle, scan in [('aero_180', 180, 1187), ('aero_0', 0, 1188)]}
BIN = Path('/home/beams12/S1IDUSER/opt/envs/midas/bin')
LOCK = Path('/home/beams/S20HEDM/apexa_eval/harness-comparison/setup/scientific-run.lock')


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as f:
        json.dump(data, f, indent=2)
        f.write('\n')


def prepare(root):
    import h5py
    import numpy as np
    import importlib.metadata as metadata
    records = []
    for role, path in [('gold', GOLD), *CALIB.items()]:
        print(f'Fingerprinting {role}: {path.name}', flush=True)
        record = {'role': role, 'path': str(path), 'bytes': path.stat().st_size, 'sha256': sha(path)}
        with h5py.File(path, 'r') as f:
            data = f['exchange/data']
            record.update(shape=list(data.shape), dtype=str(data.dtype))
            indices = sorted(set([0, 1, data.shape[0] // 2, data.shape[0] - 1]))
            record['sampled_frames'] = []
            for i in indices:
                a = data[i]
                record['sampled_frames'].append({'frame': i, 'mean': float(a.mean()),
                    'nonzero_fraction': float(np.count_nonzero(a) / a.size), 'max': int(a.max())})
            dark = f['exchange/dark'][()]
            record['dark'] = {'shape': list(dark.shape), 'mean': float(dark.mean()),
                              'max': int(dark.max())}
            record['energy_fields'] = []
            def visit(name, obj):
                if isinstance(obj, h5py.Dataset) and any(k in name.lower() for k in ['energy', 'wavelength', 'lambda', 'mono']):
                    record['energy_fields'].append(name)
            f.visititems(visit)
            if role == 'gold':
                if data.shape != (1442, 2880, 2880) or np.any(dark):
                    raise RuntimeError('Gold shape or zero-dark evidence differs from the protocol')
                aero = f['measurement/instrument/SMS/aero'][()].ravel()
                record['aero_readback'] = {'first': float(aero[0]), 'last': float(aero[-1]),
                    'median_increment': float(np.median(np.diff(aero))), 'samples': len(aero)}
                record['scan_parameters'] = {k: f[f'measurement/process/scan_parameters/{k}'][()].tolist()
                                              for k in ['start', 'end', 'step']}
            elif dark.mean() < 100:
                raise RuntimeError('Calibrant no longer has the expected measured dark pedestal')
        records.append(record)
    packages = ['midas-calibrate-v2', 'midas-pipeline', 'midas-zipper', 'midas-peakfit',
                'midas-transforms', 'midas-index', 'midas-fit-grain', 'midas-process-grains', 'midas-joint-ff-calibrate']
    write(root / 'setup/frozen.json', {'created_unix': time.time(), 'inputs': records,
        'packages': {p: metadata.version(p) for p in packages},
        'source_sha256': sha(__file__), 'protocol_sha256': sha(root / 'source/PROTOCOL.md'),
        'expected_grains': 2, 'expected_grains_source': 'operator hypothesis, not independently verified',
        'energy_keV': 63.314, 'energy_source': 'operator-supplied historical record; not HDF5 metrology',
        'selected_calibration': 'aero_180', 'old_grain_or_geometry_inputs': []})


def calibrate(root, tag):
    output = root / 'calibration' / tag / 'Parameters.txt'
    command = [str(BIN / 'midas-calibrate-v2'), '--mode', 'ff', '--image', str(CALIB[tag]),
        '--output', str(output), '--dark-group', '/exchange/dark', '--image-group', '/exchange/data',
        '--reduce', 'median', '--wavelength', str(12.398419843320026 / 63.314),
        '--initial-lsd', '900000', '--im-trans', '2', '--calibrant', 'CeO2', '--n-iter', '5',
        '--device', 'cpu', '--verbose', '--px', '150', '--n-pixels', '2880',
        '--lattice', '4.0782', '4.0782', '4.0782', '90', '90', '90', '--space-group', '225',
        '--raw-folder', str(GOLD.parent), '--file-stem', STEM, '--start-file-nr', '1181',
        '--ext', '.vrx.h5', '--padding', '6', '--dark-file', str(GOLD), '--dark-loc', '/exchange/dark',
        '--omega-start', '180', '--omega-step', '-0.25', '--skip-frame', '1', '--n-files-per-sweep', '1',
        '--ring-to-index', '1', '--strain-gate', '100']
    for ring, threshold in enumerate([20, 20, 20, 20, 30], 1):
        command += ['--ring-thresh', f'{ring}:{threshold}']
    for item in ['MinNrPx=4', 'MinOmeSpotIDsToIndex=-180', 'MaxOmeSpotIDsToIndex=180']:
        command += ['--set', item]
    output.parent.mkdir(parents=True)
    write(output.parent / 'command.json', {'argv': command, 'started_unix': time.time()})
    with (output.parent / 'execution.log').open('x') as log:
        p = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=7200)
    write(output.parent / 'exit.json', {'return_code': p.returncode, 'finished_unix': time.time()})
    if p.returncode:
        raise RuntimeError(f'{tag} calibration failed; preserve log and inspect before reconstruction')
    return output


async def reconstruct(root, source):
    sys.path.insert(0, str(source))
    # State and caches belong to this case study, never production or old runs.
    state = root / 'state'
    state.mkdir(exist_ok=True)
    for name, relative in [('APEXA_HOME', 'apexa'), ('APEXA_SESSION_DIR', 'sessions'),
                           ('APEXA_RUN_DIR', 'runs'), ('APEXA_WORKFLOW_DIR', 'workflows'),
                           ('APEXA_TRACE_DB', 'traces.sqlite3'), ('APEXA_TIMING_LOG', 'timing.jsonl')]:
        os.environ[name] = str(state / relative)
    os.environ['APEXA_MIDAS_BIN'] = str(BIN)
    import midas_comprehensive_server as server
    params = root / 'calibration/aero_180/Parameters.txt'
    output = root / 'reconstruction/raw-clean'
    call = {'result_folder': str(output), 'param_file': str(params), 'data_file': str(GOLD),
            'n_cpus': 32, 'device': 'cpu', 'indexer_backend': 'c-omp', 'refine_backend': 'c-omp',
            'start_layer': 1, 'end_layer': 1, 'process_grains': True}
    write(root / 'setup/apexa_call.json', {'tool': 'run_ff_hedm_full_workflow', 'arguments': call,
        'server_sha256': sha(source / 'midas_comprehensive_server.py'),
        'invocation': 'scripted typed-tool call; no LLM', 'started_unix': time.time()})
    fn = server.run_ff_hedm_full_workflow
    value = await (fn.fn if hasattr(fn, 'fn') else fn)(**call)
    write(root / 'setup/apexa_result.json', {'returned': value, 'finished_unix': time.time()})
    fn = server.verify_ff_reconstruction
    verification = await (fn.fn if hasattr(fn, 'fn') else fn)(result_folder=str(output))
    write(root / 'setup/apexa_verification.json', {'returned': verification, 'finished_unix': time.time()})
    print(value, flush=True)
    print(verification, flush=True)
    if json.loads(value).get('status') != 'success':
        raise RuntimeError('APEXA did not report fresh reconstruction success; inspect the preserved response')
    if json.loads(verification).get('status') != 'ok':
        raise RuntimeError('Independent APEXA artifact verification did not pass')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'calibrate', 'reconstruct'])
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--source', type=Path)
    args = parser.parse_args()
    os.environ['PATH'] = str(BIN) + os.pathsep + os.environ.get('PATH', '')
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    for key in ['OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'NUMEXPR_NUM_THREADS']:
        os.environ[key] = '8'
    if args.action == 'prepare':
        prepare(args.root)
    else:
        frozen = json.loads((args.root / 'setup/frozen.json').read_text())
        if frozen['source_sha256'] != sha(__file__):
            raise RuntimeError('Case-study source changed after freezing')
        import fcntl
        with LOCK.open('a') as lock:
            print('Waiting for the common scientific-run lock.', flush=True)
            fcntl.flock(lock, fcntl.LOCK_EX)
            if args.action == 'calibrate':
                for tag in CALIB:
                    calibrate(args.root, tag)
            else:
                if args.source is None:
                    parser.error('reconstruct requires --source')
                asyncio.run(reconstruct(args.root, args.source))


if __name__ == '__main__':
    main()
