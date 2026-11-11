"""A multi-input FF task must not inherit calibration scoring or archive raw data."""
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark/harness_comparison"))
import rotation_benchmark as rb


def test_execution_only_retains_outputs_and_omits_both_raw_inputs(tmp_path, monkeypatch):
    gold = tmp_path / "gold.h5"
    calibration = tmp_path / "calibration.h5"
    gold.write_bytes(b"gold fixture")
    calibration.write_bytes(b"calibration fixture")
    monkeypatch.setattr(rb, "sandboxed", lambda argv, *args, **kwargs: argv)

    def execute(argv, *, cwd, env, timeout):
        assert (cwd / gold.name).read_bytes() == gold.read_bytes()
        assert (cwd / calibration.name).read_bytes() == calibration.read_bytes()
        (cwd / gold.name).write_bytes(b"changed trial copy")
        (cwd / "Grains.csv").write_text("fixture output\n")
        return subprocess.CompletedProcess(argv, 0, "Task finished", "")

    def forbidden(*args, **kwargs):
        pytest.fail("An FF execution must not be scored as a detector calibration")

    monkeypatch.setattr(rb, "run_bounded", execute)
    monkeypatch.setattr(rb, "extract_result", forbidden)
    out = tmp_path / "out"
    result = rb.run_one(rb.ARMS["A6"], gold, 1181, 0, {}, None,
        "https://example.invalid/v1", "fixture", "fixture-model", .1, 60, out, False,
        ground_truth=tmp_path / "reference", scratch_root=tmp_path / "work",
        additional_inputs=[calibration], result_kind="execution_only", prompt_override="Synthetic FF task")
    assert result.outcome == "execution_completed"
    assert result.scientific_outcome == "task_specific_evaluation_required"
    assert gold.read_bytes() == b"gold fixture"
    kept = out / "workspaces/A6_1181_0"
    assert (kept / "Grains.csv").exists()
    assert not (kept / gold.name).exists()
    assert not (kept / calibration.name).exists()


def test_colliding_inputs_are_rejected_before_staging(tmp_path):
    with pytest.raises(ValueError, match="basenames collide"):
        rb.run_one(rb.ARMS["A6"], tmp_path / "raw.h5", 1181, 0, {}, None,
            "https://example.invalid/v1", "fixture", "fixture-model", .1, 60,
            tmp_path / "out", False, additional_inputs=[tmp_path / "other/raw.h5"])
    assert not (tmp_path / "out").exists()
