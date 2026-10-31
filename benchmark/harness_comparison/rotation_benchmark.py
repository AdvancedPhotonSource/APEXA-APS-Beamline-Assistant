#!/usr/bin/env python3
"""Rotation-series harness development rig with artifact-based grading.

The default reference is a leave-one-out robust trend fitted from existing
calibrations. It measures series consistency, NOT independent absolute geometry
truth. Legacy outcome names (correct/accepted_wrong) must be interpreted against
that reference_kind; they do not establish scientific accuracy or a SOTA ranking.

The current arms use native adapter/tool configurations and bundled APEXA policy
interventions. They are not a tool-matched or one-factor enforcement experiment.
Historical results from run_comparison.py remain withdrawn. See README.md and the
2026-10-28 audit study plan before any publication comparison.

    python rotation_benchmark.py --ground-truth summary.csv --data-root <dir> \
        --value-col tz_deg --arms A1 A2 A3 A4 A5 A6 --trials 1 --dry-run
"""
from __future__ import annotations

import argparse, csv, hashlib, json, math, os, re, shutil, statistics, subprocess, sys, tempfile, time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(HERE))
from run_comparison import (HARNESSES, placeholders,   # hard-won adapter knowledge
                            write_opencode_config, ensure_pi_provider)
from apexa_knowledge_policy import (load_manifest, approved_file_bytes, KnowledgePolicyError,
                                    MANIFEST_ENV, DIGEST_ENV)


# ── arms ──────────────────────────────────────────────────────────────────────
# Native-system arms; A1/A2 change multiple policy/knowledge mechanisms.
# The legacy `enforcement` field is retained for saved-record compatibility,
# not as a claim of a pure enforcement ablation.
@dataclass(frozen=True)
class Arm:
    id: str; harness: str; manuals: bool; enforcement: bool; note: str

