"""Offline checks for study containment, audit evidence and qualification gates."""
import json
import base64
import hashlib
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark/harness_comparison"))
import model_audit as ma
import rotation_benchmark as rb
import study
import procedure_audit


@pytest.fixture
def upstream():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.server.requests.append(payload)
            self.server.paths.append(self.path)
            model = self.server.model
            if self.path == "/v1/messages/count_tokens":
                body = json.dumps(self.server.token_count_response).encode()
                kind = "application/json"
            elif payload.get("stream"):
                events = [{"type": "message_start", "message": {"model": model, "usage": {"input_tokens": 9}}},
                          {"type": "message_delta", "usage": {"output_tokens": 2}},
                          {"type": "message_stop"}]
                body = "".join("data: " + json.dumps(e) + "\n\n" for e in events).encode()
                kind = "text/event-stream"
            else:
                body = json.dumps({"model": model, "choices": [],
                                   "usage": {"prompt_tokens": 9, "completion_tokens": 2}}).encode()
                kind = "application/json"
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.requests = []
    server.paths = []
    server.token_count_response = {"input_tokens": 37}
    server.model = "claude-opus-5"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, f"http://127.0.0.1:{server.server_port}/v1"
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.mark.parametrize("stream", [False, True])
def test_relay_records_actual_identity_usage_and_clamped_budget(tmp_path, upstream, stream):
    server, url = upstream
    log = tmp_path / "audit.jsonl"
    with ma.ModelAudit(url, "upstream-secret", "claudeopus5", log, max_output_tokens=12) as audit:
        response = httpx.post(audit.base_url + "/messages", headers={"x-api-key": audit.token},
                              json={"model": "claude-opus-5", "max_tokens": 100, "stream": stream,
                                    "messages": [{"role": "user", "content": "private-prompt"}]})
        assert response.status_code == 200
    summary = audit.summary()
    assert summary["qualified"]
    assert summary["response_models"] == ["claude-opus-5"]
    assert server.requests[0]["max_tokens"] == 12
    evidence = log.read_text()
    assert "private-prompt" not in evidence and "upstream-secret" not in evidence
    assert "input_tokens" in evidence if stream else "prompt_tokens" in evidence


@pytest.mark.parametrize("reason", ["model", "credential", "budget", "tokens", "endpoint"])
def test_rejected_requests_invalidate_qualification(tmp_path, upstream, reason):
    server, url = upstream
    with ma.ModelAudit(url, "secret", "claudeopus5", tmp_path / "audit.jsonl", max_requests=1) as audit:
        payload = {"model": "claudeopus5", "max_tokens": 10}
        headers = {"x-api-key": audit.token}
        path = "/messages"
        if reason == "model":
            payload["model"] = "claudeopus55"
        elif reason == "credential":
            headers = {}
        elif reason == "tokens":
            payload["max_tokens"] = False
        elif reason == "endpoint":
            path = "/arbitrary"
        elif reason == "budget":
            assert httpx.post(audit.base_url + path, headers=headers, json=payload).status_code == 200
        response = httpx.post(audit.base_url + path, headers=headers, json=payload)
        assert response.status_code >= 400
    assert audit.summary()["violations"]
    assert not audit.summary()["qualified"]
    assert len(server.requests) == (1 if reason == "budget" else 0)


def test_returned_model_mismatch_is_not_a_common_model_result(tmp_path, upstream):
    server, url = upstream
    server.model = "different-model"
    with ma.ModelAudit(url, "secret", "claudeopus5", tmp_path / "audit.jsonl") as audit:
        httpx.post(audit.base_url + "/messages", headers={"x-api-key": audit.token},
                   json={"model": "claudeopus5"})
    assert not audit.summary()["all_response_models_match"]
    assert not audit.summary()["qualified"]


def test_rejected_endpoint_keeps_route_but_not_query_or_opaque_identifiers(tmp_path, upstream):
    _, url = upstream
    log = tmp_path / "audit.jsonl"
    with ma.ModelAudit(url, "upstream-secret", "claudeopus5", log) as audit:
        response = httpx.post(audit.base_url + "/messages/unsupported?key=query-secret",
                              headers={"x-api-key": audit.token}, json={"private": "prompt-secret"})
        assert response.status_code == 404
        httpx.get(audit.base_url + "/accounts/1234-secret-5678")
    raw = log.read_text()
    for private in ("query-secret", "prompt-secret", "upstream-secret", "1234-secret-5678"):
        assert private not in raw
    assert audit.summary()["protocol_violations"][0]["path"] == "/v1/messages/unsupported"
    assert audit.summary()["protocol_violations"][0]["method"] == "POST"
    assert audit.summary()["protocol_violations"][1]["path"] == "/v1/accounts/{redacted}"
    assert not audit.summary()["scientific_evaluable"]


