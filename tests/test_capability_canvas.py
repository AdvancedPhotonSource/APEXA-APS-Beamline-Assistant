import argparse
import json
import sys
import types

import numpy as np
import pytest

import _capability_runner as runner
from apexa_canvas import science_view


def test_invalid_cif_does_not_request_canvas(tmp_path):
    path = tmp_path / "looks_like_a_structure.cif"
    path.write_text("not a CIF")
    with pytest.raises(Exception):
        science_view(str(path))


def test_pdf_returns_actual_plot_coordinates_and_honest_peak_label(monkeypatch, tmp_path):
    captured = {}
    fake = types.SimpleNamespace(Composition=lambda x: x, __version__="fixture")
    fake.i_of_q_to_Gr = lambda q, intensity, comp, r, **kw: (np.sin(r), None, np.ones(len(q)))
    monkeypatch.setitem(sys.modules, "midas_pdf", fake)
    monkeypatch.setattr(runner, "_output", captured.update)
    pattern = tmp_path / "pattern.xy"
    np.savetxt(pattern, [[1, 2], [2, 3], [3, 4]])
    args = argparse.Namespace(pattern=str(pattern), wavelength=0.2, x_is_two_theta=False,
        composition="Ni:1", r_min=0., r_max=20., n_r=500, q_max=None, window="lorch", out="")
    runner.cmd_pdf(args)
    assert len(captured["plotly"]["data"][0]["x"]) == 500
    assert "first_peak_r_A" not in captured
    assert "strongest_peak_r_A_above_0_5" in captured


def test_pf_odf_load_does_not_claim_inversion(monkeypatch):
    captured = {}
    fake = types.SimpleNamespace(__version__="fixture", load_pf_grain=lambda *a, **kw: object())
    monkeypatch.setitem(sys.modules, "midas_pf_odf", fake)
    monkeypatch.setattr(runner, "_output", captured.update)
    runner.cmd_pf_odf(argparse.Namespace(layer_dir="/fixture", grain_id=1, n_pixels_y=4, n_pixels_z=4))
    assert captured["status"] == "warning"
    assert captured["mode"] == "input-inspection"
    assert not captured["real_data_supported"] and not captured["inversion_performed"]
