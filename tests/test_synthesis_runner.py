"""Tests for _synthesis_runner.py (in-situ synthesis XRD).

Pure pieces (control-step port, replay, JMAK, residual metrics) import only numpy/scipy
and run in APEXA's own .venv. The HDF5 round trip needs pandas+tables, which live in the
synthesis env, so it runs the real runner CLI under APEXA_SYNTH_PYTHON and is skipped
where that env is absent. Fixtures are generated here — never the collaborator's data.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import _synthesis_runner as R  # noqa: E402

SYNTH_PY = os.environ.get("APEXA_SYNTH_PYTHON",
                          str(Path.home() / ".apexa" / "envs" / "synth" / "bin" / "python"))


# ── control step: faithful port of XRD_control.control_step_box ─────────────────
def test_control_step_quiet_trajectory_strides_to_max():
    x = np.arange(0, 60, 10.0)                 # 6 scans, flat scores
    scores = np.tile([0.3, -0.1], (6, 1))
    d, *_ = R.control_step_box(x, scores, nt=6, box=5, change=2, dmin=2, dmax=20)
    assert d == 20.0                           # nothing changes → dmax


def test_control_step_is_first_grid_point_over_threshold():
    x = np.arange(0, 60, 10.0)
    scores = np.column_stack([np.linspace(0, 1, 6) ** 2, np.linspace(1, 0, 6)])
    for change in (2, 50):
        d, _, _, _, D, ds = R.control_step_box(x, scores, nt=6, box=5, change=change,
                                               dmin=2, dmax=20)
        first = ds[np.flatnonzero(D >= 0.01 * change)[0]]
        assert d == pytest.approx(max(first, 2))
    # his setting (2%) on a steep trajectory: the first nonzero grid point, 20/5 = 4
    d2, *_ = R.control_step_box(x, scores, nt=6, box=5, change=2, dmin=2, dmax=20)
    assert d2 == pytest.approx(4.0)


def test_control_step_grid_quirk_is_faithful():
    # his :202 overwrites n with len(scores): the step is quantised to dmax/(n-1)
    x = np.arange(0, 70, 10.0)
    scores = np.column_stack([np.linspace(0, 0.05, 7), np.zeros(7)])
    d, _, xs, _, _, ds = R.control_step_box(x, scores, nt=7, box=5, change=2, dmin=2, dmax=20)
    assert len(ds) == 7 and ds[1] == pytest.approx(20 / 6)
    d2, _, _, _, _, ds2 = R.control_step_box(x, scores, nt=7, box=5, change=2, dmin=2,
                                             dmax=20, grid=101)
    assert len(ds2) == 101                     # opt-in fixed grid


def test_control_step_never_below_dmin():
    x = np.arange(0, 60, 10.0)
    scores = np.column_stack([np.linspace(0, 10, 6) ** 3, np.zeros(6)])
    d, *_ = R.control_step_box(x, scores, nt=6, box=5, change=0.001, dmin=2, dmax=20)
    assert d >= 2


def test_replay_shape_and_leading_nans():
    rng = np.random.default_rng(0)
    X = rng.random((12, 50)) + np.linspace(0, 1, 12)[:, None]
    x = np.linspace(100, 320, 12)
    out = R.replay_control(X, x, box=5, change=2, dmin=2, dmax=20)
    assert len(out["steps"]) == 12
    assert out["steps"][:5] == [None] * 5
    assert all(2 <= s <= 20 for s in out["steps"][5:])
    assert out["last"]["x_next"] == pytest.approx(x[-1] + out["steps"][-1])


def test_replay_too_short():
    out = R.replay_control(np.ones((3, 10)), [1, 2, 3], box=5)
    assert out.get("too_short") is True


def test_svd_unit_scores_are_unit_norm():
    rng = np.random.default_rng(1)
    U = R.svd_unit_scores(rng.random((9, 40)), 2)
    assert U.shape == (9, 2)
    assert np.allclose(np.linalg.norm(U, axis=0), 1.0)


def test_smart_norm_divides_by_largest_magnitude():
    v = R.smart_norm(np.array([0.2, -4.0, 1.0]))
    assert v[1] == 1.0 and v[0] == pytest.approx(-0.05)


# ── JMAK kinetics ──────────────────────────────────────────────────────────────
def test_jmak_recovers_known_parameters():
    t = np.linspace(0, 5400, 30)               # 15 °C per 3 min ramp
    T_C = 465 + t / 180 * 15
    T_K = T_C + 273.15
    Tref = T_K.mean()
    lnk, Ea, n = np.log(4e-4), 60.0, 3.0
    frac = 0.17 * R.jmak_model(t, T_K, lnk, Ea, n, Tref)
    out = R.jmak_fit(t, T_C, frac, n_norm=1)
    assert out["status"] == "success" and out["rms_residual"] < 1e-5
    assert out["Ea_kJ_mol"] == pytest.approx(Ea, rel=2e-2)
    assert out["n"] == pytest.approx(n, rel=2e-2)
    fixed = R.jmak_fit(t, T_C, frac, n_fixed=3.0, n_norm=1)
    assert fixed["Ea_kJ_mol"] == pytest.approx(Ea, rel=2e-2) and fixed["n_fixed"] is True


def test_jmak_normalises_by_plateau_not_edge_outlier():
    t = np.linspace(0, 3000, 12)
    f = np.array([0.235] + [0.17] * 11)        # Sample16-like edge outlier
    out = R.jmak_fit(t, 470 + t / 60, f, n_fixed=2.0)
    assert out["normalisation"]["reference"] == pytest.approx(0.17)


def test_jmak_needs_points():
    assert R.jmak_fit([0, 1, 2], [400, 410, 420], [1, .9, .8])["status"] == "error"


def test_residual_metrics_perfect_and_bad():
    X = np.abs(np.random.default_rng(2).random((4, 30))) + 1
    assert np.allclose(R.residual_metrics(X, X)["Rp"], 0)
    assert np.allclose(R.residual_metrics(X, np.zeros_like(X))["Rp"], 1)


def test_cif_shim_resolves_case_insensitively(tmp_path):
    (tmp_path / "variants").mkdir()
    (tmp_path / "variants" / "nfpp.cif").write_text("data_x\n")
    (tmp_path / "NaFePO4.Cif").write_text("data_y\n")
    shim = tmp_path / "shim"
    shim.mkdir()
    found = R._cif_shim(tmp_path, ["NFPP", "NaFePO4"], shim)
    assert (shim / "NFPP.CIF").resolve() == (tmp_path / "variants" / "nfpp.cif").resolve()
    assert set(found) == {"NFPP", "NaFePO4"}
    with pytest.raises(FileNotFoundError):
        R._cif_shim(tmp_path, ["Missing"], shim)


# ── runner CLI round trip on a synthetic processed_*.h5 (synth env only) ───────
_MAKE_H5 = r"""
import sys, h5py, numpy as np, pandas as pd
f = sys.argv[1]
d = np.linspace(2.0, 6.0, 400)
T = np.linspace(40, 700, 12)
peaks = lambda c, w: np.exp(-0.5 * ((d - c) / w) ** 2)
with h5py.File(f, "w") as g:
    g.attrs["lambda"] = 1.54059
    g.create_dataset("d", data=d)
    g.create_dataset("t2", data=np.rad2deg(2 * np.arcsin(1.54059 / (2 * d))))
    for i, t in enumerate(T):
        a = 1 - i / 11
        y = 100 * a * peaks(3.0, .02) + 100 * (1 - a) * peaks(4.5, .02) + 2
        g.create_group(f"scan{i}").create_dataset("y", data=y)
