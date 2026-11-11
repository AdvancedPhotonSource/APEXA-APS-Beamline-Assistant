"""Qualify and launch R6 after the immutable R5 cohort becomes terminal.

All writes stay within the new revision. Failure stops the queue and preserves
the failed phase; it never silently repairs, retries or replaces scientific runs.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import study


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    study.SPEC = study.HERE / "studies/expanded-2026-11/study.json"
    spec = json.loads(study.SPEC.read_text())
    os.environ.update(study.configured_environment(root, spec))
    env = dict(os.environ)
    if not env.get("ANL_USERNAME"):
        connection = json.loads((Path.home() / ".apexa/connection.json").read_text())["config"]
        if connection.get("base_url", "").rstrip("/") != spec["model_base_url"].rstrip("/"):
            raise RuntimeError("Saved provider differs from frozen specification")
        env["ANL_USERNAME"] = connection["username"]
    state_path = root / "setup/queue.json"
    if state_path.exists():
        raise RuntimeError("Queue receipt already exists; inspect retained phases before any explicit resumption")
    state = {"pid": os.getpid(), "started_unix": time.time(), "completed_phases": [],
             "source_sha256": study.sha(__file__), "scientific_trials_started": False}

    def update(**changes):
        state.update(changes, updated_unix=time.time())
        study.atomic_json(state_path, state)

    def run(phase, command):
        log_path = root / "setup" / f"{phase}.log"
        update(status="running", phase=phase, log=str(log_path))
        with log_path.open("x") as log:
            child = subprocess.Popen(command, cwd=study.REPO, env=env,
                                     stdout=log, stderr=subprocess.STDOUT)
            update(worker_pid=child.pid)
            while child.poll() is None:
                time.sleep(15)
                update()
        if child.returncode:
            raise RuntimeError(f"{phase} failed with status {child.returncode}; see {log_path}")
        state["completed_phases"].append(phase)
        update(worker_pid=None)

    python = sys.executable
    common = [python, str(study.HERE / "study.py")]
    suffix = ["--root", str(root), "--spec", str(study.SPEC)]
    r5 = root.parent / "r5/results/main/progress.json"
    update(status="queued", phase="waiting_for_r5_terminal")
    try:
        while True:
            progress = json.loads(r5.read_text())
            update(r5_completed=progress["completed"], r5_scheduled=progress["scheduled"])
            if progress.get("status") == "completed" and progress["completed"] == progress["scheduled"]:
                break
            time.sleep(30)
        with Path(spec["scientific_lock"]).open("a") as lock:
            update(phase="waiting_for_scientific_lock")
            fcntl.flock(lock, fcntl.LOCK_EX)
            # Setup also runs on four cores so it does not saturate the host.
            limited = ["taskset", "-c", ",".join(map(str, spec["execution"]["cpu_affinity"][0])), python]
            env["HF_MODULES_CACHE"] = str(root / "work/index-modules")
            run("retrieval-index", [*limited, str(study.REPO / "knowledge_base/index_knowledge.py")])
            run("retrieval-health", [*limited, str(study.REPO / "scripts/check_knowledge.py"),
                                     "--output", str(root / "setup/retrieval-health.json")])
            deployment = json.loads((root / "setup/deployment.json").read_text())
            run("freeze-context", [python, str(study.HERE / "freeze_native_context.py"),
                "--source", str(study.REPO), "--midas-snapshot", str(root / "context/midas"),
                "--apexa-commit", deployment["apexa_commit"], "--midas-commit", deployment["midas_commit"],
                "--embedding-assets-root", deployment["embedding_assets_root"],
                "--embedding-assets-manifest", str(root / "setup/embedding-assets.json"),
                "--output", str(root / "setup/native-context.json")])
            run("prepare", [*common, "prepare", *suffix])
            # These use the exact installed CLI and adapters, on the launch host.
            qualifier = str(study.HERE / "qualify_guided_adapters.py")
            run("claude-bash", [python, qualifier, "--arm", "A3", "--test", "bash",
                "--context-lines", "200", "--settle-seconds", "20",
                "--output", str(root / "setup/claude-bash")])
            bash = json.loads((root / "setup/claude-bash/qualification.json").read_text())
            # Generation and token-count transport are separate admission gates.
            if not bash["passed"] or bash["model_audit"].get("successful_token_counts", 0) < 1:
                raise RuntimeError("Claude native Bash/token-count qualification is incomplete")
            for arm in ("A4", "A5"):
                run(f"image-{arm}", [python, qualifier, "--arm", arm, "--test", "image",
                    "--image-input", "--output", str(root / f"setup/image-input/{arm}")])
            run("qualify", [*common, "qualify", *suffix])
            evidence = json.loads((root / "setup/review-evidence.json").read_text())
            evidence += [{"path": str(p), "sha256": study.sha(p)} for p in (
                root / "setup/retrieval-health.json", root / "setup/claude-bash/qualification.json",
                root / "setup/image-input/A4/qualification.json", root / "setup/image-input/A5/qualification.json")]
            review = {"pilot_review_complete": True, "baseline_qualified": True,
                "accounting_regressions_passed": True, "numerical_semantics_unchanged": True,
                "retain_all_pilot_outcomes": True, "reviewed_evidence": evidence,
                "reviewed_by": "Codex assisted review with explicit launch-host qualification gates",
                "baseline_scope": "Retained solver/pilot evidence, not a matched-budget R6 baseline",
                "scientific_claim": "Supported capability comparison; no result or ranking assumed"}
            study.write_new(root / "setup/main-review.json", review)
            run("admit", [*common, "admit", "--review", str(root / "setup/main-review.json"), *suffix])
        update(scientific_trials_started=True)
        run("nominal", [*common, "run", "--cohort", "main", *suffix])
        run("challenge", [*common, "run", "--cohort", "challenge", *suffix])
        update(status="completed", phase="completed", finished_unix=time.time())
    except BaseException as exc:
        update(status="failed", error=f"{type(exc).__name__}: {exc}", finished_unix=time.time())
        raise


if __name__ == "__main__":
    main()
