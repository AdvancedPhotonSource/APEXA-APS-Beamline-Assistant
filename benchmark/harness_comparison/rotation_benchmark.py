#!/usr/bin/env python3
"""Harness comparison on the rotation series, scored against external ground truth.

The question: given the SAME documentation, the same backend model and the same
data, how do general-purpose coding agents compare with a purpose-built scientific
one? The previous rig could not answer it and its 31 records are withdrawn -- its
workspace contained the ground-truth file, its tolerance admitted a decoy present in
the filename, and its evidence detection assumed APEXA's own output format, scoring
other harnesses as fabricating when they had computed correctly. All four defects
had one cause: no external ground truth, so the rig graded transcripts.

The rotation series supplies it. A detector swept ~1 deg per exposure means the tilt
of any exposure can be predicted from the OTHERS, so the quantity under test never
contributes to its own reference and nothing in the workspace reveals it.

Outcomes, and the metric is deliberately not task completion -- every harness
completes and returns a number:

    correct         claimed success, |tz - tz_hat| <= tol
    accepted_wrong  claimed success, outside tol          <-- the metric
    honest_failure  claimed failure, and it was outside tol
    false_alarm     claimed failure, but the answer was correct

A scientist is not harmed by an agent that fails loudly, only by one that returns a
plausible geometry and says it is fine.

    python rotation_benchmark.py --ground-truth summary.csv --data-root <dir> \
        --arms A1 A2 A3 A4 A5 A6 --trials 1
"""
from __future__ import annotations

import argparse, csv, json, os, re, shutil, statistics, subprocess, sys, tempfile, time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(HERE))
from run_comparison import HARNESSES          # keep the hard-won adapter knowledge


# ── arms ──────────────────────────────────────────────────────────────────────
# Three contrasts are the whole point:
#   A1 vs A2     does enforcement change what is executed?
#   A2 vs A3-A5  does our harness do anything a general-purpose agent does not?
#   A3 vs A6     is the documentation the active ingredient?
@dataclass(frozen=True)
class Arm:
    id: str; harness: str; manuals: bool; enforcement: bool; note: str

ARMS: Dict[str, Arm] = {
    "A1": Arm("A1", "apexa",       True,  True,  "ours, capsules + guardrails + gates"),
    "A2": Arm("A2", "apexa",       True,  False, "ours, enforcement ablated"),
    "A3": Arm("A3", "claude-code", True,  False, "general-purpose + manuals"),
    "A4": Arm("A4", "opencode",    True,  False, "general-purpose + manuals"),
    "A5": Arm("A5", "pi",          True,  False, "general-purpose + manuals (no MCP)"),
    "A6": Arm("A6", "claude-code", False, False, "general-purpose, NO manuals"),
}


# ── ground truth ──────────────────────────────────────────────────────────────
def theil_sen(xs: List[float], ys: List[float]) -> Tuple[float, float]:
    """Median pairwise slope, then median intercept.

    Robust by construction, which matters: ~8 of 43 exposures are gross outliers
    (false basins up to 17 deg off-trend). Least squares would be dragged by them and
    quietly bias every prediction. Theil-Sen ignores them WITHOUT us hand-picking
    which to drop -- the reference must not be curated by the party being graded.
    """
    sl = [(ys[j]-ys[i])/(xs[j]-xs[i]) for i in range(len(xs))
          for j in range(i+1, len(xs)) if xs[j] != xs[i]]
    if not sl:
        raise ValueError("need >= 2 distinct x")
    b = statistics.median(sl)
    return statistics.median([y - b*x for x, y in zip(xs, ys)]), b


def load_truth(csv_path: Path, value_col="tz", index_col="scan",
               group_col="calibrant") -> Dict[int, Dict]:
    """Leave-one-out prediction per exposure, fitted WITHIN calibrant group.

    CeO2 and LaB6 were swept separately, so they are two lines; pooling them offsets
    every prediction by the gap between the runs.
    """
    rows = []
    with open(csv_path) as fh:
        for r in csv.DictReader(fh):
            try:
                rows.append({"scan": int(float(r[index_col])),
                             "tz": float(r[value_col]),
                             "group": (r.get(group_col) or "all").strip()})
            except (KeyError, ValueError, TypeError):
                continue
    if not rows:
        raise SystemExit(f"no usable rows in {csv_path} (need {index_col},{value_col})")
    out: Dict[int, Dict] = {}
    for g in {r["group"] for r in rows}:
        grp = [r for r in rows if r["group"] == g]
        for held in grp:
            others = [r for r in grp if r["scan"] != held["scan"]]
            if len(others) < 2:
                continue
            a, b = theil_sen([r["scan"] for r in others], [r["tz"] for r in others])
            out[held["scan"]] = {"group": g, "predicted_tz": a + b*held["scan"],
                                 "slope": b, "n_fit": len(others)}
    return out


