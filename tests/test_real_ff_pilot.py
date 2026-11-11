"""Real-data artifact checks must not turn structure or grain count into truth."""
import hashlib
import json
import os
from pathlib import Path
import sys
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark"))
from ff_harness_pilot.evaluate import artifact, evaluate, grain_structure, parameter_facts
from ff_harness_pilot.run import wall_budget


def fixture(workspace, count=3):
    texts = {
        "parameters": "Wavelength 0.19582430178665108\npx 150\nImTransOpt 2\nSpaceGroup 225\n"
            "LatticeConstant 4.0782 4.0782 4.0782 90 90 90\nOmegaStart 180\nOmegaStep -.25\n"
            "SkipFrame 1\nNrFilesPerSweep 1\nStartFileNrFirstLayer 1181\n"
            "Lsd 900000\nBC 1440 1440\ntx 0\nty 0\ntz 0\n",
        "grains": "% synthetic fixture\n" + "".join(
            f"{i} 1 0 0 0 1 0 0 0 1 0 0 0\n" for i in range(count)),
        "calibration": '{"synthetic_test": true}\n',
    }
    manifest = {"schema_version": 1, "task": "real_gold_ff", "scientific_status": "accepted",
                "reason": "Synthetic test only", "artifacts": {}}
    for name, data in texts.items():
        path = workspace / (name + ".txt")
        path.write_text(data)
        manifest["artifacts"][name] = {"path": path.name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    (workspace / "result_manifest.json").write_text(json.dumps(manifest))
    return manifest


def test_three_structural_grains_are_not_physical_certification(tmp_path):
    started = time.time()
    fixture(tmp_path, count=3)
    result = evaluate(tmp_path, started)
    assert result["artifacts_valid"]
    assert result["grains"]["candidate_count"] == 3
    assert result["grains"]["rotations_valid"]
    assert result["physical_validity"] == "unestablished"
    assert result["trace_audit"] == "required"


def test_physical_scale_error_survives_structure_but_fails_parameter_facts(tmp_path):
    fixture(tmp_path)
    path = tmp_path / "parameters.txt"
    path.write_text(path.read_text().replace("px 150", "px 100"))
    assert not parameter_facts(path)["operator_facts_match"]
    assert grain_structure(tmp_path / "grains.txt")["rotations_valid"]


def test_hash_valid_nonrotation_is_not_a_valid_grain_artifact(tmp_path):
    manifest = fixture(tmp_path)
    path = tmp_path / "grains.txt"
    path.write_text("1 0 0 0 0 0 0 0 0 0 0 0 0\n")
    manifest["artifacts"]["grains"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    (tmp_path / "result_manifest.json").write_text(json.dumps(manifest))
    result = evaluate(tmp_path, time.time())
    assert result["artifact_hashes_valid"]
    assert not result["artifacts_valid"]


@pytest.mark.parametrize("change", ["hash", "old", "symlink"])
def test_untrusted_artifact_cannot_be_adopted(tmp_path, change):
    start = time.time()
    manifest = fixture(tmp_path)
    entry = manifest["artifacts"]["grains"]
    path = tmp_path / entry["path"]
    if change == "hash":
        path.write_text(path.read_text() + "altered\n")
    elif change == "old":
        os.utime(path, (start - 100, start - 100))
    else:
        path.rename(tmp_path / "real.txt")
        path.symlink_to(tmp_path / "real.txt")
    with pytest.raises(ValueError):
        artifact(tmp_path, entry, start, 100000)


def test_honest_abstention_is_a_decision_without_reconstruction(tmp_path):
    (tmp_path / "result_manifest.json").write_text(json.dumps({
        "schema_version": 1, "task": "real_gold_ff", "scientific_status": "abstained",
        "reason": "No adopted reconstruction", "artifacts": {}}))
    result = evaluate(tmp_path, time.time())
    assert result["manifest_valid"] and not result["artifacts_valid"]
    assert result["physical_validity"] == "unestablished"


def test_acceptance_without_artifacts_is_flagged(tmp_path):
    (tmp_path / "result_manifest.json").write_text(json.dumps({
        "schema_version": 1, "task": "real_gold_ff", "scientific_status": "accepted",
        "reason": "I think it worked", "artifacts": {}}))
    result = evaluate(tmp_path, time.time())
    assert not result["artifacts_valid"] and result["errors"]


def test_budget_uses_fresh_reference_and_has_no_hard_one_hour_ceiling():
    assert wall_budget(100) == 3600
    assert wall_budget(2825) == 6600
    with pytest.raises(ValueError):
        wall_budget(float("nan"))
