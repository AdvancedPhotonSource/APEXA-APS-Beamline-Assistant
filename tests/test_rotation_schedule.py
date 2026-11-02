"""Offline checks for trial ordering, cohort selection and schedule receipts.

Synthetic file names stand in for acquisitions; no agent or scientific engine
is started. Main-loop tests replace run_one with an explicit stub.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import random
import sys
from collections import Counter
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark/harness_comparison"))
import rotation_benchmark as rb


@pytest.mark.parametrize("arm_count", [1, 2, 3, 6])
@pytest.mark.parametrize("scan_count,trials", [(1, 1), (2, 1), (3, 2), (7, 1), (43, 3)])
def test_schedule_pairs_every_trial_and_balances_positions(arm_count, scan_count, trials):
    arms = list(rb.ARMS)[:arm_count]
    scans = list(range(261, 261 + scan_count))
    schedule = rb.make_schedule(arms, scans, trials, seed=17)
    entries = schedule["entries"]
    expected = Counter(itertools.product(arms, scans, range(trials)))
    assert Counter((e["arm"], e["scan"], e["trial"]) for e in entries) == expected
    assert [e["schedule_index"] for e in entries] == list(range(1, len(entries) + 1))
    assert schedule["run_count"] == len(expected)
    assert schedule["block_count"] == scan_count * trials
    for block, (_, group) in enumerate(itertools.groupby(entries, key=lambda e: e["schedule_block"]), 1):
        group = list(group)
        assert {e["schedule_block"] for e in group} == {block}
        assert len({(e["scan"], e["trial"]) for e in group}) == 1
        assert {e["arm"] for e in group} == set(arms)
        assert [e["schedule_position"] for e in group] == list(range(1, arm_count + 1))
    counts = Counter((e["arm"], e["schedule_position"]) for e in entries)
    for arm in arms:
        positions = [counts[arm, position] for position in range(1, arm_count + 1)]
        assert max(positions) - min(positions) <= 1


def test_schedule_seed_is_reproducible_and_independent_of_argument_order():
    arms, scans = ["A1", "A3", "A5", "A6"], [261, 262, 263, 264]
    before = random.getstate()
    first = rb.make_schedule(arms, scans, 3, seed=42)
    assert rb.make_schedule(arms[::-1], scans[::-1], 3, seed=42) == first
    assert rb.make_schedule(arms, scans, 3, seed=43)["entries"] != first["entries"]
    assert random.getstate() == before


@pytest.mark.parametrize("arms,scans,trials,match", [
    ([], [261], 1, "arms"),
    (["A1", "A1"], [261], 1, "unique"),
    (["unknown"], [261], 1, "unknown arms"),
    (["A1"], [], 1, "scans"),
    (["A1"], [261, 261], 1, "unique"),
    (["A1"], [261], 0, "positive"),
])
def test_invalid_schedule_cannot_alias_trial_workspaces(arms, scans, trials, match):
    with pytest.raises(ValueError, match=match):
        rb.make_schedule(arms, scans, trials, seed=0)


def _unexpected(*args, **kwargs):
    pytest.fail("preview/validation must not access data, stage a trial or launch a process")


def test_plan_only_needs_no_dataset_manifest_sandbox_or_credentials(tmp_path, monkeypatch, capsys):
    output = tmp_path / "must-not-be-created"
    secret = "do-not-print-this"
    monkeypatch.setattr(sys, "argv", ["rotation_benchmark.py", "--plan-only", "--scans", "261", "262",
        "--arms", "A1", "A3", "A5", "--trials", "2", "--seed", "77", "--api-key", secret,
        "--base-url", "https://user:credential@example.invalid/v1", "--outdir", str(output),
        "--ground-truth", "/unavailable/reference.csv", "--data-root", "/unavailable/raw",
        "--knowledge-manifest", "/unavailable/manifest.json"])
    for name in ("select_exposures", "load_truth", "load_manifest", "stage", "run_one"):
        monkeypatch.setattr(rb, name, _unexpected)
    monkeypatch.setattr(rb.subprocess, "run", _unexpected)
    monkeypatch.setattr(rb, "SANDBOX", None)
    assert rb.main() == 0
    stdout = capsys.readouterr().out
    result = json.loads(stdout)
    assert result["mode"] == "plan_only" and "not checked" in result["qualification"]
    assert result["schedule"] == rb.make_schedule(["A1", "A3", "A5"], [261, 262], 2, 77)
    assert secret not in stdout and "credential" not in stdout
    assert not output.exists()


@pytest.mark.parametrize("args,match", [
    (["--plan-only"], "requires explicit --scans"),
    (["--plan-only", "--scans", "261", "261"], "scans must be unique"),
    (["--plan-only", "--scans", "261", "--arms", "A1", "A1"], "arms must be unique"),
    (["--plan-only", "--scans", "261", "--trials", "0"], "positive"),
    (["--plan-only", "--dry-run", "--scans", "261"], "not allowed"),
    (["--dry-run", "--arms", "A6"], "--ground-truth and --data-root are required"),
])
def test_invalid_cli_fails_before_data_access(args, match, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["rotation_benchmark.py", *args])
    monkeypatch.setattr(rb, "load_truth", _unexpected)
    monkeypatch.setattr(rb, "run_one", _unexpected)
    with pytest.raises(SystemExit) as exc:
        rb.main()
    assert exc.value.code == 2
    assert match in capsys.readouterr().err


def test_requested_scans_are_never_silently_skipped(tmp_path):
    (tmp_path / "sample_000261.h5").write_bytes(b"synthetic")
    with pytest.raises(ValueError, match=r"missing input=\[262\]"):
        rb.select_exposures(tmp_path, "*.h5", {261: {}, 262: {}}, [261, 262])
    with pytest.raises(ValueError, match=r"missing consistency reference=\[261\]"):
        rb.select_exposures(tmp_path, "*.h5", {}, [261])


def test_selected_scan_with_multiple_files_requires_explicit_glob(tmp_path):
    for name in ("first_000261.h5", "second_000261.h5"):
        (tmp_path / name).write_bytes(b"synthetic")
    with pytest.raises(ValueError, match="multiple input files"):
        rb.select_exposures(tmp_path, "*.h5", {261: {}}, [261])
    assert rb.select_exposures(tmp_path, "first_*.h5", {261: {}}, [261]) == {
        261: tmp_path / "first_000261.h5"}


def test_inferred_cohort_is_common_valid_files_and_references(tmp_path):
    for scan in (261, 262):
        (tmp_path / f"sample_{scan:06}.h5").write_bytes(b"synthetic")
    (tmp_path / "directory_000263.h5").mkdir()
    assert rb.select_exposures(tmp_path, "*.h5", {261: {}, 263: {}}, None) == {
        261: tmp_path / "sample_000261.h5"}
    with pytest.raises(ValueError, match="no exposures matched"):
        rb.select_exposures(tmp_path, "*.h5", {999: {}}, None)


def _main_fixture(tmp_path, monkeypatch):
    reference = tmp_path / "reference.csv"
    reference.write_text("scan,tz,calibrant\n261,0,test\n262,1,test\n263,2,test\n")
    raw = tmp_path / "raw"
    raw.mkdir()
    for scan in (261, 262, 263):
        (raw / f"sample_{scan:06}.h5").write_bytes(b"synthetic")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"schema_version": 1, "study_id": "offline-test",
        "reviewed_by": "test", "review_note": "Empty synthetic fixture; no production knowledge approved", "files": {}}))
    output = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", ["rotation_benchmark.py", "--ground-truth", str(reference),
        "--data-root", str(raw), "--arms", "A1", "A3", "A5", "--scans", "263", "261", "262",
        "--trials", "2", "--seed", "42", "--dry-run", "--knowledge-manifest", str(manifest),
        "--outdir", str(output), "--api-key", "synthetic-test-secret"])
    monkeypatch.setattr(rb.subprocess, "run", _unexpected)
    return output


def _stub_result(arm, scan, trial):
    return rb.Run(arm=arm.id, harness=arm.harness, scan=scan, trial=trial, model="stub",
                  manuals=arm.manuals, enforcement=arm.enforcement, outcome="dry-run")


def test_main_executes_frozen_schedule_and_links_every_result(tmp_path, monkeypatch):
    output = _main_fixture(tmp_path, monkeypatch)
    calls = []
    def fake_run(arm, exposure, scan, trial, *args, **kwargs):
        study = next(output.iterdir())
        schedule = json.loads((study / "schedule.json").read_text())
        config = json.loads((study / "config.json").read_text())
        digest = hashlib.sha256((study / "schedule.json").read_bytes()).hexdigest()
        assert config["schedule_sha256"] == digest
        assert config["selected_scans"] == [261, 262, 263]
        entry = schedule["entries"][len(calls)]
        assert (arm.id, scan, trial) == (entry["arm"], entry["scan"], entry["trial"])
        calls.append(entry)
        result = _stub_result(arm, scan, trial)
        if len(calls) == 1:
            result.outcome = "error"  # errors do not remove or reshuffle scheduled work
        return result
    monkeypatch.setattr(rb, "run_one", fake_run)
    assert rb.main() == 0
    study = next(output.iterdir())
    schedule = json.loads((study / "schedule.json").read_text())
    assert calls == rb.make_schedule(["A1", "A3", "A5"], [261, 262, 263], 2, 42)["entries"]
    records = [json.loads(line) for line in (study / "runs.jsonl").read_text().splitlines()]
    assert [{key: r[key] for key in calls[0]} for r in records] == schedule["entries"]
    assert records[0]["outcome"] == "error" and len(records) == 18


def test_interruption_preserves_intended_schedule_and_completed_receipts(tmp_path, monkeypatch):
    output = _main_fixture(tmp_path, monkeypatch)
    completed = []
    def interrupted_run(arm, exposure, scan, trial, *args, **kwargs):
        if len(completed) == 2:
            raise KeyboardInterrupt
        completed.append((arm.id, scan, trial))
        return _stub_result(arm, scan, trial)
    monkeypatch.setattr(rb, "run_one", interrupted_run)
    with pytest.raises(KeyboardInterrupt):
        rb.main()
    study = next(output.iterdir())
    schedule = json.loads((study / "schedule.json").read_text())
    records = [json.loads(line) for line in (study / "runs.jsonl").read_text().splitlines()]
    assert len(schedule["entries"]) == 18 and len(records) == 2
    assert [r["schedule_index"] for r in records] == [1, 2]
    assert not (study / "summary.json").exists()


def test_studies_started_in_same_second_cannot_overwrite_each_other(tmp_path, monkeypatch):
    output = _main_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(rb.time, "strftime", lambda *args: "fixed-time")
    monkeypatch.setattr(rb, "run_one", lambda arm, exposure, scan, trial, *args, **kwargs:
                        _stub_result(arm, scan, trial))
    assert rb.main() == rb.main() == 0
    studies = list(output.iterdir())
    assert len(studies) == 2
    for study in studies:
        assert len((study / "runs.jsonl").read_text().splitlines()) == 18
