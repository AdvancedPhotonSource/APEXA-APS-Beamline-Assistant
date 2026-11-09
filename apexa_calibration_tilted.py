"""Typed adapter for MIDAS's single-panel, ring-gated calibration workflow.

Executed only inside the owned calibration worker. No benchmark references or
acquisition-specific constants enter this adapter.
"""
from pathlib import Path
import json
import math


def template_calibrant(v1):
    """Carry the actual lattice into seeding, fitting and the gate.

    A custom name prevents upstream from replacing explicitly supplied lattice
    values with its registered material defaults during parameter construction.
    """
    values = [float(x) for x in v1.LatticeConstant]
    if len(values) != 6 or not all(math.isfinite(x) and x > 0 for x in values):
        raise ValueError('Template requires six positive finite lattice parameters')
    return dict(zip(('a', 'b', 'c', 'alpha', 'beta', 'gamma'), values),
                sg=int(v1.SpaceGroup), name='APEXA_template_phase')


def validate_request(args, v1):
    if (args.panel_ny or args.panel_nz or len(args.calibrant) > 1
            or len(getattr(v1, 'Phases', None) or []) > 1):
        raise ValueError('The tilted pipeline requires one panel and one calibrant phase')
    if not math.isclose(float(v1.pxY), float(v1.pxZ), rel_tol=1e-12):
        raise ValueError('The tilted pipeline currently requires square detector pixels')
    if args.refine_distortion != 'full' or args.tol_tilts_deg or args.lsd_tol_um:
        raise ValueError('The tilted pipeline uses MIDAS built-in refinement settings; '
                         'use four-stage for explicit distortion or parameter-bound overrides')
    if args.device not in ('', 'cpu'):
        raise ValueError('This MIDAS tilted pipeline executes on CPU; request device=cpu')
    if args.ignore_gate:
        raise ValueError('The tilted pipeline retains the ring gate verdict; it cannot be bypassed')


def execute(args, v1, img, bad_mask, dark, *, caps, notes, scope, frame_selection,
            calibrate=None):
    import numpy as np
    validate_request(args, v1)
    if calibrate is None:
        from midas_calibrate_v2.pipelines.tilted import calibrate_tilted
        calibrate = calibrate_tilted
    cal = template_calibrant(v1)
    if dark is not None and (dark.shape != img.shape or not np.isfinite(dark).all()):
        raise ValueError('The dark must be finite and have the selected image shape')
    prepared = img if dark is None else img-dark
    result = calibrate(prepared, wavelength_A=float(v1.Wavelength), px_um=float(v1.pxY),
                       calibrant=cal, mask=bad_mask, nominal_tilt_deg=None,
                       lsd_um=(float(args.expected_lsd_um) if args.expected_lsd_um > 0
                               and not args.trust_seed_lsd else None),
                       polish=True, fallback_pv=True, verbose=True)
    out = Path(args.output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    record = result.to_dict()
    (out/'solver_result.json').write_text(json.dumps(record, indent=2, allow_nan=False)+'\n')
    (out/'candidate_geometry.json').write_text(json.dumps(result.geometry, indent=2, allow_nan=False)+'\n')
    if result.verdict not in ('PASS', 'WARN', 'FAIL'):
        raise ValueError(f'Unknown MIDAS ring verdict: {result.verdict!r}')
    if any(not np.isfinite(float(v)) for v in result.geometry.values()):
        raise ValueError('MIDAS returned nonfinite candidate geometry')
    paramstest = None
    if result.verdict != 'FAIL':
        paramstest = str(out/'paramstest_v2.txt')
        result.write_paramstest(paramstest)
    mask_path = str(out/'calibration_mask.tif')
    result.write_mask(mask_path)
    gate = dict(record['gate'], kind='ring_position', passed=result.verdict == 'PASS',
                scope='image residuals and coverage; not independent absolute geometry truth')
    payload = {
        'status': {'PASS': 'success', 'WARN': 'warning', 'FAIL': 'error'}[result.verdict],
        'scientific_status': {'PASS': 'accepted', 'WARN': 'provisional', 'FAIL': 'rejected'}[result.verdict],
        'engine': f"canonical-v2:tilted ({caps['midas_calibrate_v2']})",
        'recipe': 'MIDAS single-panel calibrate_tilted; unchanged upstream ring-position gate',
        'capabilities': caps, 'image': str(Path(args.image).resolve()),
        'dark': str(Path(args.dark).resolve()) if args.dark else None,
        'template': str(Path(args.template).resolve()), 'output_dir': str(out),
        'paramstest_file': paramstest, 'solver_result': str(out/'solver_result.json'),
        'candidate_geometry': str(out/'candidate_geometry.json'),
        'mask_file': mask_path, 'mask_coordinates': 'MIDAS oriented image coordinates',
        'im_trans_applied': frame_selection['im_trans_applied'], 'frame_selection': frame_selection,
        'phase_provenance': {'fit_phase_source': 'template', 'effective_calibrant': cal,
                             'effective_wavelength_A': float(v1.Wavelength),
                             'effective_px_um': float(v1.pxY)},
        'lsd': {'hint_um': float(args.expected_lsd_um) if args.expected_lsd_um > 0 else None,
                'hint_used': bool(args.expected_lsd_um > 0 and not args.trust_seed_lsd),
                'hint_ignored_reason': ('caller requested trust_seed_lsd; the hint was not tested'
                                        if args.trust_seed_lsd else None),
                'effective_initial_um': (float(args.expected_lsd_um)
                                         if args.expected_lsd_um > 0 and not args.trust_seed_lsd
                                         else record.get('seed', {}).get('Lsd_um')),
                'seed_um': record.get('seed', {}).get('Lsd_um'),
                'fitted_um': result.geometry.get('Lsd')},
        'seed': record.get('seed', {}), 'gate': gate, 'scope_gate': scope,
        'notes': list(notes)+list(record.get('notes', [])), 'nothing_was_run': False,
    }
    if result.verdict != 'PASS':
        payload['error'] = f"MIDAS ring-position gate returned {result.verdict}; retain this decision"
    return payload
