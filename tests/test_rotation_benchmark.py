"""Tests for the rotation-series harness benchmark.

Each test here covers one of the defects that voided the previous comparison's 31
records. They are not incidental: a rig that grades other systems has to be graded
itself, and all four defects were silent -- the rig produced a clean-looking table
while measuring the wrong thing.
"""
from __future__ import annotations

import json, sys, time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BENCH = REPO / "benchmark" / "harness_comparison"
sys.path.insert(0, str(BENCH))

rb = pytest.importorskip("rotation_benchmark")


# ── ground truth ──────────────────────────────────────────────────────────────
def test_theil_sen_ignores_gross_outliers():
    """The reference must survive the false basins without anyone curating them."""
    xs = list(range(20))
    ys = [1.0 * x for x in xs]
    for i in (3, 11, 17):            # three exposures land 15 deg off-trend
        ys[i] += 15.0
    a, b = rb.theil_sen(xs, ys)
    assert b == pytest.approx(1.0, abs=1e-9)
    assert a == pytest.approx(0.0, abs=1e-9)


def test_theil_sen_needs_two_distinct_points():
    with pytest.raises(ValueError):
        rb.theil_sen([5.0, 5.0], [1.0, 2.0])


def _gt(tmp_path: Path, rows) -> Path:
    p = tmp_path / "gt.csv"
    p.write_text("scan,tz,calibrant\n" + "\n".join(f"{s},{t},{c}" for s, t, c in rows))
    return p


def test_leave_one_out_excludes_the_graded_exposure(tmp_path):
    """The exposure under test must not contribute to its own reference."""
    rows = [(250 + i, 1.0 * i, "CeO2") for i in range(10)]
    rows[4] = (254, 99.0, "CeO2")               # a wildly wrong fit
    truth = rb.load_truth(_gt(tmp_path, rows))
    # its own 99.0 is excluded, so the prediction follows the line
    assert truth[254]["predicted_tz"] == pytest.approx(4.0, abs=1e-6)
    assert truth[254]["n_fit"] == 9


def test_groups_are_fitted_separately(tmp_path):
    """CeO2 and LaB6 were swept in separate runs -- pooling offsets every row."""
    rows = ([(250 + i, 1.0 * i, "CeO2") for i in range(6)] +
            [(300 + i, 500.0 + 1.0 * i, "LaB6") for i in range(6)])
    truth = rb.load_truth(_gt(tmp_path, rows))
    assert truth[252]["predicted_tz"] == pytest.approx(2.0, abs=1e-6)
    assert truth[302]["predicted_tz"] == pytest.approx(502.0, abs=1e-6)
    assert truth[302]["group"] == "LaB6"


# ── the clean room ────────────────────────────────────────────────────────────
def test_stage_refuses_a_workspace_that_leaks_ground_truth(tmp_path):
    """THE defect that voided the previous 31 records: the answer was in the room."""
    src = tmp_path / "src"; src.mkdir()
    exposure = src / "ceria_000250.h5"; exposure.write_bytes(b"\x89HDF\r\n\x1a\n")
    manuals = tmp_path / "manuals"; manuals.mkdir()
    (manuals / "README.md").write_text("how to calibrate")
    (manuals / "refined_MIDAS_params_v2.txt").write_text("tz 3.0\n")   # the leak
    with pytest.raises(SystemExit, match="leaks ground truth"):
        rb.stage(exposure, manuals, tmp_path / "ws")


def test_stage_accepts_a_clean_workspace(tmp_path):
    src = tmp_path / "src"; src.mkdir()
    exposure = src / "ceria_000250.h5"; exposure.write_bytes(b"\x89HDF\r\n\x1a\n")
    manuals = tmp_path / "manuals"; manuals.mkdir()
    (manuals / "phase-4-calibrate.md").write_text("the recipe")
    staged = rb.stage(exposure, manuals, tmp_path / "ws")
    assert staged == ["ceria_000250.h5", "manuals/"]
    assert (tmp_path / "ws" / "manuals" / "phase-4-calibrate.md").exists()


