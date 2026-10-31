"""Tests for the rotation-series harness benchmark.

Each test here covers one of the defects that voided the previous comparison's 31
records. They are not incidental: a rig that grades other systems has to be graded
itself, and all four defects were silent -- the rig produced a clean-looking table
while measuring the wrong thing.
"""
from __future__ import annotations

import hashlib, json, os, sys, time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BENCH = REPO / "benchmark" / "harness_comparison"
sys.path.insert(0, str(BENCH))

rb = pytest.importorskip("rotation_benchmark")


def _knowledge_manifest(tmp_path, files=None):
    path = tmp_path / "knowledge-manifest.json"
    path.write_text(json.dumps({"schema_version": 1, "study_id": "offline-fixture", "reviewed_by": "test",
                               "review_note": "Synthetic fixture only", "files": files or {}}))
    return path


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
def test_stage_refuses_a_workspace_that_leaks_ground_truth(tmp_path, monkeypatch):
    """THE defect that voided the previous 31 records: the answer was in the room."""
    src = tmp_path / "src"; src.mkdir()
    exposure = src / "ceria_000250.h5"; exposure.write_bytes(b"\x89HDF\r\n\x1a\n")
    manuals = tmp_path / "manuals"; manuals.mkdir()
    (manuals / "README.md").write_text("how to calibrate")
    (manuals / "refined_MIDAS_params_v2.txt").write_text("tz 3.0\n")   # the leak
    monkeypatch.setattr(rb, "REPO", tmp_path)
    policy = rb.load_manifest(_knowledge_manifest(tmp_path, {p.relative_to(tmp_path).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in manuals.iterdir()}))
    with pytest.raises(SystemExit, match="leaks ground truth"):
        rb.stage(exposure, manuals, tmp_path / "ws", knowledge_policy=policy)


def test_stage_accepts_a_clean_workspace(tmp_path, monkeypatch):
    src = tmp_path / "src"; src.mkdir()
    exposure = src / "ceria_000250.h5"; exposure.write_bytes(b"\x89HDF\r\n\x1a\n")
    manuals = tmp_path / "manuals"; manuals.mkdir()
    (manuals / "phase-4-calibrate.md").write_text("the recipe")
    (manuals / "LAB_NOTEBOOK.md").write_text("unapproved answer")
    monkeypatch.setattr(rb, "REPO", tmp_path)
    policy = rb.load_manifest(_knowledge_manifest(tmp_path, {"manuals/phase-4-calibrate.md": hashlib.sha256(b"the recipe").hexdigest()}))
    staged = rb.stage(exposure, manuals, tmp_path / "ws", knowledge_policy=policy)
    assert staged == ["ceria_000250.h5", "manuals/phase-4-calibrate.md"]
    assert (tmp_path / "ws" / "manuals" / "phase-4-calibrate.md").exists()
    assert not (tmp_path / "ws" / "manuals" / "LAB_NOTEBOOK.md").exists()


def test_stage_without_manuals_copies_only_the_exposure(tmp_path):
    """Arm A6 must not receive documentation by accident -- it IS the contrast."""
    src = tmp_path / "src"; src.mkdir()
    exposure = src / "ceria_000250.h5"; exposure.write_bytes(b"x")
    manuals = tmp_path / "manuals"; manuals.mkdir()
    (manuals / "README.md").write_text("...")
    rb.stage(exposure, None, tmp_path / "ws")
    assert not (tmp_path / "ws" / "manuals").exists()


def test_manual_staging_requires_reviewed_contents(tmp_path, monkeypatch):
    monkeypatch.setattr(rb, "REPO", tmp_path)
    source = tmp_path / "input.h5"
    source.write_bytes(b"fixture")
    manuals = tmp_path / "manuals"
    manuals.mkdir()
    (manuals / "README.md").write_text("unreviewed content")
    with pytest.raises(rb.KnowledgePolicyError, match="explicit reviewed"):
        rb.stage(source, manuals, tmp_path / "unreviewed")
    with pytest.raises(rb.KnowledgePolicyError, match="approves no files"):
        rb.stage(source, manuals, tmp_path / "empty", knowledge_policy=rb.load_manifest(_knowledge_manifest(tmp_path)))


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