def test_token_count_is_forwarded_exactly_and_does_not_consume_generation_budget(tmp_path, upstream):
    server, url = upstream
    log = tmp_path / "audit.jsonl"
    payload = {"model": "claudeopus5", "messages": [{"role": "user", "content": "private-fixture"}]}
    with ma.ModelAudit(url, "upstream-secret", "claudeopus5", log, max_requests=1) as audit:
        headers = {"x-api-key": audit.token}
        assert httpx.post(audit.base_url + "/messages", headers=headers, json=payload).status_code == 200
        response = httpx.post(audit.base_url + "/messages/count_tokens?beta=true", headers=headers, json=payload)
        assert response.json() == {"input_tokens": 37}
    assert server.paths == ["/v1/messages", "/v1/messages/count_tokens"]
    assert server.requests[1] == payload
    assert audit.summary()["requests"] == 1
    assert audit.summary()["successful_responses"] == 1
    assert audit.summary()["successful_token_counts"] == 1
    assert audit.summary()["qualified"]
    assert audit.records[1]["request_sha256"] == audit.records[1]["forwarded_sha256"]
    assert "private-fixture" not in log.read_text() and "upstream-secret" not in log.read_text()


@pytest.mark.parametrize("condition", ["model", "credential", "deadline", "invalid_count"])
def test_token_count_keeps_auth_model_deadline_and_response_validation(tmp_path, upstream, condition):
    server, url = upstream
    with ma.ModelAudit(url, "secret", "claudeopus5", tmp_path / "audit.jsonl") as audit:
        payload = {"model": "claudeopus5", "messages": []}
        headers = {"x-api-key": audit.token}
        httpx.post(audit.base_url + "/messages", headers=headers, json=payload)
        if condition == "model":
            payload["model"] = "other"
        elif condition == "credential":
            headers = {}
        elif condition == "deadline":
            audit.deadline = 0
        else:
            server.token_count_response = {"input_tokens": True}
        response = httpx.post(audit.base_url + "/messages/count_tokens", headers=headers, json=payload)
    assert not audit.summary()["qualified"]
    assert audit.summary()["requests"] == 1
    assert len(server.requests) == (2 if condition == "invalid_count" else 1)
    if condition == "invalid_count":
        assert response.status_code == 200  # No manufactured replacement response.
        assert audit.summary()["auxiliary_errors"]
    else:
        assert response.status_code >= 400


