"""APEXA acquisition watcher (prototype) — turn APEXA from post-hoc to acquisition-driven.

This closes the gap called out when comparing APEXA to the reflectometry
``nr-analyzer``/AuRE stack: those tools (and APEXA today) only act on data that
already exists. The reflectometry *planner* nonetheless contains the key trick —
it groups files by sample "stem", knows the EXPECTED set for the technique, and
only fires when the set is complete:

    "Sequence/stem 'Ni58_stitched' is complete because both required PNR spin
     states (_u, _d) are present ... this arriving file is the last missing
     spin state for the complete stem."

This module generalises that to synchrotron series (FF/PF-HEDM, WAXS, SAXS):

  1. **Group** files in a directory by stem (strip frame index / spin / segment
     suffixes and extension).
  2. **Judge completeness** — require non-empty files and a quiet period since
     the last write; optionally also require an exact expected file count.
  3. **Plan + fire** — build an :class:`apexa_plan.APEXAPlan`, record every
     inferred default as an assumption, write ``APEXA_plan.yaml`` next to the
     data, and (in --execute mode) call ``midas_integrate_series`` once for the
     whole stem. Each directory/stem completes once per process; failed tool
     results retry on the next poll. The plan records the exact input list.

It is a PROTOTYPE: polling (no watchdog dependency), heuristic completeness, and
integration-only execution. It is intentionally standalone — stdlib + PyYAML +
apexa_plan — so it runs on a beamline data mover without the agent stack, and
only lazy-imports the MCP server when actually executing.

Usage
-----
    # dry-run: watch a live scan dir, print the plan it WOULD run
    python apexa_acquisition_watcher.py /scratch/beam/mytscan \\
        --technique waxs --param-file ceria_params.txt --expected-count 180

    # one pass over an already-finished directory, then exit
    python apexa_acquisition_watcher.py /data/done --once --param-file p.txt

    # actually integrate each completed stem
    python apexa_acquisition_watcher.py /scratch/beam/live --execute --param-file p.txt
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import stat
import time
from glob import glob

from apexa_plan import (APEXAPlan, Beam, Calibration, DarkSpec, DataSpec,
                        Sample, PowderRefinement)

# ── stem grouping ────────────────────────────────────────────────────────────
# Suffixes that denote a member of a set rather than a distinct sample.
_SPIN_SEG = re.compile(
    r"(?:"
    r"_(?:uu|ud|du|dd|u|d)"        # polarised-neutron spin states (refl carry-over)
    r"|_partial"                    # co-refined angle segments
    r"|_combined(?:_data)?(?:_auto)?"
    r")$",
    re.IGNORECASE,
)
# A trailing frame/scan index like _00001, .00042, -12 (optionally before ext).
_FRAME_IDX = re.compile(r"[._-]\d{1,6}$")
# Compound extensions to strip whole (order matters: longest first).
_EXTS = (".vrx.h5", ".ge5", ".ge3", ".ge2", ".tif", ".tiff", ".h5",
         ".hdf5", ".dat", ".txt", ".edf", ".cbf", ".raw")


def stem_of(filename: str) -> str:
    """Reduce a data filename to the sample/scan stem that groups its set.

    JL_0Nb_00042.vrx.h5       -> JL_0Nb          (trailing _index stripped)
    Ni58_stitched_u.dat       -> Ni58_stitched   (spin state stripped)
    REFL_226642_3_partial.txt -> REFL_226642     (segment + index stripped;
                                                  co-refined partials group as one)
    scan007.ge5               -> scan007         (no separator before the index,
                                                  so it is left intact)

    Heuristic, not infallible: a trailing number is treated as a frame index only
    when a separator (``_ . -``) precedes it. For unusual naming, pass a stricter
    ``--pattern`` or an ``--expected-count`` rather than relying on grouping.
    """
    name = os.path.basename(filename)
    low = name.lower()
    for ext in _EXTS:
        if low.endswith(ext):
            name = name[: -len(ext)]
            break
    else:
        name = os.path.splitext(name)[0]
    # peel one spin/segment suffix, then one frame index (loop to catch _3_partial)
    changed = True
    while changed:
        changed = False
        new = _SPIN_SEG.sub("", name)
        if new != name:
            name, changed = new, True
    name = _FRAME_IDX.sub("", name)
    return name or os.path.basename(filename)


def is_dark(filename: str, exclude_substring: str = "dark") -> bool:
    return exclude_substring and exclude_substring.lower() in os.path.basename(filename).lower()


# ── grouping + completeness ──────────────────────────────────────────────────
def group_by_stem(files, exclude_substring="dark"):
    """{stem: [paths...]} for non-dark files."""
    groups: dict[str, list] = {}
    for f in files:
        if is_dark(f, exclude_substring):
            continue
        groups.setdefault(stem_of(f), []).append(f)
    for v in groups.values():
        v.sort()
    return groups


def is_complete(paths, *, expected_count=None, quiet_seconds=30.0, now=None):
    """Decide whether a stem's set looks finished.

    Returns (complete: bool, reason: str) — reason feeds the plan's assumptions.
    """
    n = len(paths)
    if expected_count is not None and expected_count <= 0:
        raise ValueError("expected_count must be positive")
    if not math.isfinite(quiet_seconds) or quiet_seconds < 0:
        raise ValueError("quiet_seconds must be finite and non-negative")
    if not n:
        return False, "no files present"
    if expected_count is not None:
        if n > expected_count:
            return False, f"{n} files exceed expected_count {expected_count}; check scan grouping"
        if n < expected_count:
            return False, f"{n}/{expected_count} files so far"
    # A file count alone says nothing about whether the writer has finished.
    # This is still a heuristic: production acquisition needs a completion event.
    now = time.time() if now is None else now
    try:
        stats = [os.stat(p) for p in paths]
    except OSError:
        return False, "files vanished during scan"
    if any(not stat.S_ISREG(s.st_mode) or s.st_size == 0 for s in stats):
        return False, "empty or non-regular files present"
    newest = max(s.st_mtime for s in stats)
    idle = now - newest
    if idle >= quiet_seconds:
        count_note = f"; expected_count {expected_count}" if expected_count is not None else ""
        return True, f"no new frames or writes for {idle:.0f}s (>= quiet {quiet_seconds:.0f}s); {n} files{count_note}"
    return False, f"still arriving ({idle:.0f}s idle < {quiet_seconds:.0f}s); {n} frames"


# ── plan construction ────────────────────────────────────────────────────────
def build_plan(stem, paths, args) -> APEXAPlan:
    """Assemble a reviewable plan for one completed stem, logging assumptions."""
    image_dir = os.path.dirname(paths[0]) if paths else args.directory
    plan = APEXAPlan(
        technique=args.technique,
        instrument=args.instrument or "",
        describe=f"{args.technique} of stem '{stem}' ({len(paths)} frames), "
                 f"triggered on acquisition completion.",
        sample=Sample(name=stem),
        beam=Beam(energy_keV=args.energy),
        calibration=Calibration(calibrant=args.calibrant or "",
                                parameter_file=args.param_file or ""),
        data=DataSpec(
            image_dir=image_dir,
            pattern=f"{stem}*",
            stem=stem,
            data_location=args.data_location or "",
            expected_count=args.expected_count,
            exclude_substring=args.exclude_substring,
            images=[os.path.abspath(p) for p in paths],
        ),
        dark=DarkSpec(source=args.dark_source, kind=args.dark_kind,
                      location=args.dark_location or ""),
        result_folder=(os.path.join(args.result_folder,
                                   os.path.relpath(image_dir, args.directory), f"APEXA_{stem}")
                       if args.result_folder else os.path.join(image_dir, f"APEXA_{stem}")),
        perform_execution=bool(args.execute),
    )
    if args.technique == "powder-refinement":
        plan.refinement = PowderRefinement(mode=getattr(args, "refinement_mode", "sequential"),
            observations=[{"data_path": os.path.abspath(p), "instrument_path": args.param_file,
                           "cif_paths": getattr(args, "cif", []), "sample_id": stem,
                           "frame_id": str(i), "format": getattr(args, "powder_format", "xye")}
                          for i,p in enumerate(paths)])
    # provenance for every value we inferred rather than were told
    if args.expected_count is None:
        plan.assume("data.expected_count", None,
                    "no count given; completeness judged by acquisition quiet-period")
    if not args.data_location:
        plan.assume("data.data_location", "",
                    "HDF5 dataset path not specified; integrator default assumed")
    if args.energy is None:
        plan.assume("beam.energy_keV", None,
                    "energy not supplied on CLI; must come from parameter_file")
    plan.assume("dark.kind", args.dark_kind,
                f"dark timing not specified per-stem; using CLI default '{args.dark_kind}'")
    return plan


# ── execution seam ───────────────────────────────────────────────────────────
# Azimuthal integration and durable powder submission can run from a watcher.
# Integration is
# a single, idempotency-guarded tool call (midas_integrate_series carries the
# @idempotent guard, so a stem fired twice replays the prior result rather than
# re-integrating). HEDM setup (calibrate → in-plane tx → reconstruct) is a *gated*
# procedure — it needs the human-in-the-loop decisions the FF-HEDM LangGraph
# workflow enforces (APEXA_WORKFLOW_MODE=graph). A watcher cannot answer those
# gates, so for HEDM techniques we emit the reviewable plan and hand off, never
# fire a blind integrate call (which would be the wrong tool entirely).
_AUTO_EXECUTABLE = ("waxs", "saxs", "integration", "powder-refinement")


def execute_plan(plan: APEXAPlan):
    """Dispatch the plan to its technique's tool (lazy import; async).

    Returns the tool result. Raises ValueError for a technique the watcher must
    not auto-execute (HEDM/calibration) — the caller handles the handoff.
    """
    if plan.technique not in _AUTO_EXECUTABLE:
        raise ValueError(
            f"technique '{plan.technique}' is not watcher-auto-executable; "
            "HEDM/calibration need a reviewed, gated technique-specific workflow")
    import asyncio
    if plan.technique == "powder-refinement":
        from gsas2_server import powder_refine_submit
        return asyncio.run(powder_refine_submit(**plan.to_powder_refine_kwargs()))
    from midas_comprehensive_server import midas_integrate_series  # heavy; lazy
    kwargs = plan.to_integrate_series_kwargs()
    return asyncio.run(midas_integrate_series(**kwargs))


def _is_cached_result(out) -> bool:
    """True if a tool result reports the idempotency guard replayed a prior run."""
    if isinstance(out, dict):
        return bool(out.get("cached"))
    if isinstance(out, str):
        # tool results are JSON strings; cheap substring check avoids a full parse
        return '"cached": true' in out.lower()
    return False


def _successful_result(out) -> bool:
    """Only an explicit successful tool outcome completes an acquisition job."""
    if isinstance(out, str):
        try:
            out = json.loads(out)
        except (ValueError, TypeError):
            return False
    return (isinstance(out, dict) and out.get("status") == "success"
            and not out.get("error") and not out.get("failed"))


def _accepted_powder_submission(out) -> bool:
    """A durable handoff completes watching; scientific completion is polled separately."""
    if isinstance(out, str):
        try:
            out = json.loads(out)
        except (ValueError, TypeError):
            return False
    return (isinstance(out, dict) and out.get("status") == "submitted"
            and bool(out.get("job_id")) and bool(out.get("job_ids")) and not out.get("error"))


# ── watch loop ───────────────────────────────────────────────────────────────
def scan_once(args, fired: set):
    files = glob(os.path.join(args.directory, "**", args.pattern), recursive=True)
    groups = {}
    for path in files:
        if not os.path.isfile(path) or is_dark(path, args.exclude_substring):
            continue
        key = (os.path.abspath(os.path.dirname(path)), stem_of(path))
        groups.setdefault(key, []).append(path)
    for key, paths in sorted(groups.items()):
        stem = key[1]
        paths.sort()
        if key in fired:
            continue
        complete, reason = is_complete(
            paths, expected_count=args.expected_count,
            quiet_seconds=args.quiet_seconds)
        status = "READY" if complete else "wait "
        print(f"  [{status}] {stem:<24} {len(paths):>4} frames — {reason}")
        if not complete:
            continue
        plan = build_plan(stem, paths, args)
        plan.assume("_trigger", reason, "completeness rule that fired this stem")
        plan_path = os.path.join(plan.data.image_dir, f"APEXA_plan_{stem}.yaml")
        plan.to_yaml(plan_path)
        issues = plan.validate()
        print(f"     -> wrote {plan_path}")
        if issues:
            print("     -> NOT executing; plan has issues:")
            for i in issues:
                print(f"          - {i}")
            continue
        if args.technique not in _AUTO_EXECUTABLE:
            # HEDM/calibration: the plan is the deliverable; a human runs the
            # gated FF-HEDM graph. Never auto-fire integrate for these.
            print(f"     -> technique '{args.technique}' needs a reviewed, gated workflow. "
                  "Plan written for review; select the matching FF/NF/PF or calibration "
                  "procedure. APEXA_WORKFLOW_MODE=graph currently implements FF-HEDM.")
        elif args.execute:
            tool = "powder_refine_submit" if plan.technique == "powder-refinement" else "midas_integrate_series"
            print(f"     -> executing {tool} for '{stem}' ...")
            try:
                out = execute_plan(plan)
                accepted = (_accepted_powder_submission(out) if plan.technique == "powder-refinement"
                            else _successful_result(out))
                if not accepted:
                    print(f"     -> execution NOT complete; retry on next scan: {str(out)[:200]}")
                    continue
                if _is_cached_result(out):
                    print(f"     -> idempotent skip: '{stem}' already integrated "
                          "(prior result replayed; pass force=True to redo).")
                else:
                    label = "submitted; poll refinement_status for results" if plan.technique == "powder-refinement" else "done"
                    print(f"     -> {label}: {str(out)[:200]}")
            except Exception as exc:
                print(f"     -> execution FAILED: {exc}")
                continue
        else:
            print("     -> dry-run (pass --execute to submit). Would call:")
            kwargs = (plan.to_powder_refine_kwargs() if plan.technique == "powder-refinement"
                      else plan.to_integrate_series_kwargs())
            for k, v in kwargs.items():
                print(f"          {k}={v!r}")
        fired.add(key)
    return fired


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("directory", help="acquisition directory to watch")
    ap.add_argument("--pattern", default="*.h5", help="glob for data files (default *.h5)")
    ap.add_argument("--exclude-substring", default="dark", help="skip files containing this")
    ap.add_argument("--technique", default="waxs", help="ff-hedm|pf-hedm|waxs|saxs|integration|powder-refinement")
    ap.add_argument("--instrument", default="")
    ap.add_argument("--param-file", default="", help="MIDAS parameter file (calibration)")
    ap.add_argument("--cif", action="append", default=[], help="candidate CIF for powder refinement; repeat per phase")
    ap.add_argument("--refinement-mode", default="sequential", choices=["independent", "sequential", "joint", "search"])
    ap.add_argument("--powder-format", default="xye", help="GSAS reader hint for powder observations")
    ap.add_argument("--calibrant", default="")
    ap.add_argument("--energy", type=float, default=None, help="beam energy (keV)")
    ap.add_argument("--data-location", default="", help="HDF5 dataset path, e.g. exchange/data")
    ap.add_argument("--dark-source", default="file", choices=["file", "embedded", "none"])
    ap.add_argument("--dark-kind", default="after", choices=["after", "before", "any"])
    ap.add_argument("--dark-location", default="")
    ap.add_argument("--result-folder", default="", help="output root; each directory/stem gets its own subfolder")
    ap.add_argument("--expected-count", type=int, default=None,
                    help="exact FILE count for a complete set (not frames within HDF5)")
    ap.add_argument("--quiet-seconds", type=float, default=30.0,
                    help="minimum time since last file write, also required with --expected-count")
    ap.add_argument("--interval", type=float, default=10.0, help="poll interval (s)")
    ap.add_argument("--once", action="store_true", help="one pass then exit")
    ap.add_argument("--execute", action="store_true",
                    help="actually run integration (default: dry-run)")
    args = ap.parse_args(argv)

    if args.expected_count is not None and args.expected_count <= 0:
        ap.error("--expected-count must be positive")
    if not math.isfinite(args.quiet_seconds) or args.quiet_seconds < 0:
        ap.error("--quiet-seconds must be finite and non-negative")
    if not math.isfinite(args.interval) or args.interval <= 0:
        ap.error("--interval must be finite and positive")

    if not os.path.isdir(args.directory):
        ap.error(f"not a directory: {args.directory}")

    mode = "EXECUTE" if args.execute else "DRY-RUN"
    print(f"APEXA acquisition watcher [{mode}] — {args.directory} "
          f"(pattern {args.pattern}, technique {args.technique})")
    fired: set = set()
    try:
        while True:
            print(f"-- scan @ {time.strftime('%H:%M:%S')} --")
            fired = scan_once(args, fired)
            if args.once:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nstopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
