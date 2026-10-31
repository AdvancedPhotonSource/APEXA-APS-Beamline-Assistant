import sys
from types import SimpleNamespace

import numpy as np
import pytest

import _tomo_runner as tomo


@pytest.fixture
def setup(monkeypatch, tmp_path):
    calls = []
    def normalize(*args):
        calls.append("normalize")
        return np.full((3, 3, 3), 0.5)
    def phase(projections, **kwargs):
        calls.append("phase")
        np.testing.assert_allclose(projections, 0.5)
        return projections
    def log(projections):
        calls.append("minus_log")
        return -np.log(projections)
    engine = SimpleNamespace(normalize=normalize, minus_log=log,
        prep=SimpleNamespace(phase=SimpleNamespace(retrieve_phase=phase)),
        recon=lambda *a, **k: np.arange(27, dtype=float).reshape(3, 3, 3),
        circ_mask=lambda array, **kw: array, write_center=lambda *a, **k: None,
        find_center_vo=lambda *a: 1.)
    monkeypatch.setitem(sys.modules, "tomopy", engine)
    monkeypatch.setattr(tomo, "_list_frames", lambda *a: ["stub"])
    monkeypatch.setattr(tomo, "_read_stack", lambda *a: np.ones((5, 3, 3)))
    monkeypatch.setattr(tomo, "detect_flat_dark_layout", lambda *a: {"confident": True, "dark": [0], "flat": [1], "proj": [2, 3, 4]})
    args = SimpleNamespace(pixel_size_um=150., handedness="left", data=str(tmp_path),
        pattern="*.tif", output_dir=str(tmp_path), omega_start=0., omega_step=1.,
        phase_retrieval=True, propagation_mm=100., energy_kev=63.314, paganin_alpha=1e-3,
        rotation_center=1., center_sweep=None, algorithm="gridrec", threshold=None)
    return args, engine, calls


def test_phase_retrieval_operates_on_transmission_before_log(setup):
    args, _, calls = setup
    result = tomo.cmd_reconstruct(args)
    assert result["status"] == "success"
    assert calls == ["normalize", "phase", "minus_log"]
    assert result["scientific_verdict"] == "unassessed"


def test_unselected_center_sweep_is_provisional(setup):
    args, _, _ = setup
    args.rotation_center = None
    args.center_sweep = [0., 2., 1.]
    result = tomo.cmd_reconstruct(args)
    assert result["status"] == "provisional"
    assert result["scientific_verdict"] == "provisional"


@pytest.mark.parametrize("value", [float("inf"), float("nan")])
def test_nonfinite_volume_is_rejected_before_publication(setup, tmp_path, value):
    args, engine, _ = setup
    engine.recon = lambda *a, **kw: np.array([[[1., value], [2., 3.]]])
    result = tomo.cmd_reconstruct(args)
    assert result["status"] == "error"
    assert not (tmp_path / "recon.tif").exists()


def test_finite_values_that_overflow_output_dtype_are_rejected(setup, tmp_path):
    args, engine, _ = setup
    engine.recon = lambda *a, **kw: np.arange(27, dtype=np.float64).reshape(3, 3, 3) * 1e39
    result = tomo.cmd_reconstruct(args)
    assert result["status"] == "error"
    assert not (tmp_path / "recon.tif").exists()


@pytest.mark.parametrize("key,value", [("omega_step", 0.), ("pixel_size_um", -1.), ("energy_kev", float("nan"))])
def test_invalid_geometry_does_not_invoke_engine(setup, key, value):
    args, _, calls = setup
    setattr(args, key, value)
    assert tomo.cmd_reconstruct(args)["status"] == "error"
    assert not calls