ARMS: Dict[str, Arm] = {
    "A1": Arm("A1", "apexa",       True,  True,  "APEXA native analysis tools; stage guidance on, inferred metadata confirmation required"),
    "A2": Arm("A2", "apexa",       True,  False, "APEXA bundled intervention: stage guidance off and inferred metadata accepted"),
    "A3": Arm("A3", "claude-code", True,  False, "general-purpose + manuals"),
    "A4": Arm("A4", "opencode",    True,  False, "general-purpose + manuals"),
    "A5": Arm("A5", "pi",          True,  False, "Pi + manuals; native MCP supported, no common MCP tools configured by this rig"),
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


def robust_line(xs: List[float], ys: List[float]) -> Tuple[float, float]:
    """Least median of squares, then least squares on its inliers.

    This replaced Theil-Sen as the reference after it failed on the real series.
    Theil-Sen breaks down at ~29% outliers; the CeO2 group has 6-8 bad
    exposures out of 21, past that point, so the false basins dragged its slope
    to ~0.83 deg/scan and it scored 13 correct calibrations as wrong by up to
    1.4 deg. LMedS breaks down at 50% (Rousseeuw 1984), recovers 0.9991
    deg/scan -- the commanded ~1 deg step -- and puts every accepted calibration
    within 0.005 deg of the line. Exhaustive over pairs: n is ~20, so 190 lines.
    """
    import itertools
    best = None
    for i, j in itertools.combinations(range(len(xs)), 2):
        if xs[i] == xs[j]:
            continue
        b = (ys[j] - ys[i]) / (xs[j] - xs[i])
        a = ys[i] - b * xs[i]
        med = statistics.median((y - (a + b * x)) ** 2 for x, y in zip(xs, ys))
        if best is None or med < best[0]:
            best = (med, a, b)
    if best is None:
        raise ValueError("need >= 2 distinct x")
    med, a, b = best
    # Rousseeuw's finite-sample scale; refit on points within 2.5 sigma of it.
    sigma = 1.4826 * (1 + 5 / max(len(xs) - 2, 1)) * med ** 0.5
    inl = [(x, y) for x, y in zip(xs, ys) if abs(y - (a + b * x)) <= 2.5 * max(sigma, 1e-9)]
    if len(inl) >= 3:
        mx = statistics.fmean(x for x, _ in inl)
        my = statistics.fmean(y for _, y in inl)
        den = sum((x - mx) ** 2 for x, _ in inl)
        if den:
            b = sum((x - mx) * (y - my) for x, y in inl) / den
            a = my - b * mx
    return a, b


def load_truth(csv_path: Path, value_col="tz", index_col="scan",
               group_col="calibrant") -> Dict[int, Dict]:
    """Leave-one-out prediction per exposure, fitted WITHIN calibrant group,
    with robust_line (LMedS) -- see its docstring for why not Theil-Sen.

    CeO2 and LaB6 were swept separately, so they are two lines; pooling them offsets
    every prediction by the gap between the runs. When supplied APEXA fits, this
    is an APEXA-derived consistency estimate, not independently measured truth.
    """
    rows = []
    with open(csv_path) as fh:
        for r in csv.DictReader(fh):
            try:
                value = float(r[value_col])
                if not math.isfinite(value):
                    continue
                rows.append({"scan": int(float(r[index_col])), "tz": value,
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
            a, b = robust_line([r["scan"] for r in others], [r["tz"] for r in others])
            out[held["scan"]] = {"group": g, "predicted_tz": a + b*held["scan"],
                                 "slope": b, "n_fit": len(others)}
    return out


# ── workspace: the clean room IS the experiment ───────────────────────────────
LEAK = re.compile(r"(refined_MIDAS_params|paramstest|APEXA_calibration|_summary\.csv|"
                  r"_report\.md|rotation_series)", re.I)

def _place_input(src: Path, dst: Path) -> str:
    """Put the exposure in the workspace without risking the original or the disk.

    Raw exposures are ~325 MB each (2880^2 x 21 frames); a plain copy per run,
    kept afterwards, came to ~12 GB for a 6x6 sweep on a laptop with 12 GB free.

    * APFS clone first (`cp -c`): an independent file that costs no space until
      written to -- the agent cannot touch the original.
    * Otherwise a real copy, but only with headroom to spare.
    * Never a symlink or hard link: agents open HDF5 files in write mode, and
      either would let one modify the original raw data.
    """
    import subprocess as _sp
    try:
        _sp.run(["cp", "-c", str(src), str(dst)], check=True, capture_output=True)
        return "clone"
    except Exception:
        pass
    need = src.stat().st_size
    free = shutil.disk_usage(dst.parent).free
    if free < 3 * need:
        raise SystemExit(
            f"REFUSING TO RUN: copying {src.name} ({need/2**20:.0f} MiB) would leave "
            f"{(free-need)/2**30:.1f} GiB free, and APFS cloning is unavailable here. "
            f"Free disk space, or keep the data on the same APFS volume as $TMPDIR.")
    shutil.copy2(src, dst)
    return "copy"


def stage(exposure: Path, manuals: Optional[Path], dest: Path, *, knowledge_policy: dict | None = None) -> List[str]:
    dest.mkdir(parents=True, exist_ok=True)
    _place_input(exposure, dest / exposure.name)
    staged = [exposure.name]
    if manuals is not None:
        if not manuals.is_dir():
            raise KnowledgePolicyError("Requested study manual directory does not exist")
        if knowledge_policy is None:
            raise KnowledgePolicyError("Manual staging requires an explicit reviewed knowledge manifest")
        manual_root = manuals.resolve()
        if not manual_root.is_relative_to(REPO):
            raise KnowledgePolicyError("Study manual root must be within the repository")
        for relative in sorted(knowledge_policy["files"]):
            path = REPO / relative
            if not path.resolve().is_relative_to(manual_root):
                continue
            if path.is_symlink():
                raise KnowledgePolicyError("Study manuals cannot be symlinks")
            content = approved_file_bytes(path, policy=knowledge_policy, root=REPO)
            if content is None:
                raise KnowledgePolicyError("Approved manual path does not resolve to its reviewed identity")
            target = dest / "manuals" / path.resolve().relative_to(manual_root)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            staged.append(str(target.relative_to(dest)))
        if len(staged) == 1:
            raise KnowledgePolicyError("The reviewed manifest approves no files under the requested manual directory")
    leaks = [str(p.relative_to(dest)) for p in dest.rglob("*")
             if p.is_file() and LEAK.search(p.name)]
    if leaks:
        raise SystemExit(f"REFUSING TO RUN: workspace leaks ground truth: {leaks[:5]}")
    return staged


# ── explicit artifact adoption and conservative claim classification ─────────
TZ_FILE = re.compile(r"^\s*tz\s+([-+]?\d+\.?\d*(?:[eE][-+]?\d+)?)\s*$", re.M)
TZ_TEXT = re.compile(r"\btz\b[^-\d\n]{0,24}(-?\d+\.?\d*)", re.I)
MANIFEST_NAME = "result_manifest.json"
RESULT_STATUSES = {"accepted", "rejected", "provisional", "abstained"}
_TEXT_SUFFIXES = {".txt", ".json", ".csv", ".md", ".par", ".prm", ""}
_SEED_NAME = re.compile(r"(?:^|[_\-.])(seed|initial|input|guess|template|candidate|trial|intermediate|checkpoint)(?:[_\-.]|$)", re.I)
MAX_ARTIFACT_BYTES = 2 * 1024 * 1024


@dataclass
class ResultEvidence:
    tz: Optional[float] = None
    source: str = "none"
    scientific_status: Optional[str] = None
    artifact_path: Optional[str] = None
    artifact_sha256: Optional[str] = None
    error: Optional[str] = None


def _artifact_bytes(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_ARTIFACT_BYTES:
        raise ValueError("artifact must be a regular, nonsymlink file no larger than 2 MiB")
    return path.read_bytes()


def workspace_snapshot(ws: Path) -> Dict[str, Optional[str]]:
    """Record staged text identities without reading/hashing raw detector files."""
    before = {}
    for path in ws.rglob("*"):
        if not path.is_file():
            continue
        key = path.relative_to(ws).as_posix()
        before[key] = None
        if path.suffix.lower() in _TEXT_SUFFIXES:
            try:
                before[key] = hashlib.sha256(_artifact_bytes(path)).hexdigest()
            except (OSError, ValueError):
                pass
    return before


def _geometry_value(data: bytes) -> Optional[float]:
    body = data.decode("utf-8", errors="replace")
    matches = TZ_FILE.findall(body)
    if len(matches) == 1:
        value = float(matches[0])
        return value if math.isfinite(value) else None
    if len(matches) > 1:
        return None  # several candidate values are not one adopted geometry
    try:
        obj = json.loads(body)
    except (ValueError, TypeError):
        return None
    if isinstance(obj, dict):
        values = [obj[k] for k in ("tz", "Tz", "tilt_z") if k in obj]
        if len(values) == 1 and isinstance(values[0], (int, float)) and not isinstance(values[0], bool):
            value = float(values[0])
            return value if math.isfinite(value) else None
    return None


def _fresh(path: Path, ws: Path, since: float, data: bytes, before) -> bool:
    key = path.relative_to(ws).as_posix()
    if before is not None and key in before:
        return before[key] is not None and hashlib.sha256(data).hexdigest() != before[key]
    return path.stat().st_mtime >= since - 1


def extract_result(ws: Path, stdout: str, since: float, before=None) -> ResultEvidence:
    """Use a verified explicit adoption, or one unique generated non-seed artifact.

    File identity is execution evidence only at the artifact level. Neither a
    fresh hash nor a manifest proves a numerical engine ran or validates physics.
    Transcript-only numbers are recorded as unverified, never graded as artifacts.
    """
    manifest = ws / MANIFEST_NAME
    if manifest.exists() or manifest.is_symlink():
        try:
            raw = _artifact_bytes(manifest)
            if not _fresh(manifest, ws, since, raw, before):
                raise ValueError("result manifest predates this trial or is unchanged from staging")
            record = json.loads(raw)
            if not isinstance(record, dict) or type(record.get("schema_version")) is not int or record.get("schema_version") != 1:
                raise ValueError("result manifest requires schema_version 1")
            status = record.get("scientific_status")
            if status not in RESULT_STATUSES:
                raise ValueError("invalid scientific_status")
            relative = record.get("artifact_path")
            if not relative and status in {"rejected", "abstained", "provisional"}:
                return ResultEvidence(source="manifest:no_artifact", scientific_status=status)
            if not isinstance(relative, str) or Path(relative).is_absolute():
                raise ValueError("artifact_path must be workspace-relative")
            path = ws / relative
            if not path.resolve().is_relative_to(ws.resolve()):
                raise ValueError("adopted artifact escapes the workspace")
            if path == manifest or "manuals" in path.relative_to(ws).parts:
                raise ValueError("a manifest or staged manual is not a generated geometry")
            data = _artifact_bytes(path)
            digest = hashlib.sha256(data).hexdigest()
            if record.get("artifact_sha256") != digest:
                raise ValueError("adopted artifact hash does not match manifest")
            if not _fresh(path, ws, since, data, before):
                raise ValueError("adopted artifact was not generated or changed by this trial")
            value = _geometry_value(data)
            if value is None:
                raise ValueError("adopted artifact does not contain one finite tz")
            return ResultEvidence(value, f"manifest:{relative}", status, relative, digest)
        except (OSError, ValueError, TypeError) as exc:
            return ResultEvidence(source="invalid_manifest", error=str(exc))

    candidates = []
    for path in ws.rglob("*"):
        relative = path.relative_to(ws).as_posix()
        if path.suffix.lower() not in _TEXT_SUFFIXES or "manuals" in path.relative_to(ws).parts or _SEED_NAME.search(path.stem):
            continue
        try:
            if not path.resolve().is_relative_to(ws.resolve()):
                continue
            data = _artifact_bytes(path)
            if not _fresh(path, ws, since, data, before):
                continue
            value = _geometry_value(data)
            if value is not None:
                kind = "json" if path.suffix.lower() == ".json" else "file"
                candidates.append(ResultEvidence(value, f"{kind}:{relative}", artifact_path=relative,
                                                  artifact_sha256=hashlib.sha256(data).hexdigest()))
        except (OSError, ValueError):
            continue
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        return ResultEvidence(source="ambiguous_artifacts", error="Multiple generated geometries; require explicit result_manifest.json adoption")
    if TZ_TEXT.search(stdout or ""):
        return ResultEvidence(source="unverified_transcript", error="A transcript number without an adopted/generated artifact is not executable evidence")
    return ResultEvidence()


def extract_tz(ws: Path, stdout: str, since: float, before=None) -> Tuple[Optional[float], str]:
    """Compatibility wrapper around the artifact evidence contract."""
    evidence = extract_result(ws, stdout, since, before)
    return evidence.tz, evidence.source


# Explicit rejection wins over historical positive solver messages. A clearly
# labeled final decision can supersede an earlier failed attempt. Conditional
# warnings such as "may fail on other frames" are not final rejection decisions.
_FINAL_SECTION = re.compile(r"(?:^|\n|[.!?]\s+)\s*(?:final\s+(?:decision|verdict|status|result)|scientific\s+status)\s*[:=-]\s*", re.I)
_REJECTION = re.compile(r"\b(?:do\s+not\s+use|not\s+(?:acceptable|accepted|valid|usable)|"
    r"validation\s+(?:has\s+)?failed|(?:calibration|geometry|result)\s+(?:was\s+|is\s+)?rejected|"
    r"rejected|abstained|provisional|did\s+not\s+converge|no\s+(?:valid|usable)\s+geometry|"
    r"cannot\s+provide\s+(?:a\s+)?(?:valid|usable)\s+geometry)\b", re.I)
_FAIL = re.compile(r"\b(?:failed|unable|could\s+not|refused)\b", re.I)
_ACCEPT = re.compile(r"\b(?:accepted|acceptable|success|succeeded|converged|passed|complete)\b", re.I)


def claimed_success(stdout: str, tz: Optional[float], scientific_status: Optional[str] = None) -> Tuple[bool, str]:
    if tz is None or not math.isfinite(tz):
        return False, "no verified finite geometry artifact"
    tail = (stdout or "")[-1500:]
    final = list(_FINAL_SECTION.finditer(tail))
    if final:
        tail = tail[final[-1].end():]
    if _REJECTION.search(tail):
        return False, "explicit final rejection/provisional decision overrides prior numerical success"
    if scientific_status is not None:
        return scientific_status == "accepted", f"manifest scientific_status={scientific_status}"
    if _FAIL.search(tail) and not _ACCEPT.search(tail):
        return False, "geometry present but transcript disclaims it"
    return True, "unique generated geometry reported; legacy prose decision"


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
    stderr_tail: str = ""
    return_code: Optional[int] = None
    reference_kind: str = "apexa_derived_leave_one_out_consistency"
    comparison_kind: str = "native_systems_with_bundled_ablation"
    scientific_status: Optional[str] = None
    artifact_path: Optional[str] = None
    artifact_sha256: Optional[str] = None
    policy_bundle: Dict[str, str] = field(default_factory=dict)


def score(r: Run, tol: float) -> str:
    if r.predicted_tz is None or not math.isfinite(r.predicted_tz) or not math.isfinite(tol) or tol <= 0:
        return "error"
    if r.reported_tz is not None and not math.isfinite(r.reported_tz):
        return "error"
    if r.reported_tz is None:
        return "honest_failure" if not r.claimed else "error"
    ok = abs(r.reported_tz - r.predicted_tz) <= tol
    if r.claimed:
        return "correct" if ok else "accepted_wrong"
    return "false_alarm" if ok else "honest_failure"


# Every arm gets exactly what the operator told APEXA in the ai_tune session
# (operator messages 9, 24, 30, 31, 40 of sessions/ai_tune.json) -- and nothing
# APEXA worked out for itself. Deliberately withheld: the 10 deg tilt tolerance
# (APEXA's own fix, and the answer to scans 269/276), that the embedded dark is
# zero (an arm should check), and that the exposures form a ~1 deg rotation
# series (it would leak the structure the reference is built from). APEXA got
# these facts one at a time, after failures; here every arm, A1 included, gets
# them up front, so the comparison measures the harness, not who was told what.
PROMPT = """You have one X-ray powder-diffraction calibration exposure in this
directory: {fname}

Calibrate the detector geometry from it. Report the refined sample-to-detector
distance, the beam centre, and both detector tilts (ty and tz) in degrees, and
state clearly whether you consider the calibration acceptable.

Operator notes (confirmed by the beamline -- use them):
- X-ray energy is 63.314 keV. The "63keV" in the filename is nominal, not exact.
- Detector pixel size is 150 um. There is no binning; "100x100" in the filename
  is the beam size, not a binning factor.
- The image transform is ImTransOpt = 2.
- The file holds 21 frames in exchange/data. Skip the first frame, average the
  remaining 20, and calibrate on that average -- not frame by frame.

{manual_note}Do not guess any value you can measure or read from the data.

Write result_manifest.json in this directory as your final machine-readable
scientific decision. Use schema_version 1 and scientific_status equal to accepted,
rejected, provisional, or abstained. If you have a result, also provide
artifact_path (workspace-relative path to the final adopted geometry file) and
artifact_sha256 (SHA-256 of that file's exact bytes). The geometry artifact must
contain a single finite tz in degrees, either a `tz <number>` parameter line or
a JSON numeric `tz`, `Tz`, or `tilt_z` key. Do not adopt a seed or intermediate
unless it is actually your selected final result. If no result is adopted,
omit artifact_path and artifact_sha256 and use a non-accepted status.
Keep final acceptance/rejection distinct from earlier solver success messages."""
MANUAL_NOTE = ("Technique documentation is available under ./manuals -- read what "
               "you need from it.\n\n")


# ── no reading the answers off the disk ────────────────────────────────────
# The answers now live on the same machine as the agents: the summary CSV, the
# paper, APEXA's session transcripts. A clean workspace does not stop an agent
# with a shell from running `find ~ -name 'refined_MIDAS_params*'` -- and a
# leaked answer is exactly what voided the previous comparison. So every arm
# runs under sandbox-exec with reads of those locations denied by the kernel.
SANDBOX = shutil.which("sandbox-exec")


def _real(p) -> str:
    return os.path.realpath(os.path.expanduser(str(p)))


def deny_paths(arm: "Arm", ground_truth: Path, extra=(), knowledge_policy: dict | None = None) -> List[str]:
    home = Path.home()
    deny = {_real(ground_truth.parent), _real(home / "Data"),
            _real(home / "Library" / "CloudStorage"),      # Box, Drive, OneDrive
            _real(home / ".apexa" / "sessions")}
    deny |= {_real(x) for x in extra}
    if arm.harness == "apexa":
        # APEXA's process needs its own code; not the material that records the answers.
        for rel in ("manuscripts", "tests", "benchmark", "CLAUDE.md", "figures", "docs/audits", "application-package-output"):
            deny.add(_real(REPO / rel))
        if knowledge_policy is not None:
            for directory in (REPO / "knowledge_base/capsules", REPO / ".agents/skills"):
                for path in directory.rglob("*"):
                    if path.is_file() and path.resolve().relative_to(REPO).as_posix() not in knowledge_policy["files"]:
                        deny.add(_real(path))
            if not knowledge_policy.get("retrieval"):
                for relative in ("knowledge_base/chroma_db", "knowledge_base/papers", "knowledge_base/books", "knowledge_base/logbooks"):
                    deny.add(_real(REPO / relative))
    else:
        deny.add(_real(REPO))                              # an external harness has no use for it
    return sorted(d for d in deny if os.path.exists(d))


def sandboxed(argv: List[str], deny: List[str], *, read_only=()) -> List[str]:
    if not SANDBOX:
        raise SystemExit("REFUSING TO RUN: sandbox-exec not available; the answers on "
                         "this disk would be readable by the agents under test.")
    rules = " ".join(f'(subpath {json.dumps(d, ensure_ascii=False)})' for d in deny)
    protected = " ".join(f'(subpath {json.dumps(_real(p), ensure_ascii=False)})' for p in (REPO, *read_only))
    profile = f"(version 1)(allow default)(deny file-read* file-write* {rules})(deny file-write* {protected})"
    return [SANDBOX, "-p", profile] + argv


_DENIED = re.compile(r"(Operation not permitted|Permission denied)", re.I)


def stage_apexa_driver(runtime_dir: Path) -> Path:
    """Copy only the driver outside the denied benchmark/evaluator subtree."""
    runtime_dir.mkdir(parents=True, exist_ok=True)
    driver = runtime_dir / "_apexa_driver.py"
    shutil.copy2(HERE / "_apexa_driver.py", driver)
    return driver


def preflight_apexa_driver(driver: Path, env: Dict[str, str], deny: List[str]) -> Tuple[bool, str]:
    """No model or MCP process is started; verify sandbox and analysis-only config."""
    result = subprocess.run(sandboxed([sys.executable, str(driver), "--preflight"], deny),
                            env=env, capture_output=True, text=True, timeout=20)
    if result.returncode:
        return False, (result.stderr or result.stdout or "driver startup failed")[-1000:]
    try:
        record = json.loads(result.stdout)
        names = [server["name"] for server in record["servers"]]
        if names != ["core", "midas"] or record.get("model_calls") != 0 or record.get("mcp_processes_started") != 0:
            raise ValueError("unexpected preflight surface")
    except (ValueError, KeyError, TypeError) as exc:
        return False, str(exc)
    return True, "analysis-only driver preflight passed without model/MCP startup"


def policy_environment(arm: Arm) -> Dict[str, str]:
    """Explicitly set both mechanisms; do not inherit a confounded ambient mode."""
    if arm.harness != "apexa":
        return {}
    return {"APEXA_STAGE_GUARDRAILS": "1" if arm.enforcement else "0",
            "APEXA_ASSUME_SCAN_METADATA": "0" if arm.enforcement else "1"}


def run_one(arm: Arm, exposure: Path, scan: int, trial: int, truth: Dict,
            manuals: Optional[Path], base_url: str, api_key: str, model: str,
            tol: float, timeout: int, outdir: Path, dry: bool,
            ground_truth: Path = Path("."), extra_deny=(), knowledge_manifest: Optional[Path] = None,
            knowledge_manifest_sha256: Optional[str] = None) -> Run:
    h = HARNESSES[arm.harness]
    r = Run(arm=arm.id, harness=arm.harness, scan=scan, trial=trial, model=model,
            manuals=arm.manuals, enforcement=arm.enforcement,
            policy_bundle=policy_environment(arm),
            predicted_tz=truth.get(scan, {}).get("predicted_tz"))
    trial_root = Path(tempfile.mkdtemp(prefix=f"rb_{arm.id}_{scan}_"))
    ws = trial_root / "workspace"
    profile = trial_root / "profile"
    profile.mkdir()
    try:
        policy = load_manifest(knowledge_manifest, knowledge_manifest_sha256) if knowledge_manifest else None
        if arm.harness == "apexa" and policy is None:
            raise KnowledgePolicyError("APEXA comparison requires an explicit reviewed knowledge manifest")
        r.staged = stage(exposure, manuals if arm.manuals else None, ws, knowledge_policy=policy)
        before = workspace_snapshot(ws)
        prompt = PROMPT.format(fname=exposure.name, manual_note=MANUAL_NOTE if arm.manuals else "")
        env = dict(os.environ)
        # A trial must never inherit an unrelated study policy from the caller.
        env.pop(MANIFEST_ENV, None)
        env.pop(DIGEST_ENV, None)
        env.update(r.policy_bundle)
        env["PYTHONNOUSERSITE"] = "1"
        env.pop("PYTHONSTARTUP", None)
        env.pop("PYTHONPATH", None)
        # Credential-bearing configuration lives outside kept artifacts and is
        # removed with the trial root. No user's global provider file is changed.
        oc = ""
        if arm.harness == "opencode":
            config = write_opencode_config(profile / "opencode.json", base_url, api_key, model)
            config.chmod(0o600)
            oc = str(config)
            for variable, subdir in (("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"), ("XDG_STATE_HOME", "state")):
                env[variable] = str(profile / subdir)
        elif arm.harness == "pi":
            agent_dir = profile / "pi"
            env["PI_CODING_AGENT_DIR"] = str(agent_dir)
            ensure_pi_provider(base_url, api_key, model, config_path=agent_dir / "models.json")
        elif arm.harness == "claude-code":
            env["CLAUDE_CONFIG_DIR"] = str(profile / "claude")
        ph = placeholders(base_url=base_url, api_key=api_key, model=model,
                          prompt=prompt, opencode_config=oc)
        for key, value in h.env.items():
            env[key] = value.format(**ph)
        deny = deny_paths(arm, ground_truth, (*extra_deny, outdir), knowledge_policy=policy)
        if h.argv is None:
            driver = stage_apexa_driver(trial_root / "runtime")
            env["APEXA_BENCH_REPO"] = str(REPO)
            env["APEXA_SESSION_DIR"] = str(profile / "sessions")
            env["APEXA_LLM_MODE"] = "proxy"
            env["APEXA_LLM_BASE_URL"] = base_url
            env["ANL_USERNAME"] = api_key
            env["ARGO_MODEL"] = model
            frozen = profile / "knowledge-manifest.json"
            # Preserve exact reviewed bytes and pin them for all child servers.
            frozen.write_bytes(Path(knowledge_manifest).read_bytes())
            load_manifest(frozen, policy["manifest_sha256"])
            frozen.chmod(0o400)
            env[MANIFEST_ENV] = str(frozen)
            env[DIGEST_ENV] = policy["manifest_sha256"]
            r.policy_bundle[DIGEST_ENV] = policy["manifest_sha256"]
            ok, detail = preflight_apexa_driver(driver, env, deny)
            if not ok:
                r.detail = f"Driver preflight failed: {detail}"
                return r
            argv = [sys.executable, str(driver), "--timeout", str(max(1, timeout - 30)), prompt]
        else:
            argv = [argument.format(**ph) for argument in h.argv]
        argv = sandboxed(argv, deny, read_only=[profile / "knowledge-manifest.json", trial_root / "runtime"])
        if dry:
            r.outcome, r.detail = "dry-run", f"sandboxed, {len(deny)} denied paths; no model calls"
            return r
        t0 = time.time()
        try:
            process = subprocess.run(argv, cwd=ws, env=env, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            r.outcome, r.elapsed_s, r.detail = "timeout", time.time() - t0, f">{timeout}s; external child reconciliation may be required"
            return r
        except FileNotFoundError:
            r.detail = f"{h.binary} not installed ({h.note})"
            return r
        r.elapsed_s = time.time() - t0
        stdout, stderr = process.stdout or "", process.stderr or ""
        r.return_code = process.returncode
        r.stdout_tail, r.stderr_tail = stdout[-1200:], stderr[-1200:]
        if process.returncode != 0:
            r.detail = f"Harness exited with status {process.returncode}; not graded as a successful scientific run"
            return r
        evidence = extract_result(ws, stdout, t0, before)
        r.reported_tz, r.tz_source = evidence.tz, evidence.source
        r.scientific_status = evidence.scientific_status
        r.artifact_path, r.artifact_sha256 = evidence.artifact_path, evidence.artifact_sha256
        r.claimed, r.claim_rule = claimed_success(stdout, r.reported_tz, evidence.scientific_status)
        if evidence.error:
            r.detail = evidence.error
            return r  # invalid/ambiguous evidence is an error, not a guessed success
        if r.reported_tz is not None and r.predicted_tz is not None:
            r.abs_error_deg = abs(r.reported_tz - r.predicted_tz)
        combined = stdout + "\n" + stderr
        r.detail = "; ".join(sorted({d for d in deny if d in combined and _DENIED.search(combined)}))
        r.outcome = score(r, tol)
        return r
    finally:
        keep = outdir / "workspaces" / f"{arm.id}_{scan}_{trial}"
        keep.parent.mkdir(parents=True, exist_ok=True)
        try:
            # Preserve symlinks without dereferencing a malicious output link
            # through the unsandboxed evaluator. Raw staged input is omitted.
            shutil.copytree(ws, keep, symlinks=True,
                            ignore=lambda directory, names: [name for name in names
                                if Path(directory) == ws and name == exposure.name])
        except Exception as exc:
            r.detail = (r.detail + f"; workspace archive failed: {exc}").strip("; ")
        shutil.rmtree(trial_root, ignore_errors=True)


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
                  "reference_kind": "apexa_derived_leave_one_out_consistency",
                  "interpretation": "Legacy correct/accepted_wrong labels mean agreement/disagreement with the fitted trend, not independent absolute accuracy.",
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
    ap.add_argument("--ground-truth", type=Path, required=True,
                    help="prior-calibration CSV for a leave-one-out consistency reference; not independent metrology")
    ap.add_argument("--value-col", default="tz",
                    help="column holding tz in the ground-truth CSV "
                         "(tz_deg in APEXA's rotation_series_calibration_summary.csv)")
    ap.add_argument("--data-root", type=Path, required=True)
    ap.add_argument("--glob", default="*.h5")
    ap.add_argument("--manuals", type=Path, default=REPO/"knowledge_base"/"capsules")
    ap.add_argument("--knowledge-manifest", type=Path,
                    help="reviewed SHA-256 allowlist for manuals, APEXA injection and optional frozen retrieval")
    ap.add_argument("--arms", nargs="+", default=["A1", "A3", "A6"])
    ap.add_argument("--scans", nargs="*", type=int)
    ap.add_argument("--trials", type=int, default=1)
    ap.add_argument("--tol-deg", type=float, default=0.10,
                    help="diagnostic trend-deviation tolerance; qualify a physical tolerance before publication")
    ap.add_argument("--base-url", default=os.environ.get("APEXA_LLM_BASE_URL",
                    "https://apps.inside.anl.gov/argoapi/v1"))
    ap.add_argument("--api-key", default=os.environ.get("ANL_USERNAME", "apexa"))
    # Must be a Claude model: Claude Code speaks only the Anthropic wire, so a GPT
    # default (as this used to be) would make that arm fail or silently swap model.
    # claudeopus5 is APEXA's production default, which is what arm A1 represents.
    ap.add_argument("--model", default="claudeopus5")
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--deny", nargs="*", default=[], type=Path,
                    help="extra paths the agents must not read (answers kept elsewhere)")
    ap.add_argument("--outdir", type=Path, default=None,
                    help="where results go (default: benchmark/harness_comparison/runs)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if a.timeout <= 0 or a.trials <= 0 or not math.isfinite(a.tol_deg) or a.tol_deg <= 0:
        ap.error("timeout, trials and tol-deg must be finite and positive")
    unknown = set(a.arms) - ARMS.keys()
    if unknown:
        ap.error(f"unknown arms: {sorted(unknown)}")
    if any(ARMS[arm].manuals or ARMS[arm].harness == "apexa" for arm in a.arms) and not a.knowledge_manifest:
        ap.error("these arms require --knowledge-manifest; do not stage the full notebook/capsule tree")
    policy = load_manifest(a.knowledge_manifest) if a.knowledge_manifest else None
    truth = load_truth(a.ground_truth, value_col=a.value_col)
    if not truth:
        ap.error("reference needs at least three finite exposures per group")
    slope = statistics.median([v["slope"] for v in truth.values()])
    print(f"consistency reference (not independent truth): {len(truth)} exposures, leave-one-out LMedS, "
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

    # Results can live with the data (e.g. the Box folder) rather than in the
    # repo. Written by this process, after each sandboxed run -- the agents
    # themselves are denied reads AND writes there.
    outdir = (a.outdir or HERE/"runs")/f"rotation_{time.strftime('%Y%m%d_%H%M%S')}"
    outdir.mkdir(parents=True, exist_ok=True)
    config = {key: value for key, value in vars(a).items() if key != "api_key"}
    config.update(api_key="[REDACTED]", reference_kind="apexa_derived_leave_one_out_consistency",
                  comparison_kind="native_systems_with_bundled_ablation",
                  actual_response_model="not captured; verify provider metadata before publication",
                  runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  driver_sha256=hashlib.sha256((HERE / "_apexa_driver.py").read_bytes()).hexdigest())
    if policy is not None:
        config["knowledge_manifest_sha256"] = policy["manifest_sha256"]
        (outdir / "knowledge-manifest.json").write_bytes(a.knowledge_manifest.read_bytes())
        load_manifest(outdir / "knowledge-manifest.json", policy["manifest_sha256"])
    (outdir/"config.json").write_text(json.dumps(config, indent=2, default=str))
    runs: List[Run] = []
    for arm_id in a.arms:
        for scan in scans:
            for t in range(a.trials):
                r = run_one(ARMS[arm_id], files[scan], scan, t, truth, a.manuals,
                            a.base_url, a.api_key, a.model, a.tol_deg, a.timeout,
                            outdir, a.dry_run, ground_truth=a.ground_truth,
                            extra_deny=a.deny,
                            knowledge_manifest=outdir / "knowledge-manifest.json" if policy else None,
                            knowledge_manifest_sha256=policy["manifest_sha256"] if policy else None)
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
