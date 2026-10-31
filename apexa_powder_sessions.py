"""Detached, bounded diagnosis loops; numerical state belongs to the engine."""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from apexa_powder import advise, engine_call, write_json


def session_call(python, repo, action, payload, timeout=120):
    operations = {
        "create": "RefinementSession.create(payload['result_json'], payload['output_dir'], refine_atoms=payload['refine_atoms'])",
        "status": "RefinementSession(payload['session_dir']).status()",
        "evidence": "RefinementSession(payload['session_dir']).evidence()",
        "adopt": "RefinementSession(payload['session_dir']).adopt(payload['decision_json'], expected_revision=payload['expected_revision'])",
        "restore": "RefinementSession(payload['session_dir']).restore(payload['revision'], expected_revision=payload['expected_revision'])",
        "trial": "evaluate_proposal(Proposal(**payload['proposal']), payload['bundle'], payload['output_dir'], timeout=payload['timeout'], shadow=True)",
    }
    if action not in operations:
        raise ValueError("unknown session operation")
    code = ("from agentic_gsas2.sessions import RefinementSession\n"
            "from agentic_gsas2.trials import Proposal,evaluate_proposal\nprint(json.dumps(" + operations[action] + "))")
    return engine_call(python, repo, code, payload, timeout)


def improvement_status(session_dir):
    root = Path(session_dir).expanduser().resolve(strict=True)
    pointer = root / "improvement.json"
    if not pointer.exists():
        return {"state": "idle", "rounds": []}
    run = json.loads(pointer.read_text())
    path = Path(run["run_dir"]) / "status.json"
    result = json.loads(path.read_text())
    with (root / "improvement.lock").open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            pass
        else:
            if result["state"] in ("queued", "running"):
                result.update(state="interrupted", stop_reason="worker_exited", resumable=True)
    return result


def stop_improvement(session_dir):
    status = improvement_status(session_dir)
    if status["state"] in ("queued", "running"):
        write_json(Path(status["run_dir"]) / "cancel.json", {"requested": time.time()})
        return {**status, "stop_requested": True,
                "note": "Stops at the next operation boundary; an in-flight numerical trial may finish."}
    return status


def start_improvement(python, repo, session_dir, *, model, username, max_rounds=3,
                      max_wall_seconds=900., auto_adopt=True):
    if type(max_rounds) is not int or not 1 <= max_rounds <= 20:
        raise ValueError("max_rounds must be an integer from 1 to 20")
    if type(max_wall_seconds) not in (int, float) or not math.isfinite(max_wall_seconds) or not 30 <= max_wall_seconds <= 86400:
        raise ValueError("max_wall_seconds must be finite and from 30 to 86400")
    if type(auto_adopt) is not bool or not model or not username:
        raise ValueError("auto_adopt must be boolean and an image model and provider identity are required")
    root = Path(session_dir).expanduser().resolve(strict=True)
    session_call(python, repo, "status", {"session_dir": str(root)})
    with (root / "improvement.lock").open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("an improvement loop is already running for this session") from None
        run_id = uuid.uuid4().hex
        run_dir = root / "runs" / run_id
        run_dir.mkdir(parents=True)
        request = dict(python=str(python), repo=str(repo), session_dir=str(root), model=model,
                       username=username, max_rounds=max_rounds, max_wall_seconds=max_wall_seconds,
                       auto_adopt=auto_adopt, run_dir=str(run_dir), run_id=run_id)
        write_json(run_dir / "request.json", request)
        status = {"state": "queued", "run_id": run_id, "run_dir": str(run_dir), "rounds": [],
                  "auto_adopt": auto_adopt, "max_rounds": max_rounds, "max_wall_seconds": max_wall_seconds}
        write_json(run_dir / "status.json", status)
        write_json(root / "improvement.json", {"run_id": run_id, "run_dir": str(run_dir)})
        try:
            with (run_dir / "worker.log").open('w') as log:
                process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), str(run_dir / "request.json"),
                    "--lock-fd", str(lock.fileno())], stdout=log, stderr=subprocess.STDOUT,
                    cwd=str(Path(__file__).resolve().parent), pass_fds=(lock.fileno(),), start_new_session=True)
        except Exception as exc:
            write_json(run_dir / "status.json", {**status, "state": "failed", "error": str(exc)})
            raise
        # The child inherits the locked open file description. Closing this
        # parent's descriptor does not release the child's lifetime lock.
        return {**status, "pid": process.pid, "session_dir": str(root), "status": "submitted"}


