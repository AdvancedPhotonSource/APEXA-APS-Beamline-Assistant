"""Durable acquisition fault tests: no model, numerical engine or hardware."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import time
from types import SimpleNamespace

import numpy as np
import pytest
import tifffile

from apexa_acquisition import AcquisitionStore
from apexa_acquisition_watcher import scan_events_once, plan_from_manifest
from apexa_dataset import DatasetManifest, file_identity
from apexa_jobs import JobStore, default_job_store


def fixture_manifest(tmp_path, *, acquisition_id="scan", revision=1):
    detector = tmp_path / "scan_001.tif"
    if not detector.exists():
        tifffile.imwrite(detector, np.zeros((2, 3), dtype=np.uint16))
    calibration = tmp_path / "params.txt"
    calibration.write_text("Wavelength 0.2\n")
    return {"schema_version": 1, "acquisition_id": acquisition_id, "revision": revision,
            "producer": "detector-A", "closed": True, "expected_frames": ["frame-1"],
            "files": [file_identity(detector, frame_ids=["frame-1"])],
            "required_corrections": ["calibration"],
            "corrections": {"calibration": {"revision": 1, "files": [file_identity(calibration)]}}}


def append_event(path, manifest, event_id=None):
    with path.open("a") as stream:
        stream.write(json.dumps({"event_id": event_id or f"event-{manifest['revision']}", "manifest": manifest}) + "\n")


def watch_args(tmp_path):
    return SimpleNamespace(directory=str(tmp_path), pattern="*.tif", exclude_substring="dark",
                           expected_count=None, quiet_seconds=0, technique="waxs", instrument="",
                           energy=None, param_file="", calibrant="", data_location="",
                           dark_source="none", dark_kind="after", dark_location="",
                           result_folder=str(tmp_path / "results"), execute=True,
                           events=str(tmp_path / "events.jsonl"), producer="detector-A",
                           run_dir=str(tmp_path / "store"))


def test_idempotent_submission_atomic_claim_and_trace(tmp_path):
    store = JobStore(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(lambda _: store.submit({"value": 1}, idempotency_key="scan", trace_id="trace_1"), range(20)))
    assert len(set(ids)) == 1
    with pytest.raises(ValueError, match="different payload"):
        store.submit({"value": 2}, idempotency_key="scan")
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(lambda i: store.claim(owner=str(i)), range(20)))
    assert len([claim for claim in claims if claim]) == 1
    assert store.get(ids[0])["attempts"] == 1
    events = store.events(ids[0])
    assert [event["kind"] for event in events] == ["submitted", "claimed"]
    assert all(event["trace_id"] == "trace_1" for event in events)
    assert store.events(ids[0], after=events[0]["seq"]) == events[1:]
    assert "token" not in store.get(ids[0])


def test_restart_before_start_is_safe_but_started_effect_is_uncertain(tmp_path):
    store = JobStore(tmp_path)
    run_id = store.submit({"operation": "local"})
    _, token = store.claim(owner="first", now=100, lease_seconds=10)
    assert JobStore(tmp_path).reconcile(now=111)[0]["state"] == "queued"
    assert not store.start(run_id, token, now=111)
    _, second = store.claim(owner="second", now=112, lease_seconds=10)
    assert store.start(run_id, second, now=113)
    assert store.reconcile(now=123)[0]["state"] == "uncertain"
    assert not store.finish(run_id, second, "completed", now=124)
    assert not store.claim(owner="third", now=124)
    assert store.submit({"operation": "local"}) == run_id
    assert store.get(run_id)["state"] == "uncertain"
    store.resolve(run_id, state="completed", evidence="adapter receipt verified", result={"status": "success"})
    assert store.get(run_id)["state"] == "completed"


def test_expired_worker_cannot_renew_or_publish_without_reconcile(tmp_path):
    store = JobStore(tmp_path)
    run_id = store.submit({"n": 1})
    _, token = store.claim(owner="worker", now=100, lease_seconds=2)
    assert store.start(run_id, token, now=101)
    assert not store.heartbeat(run_id, token, now=103)
    assert not store.finish(run_id, token, "completed", now=103)


@pytest.mark.parametrize("outcome,state", [
    (RuntimeError("receipt lost after effect"), "uncertain"),
    ("invalid json", "uncertain"), (None, "uncertain"),
    ({"status": "partial", "failed": 1}, "failed"),
    ({"status": "error", "error": "failed"}, "failed"),
    ({"status": "resource_unavailable", "nothing_was_run": True}, "refused"),
    ({"status": "submitted", "job_id": "engine-1", "job_ids": ["fit-1"]}, "submitted"),
    ({"status": "cancelled", "execution_stopped": False}, "uncertain"),
])
def test_receipts_never_trigger_automatic_retry(tmp_path, outcome, state):
    store = JobStore(tmp_path)
    run_id = store.submit({"n": 1})
    calls = []
    def execute(payload, context):
        calls.append(payload)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
    assert store.run_one(execute)["state"] == state
    assert JobStore(tmp_path).run_one(execute) is None
    assert len(calls) == 1
    assert store.get(run_id)["attempts"] == 1


def test_cancel_before_start_and_observed_stop_are_distinct(tmp_path):
    store = JobStore(tmp_path)
    queued = store.submit({"n": 1})
    assert store.cancel(queued)["cancellation_status"] == "stopped"
    active = store.submit({"n": 2})
    def execute(payload, context):
        requested = store.cancel(context.run_id)
        assert requested["state"] == "running"
        assert requested["cancellation_status"] == "requested"
        assert context.cancellation_requested
        assert context.acknowledge_cancel()
        assert store.get(active)["cancellation_status"] == "acknowledged"
        return {"status": "cancelled", "execution_stopped": True}
    assert store.run_one(execute)["cancellation_status"] == "stopped"
    assert store.get(active)["state"] == "cancelled"
    assert [e["kind"] for e in store.events(active)][-3:] == ["cancellation_requested", "cancellation_acknowledged", "cancelled"]


def test_crash_after_external_effect_does_not_reexecute(tmp_path):
    class ProcessDeath(BaseException):
        pass
    store = JobStore(tmp_path)
    run_id = store.submit({"n": 1})
    effects = []
    def execute(payload, context):
        effects.append("effect happened")
        raise ProcessDeath()
    with pytest.raises(ProcessDeath):
        store.run_one(execute)
    JobStore(tmp_path).reconcile(now=time.time() + 100)
    assert store.get(run_id)["state"] == "uncertain"
    assert store.run_one(execute) is None
    assert effects == ["effect happened"]


def test_default_store_honors_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("APEXA_RUN_DIR", str(tmp_path))
    assert default_job_store().root == tmp_path


def test_event_cursor_replay_and_restart_execute_once(tmp_path):
    value = fixture_manifest(tmp_path)
    args = watch_args(tmp_path)
    log = tmp_path / "events.jsonl"
    append_event(log, value)
    effects = []
    def execute(plan):
        effects.append(plan)
        return {"status": "success"}
    first = scan_events_once(args, executor=execute)
    append_event(log, value)
    second = scan_events_once(args, executor=execute)
    assert first[0]["id"] == second[0]["id"]
    assert second[0]["state"] == "completed"
    assert len(effects) == 1
    assert effects[0].data.images == [value["files"][0]["path"]]
    assert DatasetManifest(value).version in effects[0].result_folder
    store = JobStore(args.run_dir)
    with store.connect() as db:
        assert db.execute("SELECT offset FROM acquisition_cursors").fetchone()[0] == log.stat().st_size
        assert db.execute("SELECT count(*) FROM acquisition_events").fetchone()[0] == 1


def test_partial_closure_and_late_dark_require_new_version(tmp_path):
    args = watch_args(tmp_path)
    args.dark_source = "file"
    value = fixture_manifest(tmp_path)
    value["required_corrections"].append("dark")
    value["closed"] = False
    log = tmp_path / "events.jsonl"
    append_event(log, value)
    calls = []
    execute = lambda plan: calls.append(plan) or {"status": "success"}
    assert scan_events_once(args, executor=execute) == []
    value["revision"] = 2
    value["closed"] = True
    append_event(log, value)
    assert scan_events_once(args, executor=execute) == []
    dark = tmp_path / "dark.tif"
    tifffile.imwrite(dark, np.ones((2, 3), dtype=np.uint16))
    value["revision"] = 3
    value["corrections"]["dark"] = {"revision": 1, "files": [file_identity(dark)]}
    append_event(log, value)
    completed = scan_events_once(args, executor=execute)
    assert completed[0]["state"] == "completed"
    assert len(calls) == 1
    correction = tmp_path / "dark_revision2.tif"
    tifffile.imwrite(correction, np.full((2, 3), 2, dtype=np.uint16))
    value["revision"] = 4
    value["corrections"]["dark"] = {"revision": 2, "files": [file_identity(correction)]}
    append_event(log, value)
    updated = scan_events_once(args, executor=execute)
    assert updated[0]["id"] != completed[0]["id"]
    assert calls[0].result_folder != calls[1].result_folder


def test_reordered_snapshots_execute_only_latest_and_event_reuse_refused(tmp_path):
    value = fixture_manifest(tmp_path)
    args = watch_args(tmp_path)
    log = tmp_path / "events.jsonl"
    newer = deepcopy(value)
    newer["revision"] = 2
    append_event(log, newer)
    append_event(log, value)
    runs = scan_events_once(args, executor=lambda plan: {"status": "success"})
    assert len(runs) == 1 and runs[0]["payload"]["manifest"]["revision"] == 2
    append_event(log, value, event_id="event-2")
    with pytest.raises(ValueError, match="event_id reused"):
        scan_events_once(args, executor=lambda plan: pytest.fail("must not run"))


def test_partial_transport_write_and_consumed_prefix_tampering(tmp_path):
    value = fixture_manifest(tmp_path)
    log = tmp_path / "events.jsonl"
    encoded = json.dumps({"event_id": "e", "manifest": value}).encode()
    log.write_bytes(encoded[:30])
    store = AcquisitionStore(JobStore(tmp_path / "store"))
    assert store.ingest_log(log, producer="detector-A") == 0
    with log.open("ab") as stream:
        stream.write(encoded[30:] + b"\n")
    assert store.ingest_log(log, producer="detector-A") == 1
    with log.open("r+b") as stream:
        stream.write(b" ")
    with pytest.raises(ValueError, match="consumed.*changed"):
        store.ingest_log(log, producer="detector-A")


def test_missing_frame_wrong_content_and_hdf5_count_refused(tmp_path):
    value = fixture_manifest(tmp_path)
    value["expected_frames"].append("frame-2")
    assert "partial acquisition" in DatasetManifest(value).issues()[0]
    value["expected_frames"].pop()
    manifest = DatasetManifest(value)
    manifest.verify_local()
    (tmp_path / "params.txt").write_text("changed calibration")
    with pytest.raises(ValueError, match="content no longer matches"):
        manifest.verify_local()
    import h5py
    with h5py.File(tmp_path / "data.h5", "w") as stream:
        stream.create_dataset("exchange/data", data=np.zeros((2, 2, 3)))
    value = fixture_manifest(tmp_path)
    value["files"] = [file_identity(tmp_path / "data.h5", frame_ids=["frame-1"], data_location="exchange/data")]
    with pytest.raises(ValueError, match="frame count mismatch"):
        DatasetManifest(value).verify_local()


def test_preflight_changed_inputs_never_execute_after_durable_admission(tmp_path):
    args = watch_args(tmp_path)
    value = fixture_manifest(tmp_path)
    log = tmp_path / "events.jsonl"
    append_event(log, value)
    jobs = JobStore(args.run_dir)
    store = AcquisitionStore(jobs)
    store.ingest_log(log, producer=args.producer)
    manifest = DatasetManifest(value)
    run_id = store.submit(manifest, plan_from_manifest(manifest, args))
    (tmp_path / "params.txt").write_text("changed after admission")
    runs = scan_events_once(args, executor=lambda plan: pytest.fail("changed input executed"))
    assert runs[0]["id"] == run_id and runs[0]["state"] == "refused"


def test_new_revision_cancels_unstarted_old_job(tmp_path):
    value = fixture_manifest(tmp_path)
    args = watch_args(tmp_path)
    log = tmp_path / "events.jsonl"
    append_event(log, value)
    jobs = JobStore(args.run_dir)
    store = AcquisitionStore(jobs)
    store.ingest_log(log, producer=args.producer)
    manifest = DatasetManifest(value)
    run_id = store.submit(manifest, plan_from_manifest(manifest, args))
    value["revision"] = 2
    value["closed"] = False
    append_event(log, value)
    assert scan_events_once(args, executor=lambda plan: pytest.fail("superseded input executed")) == []
    assert jobs.get(run_id)["state"] == "cancelled"


def test_correction_revision_cannot_be_redefined(tmp_path):
    value = fixture_manifest(tmp_path)
    log = tmp_path / "events.jsonl"
    append_event(log, value)
    store = AcquisitionStore(JobStore(tmp_path / "store"))
    store.ingest_log(log, producer="detector-A")
    value["revision"] = 2
    calibration = tmp_path / "new-params.txt"
    calibration.write_text("changed correction")
    value["corrections"]["calibration"]["files"] = [file_identity(calibration)]
    append_event(log, value)
    with pytest.raises(ValueError, match="correction revision reused"):
        store.ingest_log(log, producer="detector-A")


def test_inputs_changed_during_execution_invalidate_receipt(tmp_path):
    value = fixture_manifest(tmp_path)
    args = watch_args(tmp_path)
    append_event(tmp_path / "events.jsonl", value)
    def execute(plan):
        (tmp_path / "params.txt").write_text("changed during execution")
        return {"status": "success"}
    runs = scan_events_once(args, executor=execute)
    assert runs[0]["state"] == "failed"
    assert "inputs changed during execution" in runs[0]["error"]


def test_invalid_producer_does_not_advance_cursor(tmp_path):
    value = fixture_manifest(tmp_path)
    log = tmp_path / "events.jsonl"
    append_event(log, value)
    jobs = JobStore(tmp_path / "store")
    with pytest.raises(ValueError, match="producer"):
        AcquisitionStore(jobs).ingest_log(log, producer="unrelated-writer")
    with jobs.connect() as db:
        assert db.execute("SELECT count(*) FROM acquisition_cursors").fetchone()[0] == 0
    assert AcquisitionStore(jobs).ingest_log(log, producer="detector-A") == 1


def test_unapplied_correction_and_silently_filtered_input_are_refused(tmp_path):
    value = fixture_manifest(tmp_path)
    args = watch_args(tmp_path)
    value["corrections"]["flat_field"] = deepcopy(value["corrections"]["calibration"])
    with pytest.raises(ValueError, match="cannot apply corrections"):
        plan_from_manifest(DatasetManifest(value), args)
    del value["corrections"]["flat_field"]
    dark_name = tmp_path / "sample_dark_corrected.tif"
    tifffile.imwrite(dark_name, np.ones((2, 3), dtype=np.uint16))
    value["files"] = [file_identity(dark_name, frame_ids=["frame-1"])]
    with pytest.raises(ValueError, match="would exclude"):
        plan_from_manifest(DatasetManifest(value), args)
