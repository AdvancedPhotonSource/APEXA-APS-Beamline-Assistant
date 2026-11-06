"""Scientific-accounting regressions found in the real revision-2 pilot."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("review_pilot", Path(__file__).resolve().parents[1] / "benchmark/harness_comparison/review_pilot.py")
reviewer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reviewer)


def test_expected_budget_limit_is_distinct_from_transport_failure():
    good = {"event": "generation", "http_status": 200, "response_models": ["Claude Opus 5"],
            "usage": {"input_tokens": 4, "cache_read_input_tokens": 8, "output_tokens": 2}}
    rejection = {"event": "rejected_request", "http_status": 429, "reason": reviewer.BUDGET_REASON}
    a = reviewer.model_evidence([good, rejection], "claudeopus5")
    assert a["successful_response_identity_matches"]
    assert (a["budget_rejections"], a["transport_errors"], a["other_request_rejections"]) == (1, 0, 0)
    assert a["reported_input_tokens"] == 12
    b = reviewer.model_evidence([good, rejection, {"event": "generation", "transport_error": "ReadTimeout"}], "claudeopus5")
    assert (b["budget_rejections"], b["transport_errors"]) == (1, 1)


def test_upstream_429_is_not_a_study_budget_limit():
    a = reviewer.model_evidence([{"event": "generation", "http_status": 429}], "claudeopus5")
    assert a["http_errors"] == 1 and a["budget_rejections"] == 0


def test_openai_cache_tokens_are_not_counted_twice():
    a = reviewer.model_evidence([{"event": "generation", "http_status": 200,
        "response_models": ["claudeopus5"], "usage": {"prompt_tokens": 12,
        "prompt_tokens_details": {"cached_tokens": 8}, "completion_tokens": 2}}], "claudeopus5")
    assert a["reported_input_tokens"] == 12


def test_intermediate_geometry_without_manifest_is_not_acceptance(tmp_path):
    (tmp_path / "refined.txt").write_text("tz -1.5\n")
    a = reviewer.adoption(tmp_path, reviewer.Evidence(tmp_path), -10.5, 0.1)
    assert a["decision"] is None and a["artifact_status"] == "missing_manifest"
    assert a["accepted_relative_consistency"] is None


@pytest.mark.parametrize("raw", [b"tz nan\n", b"tz 1\ntz 1\n", b'{"tz":true}', b'{"tz":1,"Tz":1}'])
def test_ambiguous_or_nonfinite_geometry_is_invalid(raw):
    with pytest.raises(ValueError):
        reviewer.geometry_tz(raw)


def test_adoption_is_checked_from_bytes_and_preserves_nonacceptance(tmp_path):
    raw = b"tz -10.51\n"
    (tmp_path / "geometry.txt").write_bytes(raw)
    obj = {"schema_version": 1, "scientific_status": "accepted", "artifact_path": "geometry.txt",
           "artifact_sha256": hashlib.sha256(raw).hexdigest()}
    manifest = tmp_path / "result_manifest.json"
    manifest.write_text(json.dumps(obj))
    assert reviewer.adoption(tmp_path, reviewer.Evidence(tmp_path), -10.5, 0.1)["accepted_relative_consistency"] is True
    obj["scientific_status"] = "provisional"
    manifest.write_text(json.dumps(obj))
    assert reviewer.adoption(tmp_path, reviewer.Evidence(tmp_path), -10.5, 0.1)["accepted_relative_consistency"] is None
    (tmp_path / "geometry.txt").write_text("tz 0\n")
    assert reviewer.adoption(tmp_path, reviewer.Evidence(tmp_path), -10.5, 0.1)["artifact_status"] == "invalid"


def test_symlink_adoption_is_rejected(tmp_path):
    (tmp_path / "source.txt").write_text("tz 0\n")
    (tmp_path / "geometry.txt").symlink_to(tmp_path / "source.txt")
    (tmp_path / "result_manifest.json").write_text(json.dumps({"schema_version": 1,
        "scientific_status": "accepted", "artifact_path": "geometry.txt"}))
    assert reviewer.adoption(tmp_path, reviewer.Evidence(tmp_path), 0, 0.1)["artifact_status"] == "invalid"


def test_every_scheduled_trial_is_retained_and_duplicates_refused(tmp_path):
    entry = {"arm": "A1", "scan": 262, "trial": 0, "schedule_index": 1}
    (tmp_path / "schedule.json").write_text(json.dumps({"entries": [entry]}))
    (tmp_path / "runs.jsonl").write_text("")
    report = reviewer.review(tmp_path)
    assert report["counts"]["scheduled"] == 1
    assert report["trials"][0]["record_status"] == "no_completed_record"
    (tmp_path / "schedule.json").write_text(json.dumps({"entries": [entry, entry]}))
    with pytest.raises(ValueError, match="duplicate"):
        reviewer.review(tmp_path)
