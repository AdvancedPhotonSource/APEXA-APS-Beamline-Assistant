"""Tests for synthesis_server.py — argument plumbing, env gating, error honesty."""
import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import synthesis_server as S  # noqa: E402


@pytest.fixture
def captured(monkeypatch):
    calls = []

    def fake(subcmd, args, timeout=600, need_engine=False):
        calls.append((subcmd, list(args), timeout, need_engine))
        return {"status": "success"}

    monkeypatch.setattr(S, "_run_runner", fake)
    return calls


def _run(coro):
    return json.loads(asyncio.run(coro))


def test_fit_series_builds_his_recipe(captured):
    out = _run(S.synth_fit_series("/d/processed_S.h5", ["NFPP", "Na2FeP2O7", "NaFePO4"],
                                  abc_scale_permil=[5, 10, -10], begin=28, d_min=3.6, d_max=6,
                                  constraints="cooling", d_dependent_width=True))
    assert out["tool"] == "synth_fit_series"
    sub, args, timeout, engine = captured[0]
    assert sub == "fit" and engine is True and timeout >= 1800
    assert args[args.index("--phases") + 1] == "NFPP,Na2FeP2O7,NaFePO4"
    assert args[args.index("--abc-scale") + 1] == "5,10,-10"
    assert "--sd" in args and args[args.index("--begin") + 1] == "28"
    assert args[args.index("--constraints") + 1] == "cooling"


def test_fit_series_rejects_mismatched_abc(captured):
    out = _run(S.synth_fit_series("/x.h5", ["A", "B"], abc_scale_permil=[1]))
    assert out["status"] == "error" and not captured


def test_fit_series_requires_phases(captured):
    assert _run(S.synth_fit_series("/x.h5", []))["status"] == "error"


def test_recommend_defaults_match_his_runs(captured):
    _run(S.synth_recommend_step(h5_path="/x.h5", replay=True))
    sub, args, _, engine = captured[0]
    assert sub == "recommend" and engine is False
    val = lambda k: args[args.index(k) + 1]
    assert (val("--box"), val("--change"), val("--dmin"), val("--dmax"), val("--d-min")) == \
        ("5", "2.0", "2.0", "20.0", "2.3")
    assert "--replay" in args


def test_recommend_rejects_unknown_signal(captured):
    assert _run(S.synth_recommend_step(signal="vibes"))["status"] == "error"


def test_error_payload_tells_model_nothing_was_computed(monkeypatch):
    monkeypatch.setattr(S, "_run_runner", lambda *a, **k: {"status": "error", "error": "boom"})
    out = _run(S.synth_inspect_series("/x.h5"))
    assert out["status"] == "error" and "Nothing was computed" in out["note"]


def test_env_missing_is_structured(monkeypatch, tmp_path):
    monkeypatch.setattr(S, "DEFAULT_SYNTH_PYTHON", str(tmp_path / "no-python"))
    monkeypatch.setattr(S, "_ENV_PROBE_CACHE", None)
    out = S._run_runner("inspect", ["--h5", "x"])
    assert out["status"] == "error" and "APEXA_SYNTH_PYTHON" in out["error"]


def test_engine_required_only_for_fit(monkeypatch):
    monkeypatch.setattr(S, "_ENV_PROBE_CACHE", "")          # stack import ok
    monkeypatch.setattr(S, "DEFAULT_SYNTH_PYTHON", sys.executable)
    monkeypatch.setattr(S, "SYNTH_CODE", "")
    assert S._env_ok(need_engine=False) is None
    assert "APEXA_SYNTH_CODE" in S._env_ok(need_engine=True)


def test_tool_names_do_not_collide_with_xrd_ct_capsule():
    import capsule_registry as cr
    for name in ("synth_inspect_series", "synth_pca_map", "synth_fit_series",
                 "synth_recommend_step", "synth_fit_kinetics", "synth_report"):
        assert cr.technique_for_tool(name) != "xrd-ct"


def test_report_findings_flag_uniformly_poor_fit():
    import apexa_synthesis_report as SR
    man = {"scans": [28, 29, 30], "TC": [465, 480, 495], "reduced_chi2": 2205.0,
           "recipe": {"phases": ["A", "B"]},
           "transparency": {"per_scan_Rp": [0.82, 0.83, 0.84], "params_at_bounds": [],
                            "phase_zero_scale_scans": {}}}
    verdict, note, F, A = SR.findings(man)
    assert verdict == "look" and "poor fit" in note


def test_lattice_expansion_flags_non_thermal_motion(tmp_path):
    import apexa_synthesis_report as SR
    csv = tmp_path / "t.csv"
    rows = ["TC,weight1,a1,b1,c1,weight2,a2,b2,c2"]
    for i, T in enumerate(range(466, 702, 15)):
        f = i / 15
        # phase 1: a grows 3% (non-thermal); phase 2: 0.3% (thermal), frozen
        rows.append(f"{T},0.2,{18.0*(1+0.03*f)},6.56,{10.7*(1+0.002*f)},0.8,6.5,{9.5*(1+0.003*f)},11.0")
    csv.write_text("\n".join(rows))
    exp = SR.lattice_expansion(str(csv), ["NFPP", "Na2FeP2O7"], ["Na2FeP2O7"], [])
    flagged = {e["param"] for e in exp if e["exceeds"]}
    assert flagged == {"a1"}                   # c1 at 0.2% and frozen phase 2 not flagged
    man = {"scans": list(range(16)), "TC": list(range(466, 702, 15)), "reduced_chi2": 1.0,
           "recipe": {"phases": ["NFPP", "Na2FeP2O7"]}, "outputs": {"table_csv": str(csv)},
           "transparency": {"per_scan_Rp": [0.4] * 16, "params_at_bounds": [],
                            "phase_zero_scale_scans": {},
                            "constraint_frozen_phases": ["Na2FeP2O7"]}}
    verdict, note, *_ = SR.findings(man)
    assert verdict == "look" and "thermal expansion" in note


def test_report_findings_pass_and_interior_dropout():
    import apexa_synthesis_report as SR
    base = {"scans": [28, 29, 30, 31], "TC": [465, 480, 495, 510], "reduced_chi2": 10.0,
            "recipe": {"phases": ["A", "B"]}}
    # his own reference fits sit at median Rp ~0.42-0.47 — that must not read as poor
    ok = dict(base, transparency={"per_scan_Rp": [0.42, 0.44, 0.43, 0.47],
                                  "params_at_bounds": [], "phase_zero_scale_scans": {"A": [31]}})
    assert SR.findings(ok)[0] == "pass"        # absent at the window edge = physics
    mid = dict(base, transparency={"per_scan_Rp": [0.1, 0.11, 0.1, 0.12],
                                   "params_at_bounds": [], "phase_zero_scale_scans": {"A": [29]}})
    assert SR.findings(mid)[0] == "look"
