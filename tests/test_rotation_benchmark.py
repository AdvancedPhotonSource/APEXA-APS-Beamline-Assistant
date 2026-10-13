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
