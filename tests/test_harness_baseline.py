"""The fixed baseline must preserve preprocessing and the adopted-result contract."""
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark/harness_comparison"))
import baseline
import baseline_worker as worker


@pytest.mark.parametrize("verdict,status", [("PASS", "accepted"), ("WARN", "provisional"), ("FAIL", "rejected")])
def test_baseline_retains_the_solver_gate(verdict, status):
    assert worker.decision(verdict) == status


def test_calibrant_identity_is_required_and_energy_uses_confirmed_value():
    assert worker.calibrant_for("Ceria_63keV_000262.vrx.h5") == "CeO2"
    assert worker.calibrant_for("Lab6_63keV_000284.vrx.h5") == "LaB6"
    with pytest.raises(ValueError, match="Calibrant"):
        worker.calibrant_for("unknown_000001.h5")
    assert worker.WAVELENGTH_A == pytest.approx(0.19582430, abs=1e-8)


def test_nonzero_embedded_dark_cannot_be_silently_ignored(tmp_path):
    h5py = pytest.importorskip("h5py")
    path = tmp_path / "Ceria.h5"
    with h5py.File(path, "w") as f:
        f.create_dataset("exchange/data", shape=(21, 2880, 2880), dtype="u2", chunks=(1, 120, 120))
        f.create_dataset("exchange/data_dark", shape=(1, 2880, 2880), dtype="u2", fillvalue=1, chunks=(1, 120, 120))
    def unread(*args, **kwargs):
        pytest.fail("A changed dark correction must fail before averaging or fitting")
    with pytest.raises(ValueError, match="Dark differs"):
        worker.preprocess(path, reader=unread)


def test_worker_receives_the_declared_frame_selection_and_one_transform(tmp_path):
    h5py = pytest.importorskip("h5py")
    np = pytest.importorskip("numpy")
    path = tmp_path / "Ceria.h5"
    with h5py.File(path, "w") as f:
        f.create_dataset("exchange/data", shape=(21, 2880, 2880), dtype="u2", chunks=(1, 120, 120))
        f.create_dataset("exchange/data_dark", shape=(1, 2880, 2880), dtype="u2", chunks=(1, 120, 120))
    calls = []
    def reader(source, **kwargs):
        calls.append((source, kwargs))
        return np.zeros((2880, 2880)), np.zeros((2880, 2880), dtype=bool)
    _, _, evidence = worker.preprocess(path, reader=reader)
    assert calls == [(path, {"data_loc": "exchange/data", "skip_frame": 1, "frame_reduce": "mean",
                            "im_trans": (2,), "return_mask": True})]
    assert evidence["frame_indices"] == list(range(1, 21))
    assert evidence["im_trans_applied"] == [2]


@pytest.mark.parametrize("status,tz,outcome", [("accepted", 1.0, "correct"),
                                              ("accepted", 2.0, "accepted_wrong"),
                                              ("rejected", 2.0, "honest_failure"),
                                              ("provisional", 1.0, "false_alarm")])
def test_scoring_uses_the_baselines_artifact_and_final_decision(tmp_path, status, tz, outcome):
    geometry = tmp_path / "geometry.json"
    geometry.write_text(json.dumps({"tz": tz}))
    (tmp_path / "result_manifest.json").write_text(json.dumps({"schema_version": 1,
        "scientific_status": status, "artifact_path": geometry.name,
        "artifact_sha256": hashlib.sha256(geometry.read_bytes()).hexdigest()}))
    result = baseline.grade_workspace(tmp_path, 1, {1: {"predicted_tz": 1.0}}, 1, 0, {"tilt_tolerance_deg": 0.1})
    assert result.outcome == outcome
    assert result.comparison_kind == "fixed_numerical_baseline"


def test_baseline_requires_a_manifest_even_with_one_good_geometry(tmp_path):
    (tmp_path / "geometry.json").write_text('{"tz": 1.0}')
    result = baseline.grade_workspace(tmp_path, 1, {1: {"predicted_tz": 1.0}}, 1, 0, {"tilt_tolerance_deg": 0.1})
    assert result.outcome == "error"
    assert "mandatory decision manifest" in result.detail


def test_main_baseline_is_held_until_pilot_review(tmp_path):
    with pytest.raises(RuntimeError, match="pilot review"):
        baseline.run(tmp_path, {}, "main")
    assert not list(tmp_path.iterdir())
