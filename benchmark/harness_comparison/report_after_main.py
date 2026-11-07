#!/usr/bin/env python3
"""Wait for an existing main supervisor, then export its saved outcomes once.

This never starts or restarts experimental trials. A failed/dead supervisor gets
an incomplete accounting report, without comparative estimates or figures.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--baseline-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--state", required=True, type=Path)
    args = parser.parse_args()
    for path in (args.output, args.state):
        if any(path.resolve().is_relative_to(root.resolve()) for root in (args.root, args.baseline_root)):
            parser.error("Reporting output/state must be outside frozen studies")
    args.state.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive receipt creation prevents an accidental second reporting watcher.
    with args.state.open("x") as stream:
        json.dump({"status": "starting", "pid": os.getpid()}, stream)
    def update(**changes):
        temporary = args.state.with_suffix(".tmp")
        temporary.write_text(json.dumps(dict(changes, pid=os.getpid(), updated_unix=time.time()), indent=2)+"\n")
        temporary.replace(args.state)
    try:
        while True:
            launch = json.loads((args.root/"setup/main-launch.json").read_text())
            status = launch["status"]
            if status in ("completed", "failed"):
                break
            try:
                os.kill(launch["supervisor_pid"], 0)
            except ProcessLookupError:
                status = "supervisor_absent"
                break
            progress = args.root/"results/main/progress.json"
            progress = json.loads(progress.read_text()) if progress.exists() else {}
            update(status="waiting_for_main", completed=progress.get("completed", 0),
                   scheduled=progress.get("scheduled"), main_supervisor_pid=launch["supervisor_pid"])
            time.sleep(30)
        update(status="exporting", main_status=status)
        command = [sys.executable, str(Path(__file__).with_name("analyze_main.py")),
                   "--root", str(args.root), "--baseline-root", str(args.baseline_root),
                   "--output", str(args.output)]
        subprocess.run(command, check=True)
        report = json.loads((args.output/"analysis.json").read_text())
        update(status="completed", cohort_complete=report["complete"], output=str(args.output),
               scientific_review_required=True)
    except Exception as exc:
        update(status="failed", error=f"{type(exc).__name__}: {exc}")
        raise


if __name__ == "__main__":
    main()