def test_extract_tz_does_not_grade_an_unverified_transcript(tmp_path):
    tz, src = rb.extract_tz(tmp_path, "Refined geometry: tz = -3.52 deg, ty = 0.31",
                            time.time())
    assert tz is None
    assert src == "unverified_transcript"


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
    """Hypothetical accepted rail differs from the trend; the existing real
    summary labels scans 269/276 as gate failures, not accepted-wrong cases."""
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
def test_apexa_ablation_is_explicitly_a_bundled_intervention():
    A = rb.ARMS
    assert A["A1"].harness == A["A2"].harness and A["A1"].manuals == A["A2"].manuals
    assert "bundled" in A["A2"].note
    assert rb.policy_environment(A["A1"]) == {"APEXA_STAGE_GUARDRAILS": "1", "APEXA_ASSUME_SCAN_METADATA": "0"}
    assert rb.policy_environment(A["A2"]) == {"APEXA_STAGE_GUARDRAILS": "0", "APEXA_ASSUME_SCAN_METADATA": "1"}
    assert rb.policy_environment(A["A3"]) == {}
    assert A["A3"].harness == A["A6"].harness
    assert A["A3"].manuals and not A["A6"].manuals


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
    """Synthetic stress fixture resembling the CeO2 outlier regime, with 21
    constructed points and eight outliers; not a reanalysis of the 22-row group."""
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


# ── staging raw exposures without endangering the original or the disk ───────
def test_the_staged_input_is_an_independent_file(tmp_path):
    """Writing to the workspace copy must never reach the original raw data."""
    src = tmp_path / "raw_000269.vrx.h5"; src.write_bytes(b"original")
    ws = tmp_path / "ws"
    rb.stage(src, None, ws)
    (ws / src.name).write_bytes(b"agent overwrote this")
    assert src.read_bytes() == b"original"
    assert not (ws / src.name).is_symlink()


def test_staging_refuses_rather_than_filling_the_disk(tmp_path, monkeypatch):
    import subprocess as sp, shutil as sh
    src = tmp_path / "raw.h5"; src.write_bytes(b"x" * 1000)
    def no_clone(*a, **k): raise sp.CalledProcessError(1, "cp")
    monkeypatch.setattr(sp, "run", no_clone)
    monkeypatch.setattr(sh, "disk_usage", lambda p: sh._ntuple_diskusage(10**9, 10**9 - 1500, 1500))
    with pytest.raises(SystemExit, match="REFUSING TO RUN"):
        rb.stage(src, None, tmp_path / "ws")


# ── the agents cannot read the answers off the disk ──────────────────────────
def test_external_arms_are_denied_the_answers_and_the_repo(tmp_path):
    gt = tmp_path / "analysis" / "summary.csv"; gt.parent.mkdir(); gt.write_text("x")
    deny = rb.deny_paths(rb.ARMS["A3"], gt)
    assert os.path.realpath(gt.parent) in deny
    assert os.path.realpath(rb.REPO) in deny            # no reason to read APEXA's code


def test_the_apexa_arm_keeps_its_code_but_not_the_answer_material(tmp_path):
    gt = tmp_path / "summary.csv"; gt.write_text("x")
    deny = rb.deny_paths(rb.ARMS["A1"], gt)
    assert os.path.realpath(rb.REPO) not in deny        # it has to run
    for rel in ("manuscripts", "tests", "benchmark"):
        p = os.path.realpath(rb.REPO / rel)
        if os.path.exists(p):
            assert p in deny, rel


def test_no_sandbox_means_no_run(monkeypatch):
    """Without the kernel denial the answers on this disk are readable."""
    monkeypatch.setattr(rb, "SANDBOX", None)
    with pytest.raises(SystemExit, match="sandbox-exec not available"):
        rb.sandboxed(["true"], ["/nonexistent"])