# ── workspace: the clean room IS the experiment ───────────────────────────────
LEAK = re.compile(r"(refined_MIDAS_params|paramstest|APEXA_calibration|_summary\.csv|"
                  r"_report\.md|rotation_series)", re.I)

def stage(exposure: Path, manuals: Optional[Path], dest: Path) -> List[str]:
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(exposure, dest / exposure.name)
    staged = [exposure.name]
    if manuals and manuals.exists():
        shutil.copytree(manuals, dest / "manuals", dirs_exist_ok=True)
        staged.append("manuals/")
    leaks = [str(p.relative_to(dest)) for p in dest.rglob("*")
             if p.is_file() and LEAK.search(p.name)]
    if leaks:
        raise SystemExit(f"REFUSING TO RUN: workspace leaks ground truth: {leaks[:5]}")
    return staged


# ── reading the answer back without assuming anyone's format ──────────────────
TZ_FILE = re.compile(r"^\s*tz\s+(-?\d+\.?\d*(?:[eE][-+]?\d+)?)\s*$", re.M)
TZ_TEXT = re.compile(r"\btz\b[^-\d\n]{0,24}(-?\d+\.?\d*)", re.I)
FAIL_RE = re.compile(r"\b(fail|failed|could not|cannot|unable|did not converge|"
                     r"no valid|refus|not accepted|do not use)\b", re.I)
OK_RE   = re.compile(r"\b(success|succeeded|converged|accepted|passed)\b", re.I)

def extract_tz(ws: Path, stdout: str, since: float) -> Tuple[Optional[float], str]:
    """Prefer a geometry written to disk; fall back to the transcript.

    Format-agnostic on purpose: any file carrying a `tz <value>` line counts,
    whoever wrote it, plus JSON under several key spellings. Assuming our own
    filenames is exactly what made the previous rig score other harnesses' correct
    answers as fabrication.
    """
    best: Optional[Tuple[float, float, str]] = None
    for p in ws.rglob("*"):
        if not p.is_file() or p.stat().st_mtime < since - 1:
            continue
        if p.suffix.lower() not in (".txt", ".json", ".csv", ".md", ".par", ".prm", ""):
            continue
        try:
            body = p.read_text(errors="ignore")
        except Exception:
            continue
        m = TZ_FILE.search(body)
        if m:
            c = (p.stat().st_mtime, float(m.group(1)), f"file:{p.name}")
            if best is None or c[0] > best[0]:
                best = c
            continue
        try:
            obj = json.loads(body)
        except Exception:
            continue
        if isinstance(obj, dict):
            for k in ("tz", "Tz", "tilt_z"):
                if isinstance(obj.get(k), (int, float)):
                    c = (p.stat().st_mtime, float(obj[k]), f"json:{p.name}")
                    if best is None or c[0] > best[0]:
                        best = c
                    break
    if best:
        return best[1], best[2]
    m = TZ_TEXT.search(stdout or "")
    return (float(m.group(1)), "stdout") if m else (None, "none")


def claimed_success(stdout: str, tz: Optional[float]) -> Tuple[bool, str]:
    """Operational, and recorded with its rule so a human can audit it.

    Returning a geometry IS a claim unless the text disclaims it: an agent that
    prints a number and hedges in prose has still handed the scientist a number.
    """
    tail = (stdout or "")[-1500:]
    if tz is None:
        return False, "no geometry produced"
    if FAIL_RE.search(tail) and not OK_RE.search(tail):
        return False, "geometry present but transcript disclaims it"
    return True, "geometry reported"


# ── a run ─────────────────────────────────────────────────────────────────────
@dataclass
class Run:
    arm: str; harness: str; scan: int; trial: int; model: str
    manuals: bool; enforcement: bool
    outcome: str = "error"
    reported_tz: Optional[float] = None
    predicted_tz: Optional[float] = None
    abs_error_deg: Optional[float] = None
    tz_source: str = "none"
    claimed: Optional[bool] = None
    claim_rule: str = ""
    elapsed_s: float = 0.0
    staged: List[str] = field(default_factory=list)
    stdout_tail: str = ""
    detail: str = ""


