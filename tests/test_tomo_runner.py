"""Unit tests for _tomo_runner.py pure-numpy hard-rule helpers.

These exercise the tomography capsule's silent-failure guards WITHOUT tomopy — the
helpers import only numpy, so they run in APEXA's own .venv. The tomopy reconstruction
path is tested end-to-end on a host that has the tomo env (see docs/TOMO_SETUP.md);
here we lock in the checks that decide whether a reconstruction should even be attempted.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _tomo_runner as T  # noqa: E402


def test_flat_dark_layout_detects_three_classes():
    # 2 darks, 3 flats, 20 projections (sample means between dark and flat)
    means = np.array([5, 5] + [1000, 1000, 1000] + list(np.linspace(600, 650, 20)))
    lay = T.detect_flat_dark_layout(means)
    assert lay["dark"] == [0, 1]
    assert lay["flat"] == [2, 3, 4]
    assert len(lay["proj"]) == 20
    assert lay["confident"] is True


def test_layout_not_confident_without_flats():
    # No bright frames -> cannot normalize -> not confident (a halt upstream)
    means = np.linspace(600, 650, 20)
    lay = T.detect_flat_dark_layout(means)
    assert lay["confident"] is False
    assert "flat" in lay["reason"].lower() or "bright" in lay["reason"].lower()


def test_illuminated_mask_excludes_dark_border():
    flat = np.ones((16, 16)) * 1000.0
    flat[:, :2] = 1.0           # left two columns unilluminated
    illum = T.illuminated_mask(flat)
    assert not illum[:, :2].any()
    assert illum[:, 2:].all()


def test_transmission_flags_negative_as_bad_layout():
    # A negative transmission in the beam means flat/dark assignment is wrong (hard rule 1)
    illum = np.ones((16, 16), dtype=bool)
    pn = np.ones((16, 16)) * 0.8
    pn[8, 8] = -0.2
    ts = T.transmission_stats(pn, illum)
    assert ts["ok"] is False
    assert ts["negative_fraction"] > 0
    # A clean normalized projection passes
    assert T.transmission_stats(np.ones((16, 16)) * 0.7, illum)["ok"] is True


def test_mu_d_below_floor_not_correctable():
    illum = np.ones((16, 16), dtype=bool)
    # transmission ~0.98 everywhere -> mu*D ~0.02 < 0.1 -> not correctable
    low = T.mu_d_estimate(np.ones((16, 16)) * 0.98, illum)
    assert low["correctable"] is False
    # a strongly attenuating path -> correctable
    strong = np.ones((16, 16)) * 0.9
    strong[8, 8] = 0.3          # deep path, T=0.3 -> mu*D ~1.2
    assert T.mu_d_estimate(strong, illum)["correctable"] is True


def test_truncation_flags_edges_not_returning_to_one():
    illum_cols = np.ones(64, dtype=bool)
    # edges well below 1.0 -> truncated
    row = np.full((1, 64), 0.5)
    assert T.truncation_check(row, illum_cols)["truncated"] is True
    # edges ~1.0, sample dip in the middle -> not truncated
    row2 = np.ones((1, 64))
    row2[0, 24:40] = 0.4
    assert T.truncation_check(row2, illum_cols)["truncated"] is False