async def run_improvement(request):
    from apexa_provider_openai import OpenAICompatProvider
    run_dir = Path(request["run_dir"])
    status_path = run_dir / "status.json"
    status = json.loads(status_path.read_text())
    started = time.monotonic()
    deadline = started + request["max_wall_seconds"]
    provider = None

    def checkpoint():
        status["elapsed_seconds"] = time.monotonic() - started
        status["updated"] = time.time()
        write_json(status_path, status)

    def boundary():
        if (run_dir / "cancel.json").exists():
            raise InterruptedError("cancelled")
        if time.monotonic() >= deadline:
            raise TimeoutError("wall_clock")

    async def call(action, payload, cap=120):
        boundary()
        remaining = deadline - time.monotonic()
        if remaining < 5:
            raise TimeoutError("wall_clock")
        return await asyncio.to_thread(session_call, request["python"], request["repo"], action,
                                      payload, min(cap, remaining))

    try:
        status.update(state="running", started=time.time())
        checkpoint()
        provider = OpenAICompatProvider(request["username"], request["model"])
        for number in range(1, request["max_rounds"] + 1):
            boundary()
            record = {"round": number, "state": "evidence", "trials": []}
            status["rounds"].append(record)
            checkpoint()
            bundle = await call("evidence", {"session_dir": request["session_dir"]})
            record.update(evidence_json=bundle["evidence_json"], revision=bundle["session_revision"], state="diagnosing")
            checkpoint()
            boundary()
            advice = await asyncio.wait_for(advise(provider, bundle), timeout=min(120, deadline-time.monotonic()))
            advice_path = run_dir / f"round_{number}_advice.json"
            write_json(advice_path, advice)
            record.update(advice_path=str(advice_path), proposals=advice["proposals"], state="evaluating")
            checkpoint()
            if not advice["proposals"]:
                record.update(state="abstained", reason=advice.get("abstention_reason"))
                status["stop_reason"] = "advisor_abstained"
                break
            for proposal in advice["proposals"]:
                boundary()
                budget = min(120., deadline-time.monotonic()-15)
                if budget < 5:
                    raise TimeoutError("wall_clock")
                trial = await call("trial", {"proposal": proposal, "bundle": bundle,
                    "output_dir": str(run_dir / "trials"), "timeout": budget}, cap=budget+10)
                record["trials"].append(trial)
                checkpoint()
            boundary()
            accepted = [t for t in record["trials"] if t.get("accepted") is True]
            if not accepted:
                record["state"] = "no_accepted_trial"
                status["stop_reason"] = "no_accepted_trial"
                break
            best = min(accepted, key=lambda t: t["Rwp_after"])
            record["selected_decision"] = best["decision_path"]
            if not request["auto_adopt"]:
                record["state"] = "awaiting_adoption"
                status["stop_reason"] = "review_requested"
                break
            adopted = await call("adopt", {"session_dir": request["session_dir"],
                "decision_json": best["decision_path"], "expected_revision": bundle["session_revision"]})
            record.update(state="adopted", adopted_revision=adopted["head"]["revision"],
                          scientific_status=adopted["scientific_status"])
            checkpoint()
        else:
            status["stop_reason"] = "max_rounds"
        status["state"] = "completed"
    except InterruptedError:
        status.update(state="cancelled", stop_reason="cancelled")
    except (TimeoutError, subprocess.TimeoutExpired):
        status.update(state="completed", stop_reason="wall_clock")
    except Exception as exc:
        status.update(state="failed", stop_reason="error", error=f"{type(exc).__name__}: {exc}")
    finally:
        status["finished"] = time.time()
        checkpoint()
        if provider is not None:
            await provider.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request")
    parser.add_argument("--lock-fd", type=int, required=True)
    args = parser.parse_args()
    with os.fdopen(args.lock_fd, 'a'):
        asyncio.run(run_improvement(json.loads(Path(args.request).read_text())))
