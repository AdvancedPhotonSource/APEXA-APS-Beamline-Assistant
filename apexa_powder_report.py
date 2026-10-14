"""Scientific result interpretation for APEXA's shared report format."""
from __future__ import annotations

import base64
import io
import math


def records(manifest):
    if isinstance(manifest.get("jobs"), list):
        return manifest["jobs"]
    return [{"id": manifest.get("job_id", "pattern"), "state": manifest.get("status", "unknown"),
             "result": manifest.get("result", manifest)}]


def populate(spec, manifest, *, with_plates=True):
    from apexa_report import Finding, Tile, Action, Plate
    jobs = records(manifest)
    completed = sum(j.get("state") in ("completed", "success", "complete") for j in jobs)
    failed = sum(j.get("state") in ("failed", "error", "cancelled") for j in jobs)
    results = [j.get("result", {}) for j in jobs]
    blocked = sum(r.get("scientific_status") == "blocked" for r in results)
    qualified = sum(r.get("scientific_status") == "qualified" for r in results)
    spec.title = "Powder refinement report"
    spec.verdict = "fail" if failed == len(jobs) or blocked == len(jobs) else "pass" if qualified == len(jobs) else "look"
    spec.verdict_note = (f"{completed}/{len(jobs)} jobs completed; {failed} execution failures; "
                         f"{blocked} scientifically blocked; {qualified} qualified.")
    spec.tiles = [Tile("Completed", f"{completed}/{len(jobs)}"), Tile("Execution failures", str(failed)),
                  Tile("Scientifically blocked", str(blocked)), Tile("Qualified", str(qualified))]
    spec.findings = [Finding("Execution and scientific status", spec.verdict_note,
                            "solid" if spec.verdict == "pass" else "warn", "refinement")]
    for job, result in zip(jobs, results):
        name = str(job.get("id", "pattern"))
        status = result.get("scientific_status", "unavailable")
        rwp = result.get("Rwp")
        detail = f"Scientific status: {status}."
        if isinstance(rwp, (int, float)) and math.isfinite(rwp):
            detail += f" Rwp: {rwp:.3f}%."
        detail += f" Stop: {result.get('stop_reason', 'unavailable')}."
        if job.get("error"):
            detail += " " + str(job["error"])
        coverage = result.get("evidence_coverage") or {}
        missing = [str(k) for k,v in coverage.items() if not isinstance(v, dict) or v.get("status") != "passed"]
        detail += " Evidence requiring review: " + (", ".join(missing) if coverage else "coverage unavailable") + "." if missing or not coverage else ""
        fractions = result.get("weight_fractions") or {}
        if fractions:
            detail += " Modeled crystalline mass fractions: " + ", ".join(
                f"{phase} {100*value:.2f}%" for phase,value in fractions.items() if isinstance(value, (int,float))) + "."
        spec.findings.append(Finding(name, detail, "solid" if status == "qualified" else "warn", "pattern"))
        spec.provenance.append({"name": name, "status": job.get("state", ""),
                                "elapsed_s": result.get("elapsed_seconds", "")})
    spec.findings.append(Finding("Uncertainty and model scope",
        "Phase fractions refer to modeled crystalline material. Conditional least-squares uncertainties "
        "exclude missing phases, amorphous content and calibration or model-selection errors. "
        "Inspect effective cells as well as reference cells when hydrostatic strain is active.", "warn", "scope"))
    spec.actions = [Action("Inspect diagnostic evidence",
        "Use powder_evidence on a completed project's gpx_path, then review the fitted pattern, "
        "residual windows, phase reflections and evidence coverage.",
        "A low residual alone does not establish scientific validity.")]
    numeric = [(i,r["Rwp"]) for i,r in enumerate(results) if isinstance(r.get("Rwp"), (int,float)) and math.isfinite(r["Rwp"])]
    if with_plates and numeric:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(8,3))
        ax.plot([i for i,_ in numeric], [v for _,v in numeric], "o-")
        ax.set(xlabel="Observation index", ylabel="Rwp (%)", title="Profile residuals across submitted observations")
        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=120)
        plt.close(fig)
        spec.plates.append(Plate("powder_residuals", "Profile residuals", base64.b64encode(buf.getvalue()).decode(),
                                  "Different instruments and uncertainty models can change the residual scale."))
    return spec
