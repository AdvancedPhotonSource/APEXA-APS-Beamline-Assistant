"""Acquisition watcher with explicit completion events and durable local jobs.

Quiet-file discovery writes review plans only. Automatic execution requires an
operator-trusted append-only JSONL completion log, --producer and --execute.
See docs/ACQUISITION_RUNTIME.md for the event and restart contracts.

    python apexa_acquisition_watcher.py /data/scan --once --param-file p.txt
    python apexa_acquisition_watcher.py /data/scan --events /data/events.jsonl \
        --producer detector-writer --dark-source none --execute
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
from glob import glob, escape as glob_escape
from pathlib import Path
from copy import copy
import hashlib
import fnmatch

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
# The run store prevents duplicate admission across restarts. It does not claim
# exactly-once external effects; unknown outcomes require reconciliation.
# HEDM/calibration still require reviewed technique-specific workflows.
_AUTO_EXECUTABLE = ("waxs", "saxs", "integration", "powder-refinement")


def execute_plan(plan: APEXAPlan, *, manifest=None, context=None):
    """Dispatch the plan to its technique's tool (lazy import; async).

    Returns the tool result. Raises ValueError for a technique the watcher must
    not auto-execute (HEDM/calibration) — the caller handles the handoff.
    """
    if plan.technique not in _AUTO_EXECUTABLE:
        raise ValueError(
            f"technique '{plan.technique}' is not watcher-auto-executable; "
            "HEDM/calibration need a reviewed, gated technique-specific workflow")
    import asyncio
    async def invoke():
        if plan.technique == "powder-refinement":
            from gsas2_server import powder_refine_submit
            call = powder_refine_submit(**plan.to_powder_refine_kwargs())
        else:
            from midas_comprehensive_server import midas_integrate_series
            kwargs = plan.to_integrate_series_kwargs()
            if manifest is not None and plan.dark.source == "file":
                # Bind the declared correction, never rediscover nearby darks.
                kwargs["dark_file"] = manifest.to_dict()["corrections"]["dark"]["files"][0]["path"]
            call = midas_integrate_series(**kwargs)
        task = asyncio.create_task(call)
        try:
            while not task.done():
                if context is not None and context.cancellation_requested:
                    context.acknowledge_cancel()
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    # Local cleanup does not prove an external engine has stopped.
                    return {"status": "cancelled", "execution_stopped": False,
                            "error": "cancellation propagated; verify external execution outcome"}
                await asyncio.wait({task}, timeout=0.2)
            return await task
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
    return asyncio.run(invoke())


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
        plan.perform_execution = False
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
        else:
            print("     -> review plan only: quiet files do not prove acquisition closure.")
            if args.execute:
                print("     -> automatic execution requires --events and --producer.")
        fired.add(key)
    return fired


def plan_from_manifest(manifest, args):
    """Project the supported whole-file subset onto the existing APEXAPlan."""
    value = manifest.to_dict()
    if issues := manifest.issues():
        raise ValueError("; ".join(issues))
    paths = [entry["path"] for entry in value["files"]]
    root = Path(args.directory).expanduser().resolve()
    if any(not Path(path).resolve().is_relative_to(root) for path in paths):
        raise ValueError("manifest detector files must be within the watched directory")
    if paths != sorted(paths):
        raise ValueError("this adapter sorts file paths; manifest frame order needs a different adapter")
    if args.technique != "powder-refinement" and any(
            fnmatch.fnmatch(Path(path).name.lower(), "*dark*")
            or (args.exclude_substring and args.exclude_substring.lower() in Path(path).name.lower())
            for path in paths):
        raise ValueError("integration adapter would exclude a declared detector file by its name")
    locations = {entry["data_location"] for entry in value["files"]}
    if len(locations) != 1:
        raise ValueError("this adapter requires one common detector data_location")
    options = copy(args)
    options.data_location = next(iter(locations))
    options.expected_count = len(paths)  # plan's legacy field is a file count
    corrections = value["corrections"]
    supported = {"calibration", "cif"} if args.technique == "powder-refinement" else {"calibration", "dark"}
    if set(corrections) - supported:
        raise ValueError("this adapter cannot apply corrections: " + ", ".join(sorted(set(corrections) - supported)))
    if "calibration" not in corrections or len(corrections["calibration"]["files"]) != 1:
        raise ValueError("one explicitly identified calibration correction is required")
    options.param_file = corrections["calibration"]["files"][0]["path"]
    if args.param_file and Path(args.param_file).expanduser().resolve() != Path(options.param_file).resolve():
        raise ValueError("CLI parameter file disagrees with manifest calibration identity")
    if args.technique != "powder-refinement" and args.dark_source == "file" and ("dark" not in corrections or len(corrections["dark"]["files"]) != 1):
        raise ValueError("file-dark execution requires one identified shared dark; per-frame dark mapping needs an adapter")
    if "dark" in corrections and args.dark_source != "file":
        raise ValueError("declared dark correction requires --dark-source file")
    if "dark" in corrections:
        dark_path = corrections["dark"]["files"][0]["path"]
        if Path(dark_path).suffix.lower() in (".h5", ".hdf5"):
            if not args.dark_location:
                raise ValueError("HDF5 dark correction requires an explicit --dark-location")
            import h5py
            with h5py.File(dark_path, "r") as stream:
                dark = stream[args.dark_location]
                if dark.ndim not in (2, 3) or not dark.size:
                    raise ValueError("dark correction dataset is empty or has unsupported dimensions")
    if args.technique != "powder-refinement" and args.dark_source == "embedded":
        if not args.dark_location:
            raise ValueError("embedded dark execution requires an explicit --dark-location")
        import h5py
        for path in paths:
            if Path(path).suffix.lower() not in (".h5", ".hdf5"):
                raise ValueError("embedded dark adapter requires HDF5 files")
            with h5py.File(path, "r") as stream:
                dark = stream[args.dark_location]
                if dark.ndim not in (2, 3) or not dark.size:
                    raise ValueError("embedded dark dataset is empty or has unsupported dimensions")
    if args.technique == "powder-refinement":
        if "cif" not in corrections:
            raise ValueError("powder refinement requires identified cif correction files")
        options.cif = [entry["path"] for entry in corrections["cif"]["files"]]
        if getattr(args, "cif", None) and [Path(p).expanduser().resolve() for p in args.cif] != [Path(p).resolve() for p in options.cif]:
            raise ValueError("CLI CIF files disagree with manifest correction identities")
        if any(len(entry["frame_ids"]) != 1 for entry in value["files"]):
            raise ValueError("powder observations require one frame per file")
    stem = "acq_" + hashlib.sha256(value["acquisition_id"].encode()).hexdigest()[:16]
    plan = build_plan(stem, paths, options)
    # Revisions never share an output directory, including late dark revisions.
    plan.result_folder = str(Path(args.result_folder or (root / "APEXA_results")) / stem / manifest.version)
    plan.data.images = paths
    if "dark" in corrections:
        dark_file = corrections["dark"]["files"][0]
        plan.dark.dir = str(Path(dark_file["path"]).parent)
        plan.dark.pattern = glob_escape(Path(dark_file["path"]).name)
        plan.assume("dark.file", dark_file["path"], "manifest SHA-256: " + dark_file["sha256"])
    plan.notes = f"dataset_version={manifest.version}; acquisition revision={value['revision']}"
    plan.assume("_trigger", "authoritative_manifest", "producer declared closure and explicit frame/correction identities")
    if plan.technique == "powder-refinement":
        for observation, entry in zip(plan.refinement.observations, value["files"]):
            observation["frame_id"] = entry["frame_ids"][0]
    if issues := plan.validate():
        raise ValueError("; ".join(issues))
    return plan


def scan_events_once(args, *, jobs=None, executor=None):
    """Consume events, durably admit plans, and run this producer's queued jobs.

    An injected executor(plan) replaces numerical dispatch in deterministic tests.
    Cursor ingestion and submission are separate durable transactions: after a
    crash, every latest snapshot is reconsidered before any queued work runs.
    """
    from apexa_acquisition import AcquisitionStore
    from apexa_dataset import DatasetManifest
    from apexa_jobs import JobStore, default_job_store
    jobs = jobs or (JobStore(args.run_dir) if getattr(args, "run_dir", None) else default_job_store())
    acquisition = AcquisitionStore(jobs)
    acquisition.ingest_log(args.events, producer=args.producer, source_id=getattr(args, "event_source", None))
    jobs.reconcile()
    runs = []
    for snapshot in acquisition.latest(producer=args.producer):
        manifest = DatasetManifest(snapshot["manifest"])
        run_id = snapshot["run_id"]
        if run_id is None:
            try:
                plan = plan_from_manifest(manifest, args)
                manifest.verify_local()
            except (ValueError, OSError, KeyError, ImportError) as exc:
                acquisition.note(manifest.version, "pending", str(exc))
                print(f"  [wait] {snapshot['acquisition_id']}: {exc}")
                continue
            review = jobs.root / "plans" / (manifest.version + ".yaml")
            review.parent.mkdir(parents=True, exist_ok=True)
            plan.to_yaml(str(review))
            if not args.execute or plan.technique not in _AUTO_EXECUTABLE:
                acquisition.note(manifest.version, "review", str(review))
                print(f"  [review] {review}")
                continue
            from apexa_observability import current_ids
            run_id = acquisition.submit(manifest, plan, trace_id=current_ids().get("trace_id"))
        if args.execute:
            def prepare(payload):
                current = DatasetManifest(payload["manifest"])
                if current.version != payload["dataset_version"]:
                    raise ValueError("stored manifest identity mismatch")
                current.verify_local()
                restored = APEXAPlan.from_dict(payload["plan"])
                if not restored.perform_execution or restored.technique not in _AUTO_EXECUTABLE:
                    raise ValueError("stored plan does not authorize this adapter")
                if issues := restored.validate():
                    raise ValueError("; ".join(issues))
            def dispatch(payload, context):
                restored = APEXAPlan.from_dict(payload["plan"])
                if executor is not None:
                    result = executor(restored)
                else:
                    result = execute_plan(restored, manifest=DatasetManifest(payload["manifest"]), context=context)
                # A changed input invalidates a result even if the engine exited
                # successfully. Immutable producer storage is still required.
                try:
                    DatasetManifest(payload["manifest"]).verify_local()
                except (ValueError, OSError, KeyError) as exc:
                    return {"status": "error", "error": f"inputs changed during execution: {exc}",
                            "engine_receipt": result}
                return result
            jobs.run_one(dispatch, run_id=run_id, prepare=prepare)
        row = jobs.get(run_id)
        runs.append(row)
        print(f"  [{row['state']}] {snapshot['acquisition_id']}: {run_id}")
    return runs


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("directory", help="acquisition directory to watch")
    ap.add_argument("--events", help="append-only completion-event JSONL file")
    ap.add_argument("--producer", help="trusted acquisition producer name expected in every event")
    ap.add_argument("--event-source", help="persistent source ID; change explicitly when rotating event logs")
    ap.add_argument("--run-dir", help="shared local run store (default APEXA_RUN_DIR or ~/.apexa/runs)")
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
                    help="execute supported event-manifest plans; quiet-file mode remains review-only")
    args = ap.parse_args(argv)

    if args.events and not args.producer:
        ap.error("--events requires an explicitly trusted --producer")
    if args.expected_count is not None and args.expected_count <= 0:
        ap.error("--expected-count must be positive")
    if not math.isfinite(args.quiet_seconds) or args.quiet_seconds < 0:
        ap.error("--quiet-seconds must be finite and non-negative")
    if not math.isfinite(args.interval) or args.interval <= 0:
        ap.error("--interval must be finite and positive")

    if not os.path.isdir(args.directory):
        ap.error(f"not a directory: {args.directory}")

    mode = "EXECUTE" if args.execute and args.events else "REVIEW"
    print(f"APEXA acquisition watcher [{mode}] — {args.directory} "
          f"(pattern {args.pattern}, technique {args.technique})")
    fired: set = set()
    try:
        while True:
            print(f"-- scan @ {time.strftime('%H:%M:%S')} --")
            if args.events:
                from apexa_observability import span
                with span("acquisition.scan"):
                    scan_events_once(args)
            else:
                fired = scan_once(args, fired)
            if args.once:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nstopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