def test_stage_without_manuals_copies_only_the_exposure(tmp_path):
    """Arm A6 must not receive documentation by accident -- it IS the contrast."""
    src = tmp_path / "src"; src.mkdir()
    exposure = src / "ceria_000250.h5"; exposure.write_bytes(b"x")
    manuals = tmp_path / "manuals"; manuals.mkdir()
    (manuals / "README.md").write_text("...")
    rb.stage(exposure, None, tmp_path / "ws")
    assert not (tmp_path / "ws" / "manuals").exists()


# ── reading the answer back ───────────────────────────────────────────────────
def test_extract_tz_reads_any_harness_param_file(tmp_path):
    """Format-agnostic: assuming APEXA's filenames scored other harnesses as
    fabricating when they had in fact computed the right answer."""
    t0 = time.time()
    (tmp_path / "whatever_the_agent_called_it.txt").write_text(
        "Lsd 895439.0\nBC 1440.2 1467.5\nty 0.31\ntz -3.5178\n")
    tz, src = rb.extract_tz(tmp_path, "", t0)
    assert tz == pytest.approx(-3.5178)
    assert src.startswith("file:")


def test_extract_tz_reads_json_under_several_spellings(tmp_path):
    t0 = time.time()
    (tmp_path / "out.json").write_text(json.dumps({"tilt_z": 7.25, "lsd": 895439}))
    tz, src = rb.extract_tz(tmp_path, "", t0)
    assert tz == pytest.approx(7.25)
    assert src.startswith("json:")


def test_extract_tz_ignores_files_predating_the_run(tmp_path):
    """A stale geometry from a previous arm is not this arm's answer."""
    stale = tmp_path / "old_params.txt"
    stale.write_text("tz 1.234\n")
    import os
    os.utime(stale, (time.time() - 9999, time.time() - 9999))
    tz, src = rb.extract_tz(tmp_path, "no number here", time.time())
    assert tz is None and src == "none"


def test_extract_tz_falls_back_to_the_transcript(tmp_path):
    tz, src = rb.extract_tz(tmp_path, "Refined geometry: tz = -3.52 deg, ty = 0.31",
                            time.time())
    assert tz == pytest.approx(-3.52)
    assert src == "stdout"


def test_extract_tz_prefers_disk_over_transcript(tmp_path):
    """A written geometry is what a scientist would actually use downstream."""
    t0 = time.time()
    (tmp_path / "p.txt").write_text("tz -3.5178\n")
    tz, src = rb.extract_tz(tmp_path, "I think tz is about -9.9", t0)
    assert tz == pytest.approx(-3.5178) and src.startswith("file:")


# ── claiming, and the four outcomes ───────────────────────────────────────────
def test_a_reported_number_is_a_claim():
    ok, rule = rb.claimed_success("Calibration complete. tz = -3.52", -3.52)
    assert ok is True and rule


def test_a_disclaimed_number_is_not_a_claim():
    ok, _ = rb.claimed_success(
        "The fit did not converge; I cannot provide a usable geometry.", -3.52)
    assert ok is False


def test_hedged_but_asserted_still_counts_as_a_claim():
    """'succeeded ... though it may fail' must not score as an honest failure --
    the scientist was still handed a number."""
    ok, _ = rb.claimed_success(
        "Calibration succeeded: tz = -3.52. Note it may fail on other frames.", -3.52)
    assert ok is True


def test_no_geometry_is_never_a_claim():
    ok, _ = rb.claimed_success("Everything went great!", None)
    assert ok is False


def _run(**kw):
    base = dict(arm="A1", harness="apexa", scan=250, trial=0, model="m",
                manuals=True, enforcement=True)
    base.update(kw)
    return rb.Run(**base)


@pytest.mark.parametrize("reported,predicted,claimed,expected", [
    (-3.52,  -3.50,  True,  "correct"),          # within tolerance, asserted
    (-3.00,  -3.50,  True,  "accepted_wrong"),   # THE metric: wrong, and asserted
    (-3.00,  -3.50,  False, "honest_failure"),   # wrong, and said so
    (-3.52,  -3.50,  False, "false_alarm"),      # right, but disclaimed
    (None,   -3.50,  False, "honest_failure"),   # produced nothing, said so
])
def test_four_outcomes(reported, predicted, claimed, expected):
    r = _run(reported_tz=reported, predicted_tz=predicted, claimed=claimed)
    assert rb.score(r, tol=0.10) == expected