@pytest.mark.skipif(not rb.SANDBOX, reason="macOS sandbox-exec required")
def test_the_sandbox_really_blocks_a_read(tmp_path):
    secret = tmp_path / "answers" / "summary.csv"; secret.parent.mkdir(); secret.write_text("tz,-3.5178")
    import subprocess
    p = subprocess.run(rb.sandboxed(["cat", str(secret)], [os.path.realpath(secret.parent)]),
                       capture_output=True, text=True)
    assert p.returncode != 0 and "-3.5178" not in p.stdout


# ── explicit scientific decision and artifact adoption ────────────────────────
@pytest.mark.parametrize("text", [
    "The initial attempt succeeded numerically, but validation failed. Do not use this geometry.",
    "Validation failed, even though the optimizer converged successfully.",
    "Solver passed. Final decision: rejected. The numerical solver succeeded.",
    "Geometry is not acceptable despite numerical convergence.",
])
def test_explicit_rejection_overrides_earlier_solver_success(text):
    assert rb.claimed_success(text, -3.5)[0] is False


def test_explicit_final_decision_can_supersede_failed_attempt():
    text = "The first validation failed. Final decision: accepted; tz=-3.5 after correction."
    assert rb.claimed_success(text, -3.5)[0] is True


def _manifest(ws, artifact="final.txt", status="accepted", **overrides):
    import hashlib
    record = {"schema_version": 1, "scientific_status": status}
    if artifact:
        record.update(artifact_path=artifact,
                      artifact_sha256=hashlib.sha256((ws / artifact).read_bytes()).hexdigest())
    record.update(overrides)
    (ws / rb.MANIFEST_NAME).write_text(json.dumps(record))


def test_manifest_adopts_specific_result_not_newest_seed(tmp_path):
    t0 = time.time()
    final = tmp_path / "final.txt"; final.write_text("tz -3.5\n")
    seed = tmp_path / "initial_seed.txt"; seed.write_text("tz 99\n")
    os.utime(seed, (t0 + 2, t0 + 2))
    _manifest(tmp_path)
    result = rb.extract_result(tmp_path, "seed tz=99", t0)
    assert result.tz == -3.5 and result.source == "manifest:final.txt"
    assert result.scientific_status == "accepted" and result.artifact_sha256


def test_multiple_geometries_are_ambiguous_without_adoption(tmp_path):
    t0 = time.time()
    (tmp_path / "one.txt").write_text("tz -3.5\n")
    (tmp_path / "two.txt").write_text("tz 5.0\n")
    result = rb.extract_result(tmp_path, "Final tz=-3.5", t0)
    assert result.tz is None and result.source == "ambiguous_artifacts"


def test_new_seed_is_not_an_output_artifact(tmp_path):
    t0 = time.time()
    (tmp_path / "seed_params.txt").write_text("tz 99\n")
    result = rb.extract_result(tmp_path, "", t0)
    assert result.tz is None


def test_staged_file_touch_is_not_new_execution_evidence(tmp_path):
    artifact = tmp_path / "params.txt"; artifact.write_text("tz -3.5\n")
    before = rb.workspace_snapshot(tmp_path)
    t0 = time.time()
    artifact.touch()
    result = rb.extract_result(tmp_path, "", t0, before)
    assert result.tz is None


def test_adopted_file_must_match_its_hash(tmp_path):
    t0 = time.time()
    artifact = tmp_path / "final.txt"; artifact.write_text("tz -3.5\n")
    _manifest(tmp_path)
    artifact.write_text("tz 9\n")
    result = rb.extract_result(tmp_path, "", t0)
    assert result.source == "invalid_manifest" and "hash" in result.error


def test_invalid_manifest_cannot_fall_back_to_other_file(tmp_path):
    t0 = time.time()
    (tmp_path / "final.txt").write_text("tz -3.5\n")
    (tmp_path / rb.MANIFEST_NAME).write_text("not json")
    assert rb.extract_result(tmp_path, "tz=-3.5", t0).source == "invalid_manifest"