@pytest.mark.parametrize("wire", ["anthropic", "openai"])
def test_image_audit_proves_bytes_reach_upstream_without_logging_content(tmp_path, upstream, wire):
    server, url = upstream
    raw = b"synthetic-image-private-bytes"
    encoded = base64.b64encode(raw).decode()
    block = ({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": encoded}}
             if wire == "anthropic" else
             {"type": "image_url", "image_url": {"url": "data:image/png;base64," + encoded}})
    payload = {"model": "claudeopus5", "messages": [{"role": "user", "content": [block]}]}
    log = tmp_path / "audit.jsonl"
    with ma.ModelAudit(url, "secret", "claudeopus5", log) as audit:
        response = httpx.post(audit.base_url + ("/messages" if wire == "anthropic" else "/chat/completions"),
                              headers={"x-api-key": audit.token}, json=payload)
        assert response.status_code == 200
    assert server.requests[0]["messages"] == payload["messages"]
    assert audit.records[0]["input_images"] == [{"inline": True, "bytes": len(raw),
                                               "sha256": hashlib.sha256(raw).hexdigest()}]
    assert encoded not in log.read_text() and raw.decode() not in log.read_text()
    assert audit.summary()["qualified"]


def test_guidance_requires_frozen_content_and_matching_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(study, "REPO", tmp_path)
    path = tmp_path / "guidance.md"
    path.write_text("Shared guidance\n")
    spec = {"guidance_file": "guidance.md", "guidance_sha256": study.sha(path),
            "wall_seconds": 1800, "max_model_requests": 40}
    assert study.guidance_for({}) is None
    assert study.guidance_for(spec) == path.read_text()
    with pytest.raises(ValueError, match="budget"):
        study.guidance_for({**spec, "wall_seconds": 300})
    larger = {**spec, "wall_seconds": 3600, "max_model_requests": 120,
              "guidance_budget": {"wall_seconds": 3600, "max_model_requests": 120}}
    assert study.guidance_for(larger) == path.read_text()
    with pytest.raises(ValueError, match="budget"):
        study.guidance_for({**larger, "max_model_requests": 40})
    path.write_text("Changed after freeze")
    with pytest.raises(ValueError, match="hash"):
        study.guidance_for(spec)


def test_image_flag_cannot_admit_an_unqualified_or_changed_adapter(tmp_path, monkeypatch):
    spec = {"arms": ["A5"], "model": "m", "pi_image_input": True}
    assert study.check_image_qualifications(tmp_path, {**spec, "pi_image_input": False}) == {}
    with pytest.raises(RuntimeError, match="receipt"):
        study.check_image_qualifications(tmp_path, spec)
    launcher = tmp_path / "pi"
    launcher.write_text("fake native launcher")
    monkeypatch.setattr(study.shutil, "which", lambda _: str(launcher))
    path = tmp_path / "setup/image-input/A5/qualification.json"
    path.parent.mkdir(parents=True)
    record = {"arm": "A5", "test": "image", "passed": True, "answer_correct": True,
              "exact_image_delivered": True, "image_input": True, "model_requested": "m",
              "host": {"system": study.platform.system(), "machine": study.platform.machine()},
              "harness": {"launcher_sha256": study.sha(launcher)}, "model_audit": {"qualified": True},
              "source_hashes": {n: study.sha(study.HERE / n) for n in
                                ("rotation_benchmark.py", "run_comparison.py", "model_audit.py")}}
    path.write_text(json.dumps(record))
    assert study.check_image_qualifications(tmp_path, spec) == {"A5": study.sha(path)}
    record["source_hashes"]["model_audit.py"] = "stale-source"
    path.write_text(json.dumps(record))
    with pytest.raises(RuntimeError, match="changed since"):
        study.check_image_qualifications(tmp_path, spec)


def test_shared_guidance_reaches_every_arm_but_not_smoke_tasks(tmp_path, monkeypatch):
    monkeypatch.setenv("ANL_USERNAME", "test-secret")
    monkeypatch.setattr(study, "REPO", tmp_path)
    guidance = tmp_path / "guidance.md"
    guidance.write_text("The same finishing guidance.\n")
    spec = {"guidance_file": guidance.name, "guidance_sha256": study.sha(guidance),
            "wall_seconds": 1800, "max_model_requests": 40, "max_output_tokens": 100,
            "model_base_url": "http://127.0.0.1:1/v1", "model": "m", "tilt_tolerance_deg": .1,
            "arms": ["A1", "A3", "A4", "A5", "A7"]}
    monkeypatch.setattr(study, "denied_locations", lambda *a: [])
    seen = []
    def fake_run(arm, *args, **kwargs):
        seen.append((arm.id, kwargs["guidance_text"], kwargs["prompt_override"]))
        return rb.Run(arm.id, arm.harness, 0, 0, "m", True, False)
    monkeypatch.setattr(rb, "run_one", fake_run)
    for arm in spec["arms"]:
        study.run_trial(tmp_path, spec, tmp_path / arm, {"arm": arm, "scan": 0, "trial": 0},
                        tmp_path / "fixture", {})
    study.run_trial(tmp_path, spec, tmp_path / "smoke", {"arm": "A3", "scan": 0, "trial": 0},
                    tmp_path / "fixture", {}, smoke=True)
    assert [row[0] for row in seen[:-1]] == spec["arms"]
    assert all(row[1:] == (guidance.read_text(), None) for row in seen[:-1])
    assert seen[-1][1] is None and "file-I/O qualification" in seen[-1][2]


def test_automatic_procedure_ablation_keeps_other_policies_identical():
    full, ablated = (rb.policy_environment(rb.ARMS[a]) for a in ("A1", "A7"))
    assert full.pop("APEXA_AUTOMATIC_PROCEDURES") == "1"
    assert ablated.pop("APEXA_AUTOMATIC_PROCEDURES") == "0"
    assert full == ablated


@pytest.mark.parametrize("harness,events", [
    ("claude-code", [{"type": "user", "message": "Calibration succeeded"},
                     {"type": "result", "result": "Final decision: rejected"}]),
    ("opencode", [{"type": "tool_use", "part": {"text": "Calibration succeeded"}},
                  {"type": "text", "part": {"text": "Final decision: rejected"}}]),
    ("pi", [{"type": "message_end", "message": {"role": "toolResult", "content": [{"type": "text", "text": "Calibration succeeded"}]}},
            {"type": "message_end", "message": {"role": "assistant", "content": [{"type": "text", "text": "Final decision: rejected"}]}}]),
])
def test_structured_tool_logs_do_not_become_final_claims(harness, events):
    assert rb.final_answer("\n".join(map(json.dumps, events)), harness) == "Final decision: rejected"
    assert rb.final_answer('{"unknown":"Calibration succeeded"}', harness) == ""


def test_linux_sandbox_mounts_interpreter_after_hidden_production_tree(tmp_path, monkeypatch):
    monkeypatch.setattr(rb.sys, "platform", "linux")
    monkeypatch.setattr(rb, "SANDBOX", "/usr/bin/bwrap")
    hidden = tmp_path / "production"
    interpreter = hidden / ".venv"
    interpreter.mkdir(parents=True)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setattr(rb.sys, "prefix", str(interpreter))
    argv = rb.sandboxed(["python", "-V"], [str(hidden)], writable=[work])
    assert argv[argv.index("--bind") + 1:argv.index("--bind") + 3] == [str(work)] * 2
    assert argv.index(str(hidden)) < argv.index(str(interpreter))
    assert argv[-3:] == ["--", "python", "-V"]


def test_scoped_sandbox_hides_site_data_mounts_and_keeps_software_aliases(tmp_path, monkeypatch):
    monkeypatch.setattr(rb.sys, "platform", "linux")
    monkeypatch.setattr(rb, "SANDBOX", "/usr/bin/bwrap")
    actual = tmp_path / "node-release"
    actual.mkdir()
    alias = tmp_path / "node"
    alias.symlink_to(actual, target_is_directory=True)
    monkeypatch.setenv("RB_SOFTWARE_READ_ROOTS", json.dumps([str(alias)]))
    original_iterdir, original_is_dir = Path.iterdir, Path.is_dir
    top = [Path("/gdata"), Path("/gpfs"), Path("/usr")]
    monkeypatch.setattr(Path, "iterdir", lambda p: iter(top) if str(p) == "/" else original_iterdir(p))
    monkeypatch.setattr(Path, "is_dir", lambda p: True if p in top else original_is_dir(p))
    argv = rb.sandboxed(["true"], [])
    assert argv[argv.index("/gdata") - 1] == "--tmpfs"
    assert argv[argv.index("/gpfs") - 1] == "--tmpfs"
    index = argv.index(str(actual))
    assert argv[index - 1:index + 2] == ["--ro-bind", str(actual), str(alias)]
    assert argv.index("/gpfs") < index


def test_scoped_sandbox_rejects_broad_software_allowances(monkeypatch):
    monkeypatch.setattr(rb.sys, "platform", "linux")
    monkeypatch.setattr(rb, "SANDBOX", "/usr/bin/bwrap")
    monkeypatch.setenv("RB_SOFTWARE_READ_ROOTS", '["/"]')
    with pytest.raises(ValueError, match="Invalid study software root"):
        rb.sandboxed(["true"], [])


def test_legacy_apexa_home_is_backed_only_by_the_trial_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(rb.sys, "platform", "linux")
    monkeypatch.setattr(rb, "SANDBOX", "/usr/bin/bwrap")
    software = tmp_path / "software"
    software.mkdir()
    monkeypatch.setenv("RB_SOFTWARE_READ_ROOTS", json.dumps([str(software)]))
    state = tmp_path / "trial/profile/apexa"
    state.mkdir(parents=True)
    argv = rb.sandboxed(["true"], [], writable=[tmp_path / "trial"], private_apexa_home=state)
    index = argv.index(str(Path.home() / ".apexa"))
    assert argv[index - 2:index + 1] == ["--bind", str(state.resolve()), str(Path.home() / ".apexa")]
    with pytest.raises(ValueError, match="inside a writable trial root"):
        rb.sandboxed(["true"], [], writable=[software], private_apexa_home=state)


def test_frozen_setup_detects_changed_input(tmp_path, monkeypatch):
    spec = {"scans": [1], "pilot_scans": [2], "arms": ["A1"], "repeats": 1, "order_seed": 4, "solver_python": "/python"}
    raw = tmp_path / "raw"
    raw.write_text("original")
    reference = tmp_path / "setup/reference.csv"
    reference.parent.mkdir()
    reference.write_text("reference")
    frozen = {"spec": spec, "source_hashes": {}, "reference_sha256": study.sha(reference),
              "solver": {}, "harnesses": {}, "inputs": [{"path": str(raw), "sha256": study.sha(raw)}]}
    study.write_new(tmp_path / "setup/frozen.json", frozen)
    for cohort, scans in (("main", [1]), ("pilot", [2])):
        study.write_new(tmp_path / f"setup/{cohort}_schedule.json", rb.make_schedule(["A1"], scans, 1, 4))
    monkeypatch.setattr(study, "source_hashes", lambda: {})
    monkeypatch.setattr(study, "solver_inventory", lambda _: {})
    monkeypatch.setattr(study, "harness_versions", lambda _: {})
    assert study.verify(tmp_path, spec) == frozen
    raw.write_text("changed")
    with pytest.raises(RuntimeError, match="Raw input changed"):
        study.verify(tmp_path, spec)


def test_main_cohort_cannot_launch_before_baseline_review(tmp_path, monkeypatch):
    monkeypatch.setattr(study, "verify", lambda *a, **kw: {})
    monkeypatch.setattr(study, "source_hashes", lambda: {})
    study.write_new(tmp_path / "setup/qualification.json", {"passed": True, "source_hashes": {}})
    with pytest.raises(RuntimeError, match="deterministic baseline"):
        study.run(tmp_path, {}, "main")
    assert not (tmp_path / "results").exists()


def test_publication_scoring_requires_explicit_adoption(tmp_path):
    (tmp_path / "geometry.json").write_text('{"tz": 1.0}')
    evidence = rb.extract_result(tmp_path, "Calibration succeeded", 0, {}, require_manifest=True)
    assert evidence.source == "missing_manifest" and evidence.tz is None


def test_budget_exhaustion_remains_evaluable_and_is_not_an_infrastructure_exclusion(tmp_path, upstream):
    _, url = upstream
    with ma.ModelAudit(url, "secret", "claudeopus5", tmp_path / "audit.jsonl", max_requests=1) as audit:
        for _ in range(2):
            httpx.post(audit.base_url + "/messages", headers={"x-api-key": audit.token},
                       json={"model": "claudeopus5", "max_tokens": 10})
    summary = audit.summary()
    assert summary["budget_exhausted"] and summary["scientific_evaluable"]
    assert not summary["qualified"]  # startup qualification remains stricter
    result = rb.Run("A3", "claude-code", 1, 0, "m", True, False, outcome="missing_manifest", model_audit=summary)
    study.apply_model_audit(result)
    assert result.outcome == "budget_exhausted"
    assert rb.summarise([result])["A3"]["n_scored"] == 1
    result.outcome = "timeout"
    study.apply_model_audit(result)
    assert result.outcome == "timeout"


def test_relay_records_actual_procedure_delivery_not_just_environment(tmp_path, upstream):
    _, url = upstream
    candidates = tmp_path / "candidates.jsonl"
    candidates.write_text(json.dumps({"content": "Exact automatic procedure", "kind": "automatic_skill_context"}) + "\n")
    with ma.ModelAudit(url, "secret", "claudeopus5", tmp_path / "audit.jsonl", procedure_candidates=candidates) as audit:
        for context in ("No procedure here", "Prefix Exact automatic procedure suffix"):
            httpx.post(audit.base_url + "/messages", headers={"x-api-key": audit.token},
                       json={"model": "claudeopus5", "system": [{"type": "text", "text": context}]})
    assert audit.records[0]["automatic_procedures_delivered"] == []
    assert audit.records[1]["automatic_procedures_delivered"][0]["roles"] == ["system"]
    assert len(audit.summary()["automatic_procedure_hashes_delivered"]) == 1
    assert "Prefix Exact" not in (tmp_path / "audit.jsonl").read_text()


def test_resumption_skips_completed_and_preserves_interrupted_trials(tmp_path, monkeypatch):
    spec = {"arms": ["A3"], "scans": [1, 2, 3], "pilot_scans": [1, 2, 3], "repeats": 1,
            "order_seed": 1, "model": "m", "model_base_url": "unused"}
    schedule = rb.make_schedule(["A3"], [1, 2, 3], 1, 1)
    study.write_new(tmp_path / "setup/pilot_schedule.json", schedule)
    study.write_new(tmp_path / "setup/qualification.json", {"passed": True, "source_hashes": {}})
    output = tmp_path / "results/pilot"
    study.write_new(output / "schedule.json", schedule)
    first, second, third = schedule["entries"]
    def result(entry):
        return rb.Run("A3", "claude-code", entry["scan"], entry["trial"], "m", True, False,
                      outcome="missing_manifest", schedule_index=entry["schedule_index"],
                      schedule_block=entry["schedule_block"], schedule_position=entry["schedule_position"])
    study.append_record(output / "runs.jsonl", study.asdict(result(first)))
    study.write_new(output / "started" / f"{second['schedule_index']:04d}.json", second)
    frozen = {"inputs": [{"path": f"/raw_{scan:06d}.h5", "sha256": "hash"} for scan in [1, 2, 3]]}
    monkeypatch.setattr(study, "verify", lambda *args, **kwargs: frozen)
    monkeypatch.setattr(study, "source_hashes", lambda: {})
    monkeypatch.setattr(study, "sha", lambda p: "hash")
    monkeypatch.setattr(rb, "load_truth", lambda *args, **kwargs: {})
    monkeypatch.setattr(rb, "wait_for_host", lambda *args: {})
    calls = []
    def trial(root, spec, output, entry, *args):
        calls.append(entry["schedule_index"])
        return result(entry)
    monkeypatch.setattr(study, "run_trial", trial)
    study.run_locked(tmp_path, spec, "pilot")
    study.run_locked(tmp_path, spec, "pilot")
    rows = study.prior_results(output, schedule)
    assert calls == [third["schedule_index"]]
    assert len(rows) == 3 and rows[1].outcome == "interrupted"
    assert json.loads((output / "progress.json").read_text())["completed"] == 3


def test_expanded_resumption_retains_attempts_and_batch_assignment(tmp_path, monkeypatch):
    import expanded_study as expanded
    from concurrent.futures import Future
    spec = {"arms": ["A3"], "scans": [1, 2, 3, 4], "pilot_scans": [], "repeats": 1,
            "order_seed": 1, "model": "m", "model_base_url": "unused",
            "execution": {"workers": 2}}
    schedule = rb.make_schedule(spec["arms"], spec["scans"], 1, 1)
    study.write_new(tmp_path / "setup/main_schedule.json", schedule)
    study.write_new(tmp_path / "setup/qualification.json", {"passed": True, "source_hashes": {}})
    output = tmp_path / "results/main"
    output.mkdir(parents=True)
    entries = schedule["entries"]
    study.append_record(output / "runs.jsonl", study.asdict(expanded.interrupted(entries[0], spec)))
    study.write_new(output / "started" / f"{entries[1]['schedule_index']:04d}.json", entries[1])
    frozen = {"inputs": [{"path": f"/raw_{n:06d}.h5", "sha256": "h"} for n in spec["scans"]]}
    monkeypatch.setattr(study, "verify", lambda *args, **kw: frozen)
    monkeypatch.setattr(study, "source_hashes", lambda: {})
    monkeypatch.setattr(study, "sha", lambda p: "h")
    monkeypatch.setattr(study, "load_reference", lambda *args: {})
    monkeypatch.setattr(rb, "wait_for_host", lambda *args: {})
    monkeypatch.setattr(expanded, "cpu_sets", lambda spec: [[0], [1]])
    calls = []
    class Pool:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def submit(self, fn, root, spec, out, entry, source, truth, cpus):
            calls.append((entry["schedule_index"], cpus))
            future = Future()
            if len(calls) == 1:
                future.set_exception(RuntimeError("synthetic worker failure"))
            else:
                future.set_result(expanded.interrupted(entry, spec))
            return future
    monkeypatch.setattr(expanded, "ProcessPoolExecutor", Pool)
    expanded.run_locked(tmp_path, spec, "main")
    expanded.run_locked(tmp_path, spec, "main")
    assert calls == [(entries[2]["schedule_index"], [0]), (entries[3]["schedule_index"], [1])]
    rows = study.prior_results(output, schedule)
    assert len(rows) == 4 and rows[2].outcome == "infra_error"
    assert json.loads((output / "progress.json").read_text())["status"] == "completed"


def test_reference_separates_acquisition_sweeps(tmp_path):
    reference = tmp_path / "setup/reference.csv"
    reference.parent.mkdir()
    reference.write_text("scan,calibrant,sweep_calibrant,tz_deg\n"
        "1,CeO2,first_CeO2,1\n2,CeO2,first_CeO2,2\n3,CeO2,first_CeO2,3\n"
        "4,CeO2,second_CeO2,20\n5,CeO2,second_CeO2,20.1\n6,CeO2,second_CeO2,20.2\n")
    truth = study.load_reference(tmp_path, {"reference_group": "sweep_calibrant"})
    assert truth[3]["predicted_tz"] == pytest.approx(3)
    assert truth[4]["predicted_tz"] == pytest.approx(20)
    assert truth[4]["n_fit"] == 2


def test_parallel_dispatch_keeps_explicit_specification(tmp_path, monkeypatch):
    import expanded_study as expanded
    from types import SimpleNamespace
    selected = tmp_path / "explicit.json"
    isolated_module = SimpleNamespace(SPEC=None)
    monkeypatch.setattr(study, "SPEC", selected)
    monkeypatch.setattr(expanded, "study", isolated_module)
    seen = []
    monkeypatch.setattr(expanded, "run_locked", lambda *args: seen.append(isolated_module.SPEC))
    (tmp_path / "setup").mkdir()
    study.run(tmp_path, {"execution": {"workers": 2}}, "pilot")
    assert seen == [selected]


def test_native_qualification_exercises_all_four_slots_concurrently(tmp_path, monkeypatch):
    import expanded_study as expanded
    from concurrent.futures import ThreadPoolExecutor
    sets = [[n] for n in range(4)]
    spec = {"arms": ["A1", "A3", "A4", "A5"]}
    barrier = threading.Barrier(4, timeout=5)
    observed = []
    monkeypatch.setattr(expanded, "cpu_sets", lambda spec: sets)
    monkeypatch.setattr(expanded, "ProcessPoolExecutor",
                        lambda max_workers, **kwargs: ThreadPoolExecutor(max_workers=max_workers))
    def execute(root, received, output, entry, source, truth, cpus, *, smoke=False):
        observed.append((entry["arm"], cpus, smoke))
        barrier.wait()
        return entry["arm"]
    monkeypatch.setattr(expanded, "execute", execute)
    assert expanded.qualify_trials(tmp_path, spec, tmp_path, tmp_path / "fixture") == spec["arms"]
    assert sorted(observed) == [(arm, sets[i], True) for i, arm in enumerate(spec["arms"])]


@pytest.mark.parametrize("failed_arm", [None, "A4"])
def test_concurrent_qualification_requires_every_arm_to_pass(tmp_path, monkeypatch, failed_arm):
    import expanded_study as expanded
    spec = {"arms": ["A1", "A3", "A4", "A5"], "execution": {"workers": 4}}
    monkeypatch.setattr(study, "verify", lambda *args, **kwargs: {})
    monkeypatch.setattr(study, "source_hashes", lambda: {})
    monkeypatch.setattr(study, "sandbox_check", lambda *args: {"passed": True})
    (tmp_path / "setup").mkdir()
    def qualify(root, spec, output, fixture):
        results = []
        for arm in spec["arms"]:
            dest = output / f"workspaces/{arm}_0_0/smoke_copy.txt"
            dest.parent.mkdir(parents=True)
            dest.write_bytes(fixture.read_bytes())
            result = rb.Run(arm, rb.ARMS[arm].harness, 0, 0, "m", True, False, return_code=0)
            result.model_audit = {"qualified": arm != failed_arm,
                "automatic_procedure_hashes_delivered": ["native-procedure"] if arm == "A1" else []}
            results.append(result)
        return results
    monkeypatch.setattr(expanded, "qualify_trials", qualify)
    if failed_arm:
        with pytest.raises(RuntimeError, match="Qualification failed"):
            study.qualify(tmp_path, spec)
    else:
        study.qualify(tmp_path, spec)
    receipt = json.loads((tmp_path / "setup/qualification.json").read_text())
    assert receipt["passed"] == (failed_arm is None)
    assert receipt["execution"]["workers"] == 4
    assert len(receipt["checks"]) == 4 and receipt["scientific_runs"] == 0


def test_cutoff_retains_unlaunched_cells_and_prior_interruption(tmp_path, monkeypatch):
    import expanded_study as expanded
    spec = {"arms": ["A3"], "scans": [1, 2, 3, 4], "pilot_scans": [], "repeats": 1,
            "order_seed": 1, "model": "m", "model_base_url": "unused",
            "launch_cutoff": "2000-01-01T12:00:00-06:00", "execution": {"workers": 2}}
    schedule = rb.make_schedule(spec["arms"], spec["scans"], 1, 1)
    study.write_new(tmp_path / "setup/main_schedule.json", schedule)
    study.write_new(tmp_path / "setup/qualification.json", {"passed": True, "source_hashes": {}})
    first = schedule["entries"][0]
    study.write_new(tmp_path / "results/main/started" / f"{first['schedule_index']:04d}.json", first)
    frozen = {"inputs": [{"path": f"/raw_{n:06d}.h5", "sha256": "h"} for n in spec["scans"]]}
    monkeypatch.setattr(study, "verify", lambda *args, **kw: frozen)
    monkeypatch.setattr(study, "source_hashes", lambda: {})
    monkeypatch.setattr(study, "load_reference", lambda *args: {})
    monkeypatch.setattr(expanded, "cpu_sets", lambda spec: [[0], [1]])
    monkeypatch.setattr(rb, "wait_for_host", lambda *args: pytest.fail("No host/model probe after cutoff"))
    expanded.run_locked(tmp_path, spec, "main")
    expanded.run_locked(tmp_path, spec, "main")
    rows = study.prior_results(tmp_path / "results/main", schedule)
    assert rows[0].outcome == "interrupted"
    assert [r.outcome for r in rows[1:]] == ["not_launched_cutoff"] * 3
    progress = json.loads((tmp_path / "results/main/progress.json").read_text())
    assert progress["status"] == "stopped_at_cutoff" and progress["not_launched"] == 3
    assert len(list((tmp_path / "results/main/started").glob("*.json"))) == 1


def test_cutoff_uses_explicit_chicago_offset():
    import expanded_study as expanded
    from datetime import datetime
    spec = {"launch_cutoff": "2026-11-13T12:00:00-06:00"}
    utc = datetime.fromisoformat("2026-11-13T18:00:00+00:00").timestamp()
    assert expanded.deadline_reached(spec, now=utc)
    assert not expanded.deadline_reached(spec, now=utc - 1)
    with pytest.raises(ValueError, match="timezone"):
        expanded.deadline_reached({"launch_cutoff": "2026-11-13T12:00:00"})


def test_reference_prediction_excludes_the_held_acquisition_and_other_calibrant():
    import prepare_references
    rows = [{"scan": i, "calibrant": "CeO2", "tz_deg": float(i)} for i in (1, 2, 3)]
    rows[1]["tz_deg"] = 999.
    rows += [{"scan": i, "calibrant": "LaB6", "tz_deg": 100. + i} for i in (4, 5, 6)]
    predicted = {r["scan"]: r for r in prepare_references.predict(rows)}
    assert predicted[2]["predicted_tz_deg"] == pytest.approx(2.)
    assert predicted[2]["n_fit"] == 2
    assert predicted[5]["predicted_tz_deg"] == pytest.approx(105.)


def test_paired_bootstrap_preserves_pairing_and_both_sweeps():
    import analyze_r6
    rows = [{"stratum": s, "A7": x, "A1": x} for s in
            ("first_CeO2", "first_LaB6", "second_CeO2", "second_LaB6") for x in (0., 1.)]
    result = analyze_r6.paired_estimates(rows, ["A1", "A7"], samples=200)
    assert result["A1"]["mean_difference_arm_minus_A7"] == 0
    assert result["A1"]["percentile_95_interval"] == [0., 0.]


@pytest.mark.parametrize("affected_arm,state", [
    (None, None), ("A7", "not_launched_cutoff"), ("B0", "not_launched_cutoff"), ("A7", "unrun")])
def test_r6_analysis_distinguishes_accounted_cutoff_from_completed_attempts(tmp_path, monkeypatch, affected_arm, state):
    import analyze_r6
    setup = tmp_path / "setup"
    setup.mkdir()
    reference = setup / "reference.csv"
    reference.write_text("scan,tz_deg\n261,1\n304,2\n")
    (setup / "reference_rs2_r2.csv").write_text("scan,predicted_tz_deg\n304,2\n")
    spec = {"arms": ["A1", "A7"], "order_seed": 20261111, "tilt_tolerance_deg": .1}
    study.write_new(setup / "frozen.json", {"spec": spec, "reference_sha256": study.sha(reference)})
    monkeypatch.setattr(study, "load_reference", lambda *args: {})
    rows = []
    for scan in (261, 304):
        for arm in ("A1", "A7", "B0"):
            for trial in range(1 if arm == "B0" else 2):
                row = {"scan": scan, "trial": trial, "arm": arm, "record_status": "terminal",
                       "outcome": "correct", "endpoint": True, "elapsed_s": 100.,
                       "model_requests": 0 if arm == "B0" else 10, "technically_evaluable": True}
                if scan == 304 and arm == affected_arm:
                    if state == "not_launched_cutoff":
                        row.update(outcome=state, endpoint=False, elapsed_s=0., model_requests=0)
                    else:
                        row.update(record_status=state, outcome=state, endpoint=None)
                rows.append(row)
    # analyze_main counts cutoff records as terminal before the R6-specific
    # reclassification; this reproduces the inherited completion/pairing bug.
    inherited = {"complete": state != "unrun", "illustrative_pairs": {"A7": "inherited"},
                 "trials": rows, "acquisitions": [
                     {"scan": scan, "calibrant": "CeO2", "A1": 1., "A7": 0., "B0": 0.}
                     for scan in (261, 304)]}
    monkeypatch.setattr(analyze_r6.analyze_main, "analyze", lambda *args: inherited)
    report = analyze_r6.analyze(tmp_path)
    cutoff_count = (1 if affected_arm == "B0" else 2) if state == "not_launched_cutoff" else 0
    assert report["complete"] is (affected_arm is None)
    assert report["accounting_complete"] is (state != "unrun")
    assert report["not_launched_cutoff"] == cutoff_count
    assert report["agent_attempts_complete"] is (affected_arm in (None, "B0"))
    assert (report["paired_comparisons"] is not None) is (affected_arm in (None, "B0"))
    assert (report["illustrative_pairs"] is not None) is (affected_arm is None)
    assert report["acquisitions"][0]["A7"] == 1.
    if affected_arm:
        assert report["acquisitions"][1][affected_arm] is None
        summary = report["arms"][affected_arm]
        assert summary["not_launched_cutoff"] == cutoff_count
        assert summary["attempted_terminal"] == (1 if affected_arm == "B0" else 2)
        assert summary["rate_all_scheduled"] == .5
        assert summary["attempted_cost"]["elapsed_s"]["median"] == 100.
        assert "excluding staging" in summary["attempted_cost"]["elapsed_s"]["description"]


def test_calibration_provenance_does_not_relabel_template_phase(tmp_path):
    from _calibrate_runner import phase_provenance
    param = tmp_path / "params.txt"
    param.write_text("# LaB6\nSpaceGroup 221\nLatticeConstant 4.15689 4.15689 4.15689 90 90 90 # template\ntz 1\n")
    assert phase_provenance(param) == ["SpaceGroup 221", "LatticeConstant 4.15689 4.15689 4.15689 90 90 90"]