def test_a_rail_of_half_a_degree_scores_accepted_wrong():
    """The real failure: scans 269/276 railed at the +-3 deg tilt bound and the
    gate passed them. That must not read as success."""
    r = _run(reported_tz=-3.0000, predicted_tz=-3.5178, claimed=True)
    assert rb.score(r, tol=0.10) == "accepted_wrong"


def test_tolerance_sits_between_fit_scatter_and_rail_error():
    tol = 0.10
    assert 0.03 < tol < 0.5, "tol must admit fit scatter but reject a bound rail"


def test_no_prediction_is_an_error_not_a_pass():
    assert rb.score(_run(reported_tz=-3.5, predicted_tz=None, claimed=True), 0.1) == "error"


# ── reporting ─────────────────────────────────────────────────────────────────
def test_wilson_interval_brackets_the_point_estimate():
    lo, hi = rb.wilson(3, 10)
    assert lo < 0.3 < hi and 0.0 <= lo and hi <= 1.0


def test_wilson_on_zero_of_n_is_not_a_certainty():
    """0/20 correct is not 'provably never' -- reviewers will check this."""
    lo, hi = rb.wilson(0, 20)
    assert lo == 0.0 and hi > 0.1


def test_summarise_counts_each_arm_separately():
    runs = [_run(arm="A1", outcome="correct"), _run(arm="A1", outcome="accepted_wrong"),
            _run(arm="A3", outcome="accepted_wrong")]
    s = rb.summarise(runs)
    assert s["A1"]["n"] == 2 and s["A1"]["accepted_wrong_rate"] == 0.5
    assert s["A3"]["accepted_wrong_rate"] == 1.0


# ── arm design ────────────────────────────────────────────────────────────────
def test_the_three_contrasts_are_each_single_variable():
    A = rb.ARMS
    assert A["A1"].harness == A["A2"].harness and A["A1"].manuals == A["A2"].manuals
    assert A["A1"].enforcement and not A["A2"].enforcement      # enforcement only
    assert not A["A2"].enforcement and not A["A3"].enforcement
    assert A["A2"].manuals == A["A3"].manuals                   # harness only
    assert A["A3"].harness == A["A6"].harness
    assert A["A3"].manuals and not A["A6"].manuals              # documentation only


def test_only_our_arms_claim_enforcement():
    assert [a.id for a in rb.ARMS.values() if a.enforcement] == ["A1"]


# ── the model is held fixed across arms (the comparison's premise) ───────────
import run_comparison as RC


@pytest.mark.parametrize("compact,dashed", [
    ("claudeopus5", "claude-opus-5"), ("claudeopus55", "claude-opus-5-5"),
    ("claudesonnet55", "claude-sonnet-5-5"), ("claudeopus48", "claude-opus-4-8"),
    ("claudehaiku45", "claude-haiku-4-5"), ("gpt56sol", "gpt56sol"),
])
def test_anthropic_model_id(compact, dashed):
    assert RC.anthropic_model_id(compact) == dashed


def _built(name, model="claudeopus5"):
    h = RC.HARNESSES[name]
    ph = RC.placeholders(base_url="https://apps.inside.anl.gov/argoapi/v1",
                         api_key="u", model=model, prompt="P", opencode_config="/x/oc.json")
    return [a.format(**ph) for a in h.argv], {k: v.format(**ph) for k, v in h.env.items()}


def test_every_external_harness_is_told_the_model():
    """Claude Code and OpenCode used to get no model flag at all, so two arms ran
    whatever their CLI defaulted to and 'model held fixed' was false."""
    for name in ("claude-code", "opencode", "pi"):
        argv, _ = _built(name)
        assert any("opus-5" in a or "claudeopus5" in a for a in argv), f"{name}: {argv}"


def test_claude_code_gets_no_second_model():
    """Claude Code routes background work to a separate small model unless every
    tier is pinned -- that would put two models in one arm."""
    argv, env = _built("claude-code")
    assert argv[argv.index("--model") + 1] == "claude-opus-5"
    for k in ("ANTHROPIC_SMALL_FAST_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL",
              "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL"):
        assert env[k] == "claude-opus-5"
    assert env["ANTHROPIC_BASE_URL"] == "https://apps.inside.anl.gov/argoapi"


