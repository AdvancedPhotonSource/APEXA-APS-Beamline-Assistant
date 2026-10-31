"""Scientific result interpretation for APEXA's shared report format."""
from __future__ import annotations

import base64
import io
from pathlib import Path

from apexa_powder_contract import execution_state, finite, fraction_groups, outcome_summary, records, residual


def populate(spec, manifest, *, with_plates=True):
    from apexa_report import Finding, Tile, Action, Plate
    jobs = records(manifest)
    counts = outcome_summary(manifest)
    total = counts["total"]
    spec.title = "Powder refinement report"
    # The current engine emits blocked/provisional, with no qualification path.
    spec.verdict = "fail" if total and counts["failed"] + counts["blocked"] == total else "look"
    spec.verdict_note = (
        f"{counts['completed']}/{total} jobs completed; {counts['failed']} execution failures; "
        f"{counts['cancelled']} cancelled; {counts['pending']} pending; {counts['unknown']} unknown. "
        f"Completed results: {counts['blocked']} blocked by configured checks, "
        f"{counts['provisional']} provisional, "
        f"{counts['scientific_unavailable'] + counts['scientific_other']} unavailable or other scientific status. "
        f"{counts['missing_Rwp_completed']} completed results lack a finite residual.")
    spec.tiles = [Tile("Completed", f"{counts['completed']}/{total}"),
                  Tile("Execution failures", str(counts["failed"])),
                  Tile("Blocked by checks", str(counts["blocked"])),
                  Tile("Provisional", str(counts["provisional"]))]
    spec.findings = [Finding("Execution and scientific status", spec.verdict_note, "warn", "refinement")]
    for job in jobs:
        result = job.get("result") or {}
        name = str(job.get("id", "pattern"))
        state = execution_state(job)
        status = result.get("scientific_status", "unavailable") if state == "completed" else "not assessed"
        rwp = residual(job)
        detail = f"Execution: {state}. Scientific status: {status}."
        if rwp is not None:
            detail += f" Rwp: {rwp:.3f}%."
        elif state == "completed":
            detail += " Rwp unavailable."
        detail += f" Stop: {result.get('stop_reason', 'unavailable')}."
        if job.get("error") or result.get("error"):
            detail += " " + str(job.get("error") or result.get("error"))
        coverage = result.get("evidence_coverage") or {}
        if not coverage:
            detail += " Evidence coverage unavailable."
        for check, evidence in coverage.items():
            evidence = evidence if isinstance(evidence, dict) else {"status": "unavailable"}
            detail += f" {check}: {evidence.get('status', 'unavailable')}."
            reason = evidence.get("reason") or evidence.get("error")
            if reason:
                detail += " " + str(reason) + "."
            if evidence.get("findings"):
                detail += " " + "; ".join(map(str, evidence["findings"])) + "."
        if state == "completed":
            for label, fractions in fraction_groups(result):
                values = [f"{phase} {100*value:.2f}%" for phase, value in fractions.items() if finite(value)]
                if values:
                    detail += f" Modeled crystalline mass fractions ({label}): " + ", ".join(values) + "."
        spec.findings.append(Finding(name, detail, "warn", "pattern"))
        recipe = result.get("recipe") or {}
        if recipe:
            spec.findings.append(Finding("Scientific recipe " + str(recipe.get("version", "")),
                str(recipe.get("selection", "")) + ". " + str(recipe.get("acceptance", "")) +
                ". Expert branches: " + ", ".join(recipe.get("expert_branches", [])) + ". " +
                str(recipe.get("validation", "")), "warn", "recipe"))
        if state == "completed":
            metrics = result.get("per_histogram_metrics") or []
            if len(jobs) == 1:
                if rwp is not None:
                    spec.tiles.append(Tile("Combined Rwp" if len(metrics) > 1 else "Rwp", f"{rwp:.3f}%"))
                if finite(result.get("GOF")):
                    spec.tiles.append(Tile("GOF", f"{result['GOF']:.3f}"))
            for h in metrics:
                if not finite(h.get("Rwp")):
                    continue
                label = str(h.get("name", f"Histogram {h.get('index', '?')}"))
                spec.findings.append(Finding(label,
                    f"Rwp {h['Rwp']:.3f}% from {h.get('Nobs', 'unavailable')} observations. "
                    "GOF, chi-squared and parameter counts in the saved histogram record describe the joint project.", "warn", "histogram"))
            lebail = result.get("lebail") or {}
            if finite(lebail.get("Rwp")):
                joint = result.get("mode") == "joint" or len(metrics) > 1
                spec.findings.append(Finding("Le Bail comparison scope",
                    (f"The saved Le Bail Rwp {lebail['Rwp']:.3f}% is a histogram-0 metric. "
                     "A combined Le Bail residual and compatible objective were not recorded, so this value "
                     "must not be compared with the combined Rietveld residual as a joint fit floor."
                     if joint and not (lebail.get("scope") == "all_histograms") else
                     f"Saved Le Bail Rwp: {lebail['Rwp']:.3f}%. Scope: {lebail.get('scope', lebail.get('metric_scope', 'single histogram'))}. "
                     "Comparable checks passed: " + str(lebail.get("comparable", False)) + ". "
                     "This is an achieved profile-fit residual, not a guaranteed global minimum."), "warn", "comparison"))
            history = result.get("history") or []
            for i, step in enumerate(history[:40]):
                before, after = step.get("Rwp_before"), step.get("Rwp_after")
                change = f"Rwp {before:.3f}% → {after:.3f}%. " if finite(before) and finite(after) else "Residual comparison unavailable. "
                outcome = ("Rolled back" if step.get("rolled_back") else
                           "Retained; residual worsened" if finite(before) and finite(after) and after > before else
                           "Retained; residual improved" if finite(before) and finite(after) and after < before else "Retained")
                released = ", ".join(map(str, step.get("refined") or [])) or "not recorded"
                spec.findings.append(Finding(f"Step {i + 1}: {step.get('category', 'refinement')}",
                    f"Ranked parameter: {step.get('param', 'unavailable')}. "
                    f"{'New independent variables' if 'newly_varied' in (step.get('checks') or {}) else 'Requested release'}: {released}. "
                    f"{change}{outcome}. {step.get('reason') or 'No rollback reason recorded.'}", "warn", "decision"))
            if len(history) > 40:
                spec.findings.append(Finding("Additional decisions", f"{len(history) - 40} more steps are retained in the saved result and canvas.", "warn", "history"))
            if with_plates and history:
                import matplotlib
                matplotlib.use("Agg")
                import matplotlib.pyplot as plt
                points = [(i + 1, s.get("Rwp_before") if s.get("rolled_back") else s.get("Rwp_after")) for i, s in enumerate(history)]
                points = [(i, value) for i, value in points if finite(value)]
                if points:
                    fig, ax = plt.subplots(figsize=(8, 3))
                    ax.plot([i for i, _ in points], [value for _, value in points], "o-", label="Retained fit")
                    rejected = [(i + 1, s.get("Rwp_after")) for i, s in enumerate(history) if s.get("rolled_back") and finite(s.get("Rwp_after"))]
                    if rejected:
                        ax.scatter([i for i, _ in rejected], [value for _, value in rejected], marker="x", color="red", label="Rolled-back trial")
                    ax.set(xlabel="Decision step", ylabel="Rwp (%)", title="Residual through the recorded decisions")
                    ax.legend()
                    fig.tight_layout()
                    buf = io.BytesIO()
                    fig.savefig(buf, format="png", dpi=120)
                    plt.close(fig)
                    spec.plates.append(Plate(f"decisions_{name}", "Refinement decision trajectory", base64.b64encode(buf.getvalue()).decode(),
                        "Recorded driver steps. Retention does not imply improvement or scientific qualification; final cleanup may change the final residual."))
            if not result.get("decision_stream"):
                spec.findings.append(Finding("Historical peak evidence unavailable",
                    "This run did not record per-step peak snapshots or complete candidate rankings. "
                    "The fit plots show the final state; they do not reconstruct the evidence used for past decisions.", "warn", "coverage"))
            if with_plates:
                paths = list(dict.fromkeys(([result["plot_path"]] if result.get("plot_path") else []) + list(result.get("plots") or [])))
                for i, path in enumerate(paths[:16]):
                    try:
                        source = Path(path).expanduser()
                        if source.suffix.lower() != ".png" or source.stat().st_size > 20 * 1024 * 1024:
                            raise ValueError("expected a PNG diagnostic below 20 MB")
                        blob = source.read_bytes()
                        if not blob.startswith(b"\x89PNG\r\n\x1a\n"):
                            raise ValueError("invalid PNG diagnostic")
                        label = metrics[i].get("name", f"Histogram {i}") if i < len(metrics) else f"Histogram {i}"
                        spec.plates.append(Plate(f"fit_{name}_{i}", str(label), base64.b64encode(blob).decode(),
                            "Final observed/calculated fit and residuals from the engine. Scientific status: " + status + "."))
                    except (OSError, ValueError, TypeError) as exc:
                        spec.findings.append(Finding("Fit plot unavailable", f"{path}: {exc}", "warn", "plot"))
        spec.provenance.append({"name": name, "status": job.get("state", ""),
                                "elapsed_s": result.get("elapsed_seconds", ""),
                                "input_identity": result.get("input_identity"),
                                "engine_provenance": result.get("provenance")})
    spec.findings.append(Finding("Uncertainty and model scope",
        "Blocked means a configured check failed; it is not an expert ground-truth label. "
        "Provisional may include unavailable checks and does not establish scientific validity. "
        "Phase fractions refer to modeled crystalline material. Conditional least-squares uncertainties "
        "exclude missing phases, amorphous content and calibration or model-selection errors. "
        "Fixed or missing uncertainties are not measured zero uncertainty. Inspect effective cells "
        "as well as reference cells when hydrostatic strain is active.", "warn", "scope"))
    spec.actions = [Action("Inspect diagnostic evidence",
        "Use powder_evidence on a completed project's gpx_path, then review the fitted pattern, "
        "residual windows, phase reflections and evidence coverage.",
        "A low residual alone does not establish scientific validity.")]
    numeric = [(i + 1, residual(job)) for i, job in enumerate(jobs) if residual(job) is not None]
    if with_plates and len(numeric) > 1:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(8, 3))
        ax.scatter([i for i, _ in numeric], [v for _, v in numeric])
        ax.set(xlabel="Submitted job index", ylabel="Rwp (%)", title="Profile residuals of completed jobs")
        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=120)
        plt.close(fig)
        spec.plates.append(Plate("powder_residuals", "Profile residuals", base64.b64encode(buf.getvalue()).decode(),
            "Each point is a completed job with a finite residual, including blocked results. "
            "Joint jobs can contain multiple histograms. Instrument and uncertainty models affect "
            "residual scales; unrelated observations are not a continuous series."))
    return spec