def test_manifest_cannot_read_outside_workspace(tmp_path):
    outside = tmp_path / "secret.txt"; outside.write_text("tz 9\n")
    ws = tmp_path / "workspace"; ws.mkdir()
    (ws / rb.MANIFEST_NAME).write_text(json.dumps({"schema_version": 1,
        "scientific_status": "accepted", "artifact_path": "../secret.txt", "artifact_sha256": "anything"}))
    assert "escapes" in rb.extract_result(ws, "", time.time()).error


def test_artifact_symlink_is_not_followed(tmp_path):
    (tmp_path / "one.txt").write_text("tz -3.5\n")
    (tmp_path / "link.txt").symlink_to(tmp_path / "one.txt")
    _manifest(tmp_path, "link.txt")
    result = rb.extract_result(tmp_path, "", time.time())
    assert result.source == "invalid_manifest" and "nonsymlink" in result.error


def test_rejected_manifest_is_not_an_accepted_geometry(tmp_path):
    (tmp_path / "final.txt").write_text("tz -3.5\n")
    _manifest(tmp_path, status="rejected")
    result = rb.extract_result(tmp_path, "Solver converged", time.time())
    assert rb.claimed_success("Solver converged", result.tz, result.scientific_status)[0] is False


def test_abstention_manifest_does_not_require_artifact(tmp_path):
    _manifest(tmp_path, artifact=None, status="abstained")
    result = rb.extract_result(tmp_path, "Cannot determine geometry", time.time())
    assert not result.error and result.scientific_status == "abstained" and result.tz is None


@pytest.mark.parametrize("data", ['{"tz":NaN}', '{"tz":Infinity}', 'tz 1e999\n'])
def test_nonfinite_geometry_is_not_graded(tmp_path, data):
    (tmp_path / "out.json").write_text(data)
    assert rb.extract_result(tmp_path, "", time.time()).tz is None


