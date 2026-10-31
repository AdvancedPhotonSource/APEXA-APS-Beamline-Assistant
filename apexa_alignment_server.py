#!/usr/bin/env python3
"""
APEXA Alignment MCP Server
==========================

Alignment history, analytical helpers and one durable bounded scan runtime.

`diode_max_1d_scan` supports deterministic simulation, immutable replay and
read-only shadow observations. It persists bounded proposals and observations,
fits a Gaussian with explicit quality gates, and returns recommendations. It
never dispatches a motor move. Hardware execution requires a separately
qualified broker and is refused. Other YAML tasks remain specifications or
analytical helpers; installing Xopt does not implement their optimizers.

History ingestion and live single-frame acquisition are separate tools. The
bounded runtime does not invoke either. Runtime priors require exact recipe,
configuration revision/hash and mode matches; legacy experiment history is not
treated as a qualified prior. See docs/ALIGNMENT_RUNTIME.md for the contracts.

Author: APEXA / Argonne National Laboratory
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from apexa_mcp import TracedMCPServer as MCPServer

import logging
logging.getLogger("mcp").setLevel(logging.WARNING)
logging.getLogger("fastmcp").setLevel(logging.WARNING)

mcp = MCPServer("apexa-alignment")

# ---------------------------------------------------------------------------
# Paths and configuration
# ---------------------------------------------------------------------------

APEXA_HOME = Path(os.environ.get("APEXA_HOME", Path.home() / ".apexa"))
ALIGN_DIR = APEXA_HOME / "alignment"
HISTORY_DB = ALIGN_DIR / "history.db"
TASK_LIBRARY_DIR = Path(__file__).parent / "knowledge_base" / "alignment_tasks"
RUNTIME_DB = ALIGN_DIR / "runtime.db"


def _fmt(result: dict) -> str:
    """Canonical JSON formatter used by every tool."""
    return json.dumps(result, indent=2, default=str)


def _ensure_dirs() -> None:
    ALIGN_DIR.mkdir(parents=True, exist_ok=True)
    TASK_LIBRARY_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# History database (SQLite — single file, works everywhere, no server)
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS experiments (
    experiment_id   TEXT PRIMARY KEY,          -- derived from H5 filename or ESAF
    beamline        TEXT,                      -- "1-ID-E", "6-ID-C", ...
    technique       TEXT,                      -- "ff-hedm", "nf-hedm", "saxs", ...
    detector        TEXT,                      -- "pixirad", "varex", "ge", ...
    date_utc        TEXT,                      -- ISO-8601
    source_path     TEXT,                      -- absolute path (local or host:/path)
    sample_name     TEXT,
    energy_kev      REAL,
    outcome         TEXT,                      -- "good" | "bad" | "unknown"
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS scalars (
    experiment_id   TEXT NOT NULL,             -- FK -> experiments
    name            TEXT NOT NULL,             -- "IC1", "IC2", "samX", "slit_H_cen", ...
    value_mean      REAL,
    value_std       REAL,
    value_min       REAL,
    value_max       REAL,
    units           TEXT,
    role            TEXT,                      -- "monitor" | "motor" | "slit" | "diode" | "ic" | "other"
    PRIMARY KEY (experiment_id, name),
    FOREIGN KEY (experiment_id) REFERENCES experiments(experiment_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_scalars_role ON scalars(role);
CREATE INDEX IF NOT EXISTS idx_scalars_name ON scalars(name);
CREATE INDEX IF NOT EXISTS idx_experiments_beamline_technique ON experiments(beamline, technique);

CREATE TABLE IF NOT EXISTS alignment_runs (
    run_id          TEXT PRIMARY KEY,          -- UUID per executed task
    task_name       TEXT NOT NULL,
    beamline        TEXT,
    technique       TEXT,
    started_utc     TEXT NOT NULL,
    finished_utc    TEXT,
    status          TEXT NOT NULL,             -- "converged" | "partial" | "failed" | "dry_run"
    final_score     REAL,
    n_iterations    INTEGER,
    trajectory_json TEXT,                      -- full {inputs, outputs, timestamp} list
    task_spec_json  TEXT                       -- the resolved VOCS + algorithm choice
);

CREATE INDEX IF NOT EXISTS idx_runs_task ON alignment_runs(task_name);
CREATE INDEX IF NOT EXISTS idx_runs_beamline_tech ON alignment_runs(beamline, technique);
"""


def _db() -> sqlite3.Connection:
    """Open the history DB (idempotent — creates file + schema on first call)."""
    _ensure_dirs()
    con = sqlite3.connect(str(HISTORY_DB))
    con.row_factory = sqlite3.Row
    con.executescript(_SCHEMA)
    return con


# ---------------------------------------------------------------------------
# H5 ingestion — extracts canonical scalars from APS experiment H5 files
# ---------------------------------------------------------------------------
#
# APS 1-ID DX files appear in TWO related layouts (both seen in production):
#
#   Variant A ("flat", seen in yesterday's xrds2_lab6_tomo file):
#       SMS/D/HR/samX                         sample motors at root
#       BCS/D/DS_slits/H_cen                  slits at root
#       DMS/DetX                              detector motors at root
#       instrument/Scalers/A/IC1              scalers under instrument/
#
#   Variant B ("instrument/-prefixed", seen in 2026 archive /gdata/dm/1ID/2026):
#       instrument/SMS/<station>/HR/samX      sample motors under instrument/
#       instrument/BCS/<station>/DS_slits/*   station-indexed slits
#       instrument/DMS/DetX                   detector motors under instrument/
#       (scalers often sidecar — not in the H5)
#
# Rather than enumerate every path, we PATTERN-MATCH on the trailing path
# component(s). One tree walk, both variants, resilient to new stations and
# new BCS/SMS branches.
#
# Each pattern: (path_regex, name_fn, role, units)
#   path_regex: matched against the FULL H5 path (anchored at start, flexible end)
#   name_fn:    receives the regex match; returns the canonical scalar name
#   role:       taxonomy bucket for recommend_initial filtering
#   units:      units string stored in the DB

import re as _re

def _basename_name(m):
    return m.group("name") if "name" in m.groupdict() else m.group(0).split("/")[-1]