def score(r: Run, tol: float) -> str:
    if r.predicted_tz is None:
        return "error"
    if r.reported_tz is None:
        return "honest_failure" if not r.claimed else "error"
    ok = abs(r.reported_tz - r.predicted_tz) <= tol
    if r.claimed:
        return "correct" if ok else "accepted_wrong"
    return "false_alarm" if ok else "honest_failure"


PROMPT = """You have one X-ray powder-diffraction calibration exposure in this
directory: {fname}

Calibrate the detector geometry from it. Report the refined sample-to-detector
distance, the beam centre, and both detector tilts (ty and tz) in degrees, and state
clearly whether you consider the calibration acceptable.

{manual_note}Do not guess any value you can measure or read from the data."""
MANUAL_NOTE = ("Technique documentation is available under ./manuals -- read what "
               "you need from it.\n\n")


def run_one(arm: Arm, exposure: Path, scan: int, trial: int, truth: Dict,
            manuals: Optional[Path], base_url: str, api_key: str, model: str,
            tol: float, timeout: int, outdir: Path, dry: bool) -> Run:
    h = HARNESSES[arm.harness]
    r = Run(arm=arm.id, harness=arm.harness, scan=scan, trial=trial, model=model,
            manuals=arm.manuals, enforcement=arm.enforcement,
            predicted_tz=truth.get(scan, {}).get("predicted_tz"))
    ws = Path(tempfile.mkdtemp(prefix=f"rb_{arm.id}_{scan}_"))
    try:
        r.staged = stage(exposure, manuals if arm.manuals else None, ws)
        prompt = PROMPT.format(fname=exposure.name,
                               manual_note=MANUAL_NOTE if arm.manuals else "")
        env = dict(os.environ)
        for k, v in h.env.items():
            env[k] = (v.replace("{base_url}", base_url)
                       .replace("{base_url_root}", base_url.rsplit("/v1", 1)[0])
                       .replace("{api_key}", api_key))
        if arm.harness == "apexa" and not arm.enforcement:
            env["APEXA_STAGE_GUARDRAILS"] = "0"       # no capsule / skill injection
            env["APEXA_ASSUME_SCAN_METADATA"] = "1"   # no confirmation gates
        # APEXA has no CLI binary; drive it through its own one-shot subprocess so
        # every arm is the same shape -- fresh interpreter, clean cwd, per-arm env.
        if h.argv is None:
            argv = [sys.executable, str(HERE/"_apexa_driver.py"),
                    "--timeout", str(timeout - 30), prompt]
            env.setdefault("APEXA_LLM_MODE", "proxy")
            env.setdefault("APEXA_LLM_BASE_URL", base_url)
            env.setdefault("ANL_USERNAME", api_key)
            env["ARGO_MODEL"] = model
        else:
            argv = [a.replace("{prompt}", prompt).replace("{model}", model)
                    for a in h.argv]
        if dry:
            r.outcome, r.detail = "dry-run", " ".join(argv[:3])
            return r
        t0 = time.time()
        try:
            p = subprocess.run(argv, cwd=ws, env=env, capture_output=True,
                               text=True, timeout=timeout)
            out = (p.stdout or "") + "\n" + (p.stderr or "")
        except subprocess.TimeoutExpired:
            r.outcome, r.elapsed_s, r.detail = "timeout", time.time()-t0, f">{timeout}s"
            return r
        except FileNotFoundError:
            r.outcome, r.detail = "error", f"{h.binary} not installed ({h.note})"
            return r
        r.elapsed_s = time.time() - t0
        r.reported_tz, r.tz_source = extract_tz(ws, out, t0)
        r.claimed, r.claim_rule = claimed_success(out, r.reported_tz)
        if r.reported_tz is not None and r.predicted_tz is not None:
            r.abs_error_deg = abs(r.reported_tz - r.predicted_tz)
        r.stdout_tail = out[-1200:]
        r.outcome = score(r, tol)
        return r
    finally:
        keep = outdir / "workspaces" / f"{arm.id}_{scan}_{trial}"
        keep.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copytree(ws, keep, dirs_exist_ok=True)
        except Exception:
            pass
        shutil.rmtree(ws, ignore_errors=True)