# ── no-model driver preflight and provider isolation ──────────────────────────
def test_driver_config_contains_only_rotation_analysis_servers(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("audit_apexa_driver", BENCH / "_apexa_driver.py")
    driver = importlib.util.module_from_spec(spec); spec.loader.exec_module(driver)
    for name in ("beamline_core_server.py", "midas_comprehensive_server.py", "motor.py", "alignment.py"):
        (tmp_path / name).write_text("# never executed\n")
    (tmp_path / "servers.config").write_text("core:beamline_core_server.py\nmidas:midas_comprehensive_server.py\nmotor:motor.py\nalignment:alignment.py\n")
    assert [config["name"] for config in driver.server_configs(tmp_path)] == ["core", "midas"]


@pytest.mark.skipif(not rb.SANDBOX, reason="macOS sandbox-exec required")
def test_staged_driver_help_and_preflight_run_while_benchmark_stays_denied(tmp_path):
    import subprocess
    reference = tmp_path / "answers" / "summary.csv"
    reference.parent.mkdir(); reference.write_text("secret")
    deny = rb.deny_paths(rb.ARMS["A1"], reference)
    driver = rb.stage_apexa_driver(tmp_path / "runtime")
    assert str((rb.REPO / "benchmark").resolve()) in deny
    env = dict(os.environ, APEXA_BENCH_REPO=str(rb.REPO))
    manifest = _knowledge_manifest(tmp_path)
    env[rb.MANIFEST_ENV] = str(manifest)
    env[rb.DIGEST_ENV] = hashlib.sha256(manifest.read_bytes()).hexdigest()
    result = subprocess.run(rb.sandboxed([sys.executable, str(driver), "--help"], deny),
                            env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    ok, detail = rb.preflight_apexa_driver(driver, env, deny)
    assert ok, detail


def test_pi_current_capability_and_isolated_profile(tmp_path, monkeypatch):
    assert RC.HARNESSES["pi"].supports_mcp
    home = tmp_path / "user"
    monkeypatch.setattr(RC.Path, "home", classmethod(lambda cls: home))
    global_config = home / ".pi" / "agent" / "models.json"
    global_config.parent.mkdir(parents=True)
    global_config.write_text('{"existing":"untouched"}')
    isolated = tmp_path / "trial" / "models.json"
    RC.ensure_pi_provider("https://example.invalid/v1", "secret-test-key", "model", config_path=isolated)
    assert global_config.read_text() == '{"existing":"untouched"}'
    assert isolated.stat().st_mode & 0o777 == 0o600
    assert json.loads(isolated.read_text())["providers"]["argo"]["apiKey"] == "secret-test-key"


def test_summary_labels_reference_as_consistency():
    result = rb.summarise([_run(outcome="correct")])["A1"]
    assert result["reference_kind"] == "apexa_derived_leave_one_out_consistency"
    assert "not independent" in result["interpretation"]


def test_runner_does_not_treat_stderr_success_as_final_acceptance(tmp_path, monkeypatch):
    import shutil, subprocess
    source = tmp_path / "raw_000250.h5"; source.write_bytes(b"stub")
    monkeypatch.setattr(rb, "_place_input", lambda src, dst: shutil.copy2(src, dst))
    monkeypatch.setattr(rb, "sandboxed", lambda argv, deny, **kwargs: argv)  # executor below is fully stubbed
    def fake_run(argv, cwd, **kwargs):
        ws = Path(cwd)
        (ws / "final.txt").write_text("tz -3.5\n")
        _manifest(ws, status="rejected")
        return subprocess.CompletedProcess(argv, 0, "Do not use this geometry.", "Solver succeeded: tz=-3.5")
    monkeypatch.setattr(rb.subprocess, "run", fake_run)
    result = rb.run_one(rb.ARMS["A3"], source, 250, 0, {250: {"predicted_tz": -3.5}},
                        None, "https://example.invalid/v1", "test-key", "m", .1, 60,
                        tmp_path / "out", False, ground_truth=tmp_path / "truth" / "summary.csv")
    assert result.claimed is False and "Solver succeeded" not in result.stdout_tail
    assert "Solver succeeded" in result.stderr_tail


def test_saved_run_configuration_redacts_api_key(tmp_path, monkeypatch):
    reference = _gt(tmp_path, [(250, 0, "CeO2"), (251, 1, "CeO2"), (252, 2, "CeO2")])
    raw = tmp_path / "raw"; raw.mkdir()
    (raw / "sample_000250.h5").write_bytes(b"fixture")
    output = tmp_path / "out"
    secret = "never-save-this-credential"
    manifest = _knowledge_manifest(tmp_path)
    monkeypatch.setattr(sys, "argv", ["rotation_benchmark.py", "--ground-truth", str(reference),
        "--data-root", str(raw), "--arms", "A3", "--dry-run", "--outdir", str(output), "--api-key", secret,
        "--knowledge-manifest", str(manifest)])
    monkeypatch.setattr(rb, "run_one", lambda *args, **kwargs: _run(arm="A3", outcome="dry-run"))
    assert rb.main() == 0
    configs = list(output.rglob("config.json"))
    assert len(configs) == 1
    body = configs[0].read_text()
    assert secret not in body and json.loads(body)["api_key"] == "[REDACTED]"


def test_nonzero_harness_exit_is_not_graded_as_scientific_success(tmp_path, monkeypatch):
    import shutil, subprocess
    source = tmp_path / "raw_000250.h5"; source.write_bytes(b"stub")
    monkeypatch.setattr(rb, "_place_input", lambda src, dst: shutil.copy2(src, dst))
    monkeypatch.setattr(rb, "sandboxed", lambda argv, deny, **kwargs: argv)
    def fake_run(argv, cwd, **kwargs):
        ws = Path(cwd)
        (ws / "final.txt").write_text("tz -3.5\n")
        _manifest(ws)
        return subprocess.CompletedProcess(argv, 2, "Successful calibration tz=-3.5", "startup failed")
    monkeypatch.setattr(rb.subprocess, "run", fake_run)
    result = rb.run_one(rb.ARMS["A3"], source, 250, 0, {250: {"predicted_tz": -3.5}},
                        None, "https://example.invalid/v1", "key", "m", .1, 60,
                        tmp_path / "out", False, ground_truth=tmp_path / "truth" / "summary.csv")
    assert result.outcome == "error" and result.return_code == 2 and result.claimed is None
