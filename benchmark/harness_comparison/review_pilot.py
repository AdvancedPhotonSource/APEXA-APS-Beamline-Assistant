#!/usr/bin/env python3
"""Review a saved pilot without changing its frozen runner or original scores.

Budget limits, transport failures and scientific decisions are separate evidence.
Only the explicitly adopted, hash-verified artifact is scored. This review checks
record consistency, not raw-image preprocessing or the truth of agent prose.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re


BUDGET_REASON = "Study request or wall-time budget exhausted"
STATUSES = {"accepted", "rejected", "provisional", "abstained"}


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def geometry_tz(raw):
    body = raw.decode("utf-8")
    values = re.findall(r"^\s*tz\s+(\S+)\s*$", body, re.M)
    if values:
        if len(values) != 1:
            raise ValueError("geometry must contain exactly one tz")
        value = float(values[0])
    else:
        obj = json.loads(body)
        values = [obj[k] for k in ("tz", "Tz", "tilt_z") if k in obj] if isinstance(obj, dict) else []
        if len(values) != 1:
            raise ValueError("geometry must contain exactly one top-level tz")
        value = values[0]
    if not finite(value):
        raise ValueError("tz must be finite and numeric")
    return float(value)


def key(record):
    return record["arm"], record["scan"], record["trial"]


class Evidence:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.hashes = {}

    def read(self, path):
        path = Path(path)
        relative = path.relative_to(self.root)
        if any((self.root.joinpath(*relative.parts[:i])).is_symlink()
               for i in range(1, len(relative.parts) + 1)):
            raise ValueError("evidence cannot traverse a symlink")
        if not path.resolve().is_relative_to(self.root):
            raise ValueError("evidence escapes its root")
        raw = path.read_bytes()
        self.hashes[relative.as_posix()] = hashlib.sha256(raw).hexdigest()
        return raw

    def json(self, path):
        return json.loads(self.read(path))

    def jsonl(self, path):
        return [json.loads(line) for line in self.read(path).splitlines() if line.strip()]


def adoption(ws, evidence, reference, tolerance):
    result = {"decision": None, "artifact_status": "missing_manifest",
              "adopted_tz": None, "relative_error_deg": None,
              "accepted_relative_consistency": None}
    manifest = ws / "result_manifest.json"
    if not manifest.exists() and not manifest.is_symlink():
        return result
    try:
        obj = evidence.json(manifest)
        if not isinstance(obj, dict) or type(obj.get("schema_version")) is not int or obj["schema_version"] != 1:
            raise ValueError("manifest requires schema_version 1")
        status = obj.get("scientific_status")
        if status not in STATUSES:
            raise ValueError("invalid scientific_status")
        result["decision"] = status
        relative = obj.get("artifact_path")
        if not relative and status != "accepted":
            result["artifact_status"] = "explicit_nonacceptance_without_artifact"
            return result
        if not isinstance(relative, str) or Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise ValueError("adopted artifact must be workspace-relative")
        if "manuals" in Path(relative).parts or relative == "result_manifest.json":
            raise ValueError("staged documentation or a manifest is not a geometry")
        artifact = ws / relative
        if artifact.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("geometry exceeds 2 MiB")
        raw = evidence.read(artifact)
        digest = hashlib.sha256(raw).hexdigest()
        if obj.get("artifact_sha256") != digest:
            raise ValueError("adopted artifact hash mismatch")
        value = geometry_tz(raw)
        result.update(artifact_status="verified", artifact_path=relative,
                      artifact_sha256=digest, adopted_tz=value)
        if finite(reference):
            result["relative_error_deg"] = abs(value - reference)
            if status == "accepted":
                result["accepted_relative_consistency"] = abs(value - reference) <= tolerance
    except (OSError, ValueError, TypeError, UnicodeError) as exc:
        result.update(artifact_status="invalid", artifact_error=str(exc))
    return result


def model_evidence(calls, requested_model):
    generations = [c for c in calls if c.get("event") == "generation"]
    budget = [c for c in calls if c.get("event") == "rejected_request"
              and c.get("http_status") == 429 and c.get("reason") == BUDGET_REASON]
    rejected = [c for c in calls if c.get("event") == "rejected_request" and c not in budget]
    transport = [c for c in generations if c.get("transport_error")]
    http_errors = [c for c in generations if c.get("http_status") not in (None, 200)]
    success = [c for c in generations if c.get("http_status") == 200 and not c.get("transport_error")]
    normalize = lambda s: "".join(c for c in str(s).lower() if c.isalnum())
    models = sorted({m for c in generations for m in c.get("response_models", [])})
    # Presence of matching metadata on a partial response does not prove a complete call.
    identity = bool(success) and all(c.get("response_models") and all(
        normalize(m) == normalize(requested_model) for m in c["response_models"])
        for c in success)
    reported_input = reported_output = usage_records = 0
    for call in generations:
        usage = call.get("usage") or {}
        if not usage:
            continue
        usage_records += 1
        if "prompt_tokens" in usage:  # OpenAI includes cached input in this total.
            reported_input += usage["prompt_tokens"]
        else:  # Anthropic reports new, cache-read and cache-write inputs separately.
            reported_input += sum(usage.get(k, 0) or 0 for k in
                                  ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
        reported_output += usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0
    return {"generation_requests": len(generations), "successful_responses": len(success),
            "budget_rejections": len(budget), "other_request_rejections": len(rejected),
            "transport_errors": len(transport), "http_errors": len(http_errors),
            "response_models": models, "successful_response_identity_matches": identity,
            "all_successes_have_usage": bool(success) and all(bool(c.get("usage")) for c in success),
            "usage_records": usage_records, "reported_input_tokens": reported_input,
            "reported_output_tokens": reported_output,
            "transport_error_types": dict(Counter(c["transport_error"] for c in transport))}


def review(root, tolerance=0.1):
    if not finite(tolerance) or tolerance <= 0:
        raise ValueError("tolerance must be finite and positive")
    evidence = Evidence(root)
    root = evidence.root
    entries = evidence.json(root / "schedule.json")["entries"]
    runs = evidence.jsonl(root / "runs.jsonl")
    scheduled = {key(e) for e in entries}
    recorded = {key(r): r for r in runs}
    if len(scheduled) != len(entries) or len(recorded) != len(runs):
        raise ValueError("duplicate scheduled or recorded trial")
    if not set(recorded).issubset(scheduled):
        raise ValueError("unscheduled trial record")
    rows = []
    for entry in entries:
        row = {k: entry[k] for k in ("schedule_index", "arm", "scan", "trial")}
        run = recorded.get(key(entry))
        if run is None:
            row["record_status"] = "no_completed_record"
            rows.append(row)
            continue
        tag = f"{run['arm']}_{run['scan']}_{run['trial']}"
        row.update(record_status="completed_record", harness=run["harness"],
                   original_outcome=run["outcome"], elapsed_s=run["elapsed_s"],
                   return_code=run.get("return_code"),
                   reference_tz_from_frozen_run=run.get("predicted_tz"))
        original = run.get("model_audit", {}).get("unqualified_scientific_outcome", run["outcome"])
        row["wall_timeout"] = original == "timeout" or run.get("return_code") == 124
        row.update(adoption(root / "workspaces" / tag, evidence, run.get("predicted_tz"), tolerance))
        call_path = root / "model_calls" / f"{tag}.jsonl"
        row["model"] = model_evidence(evidence.jsonl(call_path), run["model"]) if call_path.exists() else {"missing_log": True}
        row["budget_exhausted"] = bool(row["model"].get("budget_rejections"))
        rows.append(row)
    complete = [r for r in rows if r["record_status"] == "completed_record"]
    return {"schema_version": 1, "review_kind": "post_run_record_review_v1",
            "tolerance_deg": tolerance,
            "limits": ["No trials rerun; original runner outcomes preserved.",
                       "Budget exhaustion remains in the scheduled denominator, separate from transport errors.",
                       "No final manifest means no explicit adoption, even if a geometry exists.",
                       "Relative reference is the value saved by the frozen evaluator, not absolute truth.",
                       "Hashes verify saved identity, not raw preprocessing, solver execution or prose claims.",
                       "Reported token totals include available partial-response usage; missing usage is not zero cost.",
                       "No-completed-record does not distinguish running from unstarted; check live processes."],
            "counts": {"scheduled": len(rows), "completed_records": len(complete),
                       "without_completed_record": len(rows) - len(complete),
                       "accepted_with_verified_artifact": sum(r["decision"] == "accepted" and r["artifact_status"] == "verified" for r in complete),
                       "accepted_with_relative_consistency": sum(r["accepted_relative_consistency"] is True for r in complete),
                       "budget_exhausted": sum(r["budget_exhausted"] for r in complete),
                       "wall_timeouts": sum(r["wall_timeout"] for r in complete),
                       "trials_with_transport_errors": sum(bool(r["model"].get("transport_errors")) for r in complete)},
            "trials": rows, "source_sha256": evidence.hashes}


def markdown(report):
    lines = ["# Scientific pilot record review", "",
             "All scheduled trials are retained. Columns describe separate evidence and can overlap.", "",
             "| Trial | Recorded state | Final manifest / artifact | Relative error (degrees) | Budget rejections | Transport errors | Seconds |",
             "| --- | --- | --- | ---: | ---: | ---: | ---: |"]
    for r in report["trials"]:
        tag = f"{r['arm']}/{r['scan']}"
        if r["record_status"] != "completed_record":
            lines.append(f"| {tag} | No completed record | — | — | — | — | — |")
            continue
        state = "wall timeout" if r["wall_timeout"] else ("request budget exhausted" if r["budget_exhausted"] else f"exit {r['return_code']}")
        adopted = f"{r['decision'] or 'absent'} / {r['artifact_status']}"
        error = f"{r['relative_error_deg']:.6f}" if r["relative_error_deg"] is not None else "—"
        lines.append(f"| {tag} | {state} | {adopted} | {error} | {r['model'].get('budget_rejections', 'unknown')} | {r['model'].get('transport_errors', 'unknown')} | {r['elapsed_s']:.2f} |")
    lines += ["", *[f"- {x}" for x in report["limits"]], ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("output_dir", type=Path, help="New directory, outside the saved run")
    parser.add_argument("--tolerance", type=float, default=0.1)
    args = parser.parse_args()
    if args.output_dir.resolve().is_relative_to(args.run_dir.resolve()):
        parser.error("write the review outside the original run directory")
    report = review(args.run_dir, args.tolerance)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    (args.output_dir / "review.json").write_text(json.dumps(report, indent=2) + "\n")
    (args.output_dir / "review.md").write_text(markdown(report))
    print(json.dumps(report["counts"], indent=2))


if __name__ == "__main__":
    main()