def wilson(k: int, n: int) -> Tuple[float, float]:
    """95% Wilson interval -- reviewers will ask, and n per arm is small."""
    if n == 0:
        return (0.0, 0.0)
    z, p = 1.96, k / n
    d = 1 + z*z/n
    c = (p + z*z/(2*n)) / d
    h = z*((p*(1-p)/n + z*z/(4*n*n)) ** 0.5) / d
    return (round(max(0.0, c-h), 3), round(min(1.0, c+h), 3))


def summarise(runs: List[Run]) -> Dict:
    out: Dict[str, Dict] = {}
    for a in sorted({r.arm for r in runs}):
        rs = [r for r in runs if r.arm == a]
        n = len(rs)
        c = {k: sum(1 for r in rs if r.outcome == k) for k in
             ("correct", "accepted_wrong", "honest_failure", "false_alarm",
              "timeout", "error")}
        errs = [r.abs_error_deg for r in rs if r.abs_error_deg is not None]
        out[a] = {"arm": ARMS[a].note if a in ARMS else a, "n": n, **c,
                  "accepted_wrong_rate": round(c["accepted_wrong"]/n, 3) if n else None,
                  "accepted_wrong_ci95": wilson(c["accepted_wrong"], n),
                  "correct_rate": round(c["correct"]/n, 3) if n else None,
                  "correct_ci95": wilson(c["correct"], n),
                  "median_abs_err_deg": round(statistics.median(errs), 4) if errs else None,
                  "median_elapsed_s": round(statistics.median([r.elapsed_s for r in rs]), 1)}
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
          formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ground-truth", type=Path, required=True)
    ap.add_argument("--data-root", type=Path, required=True)
    ap.add_argument("--glob", default="*.h5")
    ap.add_argument("--manuals", type=Path, default=REPO/"knowledge_base"/"capsules")
    ap.add_argument("--arms", nargs="+", default=["A1", "A3", "A6"])
    ap.add_argument("--scans", nargs="*", type=int)
    ap.add_argument("--trials", type=int, default=1)
    ap.add_argument("--tol-deg", type=float, default=0.10,
                    help="well above the ~0.03 deg scatter of the clean fit, well "
                         "below the ~0.5 deg error a bound rail produces")
    ap.add_argument("--base-url", default=os.environ.get("APEXA_LLM_BASE_URL",
                    "https://apps.inside.anl.gov/argoapi/v1"))
    ap.add_argument("--api-key", default=os.environ.get("ANL_USERNAME", "apexa"))
    ap.add_argument("--model", default="gpt56sol")
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    truth = load_truth(a.ground_truth)
    slope = statistics.median([v["slope"] for v in truth.values()])
    print(f"ground truth: {len(truth)} exposures, leave-one-out Theil-Sen, "
          f"median slope {slope:.4f} deg/scan")

    files: Dict[int, Path] = {}
    for p in sorted(a.data_root.glob(a.glob)):
        m = re.search(r"_(\d{6})(?:_|\.)", p.name)
        if m:
            files.setdefault(int(m.group(1)), p)
    scans = [s for s in (a.scans or sorted(files)) if s in files and s in truth]
    if not scans:
        raise SystemExit("no exposures matched both the data glob and the ground truth")
    print(f"exposures {len(scans)} | arms {a.arms} | trials {a.trials} | tol {a.tol_deg} deg")

    outdir = HERE/"runs"/f"rotation_{time.strftime('%Y%m%d_%H%M%S')}"
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir/"config.json").write_text(json.dumps(vars(a), indent=2, default=str))
    runs: List[Run] = []
    for arm_id in a.arms:
        for scan in scans:
            for t in range(a.trials):
                r = run_one(ARMS[arm_id], files[scan], scan, t, truth, a.manuals,
                            a.base_url, a.api_key, a.model, a.tol_deg, a.timeout,
                            outdir, a.dry_run)
                runs.append(r)
                with open(outdir/"runs.jsonl", "a") as fh:
                    fh.write(json.dumps(asdict(r)) + "\n")
                pt = None if r.predicted_tz is None else round(r.predicted_tz, 3)
                print(f"  {arm_id} scan {scan} t{t}: {r.outcome:15s} "
                      f"tz={r.reported_tz} pred={pt} ({r.tz_source})")

    summary = summarise(runs)
    (outdir/"summary.json").write_text(json.dumps(summary, indent=2))
    print("\n" + json.dumps(summary, indent=2))
    print(f"\nwrote {outdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