pd.DataFrame({"t": np.arange(12) * 180.0, "TC": T}).to_hdf(f, key="table", mode="a")
"""


@pytest.mark.skipif(not Path(SYNTH_PY).exists(), reason="APEXA_SYNTH_PYTHON env not present")
def test_runner_cli_inspect_pca_recommend(tmp_path):
    h5 = tmp_path / "processed_Synthetic.h5"
    subprocess.run([SYNTH_PY, "-I", "-c", _MAKE_H5, str(h5)], check=True)

    def run(*args):
        p = subprocess.run([SYNTH_PY, "-I", str(ROOT / "_synthesis_runner.py"), *args],
                           capture_output=True, text=True, timeout=120)
        line = [l for l in p.stdout.splitlines() if l.startswith("{")][-1]
        return json.loads(line)

    ins = run("inspect", "--h5", str(h5), "--output-dir", str(tmp_path))
    assert ins["status"] == "success" and ins["n_scans"] == 12 and ins["sample"] == "Synthetic"
    assert max(ins["n_peaks"]) >= 1
    pca = run("pca", "--h5", str(h5), "--output-dir", str(tmp_path), "--n-pcs", "2")
    assert pca["status"] == "success" and len(pca["scores"]) == 2
    rec = run("recommend", "--signal", "pca", "--h5", str(h5), "--replay", "--d-min", "2")
    assert rec["status"] == "success" and rec["actuated"] is False
    assert 2 <= rec["recommended_dT_C"] <= 20
    assert len(rec["replay"]["steps"]) == 12
    bad = run("inspect", "--h5", str(tmp_path / "nope.h5"))
    assert bad["status"] == "error"