_H5_SCALAR_PATTERNS: list[tuple[_re.Pattern, Any, str, str]] = [
    # --- Beamstop (part of DMS) — DetBSX / DetBSY ---
    # LISTED FIRST so the more-specific pattern wins over the generic DMS rule.
    # Flat variant: DMS/DetBSX; prefixed variant: instrument/DMS/DetBSX
    (_re.compile(r"(?:instrument/)?DMS/(?P<name>DetBS[XY])$"),
     _basename_name, "beamstop", "mm"),

    # --- Sample motors (SMS) --- rotation + translation ---
    # Station-aware: "samX_C_HR" distinguishes SMS/C/HR/samX from SMS/E/HR/samX
    (_re.compile(r"(?:instrument/)?SMS/(?P<station>[A-Z]+)/HR/(?P<name>sam[XYZ]2?)$"),
     lambda m: m.group("name") + "_" + m.group("station") + "_HR", "motor", "mm"),
    (_re.compile(r"(?:instrument/)?SMS/(?P<station>[A-Z]+)/HL/(?P<name>sam[XYZ]2?)$"),
     lambda m: m.group("name") + "_" + m.group("station") + "_HL", "motor", "mm"),
    # Rotations (samRx/samRy/samRz) → deg, station-aware
    (_re.compile(r"(?:instrument/)?SMS/(?P<station>[A-Z]+)/HR/(?P<name>samR[xyz])$"),
     lambda m: m.group("name") + "_" + m.group("station") + "_HR", "motor", "deg"),
    (_re.compile(r"(?:instrument/)?SMS/(?P<station>[A-Z]+)/(?P<arm>HR|HL)/(?P<name>RotX)$"),
     lambda m: m.group("name") + "_" + m.group("station") + "_" + m.group("arm"), "motor", "deg"),
    # Tilts (station E HL stage, common 1-ID)
    (_re.compile(r"(?:instrument/)?SMS/(?P<station>[A-Z]+)/HL/(?P<name>tilt\d+)$"),
     lambda m: m.group("name") + "_" + m.group("station") + "_HL", "motor", "deg"),
    # Four-circle (station C): chi/phi/th/tth
    (_re.compile(r"(?:instrument/)?SMS/(?P<station>[A-Z]+)/4C/(?P<name>(?:chi|phi|th|tth))$"),
     lambda m: m.group("name") + "_" + m.group("station") + "_4C", "motor", "deg"),

    # --- Slits (BCS, both station variants, station-aware) ---
    # "DS_slits_C_H_cen" distinguishes BCS/C/DS_slits/H_cen from BCS/E/DS_slits/H_cen
    (_re.compile(r"(?:instrument/)?BCS/(?P<station>[A-Z]+)/(?P<kind>DS_slits|US_slits|Slits)/(?P<name>[HV]_(?:cen|size))$"),
     lambda m: m.group("kind") + "_" + m.group("station") + "_" + m.group("name"), "slit", "mm"),
    # White-beam slits (not station-indexed)
    (_re.compile(r"(?:instrument/)?BCS/WB_slits/(?P<name>[HV]_(?:cen|size))$"),
     lambda m: "WB_slits_" + m.group("name"), "slit", "mm"),

    # --- Attenuator ---
    (_re.compile(r"(?:instrument/)?BCS/(?P<station>[A-Z]+)/Attenuator/Position$"),
     lambda m: "Attenuator_" + m.group("station"), "attenuator", "au"),

    # --- Detector motion (DMS) --- flat + prefixed, non-beamstop ---
    # Catches DetX/DetY/DetZ/DetShieldX etc. but NOT DetBS* (handled above).
    (_re.compile(r"(?:instrument/)?DMS/(?P<name>Det(?!BS[XY])[A-Za-z]+)$"),
     _basename_name, "detector_motor", "mm"),

    # --- Scalers / ion chambers ---
    # Legacy flat path: instrument/Scalers/A/IC1 → IC1
    (_re.compile(r"instrument/Scalers/[A-Z]+/(?P<name>IC\d+(?:_[A-Za-z0-9_]+)?)$"),
     _basename_name, "ic", "cps"),
    # Legacy flat path: instrument/Scalers/SIC1_T → SIC1_T
    (_re.compile(r"instrument/Scalers/(?P<name>SIC\d+_[A-Z]+)$"),
     _basename_name, "ic", "cps"),
    # Also pick up any Diode PV
    (_re.compile(r"instrument/Scalers/[A-Z]+/(?P<name>[Dd]iode[A-Za-z0-9_]*)$"),
     _basename_name, "diode", "cps"),

    # --- Insertion device + mono (operating conditions, not alignment knobs
    # but useful for prior conditioning) ---
    (_re.compile(r"(?:instrument/)?HEM/(?P<name>Energy)$"),
     lambda m: "HEM_Energy", "mono", "keV"),
    (_re.compile(r"(?:instrument/)?HRM/(?P<name>Energy)$"),
     lambda m: "HRM_Energy", "mono", "keV"),
    (_re.compile(r"(?:instrument/)?InsertionDevice/(?P<name>IDEnergy[A-Z]*)$"),
     _basename_name, "insertion_device", "keV"),

    # --- Storage ring current (for intensity normalization) ---
    (_re.compile(r"(?:instrument/)?StorageRing/(?P<name>SRCurrent)$"),
     _basename_name, "storage_ring", "mA"),
]


# Metadata (string-valued, non-timeseries): both-variant patterns.
_SCALAR_META_PATTERNS: list[tuple[_re.Pattern, str]] = [
    (_re.compile(r"^(?:instrument/)?Detector/DetModel$"),         "detector"),
    (_re.compile(r"^DetModel$"),                                  "detector"),
    (_re.compile(r"^(?:instrument/)?HEM/Energy$"),                "energy_kev"),
    (_re.compile(r"^StorageRing/DateTimeStart$"),                 "date_utc"),
    (_re.compile(r"^misc/DetSampleName$"),                        "sample_name"),
    (_re.compile(r"^active_instrument/ExpID$"),                   "experiment_id_hint"),
    (_re.compile(r"^active_instrument/Technique$"),               "technique_hint"),
    (_re.compile(r"^active_instrument/SE$"),                      "sample_environment"),
]


def _scan_h5_scalars(f) -> list[dict]:
    """Walk the H5 tree and emit one scalar record per pattern-matched dataset.

    Walk-and-match, not path-and-lookup — robust to the two layout variants
    (flat vs instrument/-prefixed) and to new stations the installer adds.
    """
    out: list[dict] = []
    seen_names: set[str] = set()   # dedup if two patterns match the same path

    def visitor(h5path, obj):
        try:
            import h5py
            if not isinstance(obj, h5py.Dataset):
                return
        except Exception:
            return
        for pat, name_fn, role, units in _H5_SCALAR_PATTERNS:
            m = pat.match(h5path)
            if not m:
                continue
            try:
                name = name_fn(m) if callable(name_fn) else name_fn
            except Exception:
                continue
            if name in seen_names:
                continue
            seen_names.add(name)
            summary = _summarize_array(obj[()])
            out.append({
                "name":       name,
                "value_mean": summary["mean"],
                "value_std":  summary["std"],
                "value_min":  summary["min"],
                "value_max":  summary["max"],
                "units":      units,
                "role":       role,
            })
            return   # first-match-wins per path

    f.visititems(visitor)
    return out


