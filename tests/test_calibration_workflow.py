"""Input fidelity and scientific decision preservation in native calibration."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import sys

import h5py
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apexa_calibration_io import selected_source
from apexa_calibration_tilted import execute, template_calibrant


def test_bounded_frames_preserve_integers_and_leave_raw_unchanged(tmp_path):
    raw = tmp_path/'input.h5'
    data = np.arange(5*3*4, dtype=np.uint16).reshape(5, 3, 4)
    data[1, 1, 1] = np.iinfo(np.uint16).max
    with h5py.File(raw, 'w') as f:
        f['exchange/data'] = data
    digest = hashlib.sha256(raw.read_bytes()).hexdigest()
    with selected_source(raw, data_loc='exchange/data', skip_frame=1,
                         frame_count=2, frame_reduce='mean') as (path, skip, record):
        assert skip == 0 and record['frame_indices'] == [1, 2]
        with h5py.File(path) as f:
            assert f['exchange/data'].dtype == data.dtype
            np.testing.assert_array_equal(f['exchange/data'][...], data[1:3])
        temporary = Path(path)
    assert not temporary.exists()
    assert hashlib.sha256(raw.read_bytes()).hexdigest() == digest


@pytest.mark.parametrize('skip,count', [(5, 0), (4, 2), (-1, 1), (0, -1)])
def test_invalid_frame_range_refuses_instead_of_truncating(tmp_path, skip, count):
    raw = tmp_path/'input.h5'
    with h5py.File(raw, 'w') as f:
        f['exchange/data'] = np.ones((5, 2, 2), dtype=np.uint16)
    with pytest.raises(ValueError):
        with selected_source(raw, data_loc='exchange/data', skip_frame=skip, frame_count=count):
            pytest.fail('Invalid range reached the reader')


def test_unbounded_selection_uses_original_reader_path(tmp_path):
    raw = tmp_path/'input.h5'
    with h5py.File(raw, 'w') as f:
        f['exchange/data'] = np.ones((4, 2, 2), dtype=np.uint16)
    with selected_source(raw, data_loc='exchange/data', skip_frame=1) as (path, skip, record):
        assert Path(path) == raw and skip == 1 and record['frame_indices'] == [1, 2, 3]


def test_single_frame_cannot_be_skipped(tmp_path):
    raw = tmp_path/'input.h5'
    with h5py.File(raw, 'w') as f:
        f['exchange/data'] = np.ones((2, 2), dtype=np.uint16)
    with pytest.raises(ValueError):
        with selected_source(raw, data_loc='exchange/data', skip_frame=1):
            pytest.fail('Empty selection must be refused')


def args(tmp_path):
    return SimpleNamespace(panel_ny=0, panel_nz=0, calibrant=['CeO2'],
        refine_distortion='full', tol_tilts_deg=0, lsd_tol_um=0, device='cpu',
        ignore_gate=False, expected_lsd_um=900000, trust_seed_lsd=False,
        output_dir=str(tmp_path), image='input.h5', dark='', template='params.txt')


def template():
    return SimpleNamespace(LatticeConstant=(5.41234, 5.41234, 5.41234, 90, 90, 90),
                           SpaceGroup=225, Wavelength=.196, pxY=150, pxZ=150)


@pytest.mark.parametrize('verdict,status,decision', [
    ('PASS', 'success', 'accepted'), ('WARN', 'warning', 'provisional'),
    ('FAIL', 'error', 'rejected')])
def test_native_gate_controls_decision_and_failed_geometry_is_not_exported(
        tmp_path, verdict, status, decision):
    seen = {}
    class Result:
        geometry = {'Lsd': 900000., 'BC_y': 4., 'BC_z': 5., 'ty': 0., 'tz': 6.}
        def to_dict(self):
            return {'gate': {'verdict': verdict, 'median_px': .2}, 'seed': {}, 'notes': []}
        def write_paramstest(self, path):
            assert verdict != 'FAIL'
            Path(path).write_text('Lsd 900000\ntz 6\n')
        def write_mask(self, path):
            Path(path).write_bytes(b'mask')
    result = Result()
    result.verdict = verdict
    def calibrate(image, **kwargs):
        seen.update(kwargs)
        np.testing.assert_array_equal(image, np.full((2, 2), 8.))
        return result
    payload = execute(args(tmp_path), template(), np.full((2, 2), 10.),
                      np.zeros((2, 2), dtype=bool), np.full((2, 2), 2.),
                      caps={'midas_calibrate_v2': 'test'}, notes=[], scope=None,
                      frame_selection={'im_trans_applied': [2]}, calibrate=calibrate)
    assert payload['status'] == status and payload['scientific_status'] == decision
    assert payload['gate']['passed'] == (verdict == 'PASS')
    assert bool(payload['paramstest_file']) == (verdict != 'FAIL')
    assert seen['calibrant']['a'] == 5.41234
    assert seen['calibrant']['name'] not in ('CeO2', 'LaB6', 'Si')
    assert seen['nominal_tilt_deg'] is None and seen['lsd_um'] == 900000
    assert (tmp_path/'candidate_geometry.json').is_file()


@pytest.mark.parametrize('field,value', [('ignore_gate', True), ('panel_ny', 2),
    ('refine_distortion', 'none'), ('tol_tilts_deg', 20), ('device', 'cuda')])
def test_unsupported_requests_cannot_silently_run_another_model(tmp_path, field, value):
    request = args(tmp_path)
    setattr(request, field, value)
    def forbidden(*a, **k):
        pytest.fail('Solver must not run for an unsupported request')
    with pytest.raises(ValueError):
        execute(request, template(), np.ones((2, 2)), None, None,
                caps={}, notes=[], scope=None, frame_selection={}, calibrate=forbidden)


@pytest.mark.parametrize('dark', [np.ones((2, 1)), np.full((2, 2), np.nan)])
def test_dark_cannot_broadcast_or_contaminate_the_image(tmp_path, dark):
    def forbidden(*a, **k):
        pytest.fail('Invalid dark reached the numerical solver')
    with pytest.raises(ValueError, match='dark'):
        execute(args(tmp_path), template(), np.ones((2, 2)), None, dark,
                caps={}, notes=[], scope=None, frame_selection={}, calibrate=forbidden)


def test_multiphase_template_cannot_silently_become_one_phase(tmp_path):
    multi = template()
    multi.Phases = [{}, {}]
    def forbidden(*a, **k):
        pytest.fail('Multiple phases were silently dropped')
    with pytest.raises(ValueError, match='one calibrant phase'):
        execute(args(tmp_path), multi, np.ones((2, 2)), None, None,
                caps={}, notes=[], scope=None, frame_selection={}, calibrate=forbidden)
