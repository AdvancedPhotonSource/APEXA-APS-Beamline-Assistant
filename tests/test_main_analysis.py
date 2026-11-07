"""Scientific reporting: retained denominator, independent units and adoption."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("analyze_main", Path(__file__).resolve().parents[1] /
                                             "benchmark/harness_comparison/analyze_main.py")
analysis = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analysis)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def fixture(tmp_path):
    root, baseline = tmp_path/"current", tmp_path/"prior"
    inputs = [dict(path="/raw/ceria_000261.h5", sha256="a"),
              dict(path="/raw/lab6_000283.h5", sha256="b")]
    spec = dict(arms=["A1", "A3"], scans=[261, 283], repeats=3,
                wall_seconds=1800, cpu_threads=4, tilt_tolerance_deg=.1, study_id="test")
    frozen = dict(inputs=inputs, spec=spec, solver={"version": "same"})
    for p in (root, baseline):
        write(p/"setup/frozen.json", frozen)
        worker = p/"source/benchmark/harness_comparison/baseline_worker.py"
        worker.parent.mkdir(parents=True)
        worker.write_text("same worker")
    write(baseline/"setup/baseline/plan.json", {"cases": [
        dict(inputs[0], calibrant="CeO2"), dict(inputs[1], calibrant="LaB6")]})
    write(baseline/"results/baseline-main/schedule.json", {"scans": spec["scans"]})
    entries = [dict(arm=a, scan=s, trial=t) for s in spec["scans"] for t in range(3) for a in spec["arms"]]
    for i, e in enumerate(entries, 1):
        e["schedule_index"] = i
    write(root/"setup/main_schedule.json", {"entries": entries})
    return root, baseline, entries


def terminal(saved, entry, outcome="correct", *, baseline=False):
    tag = f"{entry['arm']}_{entry['scan']}_{entry['trial']}"
    workspace = saved/tag/"workspace" if baseline else saved/"workspaces"/tag
    workspace.mkdir(parents=True, exist_ok=True)
    raw = b"tz 1.0\n"
    digest = hashlib.sha256(raw).hexdigest()
    (workspace/"geometry.txt").write_bytes(raw)
    write(workspace/"result_manifest.json", dict(schema_version=1, scientific_status="accepted",
          artifact_path="geometry.txt", artifact_sha256=digest))
    row = dict(entry, outcome=outcome, elapsed_s=100, return_code=0 if outcome == "correct" else 124,
               predicted_tz=1., scientific_status="accepted", artifact_sha256=digest,
               model_audit=dict(scientific_evaluable=True, requests=3))
    with (saved/"runs.jsonl").open("a") as stream:
        stream.write(json.dumps(row)+"\n")
    return workspace


def test_unrun_trials_are_not_failures_or_interim_rates(tmp_path):
    root, baseline, entries = fixture(tmp_path)
    terminal(root/"results/main", entries[0])
    write(root/"results/main/started/0002.json", entries[1])
    report = analysis.analyze(root, baseline)
    assert len(report["trials"]) == 14
    assert report["arms"]["A1"]["scheduled"] == 6
    assert report["arms"]["A1"]["completed_endpoint"] == 1
    assert report["arms"]["A1"]["rate_all_scheduled"] is None
    assert report["arms"]["A3"]["started_without_terminal"] == 1
    assert report["paired_comparisons"] is None and report["illustrative_pairs"] is None


def test_failed_process_with_consistent_artifact_cannot_be_success(tmp_path):
    root, baseline, entries = fixture(tmp_path)
    terminal(root/"results/main", entries[0], "timeout")
    report = analysis.analyze(root, baseline)
    row = report["trials"][0]
    assert row["accepted_relative_consistency"] is True
    assert row["endpoint"] is False
    assert report["arms"]["A1"]["timeouts"] == 1


def test_modified_adoption_is_detected_even_if_new_manifest_hash_matches(tmp_path):
    root, baseline, entries = fixture(tmp_path)
    workspace = terminal(root/"results/main", entries[0])
    raw = b"tz 1.001\n"
    (workspace/"geometry.txt").write_bytes(raw)
    manifest = json.loads((workspace/"result_manifest.json").read_text())
    manifest["artifact_sha256"] = hashlib.sha256(raw).hexdigest()
    write(workspace/"result_manifest.json", manifest)
    row = analysis.analyze(root, baseline)["trials"][0]
    assert row["accepted_relative_consistency"] is True
    assert not row["artifact_matches_terminal_record"] and not row["endpoint"]


def test_completed_analysis_averages_repeats_and_keeps_one_baseline(tmp_path):
    root, baseline, entries = fixture(tmp_path)
    for e in entries:
        terminal(root/"results/main", e, "timeout" if e["arm"] == "A3" and e["trial"] == 0 else "correct")
    for i, scan in enumerate((261, 283), 1):
        terminal(baseline/"results/baseline-main", dict(arm="B0", scan=scan, trial=0, schedule_index=i), baseline=True)
    report = analysis.analyze(root, baseline)
    assert report["complete"]
    assert report["arms"]["B0"]["scheduled"] == 2
    assert len(report["acquisitions"]) == 2
    assert report["acquisitions"][0]["A3"] == pytest.approx(2/3)
    assert report["paired_comparisons"]["A3"]["mean_difference_A1_minus_arm"] == pytest.approx(1/3)
    assert report["paired_comparisons"]["B0"]["percentile_95_interval"] == [0, 0]
    assert report["illustrative_pairs"]["A3"]["A1_only"]["trial"] == 0
    assert report["illustrative_pairs"]["A3"]["both_complete"]["trial"] == 1
    assert report["illustrative_pairs"]["A3"]["external_only"] is None


def test_bootstrap_preserves_pairing_and_calibrant_strata():
    rows = [dict(calibrant=c, A1=float(v), A3=float(v)) for c in ("CeO2", "LaB6") for v in (0, 1)]
    estimates = analysis.paired_estimates(rows, ["A1", "A3"])
    assert estimates["A3"]["percentile_95_interval"] == [0, 0]
    # Constant within each stratum: stratified resampling has no variability,
    # even though the two calibrants have opposite differences.
    rows = [dict(calibrant=c, A1=float(c == "CeO2"), A3=float(c == "LaB6"))
            for c in ("CeO2", "LaB6") for _ in range(6)]
    assert analysis.paired_estimates(rows, ["A1", "A3"])["A3"]["percentile_95_interval"] == [0, 0]


def test_duplicate_and_unscheduled_records_are_rejected(tmp_path):
    root, baseline, entries = fixture(tmp_path)
    terminal(root/"results/main", entries[0])
    terminal(root/"results/main", entries[0])
    with pytest.raises(ValueError, match="Duplicate"):
        analysis.analyze(root, baseline)


def test_baseline_with_changed_solver_is_refused(tmp_path):
    root, baseline, _ = fixture(tmp_path)
    frozen = json.loads((baseline/"setup/frozen.json").read_text())
    frozen["solver"] = {"version": "different"}
    write(baseline/"setup/frozen.json", frozen)
    with pytest.raises(ValueError, match="inputs/solver differ"):
        analysis.analyze(root, baseline)