def _scan_h5_meta(f) -> dict:
    """Walk the H5 tree and extract canonical metadata fields (strings + energies)."""
    import h5py
    out: dict = {}

    def visitor(h5path, obj):
        if not isinstance(obj, h5py.Dataset):
            return
        for pat, key in _SCALAR_META_PATTERNS:
            if pat.match(h5path):
                try:
                    v = obj[()]
                    if hasattr(v, "shape") and getattr(v, "ndim", 0) >= 1:
                        try:
                            v = v.ravel()[0]
                        except Exception:
                            return
                    out[key] = _decode_bytes(v)
                except Exception:
                    pass
                return

    f.visititems(visitor)
    return out


def _summarize_array(arr) -> dict:
    """Numpy-free summary when numpy is absent; numpy fast-path when present."""
    try:
        import numpy as np  # type: ignore
        a = np.asarray(arr).ravel()
        if a.size == 0:
            return {"mean": None, "std": None, "min": None, "max": None}
        a = a[~np.isnan(a)] if a.dtype.kind == "f" else a
        if a.size == 0:
            return {"mean": None, "std": None, "min": None, "max": None}
        return {
            "mean": float(a.mean()),
            "std":  float(a.std()),
            "min":  float(a.min()),
            "max":  float(a.max()),
        }
    except Exception:
        try:
            vals = [float(v) for v in list(arr)]
            n = len(vals)
            if n == 0:
                return {"mean": None, "std": None, "min": None, "max": None}
            mu = sum(vals) / n
            var = sum((v - mu) ** 2 for v in vals) / n
            return {"mean": mu, "std": var ** 0.5, "min": min(vals), "max": max(vals)}
        except Exception:
            return {"mean": None, "std": None, "min": None, "max": None}


def _decode_bytes(v: Any) -> Any:
    """H5 string fields come back as bytes; recode to str for JSON/DB."""
    if isinstance(v, bytes):
        try:
            return v.decode("utf-8", errors="replace").strip("\x00").strip()
        except Exception:
            return str(v)
    return v


