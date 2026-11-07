"""Offline checks for study containment, audit evidence and qualification gates."""
import json
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
            model = self.server.model
            if payload.get("stream"):
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


def test_calibration_provenance_does_not_relabel_template_phase(tmp_path):
    from _calibrate_runner import phase_provenance
    param = tmp_path / "params.txt"
    param.write_text("# LaB6\nSpaceGroup 221\nLatticeConstant 4.15689 4.15689 4.15689 90 90 90 # template\ntz 1\n")
    assert phase_provenance(param) == ["SpaceGroup 221", "LatticeConstant 4.15689 4.15689 4.15689 90 90 90"]