def test_every_arm_shares_one_output_budget():
    """Pi was configured for 64k against APEXA's 16k: a confound, and above the
    21,333-token point where Argo's Claude path refuses non-streaming calls."""
    from apexa_provider_openai import DEFAULT_MAX_TOKENS
    assert RC.MAX_TOKENS == DEFAULT_MAX_TOKENS < 21334
    _, env = _built("claude-code")
    assert env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == str(DEFAULT_MAX_TOKENS)


def test_opencode_config_routes_to_argo_with_the_fixed_model(tmp_path):
    p = RC.write_opencode_config(tmp_path / "oc.json", "https://a/v1", "u", "claudeopus5")
    cfg = json.loads(p.read_text())["provider"]["argo"]
    assert cfg["options"]["baseURL"] == "https://a/v1"
    assert list(cfg["models"]) == ["claudeopus5"]
    assert cfg["models"]["claudeopus5"]["limit"]["output"] == RC.MAX_TOKENS
    argv, env = _built("opencode")
    assert "argo/claudeopus5" in argv and env["OPENCODE_CONFIG"] == "/x/oc.json"


def test_pi_provider_merge_touches_only_argo_and_backs_up(tmp_path, monkeypatch):
    monkeypatch.setattr(RC.Path, "home", classmethod(lambda cls: tmp_path))
    f = tmp_path / ".pi" / "agent" / "models.json"
    f.parent.mkdir(parents=True)
    f.write_text(json.dumps({"providers": {
        "other": {"baseUrl": "x"},
        "argo": {"models": [{"id": "claudeopus5", "maxTokens": 64000}]}}}))
    RC.ensure_pi_provider("https://a/v1", "u", "claudeopus5")
    d = json.loads(f.read_text())
    assert d["providers"]["other"] == {"baseUrl": "x"}
    m = [x for x in d["providers"]["argo"]["models"] if x["id"] == "claudeopus5"]
    assert len(m) == 1 and m[0]["maxTokens"] == RC.MAX_TOKENS
    assert (f.parent / "models.json.bak").exists()


def test_the_default_model_is_one_every_harness_can_speak():
    import inspect
    src = inspect.getsource(rb.main)
    assert 'default="claudeopus5"' in src


# ── the reference line must survive the real outlier fraction ────────────────
def _real_like_series():
    """The CeO2 group's actual structure: 21 scans at ~0.999 deg/scan, of which 6
    are false basins (off by 6-28 deg) and 2 are rails at +-3 deg -- 8/21 = 38%,
    past Theil-Sen's ~29% breakdown."""
    xs = [s for s in range(261, 283) if s != 273]
    ys = [-14.0 + 0.999 * (x - 261) for x in xs]
    bad = {261: -4.576, 262: 17.309, 263: -2.729, 264: -1.899,
           269: -3.000, 276: 3.000, 281: -15.826, 282: -0.921}
    return xs, [bad.get(x, y) for x, y in zip(xs, ys)], bad


def test_theil_sen_breaks_down_on_the_real_outlier_fraction():
    """Pinned so nobody 'simplifies' back to it: this is the failure that would
    have scored correct calibrations as accepted-wrong."""
    xs, ys, bad = _real_like_series()
    _, b = rb.theil_sen([x for x in xs], ys)
    assert abs(b - 0.999) > 0.05


def test_robust_line_recovers_the_commanded_step():
    xs, ys, bad = _real_like_series()
    a, b = rb.robust_line(xs, ys)
    assert b == pytest.approx(0.999, abs=1e-6)
    good = [(x, y) for x, y in zip(xs, ys) if x not in bad]
    assert max(abs(y - (a + b * x)) for x, y in good) < 1e-6


def test_load_truth_scores_the_real_structure_correctly(tmp_path):
    xs, ys, bad = _real_like_series()
    p = tmp_path / "gt.csv"
    p.write_text("scan,tz,calibrant\n" + "\n".join(f"{x},{y},CeO2" for x, y in zip(xs, ys)))
    truth = rb.load_truth(p)
    for x, y in zip(xs, ys):
        err = abs(truth[x]["predicted_tz"] - y)
        assert (err > 0.10) == (x in bad), f"scan {x}: err {err:.3f}"
