"""Presentation of powder outcomes; scientific decisions belong to the engine.

Keep scheduler state, execution failure and configured scientific checks separate.
Raw results are retained, including failed attempts, but only completed results
contribute to numerical summaries. No qualification is inferred here.
"""
from __future__ import annotations

import math


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def records(manifest):
    """Normalize durable jobs, legacy result lists and individual fit summaries."""
    if isinstance(manifest.get("jobs"), list):
        return [job for job in manifest["jobs"] if isinstance(job, dict)]
    if isinstance(manifest.get("results"), list):
        return [{"id": str(item.get("job_id", item.get("data_file", i))),
                 "state": item.get("execution_status", item.get("status", "completed")),
                 "error": item.get("error"), "result": item}
                for i, item in enumerate(manifest["results"]) if isinstance(item, dict)]
    result = manifest.get("result", manifest)
    if not isinstance(result, dict) or not any(key in result for key in
            ("Rwp", "joint_Rwp_pct", "gpx_path", "scientific_status", "execution_status", "error")):
        return []
    # Evidence bundles and advisory trials are not completed refinement jobs.
    if "accepted" in result or "proposals" in result or "panels" in result:
        return []
    return [{"id": manifest.get("job_id", "pattern"),
             "state": manifest.get("state", manifest.get("status", result.get("execution_status", "unknown"))),
             "error": manifest.get("error"), "result": result}]


def execution_state(job):
    result = job.get("result") or {}
    states = {job.get("state"), result.get("execution_status"), result.get("status")}
    if "cancelled" in states:
        return "cancelled"
    if states & {"failed", "error", "execution_error"} or result.get("error"):
        return "failed"
    # A retry may carry an earlier error string; active scheduler state wins.
    if job.get("state") in ("queued", "submitted", "running", "pending"):
        return "pending"
    if states & {"completed", "complete", "success"}:
        return "completed"
    return "unknown"


def residual(job):
    if execution_state(job) != "completed":
        return None
    result = job.get("result") or {}
    for key in ("Rwp", "joint_Rwp_pct"):
        if finite(result.get(key)):
            return result[key]
    return None


def outcome_summary(manifest):
    jobs = records(manifest)
    summary = dict(total=len(jobs), completed=0, failed=0, cancelled=0, pending=0,
                   unknown=0, blocked=0, provisional=0, scientific_unavailable=0,
                   scientific_other=0, finite_Rwp_completed=0, missing_Rwp_completed=0)
    for job in jobs:
        state = execution_state(job)
        summary[state] += 1
        if state != "completed":
            continue
        science = (job.get("result") or {}).get("scientific_status")
        key = science if science in ("blocked", "provisional") else (
            "scientific_unavailable" if science in (None, "", "unavailable") else "scientific_other")
        summary[key] += 1
        summary["finite_Rwp_completed" if residual(job) is not None else "missing_Rwp_completed"] += 1
    return summary


def fraction_groups(result):
    """Keep joint-project fractions scoped to their histogram."""
    reports = result.get("phase_fraction_reports") or []
    if reports:
        return [(f"histogram {report.get('histogram', i)}", report.get("values") or {})
                for i, report in enumerate(reports) if isinstance(report, dict)]
    return [("modeled phases", result.get("weight_fractions") or {})]
