#!/usr/bin/env python3
"""Data-host Slurm entry point; policy is local, requests arrive on stdin."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from apexa_slurm import SlurmSupervisor, load_policy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--execute")
    args = parser.parse_args()
    supervisor = SlurmSupervisor(load_policy(args.config), args.config)
    if args.execute:
        supervisor.execute(args.execute)
        return
    raw = sys.stdin.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError("Request exceeds the worker budget")
    request = json.loads(raw)
    action = request.get("action")
    if action == "submit":
        job_id = request.get("job_id")
        try:
            result = supervisor.submit(request["arguments"], job_id=job_id)
        except (ValueError, OSError) as exc:
            # Only the worker can establish a refusal before durable intent.
            # An existing intent always remains uncertain, never 'nothing ran'.
            if not job_id or (supervisor.path(job_id) / "job.json").exists():
                raise
            result = {"tool": "midas_integrate_series", "supervisor": "slurm", "job_id": job_id,
                      "status": "error", "execution": "not_started", "nothing_was_run": True,
                      "scientific_verdict": "unassessed", "error": str(exc)}
    elif action in ("status", "cancel"):
        result = supervisor.status(request["job_id"], cancel=action == "cancel")
    else:
        raise ValueError("Unsupported worker operation")
    print(json.dumps(result, allow_nan=False))


if __name__ == "__main__":
    main()
