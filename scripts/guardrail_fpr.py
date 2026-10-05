#!/usr/bin/env python3
"""Guardrail false-positive rate over known-good MIDAS parameter files.

The handbook lint (`evaluate_param_guardrails`) HARD-BLOCKS a run on any
`error`-severity trap. A false positive is an `error` raised on a file that is
(a) *known good* and (b) actually *runnable* — i.e. a complete parameter file the
FF/NF gate is designed to evaluate. Blocking such a file aborts a legitimate
experiment, so its error-severity rate is the number that matters for ICLR §7.

A parameter file has a **role**, and the FPR must be reported per role. The gate
(`run_ff_hedm_full_workflow`) evaluates the **user-facing FF param file** that
drives `ff_MIDAS.py` — not downstream artifacts. Roles:

  * `submit-ready` — a hand-authored, complete FF/NF parameter file an operator
    or the agent actually submits to the workflow (a `Parameters*.txt` / `ps*.txt`
    with an `OmegaRange` sweep). THIS is the gate's real input population; an
    `error` here is a true false positive.
  * `generated`   — a MIDAS-*written* per-layer artifact (`paramstest.txt`,
    `*.used.txt`). Consumed by the C indexer, which reads the ω seed window from
    the Zarr archive (`FitSetupParamsAllZarr.c:757-764`), so the key is absent by
    design. Not a gate input.
  * `pre-calib`   — an uncalibrated starting file (`*_uncal*`): valid input to
    *calibration*, not yet to *indexing*.
  * `calib-geom`  — a calibration-geometry *output* (`refined_MIDAS_params*`):
    Lsd/BC/tilts/RhoD/λ/lattice only, no ω sweep, no seed window.

For `generated`/`pre-calib`/`calib-geom`, the `MinOmeSpotIDsToIndex` error is a
*true positive* w.r.t. "would submitting THIS file to ff_MIDAS.py index 0 grains?"
— verified against MIDAS source: `parse_float_param(..., default=0.0)`
(`ff_MIDAS.py:247`) then the seed filter `sps[:,2] >= min2Index` / `<= max2Index`
(`ff_MIDAS.py:1918-1926`) collapses the window to `[0,0]` when both are absent.
Reported separately so a true positive is never scored as a false positive.

Corpus is entirely real (MIDAS examples/tests/templates, APEXA templates, real
case-study inputs/outputs, and every refined calibration output in the repo),
deduped by content hash. Nothing is synthesised.

Usage:  uv run python scripts/guardrail_fpr.py [--json OUT.json]
Offline-clean: reads local files only; no network, no MCP spin-up.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

import handbook_guardrails as hg

_MIDAS = Path("/Users/b324240/Git/MIDAS")

# A file is a MIDAS param file if it declares at least one of these geometry/keys.
_PARAM_MARKERS = re.compile(r"^\s*(Lsd|Wavelength|BC|RingThresh|Distance|px|SpaceGroup)\b",
                            re.MULTILINE)
_OMEGA_RANGE = re.compile(r"^\s*OmegaRange\b", re.MULTILINE)
# NF parameter files are the `ps*.txt` family (peaksearch-style) or *_ps_* names.
_NF_NAME = re.compile(r"(^ps|_ps_|ps_au|nf)", re.IGNORECASE)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def _candidate_paths() -> list[Path]:
    out: list[Path] = []
    for root, prune in ((_REPO, ("node_modules", ".git")),
                        (_MIDAS, ("build", "_deps", "doc"))):
        if not root.is_dir():
            continue
        for p in root.rglob("*.txt"):
            parts = set(p.parts)
            if parts & set(prune):
                continue
            low = p.name.lower()
            if low.endswith(".md") or "handbook" in low or "manual" in low or low.startswith("readme"):
                continue
            out.append(p)
    return out


def collect() -> list[dict]:
    """Return one record per unique known-good param file: {path, pipeline, role}."""
    recs: list[dict] = []
    by_hash: dict[str, dict] = {}
    for p in _candidate_paths():
        # Skip non-param captures (stdout logs echo param values but aren't files
        # the gate ever evaluates).
        low_path = str(p).lower()
        if "zipout" in low_path or "/stdout/" in low_path or ".analysis.midas.zip" in low_path:
            continue
        try:
            text = p.read_text(errors="ignore")
        except Exception:
            continue
        if not _PARAM_MARKERS.search(text):
            continue  # not a param file (skip stdout captures, CSVs, etc.)
        name = p.name
        low = name.lower()
        is_refined = name.startswith(("refined_MIDAS_params", "autogen_calib_params"))
        runnable = bool(_OMEGA_RANGE.search(text))
        if is_refined and not runnable:
            role = "calib-geom"        # geometry-only calibration output
        elif "uncal" in low:
            role = "pre-calib"         # uncalibrated starting file
        elif low.startswith("paramstest") or low.endswith(".used.txt"):
            role = "generated"         # MIDAS-written per-layer artifact
        elif runnable:
            role = "submit-ready"      # complete, hand-authored FF/NF submission
        else:
            continue                   # incomplete non-calib fragment — out of scope
        pipeline = "nf" if _NF_NAME.search(name) else "ff"
        h = hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()
        if h in by_hash:
            continue                   # content duplicate — count once
        rec = {"path": p, "pipeline": pipeline, "role": role}
        by_hash[h] = rec
        recs.append(rec)
    return sorted(recs, key=lambda r: (r["role"], str(r["path"])))


def _disp(p: Path) -> str:
    try:
        return str(p.relative_to(_REPO))
    except ValueError:
        return str(p).replace(str(_MIDAS), "MIDAS")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default="")
    ap.add_argument("--show-errors", action="store_true",
                    help="print every error-severity trap message")
    args = ap.parse_args()

    recs = collect()
    if not recs:
        print("no param files found")
        return 2

    for r in recs:
        traps = hg.evaluate_param_guardrails(str(r["path"]), pipeline=r["pipeline"])
        r["errors"] = [t for t in traps if t.get("severity") == "error"]
        r["warns"] = [t for t in traps if t.get("severity") == "warning"]
        r["infos"] = [t for t in traps if t.get("severity") == "info"]

    roles = (
        ("submit-ready", "SUBMIT-READY FF/NF FILES (the gate's true input population)"),
        ("generated",    "MIDAS-GENERATED per-layer artifacts (seed window in Zarr — not a gate input)"),
        ("pre-calib",    "UNCALIBRATED starting files (input to calibration, not indexing)"),
        ("calib-geom",   "CALIBRATION-GEOMETRY outputs (geometry only — not runnable)"),
    )
    for role, label in roles:
        sub = [r for r in recs if r["role"] == role]
        if not sub:
            continue
        n = len(sub)
        with_err = sum(1 for r in sub if r["errors"])
        lo, hi = wilson(with_err, n)
        print(f"\n{'='*90}\n{label}\n{'='*90}")
        print(f"{'file':<74}{'pipe':>5}{'E':>3}{'W':>3}{'I':>3}")
        print("-" * 90)
        for r in sub:
            d = _disp(r["path"])
            if len(d) > 72:
                d = "…" + d[-71:]
            print(f"{d:<74}{r['pipeline']:>5}{len(r['errors']):>3}"
                  f"{len(r['warns']):>3}{len(r['infos']):>3}")
        print("-" * 90)
        kind = ("false-positive" if role == "submit-ready"
                else "true-positive (not a submit-ready indexing file)")
        print(f"{with_err}/{n} files raise >=1 error → {with_err/n:.3f} "
              f"[95% CI {lo:.3f}, {hi:.3f}]   ({kind})")

    if args.show_errors:
        print(f"\n{'='*90}\nERROR-SEVERITY TRAPS (grouped by message key)\n{'='*90}")
        by_key: dict[str, list[str]] = {}
        for r in recs:
            for t in r["errors"]:
                by_key.setdefault(t.get("key", "?"), []).append(_disp(r["path"]))
        for key, files in sorted(by_key.items()):
            print(f"\n[{key}] on {len(files)} file(s)")

    if args.json:
        out = {"files": [{"path": _disp(r["path"]), "role": r["role"],
                          "pipeline": r["pipeline"], "n_error": len(r["errors"]),
                          "n_warning": len(r["warns"]), "n_info": len(r["infos"]),
                          "error_keys": [t.get("key") for t in r["errors"]]}
                         for r in recs]}
        Path(args.json).write_text(json.dumps(out, indent=2))
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