def _ingest_one_h5(path: Path, outcome: str = "unknown", notes: str = "") -> dict:
    """Extract canonical scalars + metadata from one H5 file into the DB.

    Returns a summary dict for the caller. Idempotent on experiment_id (REPLACE).
    """
    try:
        import h5py  # type: ignore
    except ImportError:
        return {"status": "error", "error": "h5py not installed; `uv sync` first"}

    if not path.is_file():
        return {"status": "error", "error": f"file not found: {path}"}

    exp_id = path.stem                 # e.g. "xrds2_lab6_tomo_000239"
    meta: dict[str, Any] = {
        "experiment_id": exp_id,
        "source_path":   str(path.resolve()),
        "outcome":       outcome,
        "notes":         notes or None,
    }
    scalars: list[dict] = []

    try:
        with h5py.File(path, "r") as f:
            # Metadata (strings, energies, sample names) via pattern walk
            meta.update(_scan_h5_meta(f))
            # Scalar observations (motors, slits, ICs, detector motion) via pattern walk
            for s in _scan_h5_scalars(f):
                s["experiment_id"] = exp_id
                scalars.append(s)
    except Exception as e:
        return {"status": "error", "error": f"H5 read failed: {e}"}

    # Normalise known meta fields
    meta.setdefault("beamline", "")
    if "DetModel" in meta.get("beamline", ""):
        pass  # leave; filled below if we find the key separately
    if "detector" in meta:
        meta["detector"] = str(meta["detector"]).strip()
    if "energy_kev" in meta:
        try:
            # HEM/Energy may be in keV already, or raw PV value — leave verbatim
            meta["energy_kev"] = float(meta["energy_kev"])
        except Exception:
            meta.pop("energy_kev", None)

    # DB upsert
    con = _db()
    try:
        con.execute(
            """
            INSERT OR REPLACE INTO experiments
              (experiment_id, beamline, technique, detector, date_utc,
               source_path, sample_name, energy_kev, outcome, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                meta.get("experiment_id"),
                meta.get("beamline") or _guess_beamline(meta.get("source_path", "")),
                meta.get("technique_hint") or _guess_technique(meta),
                meta.get("detector"),
                meta.get("date_utc"),
                meta.get("source_path"),
                meta.get("sample_name"),
                meta.get("energy_kev"),
                outcome,
                notes or None,
            ),
        )
        # Clear old scalars for this experiment (REPLACE semantics)
        con.execute("DELETE FROM scalars WHERE experiment_id = ?", (exp_id,))
        con.executemany(
            """
            INSERT INTO scalars
              (experiment_id, name, value_mean, value_std, value_min,
               value_max, units, role)
            VALUES (:experiment_id, :name, :value_mean, :value_std,
                    :value_min, :value_max, :units, :role)
            """,
            scalars,
        )
        con.commit()
    finally:
        con.close()

    return {
        "status": "ok",
        "experiment_id": exp_id,
        "scalars_ingested": len(scalars),
        "meta": {k: v for k, v in meta.items() if k != "source_path"},
    }


def _guess_beamline(source_path: str) -> str:
    """Heuristic beamline inference from the archive path."""
    p = source_path.lower()
    if "/1id" in p or "/1-id" in p or "s1iduser" in p or "/1ide" in p:
        return "1-ID-E"
    if "/6id" in p or "/6-id" in p:
        return "6-ID-C"
    if "/20id" in p or "/20-id" in p:
        return "20-ID"
    if "/11id" in p or "/11-id" in p:
        return "11-ID"
    return ""


def _guess_technique(meta: dict) -> str:
    """Infer technique from sample_name or ExpID conventions."""
    hint = (meta.get("technique_hint") or "") + " " + \
           (meta.get("sample_name") or "") + " " + \
           (meta.get("experiment_id") or "")
    hint = hint.lower()
    for keyword, tech in [
        ("tomo", "tomography"),
        ("saxs", "saxs"),
        ("waxs", "waxs"),
        ("ff_hedm", "ff-hedm"), ("ffhedm", "ff-hedm"), ("ff-hedm", "ff-hedm"),
        ("nf_hedm", "nf-hedm"), ("nfhedm", "nf-hedm"), ("nf-hedm", "nf-hedm"),
        ("pf_hedm", "pf-hedm"), ("pfhedm", "pf-hedm"), ("pf-hedm", "pf-hedm"),
        ("ceo2", "calibration"), ("lab6", "calibration"), ("calibrat", "calibration"),
    ]:
        if keyword in hint:
            return tech
    return ""


# ---------------------------------------------------------------------------
# MCP tools
# ---------------------------------------------------------------------------

@mcp.tool()
def ingest_experiment_h5(
    path: str,
    outcome: str = "unknown",
    notes: str = "",
) -> str:
    """Ingest ONE experiment H5 file into the APEXA alignment history DB.

    Extracts beamline scalars (IC/diode readings, motor positions, slit settings,
    beamstop positions, detector positions) + metadata (beamline, technique,
    detector model, sample, energy, date) and stores summary statistics.

    The DB (~/.apexa/alignment/history.db) is a persistent SQLite file populated
    across sessions; subsequent calls to `recommend_initial` use it to warm-start
    alignment runs for similar (beamline × technique × detector) contexts.

    Args:
        path:    Absolute path to the experiment H5 file (local only; use
                 `ingest_experiment_archive` for a directory, including remote).
        outcome: Quality tag — "good", "bad", "unknown". Only "good" runs are
                 used as warm-start priors; "bad" is anti-prior (avoid).
        notes:   Optional free-text annotation (e.g. "post-calibration drift").

    Returns:
        JSON with ingestion summary.
    """
    return _fmt(_ingest_one_h5(Path(path), outcome=outcome, notes=notes))


@mcp.tool()
def ingest_experiment_archive(
    archive_root: str,
    pattern: str = "*.h5",
    outcome: str = "unknown",
    max_files: int = 500,
    host: str = "",
) -> str:
    """Bulk-ingest a directory of experiment H5 files (local OR remote-via-SSH).

    For an SSH-routed path (host="copland" or auto-detected from the registry),
    the archive is enumerated remotely via `ls`; each file is scp'd to a temp
    location, ingested, then the local copy is removed. The DB stays local.

    Typical locations at 1-ID-E:
        * Active (local on beamline host): ~s1iduser/mnt/s1c
        * Archive (remote on copland/kodaly): /gdata/dm/1ID/2026

    Args:
        archive_root: Directory to crawl (local absolute path, or SSH-reachable
                      path when `host` is set or inferred from the registry).
        pattern:      Glob pattern (default "*.h5").
        outcome:      Quality tag applied to all files (default "unknown").
        max_files:    Safety cap to prevent runaway ingestion (default 500).
        host:         SSH host override (empty → use apexa_remote_exec locality
                      decision; local path → ingest in-place).

    Returns:
        JSON summary: {files_found, files_ingested, errors, elapsed_s}.
    """
    t0 = time.time()
    root = Path(archive_root)

    files: list[Path] = []
    local_mode = True
    if host:
        local_mode = False
    else:
        # Rely on apexa_remote_exec locality decision when available.
        try:
            from apexa_remote_exec import decide_exec_host  # type: ignore
            decision = decide_exec_host(archive_root)
            if decision.where == "remote":
                local_mode = False
                host = decision.host
        except Exception:
            local_mode = True  # fall back to local-only if the helper is absent

    if local_mode:
        if not root.exists():
            return _fmt({"status": "error", "error": f"not found: {archive_root}"})
        files = sorted(root.rglob(pattern))[:max_files]
    else:
        try:
            from apexa_remote_exec import remote_run  # type: ignore
            # Use `find` to enumerate; cap by `head` for the safety limit.
            rc, out, err = remote_run(
                host, f"find {archive_root!r} -name {pattern!r} -type f | head -n {max_files}",
                timeout=60,
            )
            if rc != 0:
                return _fmt({"status": "error",
                             "error": f"remote find failed: {err or out}",
                             "host": host})
            files = [Path(line) for line in out.splitlines() if line.strip()]
        except Exception as e:
            return _fmt({"status": "error", "error": f"remote enum failed: {e}"})

    ingested = 0
    errors: list[dict] = []
    for p in files:
        try:
            if not local_mode:
                # Pull one file to a temp location for ingestion
                from apexa_remote_exec import remote_get  # type: ignore
                import tempfile
                with tempfile.NamedTemporaryFile(suffix=".h5", delete=False) as tmp:
                    local = Path(tmp.name)
                remote_get(host, str(p), str(local))
                result = _ingest_one_h5(local, outcome=outcome,
                                         notes=f"src={host}:{p}")
                try:
                    local.unlink()
                except Exception:
                    pass
            else:
                result = _ingest_one_h5(p, outcome=outcome)
            if result.get("status") == "ok":
                ingested += 1
            else:
                errors.append({"file": str(p), "error": result.get("error", "")})
        except Exception as e:
            errors.append({"file": str(p), "error": str(e)})

    return _fmt({
        "status": "ok",
        "archive_root": archive_root,
        "host": host or "local",
        "files_found": len(files),
        "files_ingested": ingested,
        "errors": errors[:20],   # cap error list for sanity
        "elapsed_s": round(time.time() - t0, 2),
    })


@mcp.tool()
def recommend_initial(
    beamline: str = "",
    technique: str = "",
    detector: str = "",
    role: str = "ic",
    min_good_runs: int = 3,
) -> str:
    """Return typical values of scalars (IC/diode/slit/motor) from past GOOD runs.

    Query shape: scalars whose role matches, from experiments filtered on
    (beamline, technique, detector) with outcome='good'. Grouped per scalar name
    with mean/std across runs — the agent uses mean±std as a BO warm-start prior
    or presents it as a human-readable "typical range" to the operator.

    Args:
        beamline:        e.g. "1-ID-E" (empty = any)
        technique:       e.g. "ff-hedm", "saxs" (empty = any)
        detector:        e.g. "pixirad", "varex" (empty = any)
        role:            Which family of scalars to recommend. One of:
                           "ic", "diode", "slit", "motor", "beamstop",
                           "detector_motor", or "all".
        min_good_runs:   Only report scalars observed in ≥N good runs
                         (default 3; avoids one-shot anomalies).

    Returns:
        JSON: {filter, n_good_runs_matched, recommendations: [{name, units,
                typical_mean, typical_std, across_n_runs, span: [min, max]}, ...]}
    """
    con = _db()
    try:
        # Build the experiment-level WHERE clause first (used by BOTH the
        # n_runs count and the scalar aggregation). The role clause is SEPARATE
        # because it only applies to the scalar query, not the experiment count.
        where = ["e.outcome = 'good'"]
        exp_params: list[Any] = []
        if beamline:
            where.append("e.beamline = ?"); exp_params.append(beamline)
        if technique:
            where.append("e.technique = ?"); exp_params.append(technique)
        if detector:
            where.append("e.detector LIKE ?"); exp_params.append(f"%{detector}%")
        where_sql = " AND ".join(where)

        role_clause = ""
        scalar_params = list(exp_params)
        if role and role != "all":
            role_clause = " AND s.role = ?"
            scalar_params.append(role)

        n_runs = con.execute(
            f"SELECT COUNT(*) AS n FROM experiments e WHERE {where_sql}",
            exp_params,
        ).fetchone()["n"]

        rows = con.execute(
            f"""
            SELECT s.name, s.units, s.role,
                   AVG(s.value_mean) AS typ_mean,
                   AVG(COALESCE(s.value_std, 0)) AS typ_within_run_std,
                   COUNT(DISTINCT s.experiment_id) AS n_runs,
                   MIN(s.value_min) AS span_min,
                   MAX(s.value_max) AS span_max
            FROM scalars s
            JOIN experiments e ON e.experiment_id = s.experiment_id
            WHERE {where_sql}{role_clause}
            GROUP BY s.name, s.units, s.role
            HAVING n_runs >= ?
            ORDER BY s.role, s.name
            """,
            scalar_params + [min_good_runs],
        ).fetchall()
    finally:
        con.close()

    recs = []
    for r in rows:
        recs.append({
            "name":         r["name"],
            "role":         r["role"],
            "units":        r["units"],
            "typical_mean": r["typ_mean"],
            "typical_std":  r["typ_within_run_std"],
            "across_n_runs": r["n_runs"],
            "span":         [r["span_min"], r["span_max"]],
        })

    return _fmt({
        "filter": {"beamline": beamline, "technique": technique,
                   "detector": detector, "role": role,
                   "min_good_runs": min_good_runs},
        "n_good_runs_matched": n_runs,
        "recommendations": recs,
        "notes": "typical_std is averaged within-run noise; span is the "
                 "aggregate min/max across all matched runs.",
    })


@mcp.tool()
def query_alignment_history(
    beamline: str = "",
    technique: str = "",
    outcome: str = "",
    task_name: str = "",
    limit: int = 50,
) -> str:
    """Browse the alignment history DB (experiments + past alignment runs).

    Args:
        beamline:   filter
        technique:  filter
        outcome:    "good" | "bad" | "unknown" (empty = any)
        task_name:  filter alignment_runs by task_name (empty = any; "" = all)
        limit:      row cap per table

    Returns:
        JSON {experiments: [...], runs: [...], totals: {experiments, runs}}.
    """
    con = _db()
    try:
        clauses = []; params: list[Any] = []
        if beamline:   clauses.append("beamline = ?");  params.append(beamline)
        if technique:  clauses.append("technique = ?"); params.append(technique)
        if outcome:    clauses.append("outcome = ?");   params.append(outcome)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

        experiments = [dict(r) for r in con.execute(
            f"SELECT * FROM experiments{where} ORDER BY date_utc DESC LIMIT ?",
            params + [limit]
        ).fetchall()]
        total_experiments = con.execute(
            f"SELECT COUNT(*) AS n FROM experiments{where}", params
        ).fetchone()["n"]

        run_clauses = []; run_params: list[Any] = []
        if beamline:   run_clauses.append("beamline = ?");  run_params.append(beamline)
        if technique:  run_clauses.append("technique = ?"); run_params.append(technique)
        if task_name:  run_clauses.append("task_name = ?"); run_params.append(task_name)
        run_where = (" WHERE " + " AND ".join(run_clauses)) if run_clauses else ""
        runs = [dict(r) for r in con.execute(
            f"SELECT run_id, task_name, beamline, technique, started_utc, "
            f"finished_utc, status, final_score, n_iterations "
            f"FROM alignment_runs{run_where} ORDER BY started_utc DESC LIMIT ?",
            run_params + [limit]
        ).fetchall()]
        total_runs = con.execute(
            f"SELECT COUNT(*) AS n FROM alignment_runs{run_where}", run_params
        ).fetchone()["n"]
    finally:
        con.close()

    return _fmt({
        "filter": {"beamline": beamline, "technique": technique,
                   "outcome": outcome, "task_name": task_name},
        "experiments": experiments,
        "runs": runs,
        "totals": {"experiments": total_experiments, "runs": total_runs,
                   "db_path": str(HISTORY_DB)},
    })


# ---------------------------------------------------------------------------
# Analytical alignment (no live beam needed): rotation axis + drift from tomo
# ---------------------------------------------------------------------------

@mcp.tool()
def fit_rotation_axis(
    tomo_h5: str,
    projection_dataset: str = "exchange/data",
    theta_dataset: str = "exchange/theta",
    dark_dataset: str = "exchange/data_dark",
    white_dataset: str = "exchange/data_white",
    row_band_half_width: int = 2,
) -> str:
    """Fit the center-of-rotation + vertical beam drift from a tomo projection stack.

    Pure-classical pipeline (no ML needed, as demonstrated on a difficult
    LaB6-on-diamond case at 1-ID: Vo CoR within 0.5 px of ideal, drift fit RMS
    < 1.5 rows). Three steps:

      1. Normalize: -log((projs - dark_mean) / (white_mean - dark_mean))
      2. Pick the detector-row band with maximum motion variance across angles.
      3. Vo's method (algotom) on the 0..180° half-sinogram → CoR.
      4. Centroid trajectory + sinusoid+drift fit → vertical drift rate.

    No motor moves are issued. Output = {cor_px, drift_per_360deg,
    recommended_correction_moves} the agent can hand to the EPICS motor server.

    Args:
        tomo_h5:                Path to the tomography H5 file (local).
        projection_dataset:     H5 dataset path for the (angle, y, x) stack.
        theta_dataset:          H5 dataset path for the (angle,) array in degrees.
        dark_dataset:           H5 dataset path for dark field(s).
        white_dataset:          H5 dataset path for white (flat) field(s).
        row_band_half_width:    Half-width of the detector-row band to analyze
                                around the automatically-chosen best row.

    Returns:
        JSON report + rotation-axis correction proposal.
    """
    try:
        import h5py                  # type: ignore
        import numpy as np           # type: ignore
    except ImportError as e:
        return _fmt({"status": "error", "error": f"missing dep: {e}"})

    p = Path(tomo_h5)
    if not p.is_file():
        return _fmt({"status": "error", "error": f"not found: {tomo_h5}"})

    try:
        with h5py.File(p, "r") as f:
            if projection_dataset not in f:
                return _fmt({"status": "error",
                             "error": f"missing dataset: {projection_dataset}"})
            proj = f[projection_dataset][:].astype("float32")
            dark = (f[dark_dataset][:].astype("float32").mean(0)
                    if dark_dataset in f else None)
            white = (f[white_dataset][:].astype("float32").mean(0) - (dark if dark is not None else 0)
                     if white_dataset in f else None)
            theta = (f[theta_dataset][:].astype("float64")
                     if theta_dataset in f else None)
    except Exception as e:
        return _fmt({"status": "error", "error": f"H5 read failed: {e}"})

    if theta is None:
        theta = np.linspace(-180.0, 180.0, proj.shape[0], endpoint=False)
    if dark is None:
        dark = np.zeros(proj.shape[1:], dtype="float32")
    if white is None:
        white = np.ones(proj.shape[1:], dtype="float32")
    white = np.maximum(white, 1.0)

    # Normalize
    trans = (proj - dark) / white
    trans = np.clip(trans, 0.05, 1.5)
    sino_full = -np.log(trans)   # (n_angle, n_row, n_col)

    # Pick best row (excl. edges)
    row_var = sino_full.var(axis=(0, 2))
    row_var[:5] = 0; row_var[-5:] = 0
    best_row = int(np.argmax(row_var))
    rs0 = max(0, best_row - row_band_half_width)
    rs1 = min(sino_full.shape[1], best_row + row_band_half_width + 1)
    sino_band = sino_full[:, rs0:rs1].mean(axis=1)   # (n_angle, n_col)

    # Half-sinogram for Vo's method
    half_mask = (theta >= 0.0) & (theta < 180.0)
    sino_180 = sino_band[half_mask]
    cor_vo: Optional[float] = None
    vo_error: Optional[str] = None
    try:
        import algotom.prep.calculation as calc   # type: ignore
        cor_vo = float(calc.find_center_vo(sino_180, step=0.25, radius=8, ratio=0.5))
    except ImportError:
        vo_error = "algotom not installed (`uv pip install algotom` or `uv sync --extra alignment`)"
    except Exception as e:
        vo_error = str(e)

    # Vertical centroid trajectory → drift fit (no scipy fallback needed)
    centroid_y = np.full(sino_full.shape[0], np.nan)
    for k in range(sino_full.shape[0]):
        cm = sino_full[k].mean(axis=1)
        cm = cm - cm.min()
        if cm.sum() <= 0:
            continue
        thr = np.quantile(cm, 0.80)
        m = cm >= thr
        if not m.any():
            continue
        ys = np.arange(sino_full.shape[1])[m]
        ws = cm[m]
        centroid_y[k] = float((ys * ws).sum() / ws.sum())

    th_rad = np.deg2rad(theta)
    drift_rows_per_360 = None
    rms_rows = None
    try:
        from scipy.optimize import curve_fit  # type: ignore

        def model(t, A, phi, b, c):
            return A * np.sin(t + phi) + b * t + c

        valid = ~np.isnan(centroid_y)
        if valid.sum() > 20:
            A0 = (np.nanmax(centroid_y) - np.nanmin(centroid_y)) / 2
            c0 = float(np.nanmean(centroid_y))
            popt, _ = curve_fit(model, th_rad[valid], centroid_y[valid],
                                 p0=[A0, 0.0, 0.0, c0])
            A, phi, brad, c = popt
            full = brad * (th_rad[-1] - th_rad[0])
            drift_rows_per_360 = float(full)
            resid = centroid_y - model(th_rad, *popt)
            rms_rows = float(np.sqrt(np.nanmean(resid ** 2)))
    except Exception:
        pass

    # Convergence verdict
    ideal_center = sino_180.shape[1] / 2.0 if sino_180.size else None
    cor_offset_px = (cor_vo - ideal_center) if (cor_vo is not None and ideal_center is not None) else None

    status = "converged"
    if cor_vo is None:
        status = "partial"           # drift measured, CoR unavailable → partial

    report = {
        "status": status,
        "input": {"tomo_h5": str(p), "n_proj": int(proj.shape[0]),
                   "detector_shape": list(proj.shape[1:]),
                   "theta_deg_range": [float(theta.min()), float(theta.max())]},
        "best_row": best_row,
        "rows_used": [rs0, rs1 - 1],
        "center_of_rotation_px": cor_vo,
        "ideal_center_px": ideal_center,
        "cor_offset_px": cor_offset_px,
        "vo_error": vo_error,
        "vertical_drift_rows_per_360deg": drift_rows_per_360,
        "vertical_fit_rms_rows": rms_rows,
        "recommended_correction_proposal": _rotation_axis_correction_moves(
            cor_offset_px=cor_offset_px,
            drift_rows=drift_rows_per_360,
        ),
        "notes": [
            "No motor moves have been issued. Correction proposals are for review.",
            "Dispatch chosen moves through epics_motor_server (safety-gated).",
            "The vertical-drift fit assumes a linear drift + rotational sinusoid.",
        ],
    }
    return _fmt(report)


def _rotation_axis_correction_moves(cor_offset_px: Optional[float],
                                     drift_rows: Optional[float]) -> dict:
    """Translate pixel offsets into motor-move proposals (geometry-pending).

    The pixel→µm scale and the sign convention are beamline-specific and live
    in the per-beamline RUNBOOK.md. This function emits the raw offsets so the
    agent can apply the correct scale from the capsule.
    """
    out: dict[str, Any] = {}
    if cor_offset_px is not None:
        out["rotation_axis_px"] = {
            "detector_x_offset_px": cor_offset_px,
            "action": "translate sample stage along X by -offset × px_to_mm "
                      "(sign per RUNBOOK) OR translate detector by +offset × px_to_mm",
        }
    if drift_rows is not None:
        out["vertical_drift"] = {
            "drift_rows_per_360deg": drift_rows,
            "action": "if |drift| > 1 row, re-check beam vertical steering OR "
                      "re-tune upstream slits V_cen; drift is OBSERVED, not corrected here",
        }
    return out


# ---------------------------------------------------------------------------
# Task library — list the YAML task specs available for `run_alignment`
# ---------------------------------------------------------------------------

def _runtime_call(method: str, *args, **kwargs) -> str:
    from apexa_alignment_runtime import AlignmentError, AlignmentRuntime
    try:
        return _fmt(getattr(AlignmentRuntime(RUNTIME_DB), method)(*args, **kwargs))
    except AlignmentError as exc:
        return _fmt({"status": "error", "error_code": exc.code, "detail": exc.detail,
                     "motion_executed": False, "hardware_alignment_verified": False})


@mcp.tool()
def preflight_alignment(plan: dict) -> str:
    """Validate an explicit bounded diode scan plan without device access or writes.

    Returns canonical plan/configuration hashes and the software preflight scope.
    Modes: simulation, replay, shadow. Hardware mode is refused. The JSON plan
    schema and examples are in docs/ALIGNMENT_RUNTIME.md.
    """
    from apexa_alignment_runtime import AlignmentError, preflight
    try:
        return _fmt(preflight(plan))
    except AlignmentError as exc:
        return _fmt({"status": "error", "error_code": exc.code, "detail": exc.detail,
                     "motion_executed": False, "hardware_alignment_verified": False})


@mcp.tool()
def run_alignment(plan: dict, max_steps: int = 21) -> str:
    """Create and advance a durable diode scan in simulation/replay/shadow mode.

    Persists each candidate before observation. Never commands or reads hardware.
    Shadow returns a pending advisory request for an external read-only monitor.
    A completed fit is a recommendation, never verified hardware alignment.
    """
    from apexa_alignment_runtime import AlignmentError, AlignmentRuntime, _integer
    try:
        _integer(max_steps, "max_steps", 1, 201)
        runtime = AlignmentRuntime(RUNTIME_DB)
        created = runtime.create(plan)
        return _fmt(runtime.advance(created["run_id"], created["configuration_hash"], max_steps=max_steps,
                                    expected_revision=created["revision"]))
    except AlignmentError as exc:
        out = {"status": "error", "error_code": exc.code, "detail": exc.detail,
               "motion_executed": False, "hardware_alignment_verified": False}
        if "created" in locals():
            out["run_id"] = created["run_id"]
        return _fmt(out)


@mcp.tool()
def get_alignment_status(run_id: str, include_trajectory: bool = False) -> str:
    """Read compact durable status; opt in to the frozen plan, samples and event log."""
    return _runtime_call("status", run_id, include_trajectory=include_trajectory)


@mcp.tool()
def resume_alignment(run_id: str, configuration_hash: str, expected_revision: int,
                     max_steps: int = 21) -> str:
    """Resume the immutable plan after verifying configuration, recipe and revision.

    An expired worker lease is recovered; an active lease refuses another owner.
    Terminal runs remain terminal. This never resumes or dispatches hardware.
    """
    return _runtime_call("advance", run_id, configuration_hash, expected_revision=expected_revision,
                         max_steps=max_steps)


@mcp.tool()
def submit_alignment_observation(run_id: str, observation: dict, expected_revision: int) -> str:
    """Commit one fresh, identity-matched observation to a pending shadow request.

    Caller supplies request/step/source/sequence/configuration, positions, units
    and acquisition time. Invalid observations fail the run with a durable event.
    These external observations are advisory and have no broker attestation.
    """
    return _runtime_call("submit", run_id, observation, expected_revision=expected_revision)


@mcp.tool()
def stop_alignment(run_id: str, expected_revision: Optional[int] = None) -> str:
    """Stop recommendations and revoke the worker lease; no subsequent sample commits.

    This is not a physical emergency stop. Use the beamline's independent stop
    for hardware controlled outside this runtime. No return move is attempted.
    """
    return _runtime_call("stop", run_id, expected_revision=expected_revision)

@mcp.tool()
def list_alignment_tasks(filter: str = "") -> str:
    """List available alignment task templates (YAML files in the task library).

    Args:
        filter: Case-insensitive substring filter on task name.

    Returns:
        JSON [{name, path, description, variables, objectives}, ...].
    """
    _ensure_dirs()
    out: list[dict] = []
    for yml in sorted(TASK_LIBRARY_DIR.glob("*.yaml")):
        if filter and filter.lower() not in yml.stem.lower():
            continue
        info = {"name": yml.stem, "path": str(yml), "hardware_execution": False,
                "runtime_modes": ["simulation", "replay", "shadow"] if yml.stem == "diode_max_1d_scan" else [],
                "implementation": "bounded_scan_runtime" if yml.stem == "diode_max_1d_scan" else "specification_or_analytical_helper"}
        try:
            # Lightweight parse — avoids a hard dep on PyYAML at list time.
            text = yml.read_text()
            info["description"] = _first_description_line(text) or ""
            info["variables"] = _grep_block_keys(text, "variables")
            info["objectives"] = _grep_block_keys(text, "objectives")
        except Exception as e:
            info["parse_error"] = str(e)
        out.append(info)

    if not out:
        return _fmt({"status": "ok", "tasks": [], "task_library_dir": str(TASK_LIBRARY_DIR),
                     "note": "No tasks installed yet. See knowledge_base/alignment_tasks/."})
    return _fmt({"status": "ok", "task_library_dir": str(TASK_LIBRARY_DIR),
                 "tasks": out})


def _first_description_line(text: str) -> str:
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("description:"):
            return s.split(":", 1)[1].strip().strip('"\'')
    return ""


def _grep_block_keys(text: str, block_name: str) -> list[str]:
    """Dumb YAML block-key extractor — avoids forcing PyYAML at list time."""
    out: list[str] = []
    in_vocs = False
    in_block = False
    block_indent = None
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped == "vocs:":
            in_vocs = True; continue
        if in_vocs and stripped.startswith(f"{block_name}:"):
            in_block = True; block_indent = len(raw) - len(raw.lstrip()); continue
        if in_block:
            cur_indent = len(raw) - len(raw.lstrip())
            if raw.strip() == "" or raw.lstrip().startswith("#"):
                continue
            if cur_indent <= (block_indent or 0):
                break
            key = stripped.split(":", 1)[0]
            out.append(key)
    return out


# ---------------------------------------------------------------------------
# Live PVA + Xopt — present as stubs that fail loudly when the extras aren't
# installed, so the agent gets a clear message and does not fabricate a result.
# ---------------------------------------------------------------------------

@mcp.tool()
def grab_pva_frame(pv: str, timeout_s: float = 5.0) -> str:
    """Grab ONE frame from an EPICS PVA detector stream (same stream ImageJ reads).

    Requires `pvapy` (install via `uv sync --extra alignment`). Returns the frame
    shape + a quick-look stats block — the raw array is NOT returned over MCP
    (too large); it is cached under ~/.apexa/alignment/frames/<timestamp>.npy
    for the follow-up `analyze_detector_frame` call.

    Args:
        pv:        PVA channel name, e.g. "1ide:pixirad:image".
        timeout_s: How long to wait for a frame.

    Returns:
        JSON {status, frame_cached_at, shape, dtype, stats}.
    """
    try:
        import pvapy as pva      # type: ignore
        import numpy as np       # type: ignore
    except ImportError:
        return _fmt({
            "status": "error",
            "error": "pvapy not installed",
            "install": "uv sync --extra alignment",
            "note": "This tool requires PV Access (EPICS7). Base APEXA does not "
                    "bundle it (keeps the offline install clean).",
        })

    try:
        channel = pva.Channel(pv)
        value = channel.get(f"field()", float(timeout_s))
        # pvapy area-detector image: get the ND array
        arr = value["value"][0]["ubyteValue"] if "value" in value else None
        if arr is None:
            # try common alternative keys for 16-bit cameras
            for key in ("ushortValue", "shortValue", "intValue"):
                try:
                    arr = value["value"][0][key]
                    break
                except Exception:
                    continue
        if arr is None:
            return _fmt({"status": "error",
                         "error": "could not decode frame payload from PVA value"})
        a = np.asarray(arr)
        # NTNDArray carries 'dimension' metadata — reshape when present
        try:
            dims = value["dimension"]
            shape = [d["size"] for d in dims]
            a = a.reshape(shape[::-1])
        except Exception:
            pass

        _ensure_dirs()
        frames_dir = ALIGN_DIR / "frames"
        frames_dir.mkdir(exist_ok=True)
        ts = time.strftime("%Y%m%dT%H%M%S")
        cache = frames_dir / f"{pv.replace(':', '_')}_{ts}.npy"
        np.save(cache, a)

        return _fmt({
            "status": "ok",
            "pv": pv,
            "frame_cached_at": str(cache),
            "shape": list(a.shape),
            "dtype": str(a.dtype),
            "stats": {"min": float(a.min()), "max": float(a.max()),
                       "mean": float(a.mean()), "std": float(a.std())},
        })
    except Exception as e:
        return _fmt({"status": "error", "error": str(e), "pv": pv})


@mcp.tool()
def analyze_detector_frame(
    frame_path: str,
    geometry: str = "rect",
) -> str:
    """Analyze a cached detector frame: locate beam centroid + beamstop.

    Four geometry presets:
      * "rect"               — rectangular beam; returns centroid + width/height
      * "square"             — square beam; same as rect, uses symmetric fit
      * "circ"               — circular beam; returns centroid + radius
      * "circ_with_beamstop" — circular beam PLUS circular beamstop; returns
                               both centroids and the distance between them
                               (the alignment objective for SAXS centering)

    Args:
        frame_path: Path to a .npy file produced by `grab_pva_frame`.
        geometry:   One of the presets above.

    Returns:
        JSON with per-geometry measurements + recommended `objective` scalar(s)
        for a VOCS task spec.
    """
    try:
        import numpy as np                      # type: ignore
    except ImportError:
        return _fmt({"status": "error", "error": "numpy missing"})

    p = Path(frame_path)
    if not p.is_file():
        return _fmt({"status": "error", "error": f"not found: {frame_path}"})

    try:
        img = np.load(p)
    except Exception as e:
        return _fmt({"status": "error", "error": f"load failed: {e}"})

    if img.ndim != 2:
        return _fmt({"status": "error", "error": f"expected 2D frame, got {img.shape}"})

    # Common: compute saturation + total counts
    sat_frac = float((img >= img.max()).mean())
    total_counts = float(img.sum())

    # Centroid (intensity-weighted) on the top 20% of pixels
    try:
        from scipy.ndimage import gaussian_filter  # type: ignore
        s = gaussian_filter(img.astype("float32"), 1.5)
    except Exception:
        s = img.astype("float32")

    thr = float(np.quantile(s, 0.80))
    m = s >= thr
    if not m.any():
        return _fmt({"status": "error", "error": "no bright pixels above threshold"})

    ys, xs = np.nonzero(m)
    ws = s[m]
    cx = float((xs * ws).sum() / ws.sum())
    cy = float((ys * ws).sum() / ws.sum())
    # Width/height from the bright-pixel bounding box
    bbox = {"y_lo": int(ys.min()), "y_hi": int(ys.max()),
            "x_lo": int(xs.min()), "x_hi": int(xs.max())}
    width_px = bbox["x_hi"] - bbox["x_lo"]
    height_px = bbox["y_hi"] - bbox["y_lo"]

    result: dict[str, Any] = {
        "status": "ok",
        "geometry": geometry,
        "frame_shape": list(img.shape),
        "centroid_x_px": cx,
        "centroid_y_px": cy,
        "beam_bbox_px": bbox,
        "beam_width_px": width_px,
        "beam_height_px": height_px,
        "saturation_fraction": sat_frac,
        "total_counts": total_counts,
    }

    if geometry == "circ":
        # Equivalent circle radius from bright-pixel area
        area = int(m.sum())
        result["beam_radius_px"] = float((area / 3.14159) ** 0.5)
    elif geometry == "circ_with_beamstop":
        # Beamstop = cold region (low counts) inside the beam ROI.
        # Use the (1 - top-20%) complement inside the beam bbox.
        roi = s[bbox["y_lo"]:bbox["y_hi"] + 1,
                 bbox["x_lo"]:bbox["x_hi"] + 1]
        bs_mask = roi <= float(np.quantile(roi, 0.20))
        if bs_mask.any():
            bs_ys, bs_xs = np.nonzero(bs_mask)
            bs_ws = (roi.max() - roi[bs_mask])   # weight = depth of shadow
            if bs_ws.sum() > 0:
                bs_cx_local = float((bs_xs * bs_ws).sum() / bs_ws.sum())
                bs_cy_local = float((bs_ys * bs_ws).sum() / bs_ws.sum())
                bs_cx = bs_cx_local + bbox["x_lo"]
                bs_cy = bs_cy_local + bbox["y_lo"]
                dist = float(((cx - bs_cx) ** 2 + (cy - bs_cy) ** 2) ** 0.5)
                result["beamstop_centroid_x_px"] = bs_cx
                result["beamstop_centroid_y_px"] = bs_cy
                result["distance_from_beamstop_px"] = dist
                result["objective"] = {
                    "distance_from_beamstop": dist,
                    "note": "SAXS centering objective — MINIMIZE this scalar.",
                }

    if "objective" not in result:
        result["objective"] = {
            "beam_centroid": [cx, cy],
            "note": "For centering tasks, MINIMIZE "
                    "sqrt((cx - target_x)^2 + (cy - target_y)^2).",
        }
    return _fmt(result)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    _ensure_dirs()
    try:
        # Touch the DB so the schema is in place at startup
        _db().close()
        print(f"[apexa-alignment] history DB: {HISTORY_DB}", file=sys.stderr, flush=True)
    except Exception as e:
        print(f"[apexa-alignment] DB init warn: {e}", file=sys.stderr, flush=True)
    mcp.run(transport="stdio")
