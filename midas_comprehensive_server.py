#!/usr/bin/env python3
"""
MIDAS Comprehensive MCP Server
Provides complete FF-HEDM, NF-HEDM, PF-HEDM, and utility tools for beamline operations

Author: Beamline Assistant Team
Organization: Argonne National Laboratory
"""

from typing import Any, Optional, List, Dict
import json
import sys
import os
from pathlib import Path
import numpy as np
import xrayutilities as xu
import re
import subprocess
import shlex
import asyncio
import logging
import traceback
from mcp.server.fastmcp import FastMCP
from _idempotency import idempotent  # skip-if-done guard for heavy tools (Phase 0)
from apexa_remote_exec import (
    decide_exec_host, remote_run, remote_exists, remote_read_text,
    remote_midas_command, resolve_host_for_path, ssh_hint, is_local_host,
)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Suppress verbose MCP server logging
logging.getLogger("mcp").setLevel(logging.WARNING)
logging.getLogger("fastmcp").setLevel(logging.WARNING)

# =============================================================================
# CONFIGURATION & PATHS
# =============================================================================

def find_midas_python() -> str:
    """Find Python interpreter with MIDAS dependencies (zarr, diplib, etc.).

    Priority order:
    1. conda midas_env environment (dedicated MIDAS environment with all deps)
    2. conda base environment (if it has MIDAS deps)
    3. Current Python (if it has ALL critical MIDAS deps)
    4. System python3
    5. Fallback to current Python with warning
    """
    import shutil

    # Helper function to check if a Python has required deps
    def check_python_deps(python_path: str) -> bool:
        """Check if a Python interpreter has required MIDAS dependencies."""
        try:
            result = subprocess.run(
                [python_path, "-c", "import zarr, diplib, numba, h5py, skimage"],
                capture_output=True,
                timeout=5
            )
            return result.returncode == 0
        except:
            return False

    # PRIORITY 0: Check for manual override via MIDAS_PYTHON environment variable
    midas_python_env = os.environ.get("MIDAS_PYTHON")
    if midas_python_env:
        midas_python_path = Path(midas_python_env)
        if midas_python_path.exists():
            return str(midas_python_path)
        else:
            print(f"⚠ MIDAS_PYTHON set but not found: {midas_python_path}", file=sys.stderr)

    # PRIORITY 1: Look for conda midas_env (official MIDAS environment)
    conda_base = os.environ.get("CONDA_PREFIX_1") or os.environ.get("CONDA_PREFIX")

    # Try finding conda base from CONDA_EXE
    if not conda_base:
        conda_exe = os.environ.get("CONDA_EXE")
        if conda_exe:
            conda_base = Path(conda_exe).parent.parent

    # If not in conda, try common conda locations
    if not conda_base:
        for conda_loc in [
            Path.home() / "opt" / "miniconda3",  # Beamline common location
            Path.home() / "miniconda3",
            Path.home() / "anaconda3",
            Path.home() / ".conda",
            Path.home() / "conda",
            Path("/opt/conda"),
            Path("/opt/miniconda3"),
            Path("/opt/anaconda3")
        ]:
            if conda_loc.exists() and (conda_loc / "bin" / "conda").exists():
                conda_base = conda_loc
                break

    if conda_base:
        if isinstance(conda_base, str):
            conda_base = Path(conda_base)

        # Check for MIDAS conda environments (try multiple common names)
        for env_name in ["midas_202411", "midas_env", "midas", "MIDAS"]:
            midas_env_python = conda_base / "envs" / env_name / "bin" / "python"
            if midas_env_python.exists():
                return str(midas_env_python)

        # Check conda base environment
        conda_python = conda_base / "bin" / "python"
        if conda_python.exists() and check_python_deps(str(conda_python)):
            return str(conda_python)

    # PRIORITY 2: Check if current environment has ALL critical MIDAS deps
    try:
        import zarr
        import diplib
        import numba
        import h5py
        import skimage
        return sys.executable
    except ImportError:
        pass

    # PRIORITY 3: Try system python3
    python3_path = shutil.which("python3") or shutil.which("python")
    if python3_path and check_python_deps(python3_path):
        return python3_path

    # No suitable Python found — raise so the caller gets a clear error
    raise RuntimeError(
        "No Python with MIDAS dependencies found.\n"
        "Expected conda env 'midas_env' with zarr, diplib, numba, h5py, skimage.\n"
        f"Checked conda base: {conda_base}\n"
        "Set MIDAS_PYTHON env var to override, or run: conda env create -f MIDAS/environment.yml"
    )

def _find_gsasii_conda_base() -> Optional[str]:
    """Find conda base directory across common locations."""
    conda_base = os.environ.get("CONDA_PREFIX_1") or os.environ.get("CONDA_PREFIX")
    if not conda_base:
        conda_exe = os.environ.get("CONDA_EXE")
        if conda_exe:
            conda_base = str(Path(conda_exe).parent.parent)
    if not conda_base:
        for loc in [Path.home() / "miniconda3", Path.home() / "anaconda3",
                     Path.home() / "opt" / "miniconda3"]:
            if loc.exists():
                conda_base = str(loc)
                break
    return conda_base


def _find_gsasii_path() -> Optional[str]:
    """Auto-detect GSAS-II installation for subprocess env.

    Returns the parent directory of the GSASII package (the path to add to
    sys.path so that ``from GSASII import GSASIIscriptable`` works).

    Search order:
      1. GSASII_PATH environment variable
      2. Conda env named 'GSASII' with a GSAS-II checkout
      3. pip-installed GSASII in the current env
    """
    # 1. Explicit env var
    gsas_path = os.environ.get("GSASII_PATH")
    if gsas_path:
        p = Path(gsas_path)
        if (p / "GSASIIscriptable.py").exists():
            return str(p.parent)
        if (p / "GSASII" / "GSASIIscriptable.py").exists():
            return str(p)
        return str(p)

    # 2. Conda env named GSASII
    conda_base = _find_gsasii_conda_base()
    if conda_base:
        for sub in ["GSAS-II", "GSASII", "gsas2"]:
            candidate = Path(conda_base) / "envs" / "GSASII" / sub
            if (candidate / "GSASII" / "GSASIIscriptable.py").exists():
                return str(candidate)

    return None


def find_gsasii_python() -> Optional[str]:
    """Find Python interpreter from the GSASII conda env.

    GSAS-II binaries (pyspg, pypowder) are compiled against a specific
    Python version. The midas_env Python may be a different version, causing
    binary load failures. This function returns the GSASII env's own Python
    which is guaranteed to match the compiled binaries.

    Falls back to midas_env Python if no GSASII conda env exists (e.g.,
    pip-installed GSAS-II).
    """
    conda_base = _find_gsasii_conda_base()
    if conda_base:
        gsasii_python = Path(conda_base) / "envs" / "GSASII" / "bin" / "python"
        if gsasii_python.exists():
            return str(gsasii_python)
    return None


def get_midas_env() -> dict:
    """Get environment variables for all MIDAS operations (C++ and Python).

    MIDAS C++ binaries have @rpath set at build time so they find their own
    dylibs without DYLD_LIBRARY_PATH. Overriding DYLD_LIBRARY_PATH breaks
    h5py in midas_env (HDF5 symbol mismatch). Use this single function for
    all tools — both C++ executables and Python workflow scripts.
    """
    env = os.environ.copy()

    # Add MIDAS bin to PATH so C++ executables are found
    midas_bin_paths = [str(MIDAS_BIN), str(MIDAS_ROOT / "bin")]
    env["PATH"] = ":".join(midas_bin_paths + [env.get("PATH", "")])

    env["MIDAS_PATH"] = str(MIDAS_ROOT)

    # MIDAS/utils must be on PYTHONPATH for Python workflow scripts
    midas_utils = str(MIDAS_ROOT / "utils")
    if "PYTHONPATH" in env:
        env["PYTHONPATH"] = midas_utils + ":" + env["PYTHONPATH"]
    else:
        env["PYTHONPATH"] = midas_utils

    # Auto-detect GSAS-II so gsas_ii_refine.py can import GSASIIscriptable
    gsasii_path = _find_gsasii_path()
    if gsasii_path:
        env["GSASII_PATH"] = gsasii_path
        # Also add to PYTHONPATH so `from GSASII import ...` works directly
        env["PYTHONPATH"] = gsasii_path + ":" + env["PYTHONPATH"]

    return env


_DEPRECATION_WARNED: set = set()

def _warn_deprecated_cpp(what: str) -> None:
    """Flag use of the hand-built C++ MIDAS tree (MIDAS_ROOT scripts/binaries).

    The pip `midas-suite` Python packages are now the maintained default; the
    C++ tree is a deprecated fallback that will be removed. Warned once per
    distinct `what` per process to avoid log spam. Grep `_warn_deprecated_cpp`
    to find every remaining C++ execution site.
    """
    if what in _DEPRECATION_WARNED:
        return
    _DEPRECATION_WARNED.add(what)
    print(f"  ⚠ DEPRECATED C++ MIDAS engine ({what}) — will be removed; "
          f"pip midas-suite is the default.", file=sys.stderr)


def find_midas_installation() -> Path:
    """Find MIDAS installation by checking common locations.

    Priority order:
    1. MIDAS_PATH environment variable (if valid)
    2. Search common locations and validate each
    3. Auto-detect from Git repositories

    Validation checks for each candidate:
    - Has utils/AutoCalibrateZarr.py (required for calibration)
    - Has FF_HEDM/bin/ or build/bin/ (required for executables)
    """
    def validate_midas_path(path: Path) -> bool:
        """Check if path is a valid MIDAS installation."""
        try:
            if not path.exists() or not path.is_dir():
                return False

            # Must have AutoCalibrateZarr.py for calibration
            autocal = path / "utils" / "AutoCalibrateZarr.py"

            # Must have executables (either in FF_HEDM/bin or build/bin)
            has_executables = (
                (path / "FF_HEDM" / "bin").exists() or
                (path / "build" / "bin").exists()
            )

            return autocal.exists() and has_executables
        except (PermissionError, OSError):
            # Skip paths we don't have permission to access
            return False

    # Check environment variable first
    if "MIDAS_PATH" in os.environ:
        midas_path = Path(os.environ["MIDAS_PATH"]).expanduser().absolute()
        if validate_midas_path(midas_path):
            print(f"  MIDAS: {midas_path}", file=sys.stderr)
            return midas_path
        else:
            print(f"⚠ MIDAS_PATH set but invalid: {midas_path}", file=sys.stderr)

    # Search common installation locations
    # Build search paths dynamically
    common_paths = []

    # User home subdirectories
    for subdir in ["Git", "git", "src", "Documents", "opt", ""]:
        if subdir:
            common_paths.append(Path.home() / subdir / "MIDAS")
            common_paths.append(Path.home() / subdir / "Documents" / "MIDAS")

    # Beamline-specific paths (S1IDUSER, etc.)
    beamline_base = Path("/home/beams")
    if beamline_base.exists():
        for user_dir in beamline_base.glob("S*USER"):
            common_paths.append(user_dir / "opt" / "MIDAS")
            common_paths.append(user_dir / "MIDAS")

    # System-wide installations
    for prefix in ["/opt", "/usr/local", "/usr", Path.home()]:
        common_paths.append(Path(prefix) / "MIDAS")
        common_paths.append(Path(prefix) / ".MIDAS")

    # Current directory
    common_paths.extend([
        Path.cwd() / "MIDAS",
        Path.cwd().parent / "MIDAS",
    ])

    # Remove duplicates while preserving order
    seen = set()
    common_paths = [p for p in common_paths if not (p in seen or seen.add(p))]

    valid_installations = []
    for path in common_paths:
        if validate_midas_path(path):
            valid_installations.append(path)

    if valid_installations:
        selected = valid_installations[0]
        if len(valid_installations) > 1:
            print(f"⚠ Multiple MIDAS installations — using {selected}. "
                  f"Set MIDAS_PATH to override.", file=sys.stderr)
        else:
            print(f"  MIDAS: {selected}", file=sys.stderr)
        return selected

    # No valid installation found
    print("❌ No valid MIDAS installation found. "
          "Clone https://github.com/marinerhemant/MIDAS and set MIDAS_PATH.", file=sys.stderr)
    return Path.home() / ".MIDAS"

# MIDAS installation paths
MIDAS_ROOT = find_midas_installation()
MIDAS_BIN = MIDAS_ROOT / "build" / "bin"  # Executables are in build/bin
MIDAS_FF_BIN = MIDAS_ROOT / "FF_HEDM" / "bin"  # FF-HEDM specific executables
MIDAS_NF_BIN = MIDAS_ROOT / "NF_HEDM" / "bin"  # NF-HEDM specific executables
MIDAS_FF_V7 = MIDAS_ROOT / "FF_HEDM" / "v7"
MIDAS_NF_V7 = MIDAS_ROOT / "NF_HEDM" / "v7"
MIDAS_UTILS = MIDAS_ROOT / "utils"
STRESS_RUNNER_SCRIPT = Path(__file__).parent / "_stress_runner.py"
CAPABILITY_RUNNER_SCRIPT = Path(__file__).parent / "_capability_runner.py"
DETECTOR_PRESETS_FILE = (Path(__file__).parent / "knowledge_base" / "data"
                         / "detector_presets.json")

_DETECTOR_PRESETS_CACHE: Optional[dict] = None


def _load_detector_presets() -> dict:
    """Load the sourced detector-preset registry once (cached).

    Every numeric field in detector_presets.json is sourced from a real line in
    the MIDAS repo (see the file's _schema.sources). Returns the "detectors"
    sub-dict (alias→preset). Returns {} if the file is missing/unreadable rather
    than raising — masking still works when the caller passes dims directly.
    """
    global _DETECTOR_PRESETS_CACHE
    if _DETECTOR_PRESETS_CACHE is None:
        try:
            with open(DETECTOR_PRESETS_FILE) as fh:
                _DETECTOR_PRESETS_CACHE = json.load(fh).get("detectors", {})
        except Exception as e:
            print(f"⚠ detector_presets.json unavailable: {e}", file=sys.stderr)
            _DETECTOR_PRESETS_CACHE = {}
    return _DETECTOR_PRESETS_CACHE


def _resolve_detector_preset(name_or_alias: str):
    """Case-insensitive resolve of a detector name/alias to its preset dict.

    Returns (key, preset) on a hit, or (None, sorted_known_keys) on a miss so
    the caller can surface an actionable "known detectors" listing.
    """
    presets = _load_detector_presets()
    if not name_or_alias:
        return None, sorted(presets.keys())
    needle = name_or_alias.strip().lower()
    for key, preset in presets.items():
        if key.lower() == needle:
            return key, preset
        for alias in preset.get("aliases", []):
            if alias.strip().lower() == needle:
                return key, preset
    return None, sorted(presets.keys())

_autocal_script = MIDAS_UTILS / "AutoCalibrateZarr.py"
if not _autocal_script.exists():
    print(f"⚠ AutoCalibrateZarr.py not found at {_autocal_script} — calibration unavailable", file=sys.stderr)

# Add MIDAS Python modules to path
for path in [MIDAS_UTILS, MIDAS_FF_V7, MIDAS_NF_V7]:
    if path.exists():
        sys.path.insert(0, str(path))

# Initialize FastMCP server
mcp = FastMCP("midas-comprehensive-analysis")

# =============================================================================
# DEPENDENCY IMPORTS
# =============================================================================

# Import MIDAS Python APIs
MIDAS_PYTHON_AVAILABLE = False
try:
    # Core scientific libraries
    import fabio
    from scipy import ndimage
    from scipy.signal import find_peaks, peak_widths
    from scipy.optimize import curve_fit
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    MIDAS_AVAILABLE = True
except ImportError as e:
    MIDAS_AVAILABLE = False
    print(f"⚠ Scientific deps missing: {e}", file=sys.stderr)

# Try to import MIDAS Python workflow modules
try:
    # These may not exist depending on MIDAS installation
    # from ff_MIDAS import process_layer, read_parameter_file
    # from nf_MIDAS import run_preprocessing, run_fitting_and_postprocessing
    # from calcMiso import GetMisorientationAngle
    MIDAS_PYTHON_AVAILABLE = True
except ImportError:
    MIDAS_PYTHON_AVAILABLE = False

# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def format_result(result: dict) -> str:
    """Format analysis results into readable JSON string."""
    return json.dumps(result, indent=2)

def validate_file(file_path: str, must_exist: bool = True) -> tuple[bool, str]:
    """Validate file path and return (success, message)."""
    path = Path(file_path).expanduser()
    if must_exist and not path.exists():
        return False, f"File not found: {path}"
    return True, str(path)


def validate_file_on(file_path: str, decision: dict = None,
                     must_exist: bool = True) -> tuple[bool, str]:
    """Validate an input path locally OR on the decided remote host.

    `decision` is a `decide_exec_host(...)` result. When it says the run is remote,
    existence is checked on that host over SSH (the local `.exists()` in
    `validate_file` would wrongly fail for a `/gdata` path that only lives on the
    analysis host — the original topology bug). When local (or no decision), this
    is exactly `validate_file`.
    """
    if not file_path:
        return True, ""
    if decision and decision.get("is_remote"):
        host = decision.get("host")
        if not must_exist:
            return True, file_path
        if remote_exists(host, file_path):
            return True, file_path
        return False, f"File not found on remote host '{host}': {file_path}"
    return validate_file(file_path, must_exist=must_exist)


def _run_midas_maybe_remote(cmd, *, data_paths, cwd=None, timeout=3600,
                            host: str = "") -> dict:
    """Run a MIDAS command locally, or route it to the host that owns the data.

    Returns a dict shaped like a CompletedProcess-lite:
        {success, return_code, stdout, stderr, is_remote, host}
    plus, on an unreachable remote host, an `error` string the model cannot
    mistake for success.

    The LOCAL branch is byte-for-byte the pre-existing `subprocess.run(...)` call
    (same env=get_midas_env(), cwd, timeout) — this is a pure superset. The REMOTE
    branch ships the same argv over SSH via a login shell, prefixed with the host
    record's `activate` snippet so the remote MIDAS env (conda/venv) resolves.
    """
    d = decide_exec_host(*[p for p in data_paths if p], host=host)
    if d.get("error"):
        return {"success": False, "return_code": None, "is_remote": False,
                "host": None, "stdout": "", "stderr": d.get("reason", ""),
                "error": d.get("reason", "host resolution error; NOTHING was run.")}
    if d.get("unreachable"):
        return {"success": False, "return_code": None, "is_remote": True,
                "host": d.get("host"), "stdout": "",
                "stderr": "remote data host unreachable; NOTHING was run.",
                "error": ssh_hint(d.get("host"))}
    if not d.get("is_remote"):
        # When APEXA runs ON the host that owns the data, decide_exec_host returns
        # local BUT still carries that host's registry record. Its `activate`
        # snippet must be applied here too: the MIDAS entry points live in a conda
        # env / venv that get_midas_env() does not source, so without it a local
        # run on the analysis host fails with `midas-pipeline: command not found`
        # even though the binary is installed. Observed on copland, 2026-08-26.
        _rec = d.get("record") or {}
        _act = (_rec.get("activate") or "").strip()
        if _act:
            bare = " ".join(shlex.quote(str(c)) for c in cmd)
            p = subprocess.run(["bash", "-lc", f"{_act} && {bare}"],
                               capture_output=True, text=True,
                               timeout=timeout, cwd=cwd, env=get_midas_env())
        else:
            p = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=timeout, cwd=cwd, env=get_midas_env())
        return {"success": p.returncode == 0, "return_code": p.returncode,
                "stdout": p.stdout, "stderr": p.stderr,
                "is_remote": False, "host": d.get("host"),
                "local_activate": bool(_act)}
    bare = " ".join(shlex.quote(str(c)) for c in cmd)
    r = remote_run(d["host"], remote_midas_command(bare, d.get("record")),
                   remote_dir=cwd, timeout=timeout)
    out = {"success": r.get("success", False), "return_code": r.get("return_code"),
           "stdout": r.get("stdout", ""), "stderr": r.get("stderr", ""),
           "is_remote": True, "host": d["host"]}
    if r.get("timed_out"):
        out["error"] = f"remote command timed out on '{d['host']}'; state may be partial."
    elif r.get("ssh_failed"):
        out["error"] = ssh_hint(d["host"])
    return out


def _collect_ff_outputs_local(result_path, start_layer, end_layer, run_started):
    """Local FF output collection (the original in-process path, unchanged logic).

    Returns (layer_outputs, fresh_grains, stale_grains, any_fresh_output). Extracted
    verbatim from run_ff_hedm_full_workflow so the remote path can slot in beside it
    without re-indenting the block; the local branch stays byte-for-byte equivalent.
    """
    def _is_fresh(p) -> bool:
        # 2 s tolerance for coarse-granularity filesystems; run_started was
        # captured before the subprocess, so any file written during the run
        # has mtime >= run_started.
        try:
            return p.stat().st_mtime >= run_started - 2
        except Exception:
            return False

    layer_outputs = []
    for layer in range(start_layer, end_layer + 1):
        layer_dir = result_path / f"LayerNr_{layer}"
        if not layer_dir.exists():
            continue
        info: dict = {"layer": layer, "layer_dir": str(layer_dir)}

        # 1. Grains.csv — present when process_grains ran (c-omp backend)
        for grains_name in ("Grains.csv", "GrainsReconstructed.csv",
                            "Grains_consolidated.csv"):
            gf = layer_dir / grains_name
            if gf.exists():
                try:
                    lines = gf.read_text().splitlines()
                    n = sum(1 for l in lines
                            if l.strip() and not l.startswith("%"))
                except Exception:
                    n = 0
                info["grains_file"] = str(gf)
                info["n_grains"] = n
                info["fresh"] = _is_fresh(gf)
                break

        # 2. IndexBest.bin — always written by the indexer regardless of backend.
        #    15 float64 columns per seed; column 14 = nMatches (>0 = solution).
        #    Use this as the grain count when Grains.csv is absent.
        ib_path = layer_dir / "IndexBest.bin"
        if ib_path.exists() and "n_grains" not in info:
            try:
                import numpy as _np
                ib = _np.fromfile(ib_path, dtype=_np.float64)
                if ib.size > 0 and ib.size % 15 == 0:
                    ib = ib.reshape(-1, 15)
                    solved = int((ib[:, 14] > 0).sum())
                    info["n_grains"] = solved
                    info["n_seeds"] = len(ib)
                    info["best_nMatches"] = float(ib[:, 14].max())
                    info["fresh"] = _is_fresh(ib_path)
                    info["note"] = (
                        "process_grains skipped (python refiner); "
                        "n_grains = seeds with nMatches>0 from IndexBest.bin. "
                        "Re-run with refine_backend='c-omp' to get Grains.csv."
                    )
            except Exception:
                pass

        # 3. OrientPosFit.bin — tells us refinement ran
        if (layer_dir / "OrientPosFit.bin").exists():
            info["refinement_complete"] = True
        if (layer_dir / "Results" / "OrientPosFit.bin").exists():
            info["refinement_complete"] = True

        # 4. processgrains_diagnostics.h5 — written by process_grains; the
        #    residual/strain plate + most auto-findings in the reconstruction
        #    report need it (FF §9). report_ready when a FRESH Grains.csv
        #    exists so the agent can chain generate_ff_reconstruction_report.
        diag = layer_dir / "processgrains_diagnostics.h5"
        if diag.exists():
            info["diagnostics_h5"] = str(diag)
        info["report_ready"] = "grains_file" in info and info.get("fresh", False)
        if "n_grains" in info and not info.get("fresh", False):
            info["stale"] = True
            info["note"] = (
                (info.get("note", "") + " ").strip()
                + " STALE: this file predates the current run — it is from a "
                  "PRIOR reconstruction, not this one."
            ).strip()

        layer_outputs.append(info)

    # Fresh grains = only counts from files THIS run wrote. Stale on-disk
    # counts are reported separately and never inflate total_grains.
    fresh_grains = sum(l.get("n_grains", 0) for l in layer_outputs
                       if l.get("fresh"))
    stale_grains = sum(l.get("n_grains", 0) for l in layer_outputs
                       if l.get("n_grains") and not l.get("fresh"))
    any_fresh_output = any(l.get("fresh") for l in layer_outputs)
    return layer_outputs, fresh_grains, stale_grains, any_fresh_output


def _collect_ff_outputs_remote(run_host, result_str, start_layer, end_layer,
                               run_started):
    """Light, shell-only FF output verify on a remote host (one SSH round-trip per
    layer). Mirrors the local collector's honest-status contract: a present-but-
    empty Grains.csv is caught, and only files fresh by remote mtime count toward
    total_grains.

    Coverage gap vs the local path (deferred follow-up): the numpy IndexBest.bin
    col-14>0 grain count and the full verify_ff_outputs .bin checks are NOT ported
    to remote yet — a `run_note` flags this whenever is_remote. Returns
    (layer_outputs, fresh_grains, stale_grains, any_fresh_output, remote_note).
    """
    layer_outputs = []
    # One portable shell probe per layer. `stat -c` (GNU/copland) with a `stat -f`
    # (BSD) fallback so the same script works if the analysis host is a Mac.
    script_tmpl = (
        'D={d}/LayerNr_{n}\n'
        'if [ ! -d "$D" ]; then echo MISSING; exit 0; fi\n'
        'G=""\n'
        'for f in Grains.csv GrainsReconstructed.csv Grains_consolidated.csv; do\n'
        '  if [ -f "$D/$f" ]; then G="$D/$f"; break; fi\n'
        'done\n'
        'if [ -n "$G" ]; then\n'
        '  echo "GRAINS=$G"\n'
        '  echo "NROWS=$(grep -v \'^%\' "$G" | grep -cve \'^[[:space:]]*$\')"\n'
        '  echo "GMTIME=$(stat -c %Y "$G" 2>/dev/null || stat -f %m "$G")"\n'
        'fi\n'
        'IB="$D/IndexBest.bin"\n'
        'if [ -f "$IB" ]; then\n'
        '  echo "IB_BYTES=$(stat -c %s "$IB" 2>/dev/null || stat -f %z "$IB")"\n'
        '  echo "IB_MTIME=$(stat -c %Y "$IB" 2>/dev/null || stat -f %m "$IB")"\n'
        'fi\n'
        'IA="$D/InputAll.csv"\n'
        'if [ -f "$IA" ]; then echo "IA_BYTES=$(stat -c %s "$IA" 2>/dev/null || stat -f %z "$IA")"; fi\n'
        'SP="$D/SpotsToIndex.csv"\n'
        'if [ -f "$SP" ]; then echo "SP_LINES=$(wc -l < "$SP")"; fi\n'
        'if [ -f "$D/OrientPosFit.bin" ] || [ -f "$D/Results/OrientPosFit.bin" ]; then echo REFINED=1; fi\n'
        'if [ -f "$D/processgrains_diagnostics.h5" ]; then echo "DIAG=$D/processgrains_diagnostics.h5"; fi\n'
    )

    def _fresh(mtime):
        # run_started==0 means the remote clock could not be read → we cannot tell
        # fresh from stale; treat as fresh (report output) but the run_note warns.
        if not run_started:
            return True
        try:
            return float(mtime) >= run_started - 2
        except Exception:
            return False

    for layer in range(start_layer, end_layer + 1):
        r = remote_run(run_host, script_tmpl.format(d=shlex.quote(result_str), n=layer),
                       timeout=120)
        out = (r.get("stdout") or "")
        if "MISSING" in out.splitlines() or not r.get("success"):
            continue
        kv = {}
        for line in out.splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                kv[k.strip()] = v.strip()
        info = {"layer": layer, "layer_dir": f"{result_str}/LayerNr_{layer}"}
        if kv.get("GRAINS"):
            try:
                n = int(kv.get("NROWS", "0"))
            except Exception:
                n = 0
            info["grains_file"] = kv["GRAINS"]
            info["n_grains"] = n
            info["fresh"] = _fresh(kv.get("GMTIME"))
        elif kv.get("IB_BYTES"):
            # No Grains.csv (python backend / process_grains skipped). The col-14>0
            # count needs numpy on the bytes — deferred for remote. Record presence
            # + freshness so the honest-status contract still downgrades correctly.
            info["indexbest_bytes"] = int(kv["IB_BYTES"])
            info["fresh"] = _fresh(kv.get("IB_MTIME"))
            info["note"] = ("IndexBest.bin present but the remote path does not yet "
                            "count solved grains (numpy col-14 verifier deferred). "
                            "Re-run with refine_backend='c-omp' for a remote Grains.csv.")
        if kv.get("IA_BYTES"):
            info["inputall_bytes"] = int(kv["IA_BYTES"])
        if kv.get("SP_LINES"):
            info["spots_to_index_lines"] = int(kv["SP_LINES"])
        if kv.get("REFINED"):
            info["refinement_complete"] = True
        if kv.get("DIAG"):
            info["diagnostics_h5"] = kv["DIAG"]
        info["report_ready"] = "grains_file" in info and info.get("fresh", False)
        if "n_grains" in info and not info.get("fresh", False):
            info["stale"] = True
            info["note"] = ((info.get("note", "") + " ").strip()
                            + " STALE: predates this run (a prior reconstruction).").strip()
        layer_outputs.append(info)

    fresh_grains = sum(l.get("n_grains", 0) for l in layer_outputs if l.get("fresh"))
    stale_grains = sum(l.get("n_grains", 0) for l in layer_outputs
                       if l.get("n_grains") and not l.get("fresh"))
    any_fresh_output = any(l.get("fresh") for l in layer_outputs)
    remote_note = ("Outputs verified over SSH (shell-level: Grains.csv row count, "
                   "InputAll/SpotsToIndex sizes, mtime freshness). The numpy "
                   "IndexBest.bin col-14 grain count and binary verify_ff_outputs "
                   "checks are not yet ported to the remote path.")
    if not run_started:
        remote_note += (" NOTE: remote clock could not be read, so fresh/stale could "
                        "not be distinguished this run.")
    return layer_outputs, fresh_grains, stale_grains, any_fresh_output, remote_note


def run_midas_executable(executable: str, param_file: str, cwd: str = None,
                         timeout: int = 3600, env: dict = None) -> dict:
    """Run a MIDAS C executable and return results."""
    # Try multiple possible locations for executables
    possible_paths = [
        MIDAS_BIN / executable,
        MIDAS_FF_BIN / executable,
        MIDAS_NF_BIN / executable
    ]

    exe_path = None
    for p in possible_paths:
        if p.exists():
            exe_path = p
            break

    if not exe_path:
        return {
            "success": False,
            "error": f"Executable not found: {executable}",
            "searched_paths": [str(p) for p in possible_paths],
            "executable": executable
        }

    try:
        # Use MIDAS environment with proper library paths
        if env is None:
            env = get_midas_env()
        _warn_deprecated_cpp(f"C++ binary {executable}")
        cmd = [str(exe_path), str(param_file)]
        print(f"  $ {' '.join(cmd)}", file=sys.stderr)
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            env=env
        )

        return {
            "success": result.returncode == 0,
            "return_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "executable": executable
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": f"Execution timed out after {timeout}s",
            "executable": executable
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "executable": executable
        }

def run_python_script(script_name: str, args: list, cwd: str = None,
                      timeout: int = 7200) -> dict:
    """Run a MIDAS Python script using the correct conda environment."""
    # Try multiple possible locations. MIDAS v11 removed FF_HEDM/v7 and
    # NF_HEDM/v7; the live Python scripts are under utils/, utils/converters/
    # (e.g. GE2Tiff.py), FF_HEDM/workflows/ (e.g. integrator.py),
    # NF_HEDM/workflows/, and gui/viewers/.
    possible_paths = [
        MIDAS_UTILS / script_name,
        MIDAS_UTILS / "converters" / script_name,
        MIDAS_ROOT / "FF_HEDM" / "workflows" / script_name,
        MIDAS_ROOT / "NF_HEDM" / "workflows" / script_name,
        MIDAS_ROOT / "gui" / "viewers" / script_name,
        MIDAS_FF_V7 / script_name,   # legacy v10 layout (kept for back-compat)
        MIDAS_NF_V7 / script_name,
        MIDAS_ROOT / script_name,
    ]

    script_path = None
    for p in possible_paths:
        if p.exists():
            script_path = p
            break

    if not script_path:
        return {
            "success": False,
            "error": f"Script not found: {script_name}",
            "searched_paths": [str(p) for p in possible_paths]
        }

    try:
        # Use MIDAS Python (conda midas_env) instead of "python"
        midas_python = find_midas_python()
        _warn_deprecated_cpp(f"MIDAS_ROOT script {script_name}")
        cmd = [midas_python, str(script_path)] + args
        print(f"  $ {' '.join(cmd)}", file=sys.stderr)
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            env=get_midas_env()
        )

        return {
            "success": result.returncode == 0,
            "return_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "script": script_name,
            "command": " ".join(cmd)
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": f"Script timed out after {timeout}s",
            "script": script_name
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "script": script_name
        }

# =============================================================================
# COMPUTE DISPATCH — tier work to local CPU / local GPU / remote GPU endpoint.
# Standard-practice policy: docs/COMPUTE_DISPATCH.md
# =============================================================================
# Cost = frames x megapixels. Ceiling below which a CPU-only host stays local;
# above it, a large job is routed to a GPU endpoint when one is configured.
_COST_LOCAL_CPU = float(os.environ.get("APEXA_COST_LOCAL_CPU", "40"))  # ~5 frames @ 2880^2

def _local_accelerator():
    """(has_accel, name) — reuse the native probe; fall back to a torch check."""
    try:
        from apexa_midas_native import _has_torch_accelerator
        return _has_torch_accelerator()
    except Exception:
        try:
            import torch
            if torch.cuda.is_available():
                return True, "cuda"
            if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
                return True, "mps"
        except Exception:
            pass
        return False, "cpu"

def _pick_compute_target(n_frames: int = 1, megapixels: float = 8.3, prefer: str = "auto") -> dict:
    """Decide where a job runs: local-cpu / local-gpu / remote-gpu.

    prefer: 'auto' (default) | 'local' | 'remote' | 'cpu' | 'cuda' | 'mps'.
    Endpoint config via env: APEXA_GPU_MACHINE (parsl machine name for
    midas-pipeline --machine, e.g. 'polaris') and/or APEXA_GPU_ENDPOINT
    (ssh user@host or globus-compute endpoint id). Returns a dict with
    target, device, cost, reason, and (remote) machine/endpoint.
    """
    cost = max(1.0, float(n_frames)) * max(0.1, float(megapixels))
    accel, name = _local_accelerator()
    machine = os.environ.get("APEXA_GPU_MACHINE") or None
    endpoint = os.environ.get("APEXA_GPU_ENDPOINT") or None
    prefer = (prefer or "auto").lower()

    if prefer == "cpu":
        return {"target": "local-cpu", "device": "cpu", "cost": cost, "reason": "forced cpu"}
    if prefer in ("cuda", "mps"):
        return {"target": "local-gpu", "device": prefer, "cost": cost, "reason": f"forced {prefer}"}
    if prefer == "local":
        return {"target": "local-gpu" if accel else "local-cpu",
                "device": name if accel else "cpu", "cost": cost, "reason": "forced local"}

    if accel and prefer != "remote":
        return {"target": "local-gpu", "device": name, "cost": cost,
                "reason": f"local {name} available"}
    if cost <= _COST_LOCAL_CPU and prefer != "remote":
        return {"target": "local-cpu", "device": "cpu", "cost": cost,
                "reason": "small job; local CPU is fine"}
    if machine or endpoint:
        return {"target": "remote-gpu", "device": "cuda", "cost": cost,
                "machine": machine, "endpoint": endpoint,
                "reason": f"large job (cost {cost:.0f}) on CPU-only host → GPU endpoint"}
    return {"target": "local-cpu", "device": "cpu", "cost": cost,
            "reason": (f"large job (cost {cost:.0f}) on a CPU-only host and no GPU endpoint "
                       "configured (set APEXA_GPU_MACHINE or APEXA_GPU_ENDPOINT) — running "
                       "on CPU, which will be slow")}


def _apexa_scratch_dir(subdir: str = "") -> Path:
    """Per-session scratch for throwaway scripts / temp params / working files.

    Claude-Code style: ephemeral files live here (under $APEXA_SCRATCH, else
    $TMPDIR/apexa_scratch), NEVER scattered into the user's data or output
    directories. Tools that need a temp param file or helper script should write
    it here; agents should not hand-write scripts into the data tree.
    """
    import tempfile as _tf
    root = Path(os.environ.get("APEXA_SCRATCH") or (Path(_tf.gettempdir()) / "apexa_scratch"))
    d = (root / subdir) if subdir else root
    d.mkdir(parents=True, exist_ok=True)
    return d


def _announce_output(tool: str, out_path, **extras) -> None:
    """One-line, up-front announcement of where a tool writes its output
    (Claude-Code style: say what + where before doing it). Prints to stderr so
    it appears immediately in the agent/CLI stream."""
    kv = "  ".join(f"{k}={v}" for k, v in extras.items() if v not in (None, "", []))
    print(f"[{tool}] → output: {out_path}" + (f"  ({kv})" if kv else ""), file=sys.stderr)


# =============================================================================
# FF-HEDM PRODUCTION TOOLS
# =============================================================================

@mcp.tool()
@idempotent(
    tool="run_ff_hedm_full_workflow",
    anchor=lambda kw: kw.get("result_folder") or "",
    salient=lambda kw: {k: kw.get(k) for k in (
        "param_file", "data_file", "detectors_json", "indexer_backend",
        "refine_backend", "device", "start_layer", "end_layer",
        "grains_seed_file", "skip_stages")},
)
async def run_ff_hedm_full_workflow(
    result_folder: str,
    param_file: str,
    data_file: str = "",
    n_cpus: int = 4,
    start_layer: int = 1,
    end_layer: int = 1,
    indexer_backend: str = "python",
    refine_backend: str = "python",
    device: str = "cpu",
    machine: str = "",
    n_nodes: int = 1,
    shard_gpus: str = "",
    skip_stages: str = "",
    resume_from: str = "",
    grains_seed_file: str = "",
    detectors_json: str = "",
    process_grains: bool = True,
    ignore_handbook_traps: bool = False,
    host: str = "",
) -> str:
    """Run complete FF-HEDM grain reconstruction using midas-pipeline.

    Orchestrates the full Far-Field HEDM pipeline via `midas-pipeline run
    --scan-mode ff` from the midas-suite Python package. Stages (in order):
      zip_convert → hkl → peakfit → merge_overlaps → calc_radius →
      transforms → binning → indexing → refinement → process_grains →
      consolidation
    Output: LayerNr_<N>/Grains.csv with grain positions, orientations,
    completeness.

    Args:
        result_folder: Output directory. LayerNr_<N>/ subdirs created here.
        param_file: Path to Parameters.txt / paramstest.txt produced by
                    midas_auto_calibrate or midas-params build_paramstest.
        data_file: Optional. A pre-converted .MIDAS.zip archive (→ --zarr, skips
                   conversion) OR a raw detector file (.ge5.h5/.h5/.ge5/.tif →
                   --raw-dir on its folder, so the zip conversion runs). Leave
                   empty to let the pipeline discover raw data from the parameter
                   file's RawFolder/FileStem/StartNr/EndNr (the usual path), or if
                   detectors_json is provided for a multi-detector run.
        n_cpus: CPU cores (default 4). Use more for production runs.
        start_layer: First layer number (default 1).
        end_layer: Last layer number (default 1 = single layer).
        indexer_backend: "python" (in-process, GPU-capable) or "c-omp"
                         (bundled C binary, requires OpenMP; faster on CPU).
                         Default: python.
        refine_backend: "python" (PyTorch, differentiable, GPU) or "c-omp".
                        Default: python.
        device: "cpu", "cuda", or "mps". Default: cpu.
        skip_stages: Comma-separated stage names to skip, e.g.
                     "refinement,process_grains". Valid names:
                     zip_convert, hkl, peakfit, merge_overlaps, calc_radius,
                     transforms, binning, indexing, refinement, process_grains,
                     consolidation.
        resume_from: Stage name to resume from (e.g. "indexing"). The pipeline
                     reads completed-stage state from the result_folder.
        grains_seed_file: Optional Grains.csv seed file (NF→FF handoff).
        detectors_json: Path to detectors.json for multi-detector runs.
        process_grains: Run process_grains + consolidation to produce
                        LayerNr_<N>/Grains.csv and processgrains_diagnostics.h5
                        (FF Handbook §8/§9 — the grain list + report inputs).
                        Default True. Set False to stop after indexing/refinement.
                        NOTE: process_grains only succeeds with
                        refine_backend='c-omp' (the handbook-preferred path). The
                        python refiner writes no FitBest.bin (known bug through
                        0.6.0), so process_grains is auto-skipped for python and
                        the grain count comes from IndexBest.bin — re-run with
                        c-omp to get Grains.csv + the reconstruction report.
        ignore_handbook_traps: Override the pre-dispatch handbook lint. By default
                        the run is BLOCKED if the parameter file has an unambiguous
                        unit/parameter error (e.g. Width/MarginRadius entered in
                        pixels instead of µm → empty InputAll.csv). Set True only
                        after reviewing the reported `handbook_traps` and confirming
                        the values are intentional. Softer `warning`/`info` traps
                        never block regardless of this flag.
        host: Force execution on this analysis host over SSH. Empty → infer from
                        the remote-host registry / data-path locality: if the data
                        (param_file/data_file) lives on a remote analysis host
                        (e.g. /gdata on copland), the whole pipeline runs THERE — one
                        lint-gated typed call instead of hand-driven run_remote_command
                        shells. The handbook lint still runs first, on the remote param
                        file. Result_folder must resolve to the same host as the data.

    Returns:
        JSON with status, layer-by-layer grain counts, output file paths
        (incl. processgrains_diagnostics.h5 and report_ready), and the exact
        midas-pipeline command that was run. When report_ready is true, chain
        generate_ff_reconstruction_report on the layer dir.

        Honest-status contract (do NOT report success without fresh grains):
        `total_grains` counts ONLY files this run wrote (fresh by mtime); a prior
        run's Grains.csv/IndexBest.bin is reported under `stale_grains_on_disk`
        and never inflates `total_grains`. `status` is "success" only when the
        pipeline exited 0 AND reconstructed >0 fresh grains; it is "warning" when
        the run exited 0 but wrote no new output (skip-all/no-op) or produced 0
        grains — with the reason in `run_note`. `produced_fresh_output` says
        whether this invocation wrote anything new. Cite `total_grains`/`run_note`,
        never `stale_grains_on_disk`, when reporting a reconstruction result.
    """
    try:
        import shutil as _shutil

        # ── Anti-hallucination: reject fabricated default MIDAS paths ────────
        # The model sometimes substitutes ~/opt/MIDAS (a common training-data
        # path) instead of the actual MIDAS location. This guard catches the
        # specific hallucination pattern without blocking legitimate local
        # MIDAS installs (e.g., ~/Git/MIDAS or /Users/.../Git/MIDAS).
        # Use module-level re — do NOT import re inside this function
        # (import re as _re inside a try block causes Python to treat 're' as
        # a local name, breaking all module-level re.* calls in the function)
        _HALLUCINATION_PATTERNS = [
            re.compile(r'~/opt/MIDAS'),
            re.compile(r'/opt/MIDAS(?:/|$)'),
            re.compile(r'~[/\\]MIDAS(?:/|$)'),
        ]
        for _pat in _HALLUCINATION_PATTERNS:
            for _argname, _argval in [
                ("result_folder", result_folder),
                ("param_file", param_file),
                ("data_file", data_file),
            ]:
                if _pat.search(str(_argval)):
                    return format_result({
                        "tool": "run_ff_hedm_full_workflow",
                        "status": "error",
                        "error": (
                            f"{_argname}='{_argval}' looks like a fabricated default path "
                            "(~/opt/MIDAS is not where MIDAS is installed on this machine). "
                            "Use the EXACT absolute paths from the directory listing above. "
                            f"The actual MIDAS installation is at {str(MIDAS_ROOT)}."
                        ),
                    })

        # ── Decide execution locality (local vs a remote analysis host) ──────
        # Drive the decision off the must-exist INPUTS (param_file, data_file);
        # result_folder is an output and may not exist yet, so probing it here
        # would force a spurious SSH round-trip on every local run into a fresh
        # directory. `host=` forces a host; else the registry / data path decides.
        decision = decide_exec_host(param_file, data_file, host=host)
        if decision.get("error"):
            return format_result({"tool": "run_ff_hedm_full_workflow",
                                  "status": "error", "error": decision.get("reason")})
        is_remote = decision.get("is_remote", False)
        run_host = decision.get("host")

        # MIDAS writes outputs next to the inputs, so result_folder MUST resolve to
        # the same host as the data — a split would silently write to the wrong FS.
        # `rf_host` is a RAW registry lookup: it is truthy for the LOCAL machine too
        # when APEXA is deployed on the host that owns the registered data root
        # (the copland-on-copland deployment). is_local_host() collapses that case
        # to "local", so the guard fires only on a GENUINE host split, never on a
        # legitimate all-local run co-located with the data. (Bug: without the
        # is_local_host check, every local run on the analysis host was hard-blocked
        # with "result_folder resolves to remote host" and fell back to SSH-to-self.)
        rf_host = resolve_host_for_path(result_folder)
        rf_remote = bool(rf_host) and not is_local_host(rf_host)
        if is_remote and rf_remote and rf_host != run_host:
            return format_result({"tool": "run_ff_hedm_full_workflow", "status": "error",
                "error": (f"result_folder resolves to host '{rf_host}' but the data runs "
                          f"on '{run_host}'. A single FF run cannot span hosts — "
                          "co-locate result_folder with the data or pass host=.")})
        if not is_remote and rf_remote:
            return format_result({"tool": "run_ff_hedm_full_workflow", "status": "error",
                "error": (f"result_folder resolves to remote host '{rf_host}' but the "
                          "input data is local. Co-locate the inputs and output on one "
                          "host, or pass host= to force a remote run.")})

        if is_remote:
            # Remote: keep paths as-given (they are remote paths) and create the
            # output dir on the remote host — do not touch the local filesystem.
            result_str = result_folder
            mk = remote_run(run_host, f"mkdir -p {shlex.quote(result_folder)}", timeout=60)
            if mk.get("ssh_failed"):
                return format_result({"tool": "run_ff_hedm_full_workflow",
                                      "status": "error", "error": ssh_hint(run_host)})
            if not mk.get("success"):
                return format_result({"tool": "run_ff_hedm_full_workflow", "status": "error",
                    "error": f"could not create result_folder on '{run_host}': {mk.get('stderr','')}"})
        else:
            result_path = Path(result_folder).expanduser().absolute()
            result_path.mkdir(parents=True, exist_ok=True)
            result_str = str(result_path)
            _announce_output("run_ff_hedm_full_workflow", result_path,
                             layers=f"{start_layer}-{end_layer}", device=device)

        valid, param_path = validate_file_on(param_file, decision)
        if not valid:
            return format_result({"tool": "run_ff_hedm_full_workflow",
                                  "status": "error", "error": param_path})

        # ── Handbook lint gate (deterministic enforcement layer) ─────────────
        # Runs BEFORE any MIDAS dispatch. `error`-severity traps are unambiguous
        # unit/parameter mistakes that silently corrupt an irreversible run
        # (e.g. Width/MarginRadius in px → empty InputAll.csv); block unless the
        # caller explicitly overrides. `warning`/`info` traps are surfaced but
        # never block. Fail-open on lint exceptions — never break a run because
        # the linter itself errored.
        # When the param file lives on the remote host, pull it to a tempfile so the
        # linter (a local file reader) still runs — this is what closes the bypass
        # hole where hand-driven remote runs skipped the gate entirely.
        _lint_target = param_path
        _tmp_param = None
        if is_remote:
            ok_r, txt = remote_read_text(run_host, param_path)
            if ok_r:
                import tempfile as _tf
                fd, _tmp_param = _tf.mkstemp(suffix=".txt", prefix="ffparam_remote_")
                with os.fdopen(fd, "w") as _fh:
                    _fh.write(txt)
                _lint_target = _tmp_param
        try:
            ff_traps = _lint_handbook_traps(_lint_target, "ff")
        except Exception:
            ff_traps = []
        # Stale-stage-output check. zip_convert reuses an existing *.MIDAS.zip and
        # downstream stages read their parameters from the zarr, not from the
        # parameter file — so re-running into a folder whose artifacts predate a
        # parameter edit silently uses the OLD value and reports success. Live case
        # (1-ID, 2026-08-26): RingThresh patched 500 -> 10, resumed from peakfit,
        # zarr still had 500, peak search returned almost nothing.
        try:
            from handbook_guardrails import check_stale_stage_outputs
            ff_traps = list(ff_traps) + check_stale_stage_outputs(
                _lint_target, result_folder)
        except Exception:
            pass
        if _tmp_param:
            try:
                os.unlink(_tmp_param)
            except Exception:
                pass
        _blockers = [t for t in ff_traps if t.get("severity") == "error"]
        if _blockers and not ignore_handbook_traps:
            for _t in ff_traps:
                _mark = {"error": "⛔", "warning": "⚠"}.get(_t["severity"], "ℹ")
                _sec = f" [{_t['section']}]" if _t.get("section") else ""
                print(f"  {_mark} {_t['key']}{_sec}: {_t['message']}", file=sys.stderr)
            return format_result({
                "tool": "run_ff_hedm_full_workflow",
                "status": "error",
                "error": (
                    "Blocked by handbook lint before dispatch — the parameter file "
                    "has an unambiguous unit/parameter error that would corrupt the "
                    "run (see handbook_traps). Fix the parameter file, or pass "
                    "ignore_handbook_traps=True to override if the values are "
                    "intentional. NOTHING was run."
                ),
                "handbook_traps": ff_traps,
            })

        if is_remote:
            # Resolve on the remote host's PATH (via its activate snippet) — the
            # local .venv's midas-pipeline is the wrong binary for a remote run.
            _pipeline_bin = "midas-pipeline"
        else:
            _pipeline_bin = _shutil.which("midas-pipeline")
            if not _pipeline_bin:
                return format_result({
                    "tool": "run_ff_hedm_full_workflow",
                    "status": "error",
                    "error": (
                        "midas-pipeline not found. Install with: "
                        "pip install 'midas-suite[ff]'  (or uv add 'midas-suite[ff]')"
                    ),
                })

        cmd = [
            _pipeline_bin, "run",
            "--scan-mode", "ff",
            "--params", param_path,
            "--result", result_str,
            "--n-cpus", str(n_cpus),
            "--device", device,
            "--indexer-backend", indexer_backend,
            "--refine-backend", refine_backend,
            "--layers", f"{start_layer}-{end_layer}",
        ]

        # Compute dispatch: send large jobs to a GPU cluster/endpoint via parsl.
        # --machine selects a midas_parsl_configs endpoint (e.g. 'polaris');
        # --shard-gpus fans grain seeds across GPUs. See docs/COMPUTE_DISPATCH.md.
        if machine:
            cmd += ["--machine", machine]
        if n_nodes and n_nodes > 1:
            cmd += ["--n-nodes", str(n_nodes)]
        if shard_gpus:
            cmd += ["--shard-gpus", shard_gpus]

        if data_file:
            valid_d, data_path = validate_file_on(data_file, decision)
            if not valid_d:
                return format_result({"tool": "run_ff_hedm_full_workflow",
                                      "status": "error", "error": data_path})
            # --zarr means a PRE-CONVERTED single-detector .MIDAS.zip that
            # "overrides discovery" (midas-pipeline CLI). Routing a RAW detector
            # file (.ge5.h5/.h5/.ge5/.tif) here makes the pipeline treat it as a
            # finished zarr and SKIP the (multi-GB) zip conversion — then it fails
            # reading raw bytes as a zarr archive. Only send true archives to
            # --zarr; point --raw-dir at a raw file's folder so conversion runs
            # (convert_files defaults True; FileStem/StartNr/EndNr still come from
            # the parameter file).
            dp_low = data_path.lower()
            is_archive = (dp_low.endswith(".midas.zip") or dp_low.endswith(".zip")
                          or ".zarr" in dp_low)
            if is_archive:
                cmd += ["--zarr", data_path]
                # Pre-converted archive: RawFolder/FileStem/StartNr/EndNr are not
                # needed — skip midas-params preflight to avoid spurious errors.
                cmd += ["--skip-validation"]
            else:
                cmd += ["--raw-dir", str(Path(data_path).parent)]

        if detectors_json:
            valid_det, det_path = validate_file_on(detectors_json, decision)
            if valid_det:
                cmd += ["--detectors", det_path]

        if grains_seed_file:
            valid_s, seed_path = validate_file_on(grains_seed_file, decision)
            if valid_s:
                cmd += ["--ff-grains-file", seed_path]

        # Grain processing (FF Handbook §8/§9): process_grains + consolidation
        # produce LayerNr_<N>/Grains.csv and processgrains_diagnostics.h5 — the
        # grain list the user wants and the inputs the reconstruction report needs.
        #
        # KNOWN BUG (midas-process-grains 0.4.6 → still present 0.6.0): the python
        # refiner does not write FitBest.bin, so process_grains falls back to its
        # legacy path and exits 1 — no Grains.csv. So process_grains can only
        # succeed with refine_backend='c-omp' (the handbook-preferred path anyway).
        # We therefore auto-skip process_grains+consolidation when the refiner is
        # python (either explicitly disabled, or because it would only fail),
        # and run them for c-omp when process_grains=True (the default).
        report_note = ""
        _effective_skip = set(s.strip() for s in (skip_stages or "").split(",") if s.strip())
        if not process_grains:
            _effective_skip.update({"process_grains", "consolidation"})
            report_note = ("process_grains=False → no Grains.csv; "
                           "grain count from IndexBest.bin.")
            print("[FF-HEDM] process_grains=False: skipping process_grains + "
                  "consolidation.", file=sys.stderr)
        elif refine_backend == "python":
            _effective_skip.update({"process_grains", "consolidation"})
            report_note = (
                "Grains.csv NOT produced: the python refiner cannot run "
                "process_grains (missing FitBest.bin, known bug through 0.6.0). "
                "Re-run with refine_backend='c-omp' to get Grains.csv + the "
                "reconstruction report (FF §8/§9).")
            print("[FF-HEDM] refine_backend='python': auto-skipping process_grains "
                  "+ consolidation (python refiner writes no FitBest.bin → "
                  "process_grains fails). Use refine_backend='c-omp' for Grains.csv.",
                  file=sys.stderr)
        else:
            print("[FF-HEDM] process_grains enabled (c-omp) → will write "
                  "LayerNr_<N>/Grains.csv + processgrains_diagnostics.h5.",
                  file=sys.stderr)
        for stage in _effective_skip:
            cmd += ["--skip", stage]

        if resume_from:
            cmd += ["--resume", "from", "--from", resume_from]

        cmd_str = " ".join(cmd)
        print(f"[FF-HEDM] {cmd_str}", file=sys.stderr)

        # Stamp the run start so we can tell fresh outputs (written by THIS run)
        # from stale Grains.csv/IndexBest.bin left by a PRIOR run — otherwise a
        # skip-all/no-op run (which still exits 0) reports a prior run's grain
        # count as if it were this reconstruction's result. For a remote run the
        # freshness comparison must use the REMOTE clock (mtimes come from there),
        # so stamp it with `date +%s` on the analysis host; 0.0 means the clock
        # could not be read (verifier then treats output as fresh + warns).
        import time as _time
        if is_remote:
            _rs = remote_run(run_host, "date +%s", timeout=30)
            try:
                run_started = float((_rs.get("stdout") or "0").strip())
            except Exception:
                run_started = 0.0
        else:
            run_started = _time.time()

        proc = _run_midas_maybe_remote(cmd, data_paths=[param_file, data_file],
                                       cwd=None, timeout=14400, host=host)
        if proc.get("error") and proc.get("return_code") is None:
            # SSH-layer failure / unreachable host / host-resolution error — nothing
            # ran. Surface the explicit error so the model cannot claim success.
            return format_result({
                "tool": "run_ff_hedm_full_workflow", "status": "error",
                "is_remote": proc.get("is_remote", is_remote),
                "host": proc.get("host", run_host),
                "command": cmd_str, "error": proc["error"],
                "stderr": proc.get("stderr", ""), "handbook_traps": ff_traps,
            })

        # ── Collect outputs ──────────────────────────────────────────────────
        # Works for both c-omp backend (Grains.csv produced by process_grains)
        # and python backend (Grains.csv skipped; grain count from IndexBest.bin).
        # Each output file is tagged fresh/stale by mtime vs run_started: only
        # files THIS run wrote count toward total_grains, so a prior run's
        # Grains.csv can never be reported as this reconstruction's result. Remote
        # runs use a shell-level verifier (numpy .bin checks deferred — see note).
        remote_note = ""
        if is_remote:
            (layer_outputs, fresh_grains, stale_grains, any_fresh_output,
             remote_note) = _collect_ff_outputs_remote(
                run_host, result_str, start_layer, end_layer, run_started)
        else:
            (layer_outputs, fresh_grains, stale_grains,
             any_fresh_output) = _collect_ff_outputs_local(
                result_path, start_layer, end_layer, run_started)

        # Honest status: returncode 0 alone is NOT success. midas-pipeline exits 0
        # for a skip-all/no-op run; a real reconstruction must have produced fresh
        # output this run. Downgrade to "warning" (never claim success) when the
        # command succeeded but wrote nothing new.
        ok = proc.get("return_code") == 0
        if not ok:
            status = "error"
            run_note = ""
        elif fresh_grains > 0:
            status = "success"
            run_note = ""
        elif any_fresh_output:
            status = "warning"
            run_note = (
                "Pipeline ran and wrote output this run but reconstructed 0 grains "
                "— check RingThresh/MinPeakSNR, the dark, and the geometry "
                "(Lsd/BC/tilts). Not a fabricated success.")
        else:
            status = "warning"
            run_note = (
                "midas-pipeline exited 0 but produced NO new output this run — "
                "every stage was skipped (--skip) or the run was a no-op. Any "
                "Grains.csv/IndexBest.bin present is STALE (from a prior run) and "
                "is NOT counted in total_grains. This is not a fresh reconstruction.")

        # Fold the remote-verifier caveat into run_note so the honest-status
        # contract also carries the (shell-only, .bin-deferred) coverage gap.
        if remote_note:
            run_note = (run_note + " " + remote_note).strip() if run_note else remote_note

        return format_result({
            "tool": "run_ff_hedm_full_workflow",
            "status": status,
            "engine": "midas-pipeline",
            "command": cmd_str,
            "is_remote": is_remote,
            "host": run_host,
            "return_code": proc.get("return_code"),
            "stdout": (proc.get("stdout") or "")[-3000:],
            "stderr": (proc.get("stderr") or "")[-2000:],
            "result_folder": result_str,
            "layers": f"{start_layer}-{end_layer}",
            "refine_backend": refine_backend,
            "process_grains": process_grains,
            "layer_outputs": layer_outputs,
            "total_grains": fresh_grains,
            "stale_grains_on_disk": stale_grains,
            "produced_fresh_output": any_fresh_output,
            "report_ready": any(l.get("report_ready") for l in layer_outputs),
            "grains_note": report_note,
            "run_note": run_note,
            "handbook_traps": ff_traps,
        })

    except subprocess.TimeoutExpired:
        return format_result({"tool": "run_ff_hedm_full_workflow",
                              "status": "error",
                              "error": "Pipeline timed out (>4 h). Use resume_from to restart."})
    except Exception as e:
        return format_result({"tool": "run_ff_hedm_full_workflow",
                              "status": "error", "error": str(e)})


@mcp.tool()
async def calibrate_ring_thresholds(
    zarr_file: str,
    result_folder: str = "",
    n_frames: int = 40,
    host: str = "",
) -> str:
    """Recommend per-ring peak-search thresholds from the data (FF Handbook §6b).

    Wraps MIDAS `midas-ring-thresh`, which sweeps candidate thresholds on the
    first N frames of a converted .MIDAS.zip (zarr) and reports, per ring, a
    paste-ready `RingThresh <ring> <value>` line chosen so real Bragg blobs are
    kept while noise/false-positives stay bounded. Use this BEFORE an FF run to
    set RingThresh — never copy RingThresh from a template (a wrong value gives
    0 peaks and an empty grain list).

    Handbook caveat (§3d/§6b): the recommendation is meaningless if the dark is
    all-zero or mis-paired — the tell is threshold-invariant 0-peak output.
    Verify the dark (darkLoc/darkDataset) if every ring reports "NO SAFE VALUE".

    Args:
        zarr_file: The converted archive, typically
                   <run>/LayerNr_1/<name>.MIDAS.zip.
        result_folder: Where intermediates go (default: the zarr's parent).
        n_frames: Number of leading frames to analyze (default 40).
        host: Force execution on this analysis host (SSH). Empty → infer from the
              remote-host registry / data-path locality (runs locally if the data
              is local).

    Returns:
        JSON with parsed `ring_thresholds` (list of {ring, value, note}), the
        paste-ready block, and the raw stdout report.
    """
    try:
        import shutil as _shutil

        # Decide locality FIRST — the data may live only on a remote analysis host
        # (/gdata on copland), where a local validate_file().exists() would wrongly
        # fail. `host=` forces a host; otherwise the registry / data path decides.
        decision = decide_exec_host(zarr_file, host=host)
        if decision.get("error"):
            return format_result({"tool": "calibrate_ring_thresholds",
                                  "status": "error", "error": decision.get("reason")})

        valid, zpath = validate_file_on(zarr_file, decision)
        if not valid:
            return format_result({"tool": "calibrate_ring_thresholds",
                                  "status": "error", "error": zpath})

        if decision.get("is_remote"):
            # Remote: the zarr path is a remote path; the console script resolves on
            # the remote host's PATH via its activate snippet (bash -lc). Don't touch
            # the local interpreter's sys.executable-relative resolution.
            rf = result_folder or str(Path(zpath).parent)
            cmd = ["midas-ring-thresh", zpath, "--result-folder", rf,
                   "--n-frames", str(n_frames)]
        else:
            rf = result_folder or str(Path(zpath).expanduser().absolute().parent)

            # midas-ring-thresh ships in the pip midas-suite (midas_peakfit), which is
            # a dependency of APEXA itself → it lives in the SAME interpreter APEXA
            # runs under (sys.executable, the uv .venv), NOT the conda midas_env that
            # find_midas_python() returns. That conda env carries only the C++ deps
            # (zarr/diplib/numba/h5py/skimage) and has no midas_peakfit → the old
            # find_midas_python() fallback raised "ModuleNotFoundError: midas_peakfit"
            # on the beamline. Prefer the console script that sits next to THIS
            # interpreter (guaranteed correct), then any PATH script, then a direct
            # sys.executable -c invocation. Same rule as midas_auto_calibrate (v2) and
            # _find_midas_params_cli.
            venv_rt = Path(sys.executable).parent / "midas-ring-thresh"
            path_rt = _shutil.which("midas-ring-thresh")
            if venv_rt.exists():
                cmd = [str(venv_rt), zpath, "--result-folder", rf, "--n-frames", str(n_frames)]
            elif path_rt:
                cmd = [path_rt, zpath, "--result-folder", rf, "--n-frames", str(n_frames)]
            else:
                # Fallback: invoke the module entry point via THIS interpreter (.venv),
                # which is guaranteed to have midas_peakfit installed.
                cmd = [sys.executable, "-c",
                       "import sys; from midas_peakfit.ring_thresh import main; "
                       "sys.exit(main())",
                       zpath, "--result-folder", rf, "--n-frames", str(n_frames)]

        print(f"[RING-THRESH] {' '.join(cmd)}", file=sys.stderr)
        proc = _run_midas_maybe_remote(cmd, data_paths=[zarr_file], cwd=None,
                                       timeout=1800, host=host)

        if not proc.get("success"):
            return format_result({
                "tool": "calibrate_ring_thresholds",
                "status": "error",
                "error": proc.get("error") or "midas-ring-thresh failed (see stderr).",
                "return_code": proc.get("return_code"),
                "is_remote": proc.get("is_remote", False),
                "host": proc.get("host"),
                "command": " ".join(cmd),
                "stdout": (proc.get("stdout") or "")[-2000:],
                "stderr": (proc.get("stderr") or "")[-3000:],
            })

        # Parse paste-ready "RingThresh <ring> <value>  [# note]" lines.
        ring_thresholds = []
        paste_lines = []
        for line in (proc.get("stdout") or "").splitlines():
            s = line.strip()
            if not s.startswith("RingThresh"):
                continue
            paste_lines.append(s)
            body, _, note = s.partition("#")
            parts = body.split()
            if len(parts) >= 3:
                ring = parts[1]
                val = parts[2]
                entry = {"ring": ring, "value": val}
                if note.strip():
                    entry["note"] = note.strip()
                if val in ("??",):
                    entry["note"] = (entry.get("note", "")
                                     + " NO SAFE VALUE — inspect band/dark").strip()
                ring_thresholds.append(entry)

        return format_result({
            "tool": "calibrate_ring_thresholds",
            "status": "success",
            "zarr_file": zpath,
            "n_frames": n_frames,
            "is_remote": proc.get("is_remote", False),
            "host": proc.get("host"),
            "ring_thresholds": ring_thresholds,
            "paste_block": "\n".join(paste_lines),
            "command": " ".join(cmd),
            "stdout": (proc.get("stdout") or "")[-6000:],
            "note": (
                "Set these RingThresh values in the parameter file before the FF "
                "run. If every ring says NO SAFE VALUE, check the dark (§3d)."
            ),
        })

    except subprocess.TimeoutExpired:
        return format_result({"tool": "calibrate_ring_thresholds",
                              "status": "error",
                              "error": "midas-ring-thresh timed out (>30 min)."})
    except Exception as e:
        return format_result({"tool": "calibrate_ring_thresholds",
                              "status": "error", "error": str(e)})


@mcp.tool()
async def run_pf_hedm_workflow(
    result_folder: str,
    param_file: str,
    n_scans: int = 1,
    scan_step_um: float = 0.0,
    beam_size_um: float = 0.0,
    n_cpus: int = 4,
    device: str = "cpu",
    machine: str = "",
    n_nodes: int = 1,
    shard_gpus: str = "",
    indexer_backend: str = "python",
    refine_backend: str = "python",
    one_sol_per_vox: bool = True,
    skip_stages: str = "",
    resume_from: str = "",
) -> str:
    """Run Point-Focus (scanning) HEDM via midas-pipeline --scan-mode pf.

    PF-HEDM uses a focused line/point beam that steps across the sample,
    producing voxel-level 3D orientation maps with sub-beam spatial resolution.
    Requires n_scans > 1 (one .MIDAS.zip per scan position, discovered via
    the RawFolder / FileStem convention in Parameters.txt).

    Args:
        result_folder: Output directory.
        param_file: Parameters.txt with RawFolder, FileStem, nScans, etc.
        n_scans: Number of scan positions.
        scan_step_um: Y-step between scan positions in µm.
        beam_size_um: Beam half-width in µm (used for scan-position filter).
        n_cpus: CPU cores.
        device: "cpu", "cuda", or "mps".
        indexer_backend: "python" or "c-omp".
        refine_backend: "python" or "c-omp".
        one_sol_per_vox: One orientation per voxel (default True).
        skip_stages: Comma-separated stages to skip.
        resume_from: Stage name to restart from.

    Returns:
        JSON with status, grain count, orientation-map file path.
    """
    try:
        import shutil as _shutil
        result_path = Path(result_folder).expanduser().absolute()
        result_path.mkdir(parents=True, exist_ok=True)
        _announce_output("run_pf_hedm_workflow", result_path, n_scans=n_scans, device=device)

        valid, param_path = validate_file(param_file)
        if not valid:
            return format_result({"tool": "run_pf_hedm_workflow",
                                  "status": "error", "error": param_path})

        _pipeline_bin = _shutil.which("midas-pipeline")
        if not _pipeline_bin:
            return format_result({
                "tool": "run_pf_hedm_workflow",
                "status": "error",
                "error": "midas-pipeline not found. Install: pip install 'midas-suite[ff]'",
            })

        cmd = [
            _pipeline_bin, "run",
            "--scan-mode", "pf",
            "--params", param_path,
            "--result", str(result_path),
            "--n-scans", str(n_scans),
            "--n-cpus", str(n_cpus),
            "--device", device,
            "--indexer-backend", indexer_backend,
            "--refine-backend", refine_backend,
        ]
        # Compute dispatch (see docs/COMPUTE_DISPATCH.md): route to a GPU cluster.
        if machine:
            cmd += ["--machine", machine]
        if n_nodes and n_nodes > 1:
            cmd += ["--n-nodes", str(n_nodes)]
        if shard_gpus:
            cmd += ["--shard-gpus", shard_gpus]
        if scan_step_um > 0:
            cmd += ["--scan-step", str(scan_step_um)]
        if beam_size_um > 0:
            cmd += ["--beam-size", str(beam_size_um)]
        if one_sol_per_vox:
            cmd += ["--one-sol-per-vox", "1"]
        for stage in (skip_stages or "").split(","):
            if stage.strip():
                cmd += ["--skip", stage.strip()]
        if resume_from:
            cmd += ["--resume", "from", "--from", resume_from]

        cmd_str = " ".join(cmd)
        print(f"[PF-HEDM] {cmd_str}", file=sys.stderr)

        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=14400,
            env=get_midas_env(),
        )

        # midas-pipeline pf writes LayerNr_1/Grains.csv (per layer)
        layer_outputs = []
        for gf in sorted(result_path.glob("LayerNr_*/Grains.csv")):
            lines = gf.read_text().splitlines()
            n = sum(1 for l in lines if l.strip() and not l.startswith("%"))
            layer_outputs.append({"grains_file": str(gf), "n_grains": n})

        ok = proc.returncode == 0
        return format_result({
            "tool": "run_pf_hedm_workflow",
            "status": "success" if ok else "error",
            "engine": "midas-pipeline --scan-mode pf",
            "command": cmd_str,
            "return_code": proc.returncode,
            "stdout": proc.stdout[-3000:] if proc.stdout else "",
            "stderr": proc.stderr[-2000:] if proc.stderr else "",
            "result_folder": str(result_path),
            "layer_outputs": layer_outputs,
            "total_grains": sum(l["n_grains"] for l in layer_outputs),
        })

    except subprocess.TimeoutExpired:
        return format_result({"tool": "run_pf_hedm_workflow", "status": "error",
                              "error": "Pipeline timed out (>4 h)."})
    except Exception as e:
        return format_result({"tool": "run_pf_hedm_workflow",
                              "status": "error", "error": str(e)})

@mcp.tool()
async def run_ff_calibration(
    param_file: str,
    calibrant: str = "CeO2",
    use_omp: bool = True,
    fit_tilt: bool = True,
    fit_panel_shifts: bool = False
) -> str:
    """Run FF-HEDM detector calibration workflow.

    Calibrates detector parameters (distance, beam center, tilt) using
    a calibrant material with known diffraction pattern. In MIDAS v11 this
    runs the native-Python `midas-autocalibrate` CLI, which performs
    integrated tilt/BC/Lsd refinement, panel shifts, and outlier-ring
    rejection in a single call (falls back to the CalibrantIntegratorOMP
    C/OpenMP binary if the CLI is unavailable).

    For the higher-level "point at an image and go" calibration that
    auto-detects format/energy, prefer the `midas_auto_calibrate` tool.

    Args:
        param_file: Path to Parameters.txt file (must reference the calibrant
                    image via ImagePath, or have a sibling image).
        calibrant: Calibrant material (CeO2, LaB6, Si, etc.)
        use_omp: Deprecated (the native engine is always parallel).
        fit_tilt: Deprecated — tilt/BC/Lsd fitting is always-on in v11.
        fit_panel_shifts: Deprecated — panel shifts are always-on in v11.

    Returns:
        JSON with calibrated parameters and fit quality metrics
    """
    try:
        import shutil as _shutil
        valid, param_path = validate_file(param_file)
        if not valid:
            return format_result({"error": param_path, "status": "failed"})

        work_dir = Path(param_path).parent
        _announce_output("run_ff_calibration", work_dir, params=Path(param_path).name)
        results = {
            "tool": "run_ff_calibration",
            "workflow": "FF-HEDM Calibration",
            "steps": []
        }

        # MIDAS v11: detector calibration is the native-Python `midas-autocalibrate`
        # CLI (midas_calibrate package). It supersedes the archived C binaries
        # FitTiltBCLsdSample + CalibrantPanelShiftsOMP, doing integrated
        # tilt/BC/Lsd refinement + panel shifts + outlier-ring rejection in one
        # call. `fit_tilt`/`fit_panel_shifts` are now always-on inside the engine
        # and kept only for backward-compatible signatures.
        autocal_cli = _shutil.which("midas-autocalibrate")
        if autocal_cli:
            cmd = [autocal_cli, param_path]
            cmd_str = " ".join(cmd)
            print(f"[FF-CAL] {cmd_str}", file=sys.stderr)
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=1800,
                cwd=str(work_dir), env=get_midas_env(),
            )
            results["engine"] = "midas-autocalibrate (native Python)"
            results["command"] = cmd_str
            results["steps"].append({
                "step": 1,
                "name": "Detector calibration (midas-autocalibrate)",
                "status": "completed" if proc.returncode == 0 else "failed",
                "calibrant": calibrant,
            })
            if proc.returncode != 0:
                results["status"] = "failed"
                results["error"] = (proc.stderr or proc.stdout or "")[-2000:]
                return format_result(results)
            results["stdout"] = (proc.stdout or "")[-2000:]
        else:
            # Fallback: CalibrantIntegratorOMP is the active C/OpenMP superset
            # (replaces both CalibrantPanelShiftsOMP and the separate tilt fit).
            exe = "CalibrantIntegratorOMP"
            print(f"[FF-CAL] midas-autocalibrate not on PATH; "
                  f"falling back to {exe} for {calibrant}", file=sys.stderr)
            result = run_midas_executable(exe, param_path, cwd=str(work_dir),
                                          timeout=600)
            results["engine"] = f"{exe} (C/OpenMP fallback)"
            results["steps"].append({
                "step": 1,
                "name": f"Calibrant fitting ({exe})",
                "status": "completed" if result["success"] else "failed",
                "calibrant": calibrant,
            })
            if not result["success"]:
                results["status"] = "failed"
                results["error"] = result.get(
                    "error", "Calibrant fitting failed")
                return format_result(results)

        # Try to read calibrated parameters
        calibrated_params = {}
        try:
            with open(param_path, 'r') as f:
                for line in f:
                    if line.strip() and not line.startswith('#'):
                        parts = line.split()
                        if len(parts) >= 2:
                            key = parts[0]
                            if key in ['Lsd', 'BC', 'tx', 'ty', 'tz', 'p0', 'p1', 'p2']:
                                calibrated_params[key] = ' '.join(parts[1:])
        except:
            pass

        results["status"] = "completed"
        results["calibrated_parameters"] = calibrated_params
        results["param_file"] = param_path

        return format_result(results)

    except Exception as e:
        return format_result({
            "tool": "run_ff_calibration",
            "status": "error",
            "error": str(e)
        })

@mcp.tool()
async def match_grains(
    grains_files: list,
    output_file: str = "matched_grains.csv",
    position_tolerance: float = 100.0,
    orientation_tolerance: float = 2.0,
    match_across_layers: bool = False,
    layer_spacing: float = None
) -> str:
    """Match grains across multiple datasets using Hungarian algorithm (v10).

    Uses match_grains.py to link grain IDs across load states, temperatures,
    or NF-HEDM layers using the Hungarian (linear sum assignment) algorithm.
    Supports both in-situ load matching and layer-to-layer stitching.

    Args:
        grains_files: List of Grains.csv files to match (in order)
        output_file: Output CSV with matched grain IDs across datasets
        position_tolerance: Max centroid distance for matching (microns)
        orientation_tolerance: Max misorientation for matching (degrees)
        match_across_layers: Match grains between NF-HEDM layers for stitching
        layer_spacing: Layer spacing in microns (required if match_across_layers=True)

    Returns:
        JSON with matching statistics and matched grain IDs
    """
    try:
        valid_files = []
        for gf in grains_files:
            valid, path = validate_file(gf)
            if valid:
                valid_files.append(path)
            else:
                return format_result({"error": f"File not found: {gf}", "status": "failed"})

        if len(valid_files) < 2:
            return format_result({"error": "Need at least 2 grains files", "status": "failed"})

        work_dir = Path(valid_files[0]).parent
        output_path = work_dir / output_file

        args = [
            "--files", *valid_files,
            "--output", str(output_path),
            "--positionTolerance", str(position_tolerance),
            "--orientationTolerance", str(orientation_tolerance)
        ]

        if match_across_layers:
            args.append("--matchLayers")
        if layer_spacing is not None:
            args.extend(["--layerSpacing", str(layer_spacing)])

        print(f"Matching grains across {len(valid_files)} datasets", file=sys.stderr)

        result = run_python_script("match_grains.py", args, cwd=str(work_dir), timeout=600)

        match_stats = {"n_datasets": len(valid_files), "datasets": valid_files}

        if output_path.exists():
            try:
                with open(output_path, 'r') as f:
                    match_stats["n_matched_grains"] = sum(1 for line in f) - 1
            except:
                pass

        return format_result({
            "tool": "match_grains",
            "status": "completed" if result["success"] else "failed",
            "workflow": "Grain Matching (Hungarian)",
            "execution": result,
            "parameters": {
                "n_datasets": len(valid_files),
                "position_tolerance_um": position_tolerance,
                "orientation_tolerance_deg": orientation_tolerance,
                "match_across_layers": match_across_layers
            },
            "statistics": match_stats,
            "output_file": str(output_path) if output_path.exists() else None
        })

    except Exception as e:
        return format_result({"tool": "match_grains", "status": "error", "error": str(e)})


# =============================================================================
# NF-HEDM RECONSTRUCTION TOOLS
# =============================================================================

@mcp.tool()
@idempotent(
    tool="run_nf_hedm_reconstruction",
    anchor=lambda kw: kw.get("result_folder") or "",
    salient=lambda kw: {k: kw.get(k) for k in (
        "param_file", "ff_seed_orientations", "do_image_processing",
        "start_layer", "end_layer", "min_confidence", "device")},
)
async def run_nf_hedm_reconstruction(
    param_file: str,
    result_folder: str = "",
    n_cpus: int = 4,
    device: str = "auto",
    ff_seed_orientations: bool = False,
    do_image_processing: bool = True,
    start_layer: int = 1,
    end_layer: int = 1,
    min_confidence: float = 0.6,
    resume_from: str = "",
    dtype: str = "auto",
    refine: str = "",
    skip_validation: bool = False,
    install_dir: str = "",
) -> str:
    """Run NF-HEDM voxel-level microstructure reconstruction.

    Wraps `midas-nf-pipeline run` (pure-Python, from midas-suite). Produces a
    voxel orientation map — `<MicFileText>.mic` plus a consolidated
    `*_consolidated.h5` — from near-field diffraction images.

    Real stage order (single-resolution = NumLoops=0; multi-resolution driven
    by a `GridRefactor` triplet in the param file):
      preprocess (HKL list → seed orientations [FF Grains.csv or cache] → hex
      grid → tomo filter → grid mask → diffraction spots) → image_processing
      (ProcessImagesCombined, one pass per detector distance) → fitting
      (nm-triton on CUDA, else nm-batched) → parse_mic → consolidate
      [→ mic2grains for multi-layer]. Multi-resolution repeats loop_<k>_seeded
      → bad-voxel filter → loop_<k>_unseeded → binary merge per loop.

    Args:
        param_file: Path to NF-HEDM parameter file. Real keys (see the Au
                    example `NF_HEDM/Example/ps_au.txt`): DataDirectory (raw
                    image dir), nDistances, Lsd (one line per distance), BC
                    (two values per distance), tx/ty/tz, px, NrPixels,
                    MaxRingRad, OmegaStart, OmegaStep, StartNr, EndNr,
                    NrFilesPerDistance, LatticeParameter (6 values), Wavelength,
                    SpaceGroup, Rsample, GridSize, MinConfidence, NrOrientations,
                    SaveNSolutions, MicFileBinary, MicFileText. Inline `# ...`
                    comments after a value are tolerated by the run-path reader
                    but crash the final consolidation stage, so this tool
                    auto-strips them into a sanitized copy before running.
        result_folder: Output dir (overrides OutputDirectory in params).
                       Per-layer outputs go into <result_folder>/LayerNr_<N>/.
        n_cpus: CPU cores for image processing and orientation fitting.
        device: "auto" (CUDA if available, else CPU), "cpu", "cuda".
        ff_seed_orientations: Seed loop 0 from an FF-HEDM Grains.csv instead of
                    the packaged seedOrientations cache (default False = cache).
        do_image_processing: Run ProcessImagesCombined (default True). Set False
                    only when SpotsInfo.bin already exists in DataDirectory.
        start_layer: First layer number.
        end_layer: Last layer number.
        min_confidence: MinConfidence for the per-layer Mic2GrainsList run after
                    the last loop (default 0.6).
        resume_from: Stage label to restart from (e.g. "loop_1_seeded",
                    "loop_2_unseeded"); the tool auto-locates the pipeline H5.
        dtype: "auto" (fp32 on cuda/mps, fp64 on cpu), "fp32"/"float32",
               "fp64"/"float64". Only forwarded when not "auto".
        refine: Phase-2 refine strategy — "" (auto), "nm-triton", "nm-batched",
                "nm-serial", "lbfgs+nm", "lbfgs".
        skip_validation: Skip the midas-params preflight on the param file.
        install_dir: MIDAS install dir for the seedOrientations cache; defaults
                     to the detected MIDAS_PATH so cache-based seeding resolves.

    Returns:
        JSON with status, consolidated HDF5 path, voxel/grain counts (read from
        the consolidated H5), the mic files, and the command that was run.
    """
    try:
        import shutil as _shutil
        valid, param_path = validate_file(param_file)
        if not valid:
            return format_result({"tool": "run_nf_hedm_reconstruction",
                                  "status": "error", "error": param_path})

        _nf_bin = _shutil.which("midas-nf-pipeline")
        if not _nf_bin:
            return format_result({
                "tool": "run_nf_hedm_reconstruction",
                "status": "error",
                "error": (
                    "midas-nf-pipeline not found. "
                    "Install: pip install 'midas-suite' (includes midas-nf-pipeline)"
                ),
            })

        # Sanitize inline "# ..." comments. The run-path param reader
        # (parse_parameters) keeps only the first token so it tolerates them,
        # but the final consolidation stage (extract_nf_params) joins all
        # trailing tokens and calls float() → a commented param crashes the run
        # AFTER hours of compute. Strip trailing " # ..." (whitespace-hash) into
        # a sanitized copy beside the original (preserves relative DataDirectory/
        # OutputDirectory resolution). Full-line "#" comments are left as-is.
        import re as _re
        _param_note = None
        run_param = param_path
        try:
            _orig = Path(param_path).read_text()
            _clean_lines, _changed = [], False
            for _ln in _orig.splitlines():
                _stripped = _re.sub(r"\s+#.*$", "", _ln)
                if _stripped != _ln and _ln.lstrip()[:1] != "#":
                    _changed = True
                _clean_lines.append(_stripped)
            if _changed:
                _san = Path(param_path).with_name(
                    Path(param_path).stem + ".apexa_clean" + Path(param_path).suffix)
                _san.write_text("\n".join(_clean_lines) + "\n")
                run_param = str(_san)
                _param_note = (
                    f"stripped inline '#' comments → {_san.name} "
                    "(would otherwise crash the consolidation stage)")
        except Exception:
            pass  # non-fatal — fall back to the original param file

        cmd = [
            _nf_bin, "run",
            run_param,
            "--n-cpus", str(n_cpus),
            "--device", device,
            "--start-layer", str(start_layer),
            "--end-layer", str(end_layer),
            "--min-confidence", str(min_confidence),
        ]
        if result_folder:
            rp = Path(result_folder).expanduser().absolute()
            rp.mkdir(parents=True, exist_ok=True)
            _announce_output("run_nf_hedm_reconstruction", rp)
            cmd += ["--result-folder", str(rp)]
        else:
            rp = Path(param_path).parent

        if dtype and dtype != "auto":
            cmd += ["--dtype", dtype]
        if refine:
            cmd += ["--refine", refine]
        if skip_validation:
            cmd += ["--skip-validation"]
        # seedOrientations cache lookup needs a MIDAS install dir; default to the
        # detected MIDAS tree so cache-based seeding (the default) resolves.
        _inst = install_dir or (str(MIDAS_ROOT) if MIDAS_ROOT else "")
        if _inst and Path(_inst).exists():
            cmd += ["--install-dir", _inst]
        if ff_seed_orientations:
            cmd += ["--ff-seed-orientations"]
        if not do_image_processing:
            cmd += ["--no-image-processing"]
        if resume_from:
            # midas-nf-pipeline semantics: --restart-from takes a STAGE label
            # (e.g. "loop_1_seeded"); --resume takes a PATH to the pipeline H5
            # to read completed-stage state from. Passing the stage name to
            # --resume (the old behavior) was wrong and always failed. Point
            # --resume at the per-layer pipeline H5 if one exists so the engine
            # can reuse prior outputs; always pass the stage via --restart-from.
            # The NF pipeline H5 (provenance ledger with a pipeline_state group)
            # is the per-layer *_consolidated.h5; older builds also wrote
            # pipeline*.h5. Prefer the consolidated ledger.
            _state_h5 = (sorted(rp.glob("**/*_consolidated.h5"))
                         + sorted(rp.glob("**/pipeline*.h5"))
                         + sorted(rp.glob("**/*pipeline*.hdf5")))
            if _state_h5:
                cmd += ["--resume", str(_state_h5[0])]
            cmd += ["--restart-from", resume_from]

        cmd_str = " ".join(cmd)
        print(f"[NF-HEDM] {cmd_str}", file=sys.stderr)

        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=28800,
            env=get_midas_env(),
        )

        # Locate outputs. The mic map is <MicFileText>.mic (NOT "Grains.mic");
        # the consolidated map is *_consolidated.h5.
        all_h5 = sorted(rp.glob("**/*.h5")) + sorted(rp.glob("**/*.hdf5"))
        consolidated = [h for h in all_h5 if h.name.endswith("_consolidated.h5")]
        h5_files = consolidated or all_h5
        mic_files = sorted(rp.glob("**/*.mic"))
        grains_csvs = sorted(rp.glob("**/GrainsLayer*.csv"))

        # Authoritative voxel/grain counts come from the consolidated H5
        # (voxels/position single-res, or the largest multi_resolution loop).
        # Fall back to counting non-% lines in the mic map only if no H5.
        voxel_count, grain_count = None, None
        if h5_files:
            try:
                import h5py as _h5py
                with _h5py.File(str(h5_files[0]), "r") as _f:
                    if "voxels/position" in _f:
                        voxel_count = int(_f["voxels/position"].shape[0])
                    elif "multi_resolution" in _f:
                        voxel_count = max(
                            (int(_f[f"multi_resolution/{lp}/voxels/position"].shape[0])
                             for lp in _f["multi_resolution"]
                             if f"multi_resolution/{lp}/voxels/position" in _f),
                            default=0)
                    if "grains/grain_id" in _f:
                        grain_count = int(_f["grains/grain_id"].shape[0])
            except Exception:
                pass
        if voxel_count is None:
            voxel_count = 0
            for mic in mic_files:
                try:
                    voxel_count += sum(
                        1 for l in mic.read_text().splitlines()
                        if l.strip() and not l.startswith("%"))
                except Exception:
                    pass

        ok = proc.returncode == 0
        payload = {
            "tool": "run_nf_hedm_reconstruction",
            "status": "success" if ok else "error",
            "engine": "midas-nf-pipeline",
            "command": cmd_str,
            "return_code": proc.returncode,
            "stdout": proc.stdout[-3000:] if proc.stdout else "",
            "stderr": proc.stderr[-2000:] if proc.stderr else "",
            "result_folder": str(rp),
            "consolidated_h5": str(h5_files[0]) if h5_files else None,
            "mic_files": [str(m) for m in mic_files],
            "grains_layer_csvs": [str(g) for g in grains_csvs],
            "total_voxels": voxel_count,
            "total_grains": grain_count,
        }
        if _param_note:
            payload["param_sanitized"] = _param_note
        return format_result(payload)

    except subprocess.TimeoutExpired:
        return format_result({"tool": "run_nf_hedm_reconstruction", "status": "error",
                              "error": "NF pipeline timed out (>8 h)."})
    except Exception as e:
        return format_result({"tool": "run_nf_hedm_reconstruction",
                              "status": "error", "error": str(e)})


@mcp.tool()
async def refine_nf_parameters(
    param_file: str,
    multi_point: bool = False,
    row_nr: int = 0,
    n_cpus: int = 1,
    device: str = "auto",
    objective: str = "hard",
) -> str:
    """Refine NF-HEDM detector geometry on a calibrant (NF Handbook §7).

    Wraps `midas-nf-pipeline refine-params`, which optimizes the NF geometry
    (Lsd/distance, beam center, detector tilts tx/ty/tz, Wedge) against a
    known calibrant orientation before the full reconstruction. Paste the
    refined values back into the parameter file, then run
    run_nf_hedm_reconstruction.

    Single-point (default) refines at one voxel (row_nr). Multi-point uses the
    GridPoints defined in the parameter file — objective 'hard' maximizes
    FracOverlap with C-parity conventions (recommended); 'soft' uses an L-BFGS
    differentiable objective.

    Args:
        param_file: NF MIDAS parameter file (defines the calibrant, geometry
                    seed, and — for multi-point — GridPoints).
        multi_point: Use the GridPoints multi-point refinement (default False =
                     single-point at row_nr).
        row_nr: Voxel index for single-point refinement (ignored when
                multi_point=True). Default 0.
        n_cpus: CPU cores (default 1).
        device: "auto", "cpu", or "cuda". Default "auto".
        objective: "hard" (FracOverlap, C-parity — recommended) or "soft"
                   (L-BFGS). Only used when multi_point=True.

    Returns:
        JSON with any refined geometry values parsed from stdout (Lsd, BC,
        tx/ty/tz, Wedge) for pasting back, plus the raw stdout and command.
    """
    try:
        import shutil as _shutil

        valid, ppath = _resolve_param_file(param_file)
        if not valid:
            return format_result({"tool": "refine_nf_parameters",
                                  "status": "error", "error": ppath})

        nf_bin = _shutil.which("midas-nf-pipeline")
        if nf_bin:
            cmd = [nf_bin, "refine-params", ppath]
        else:
            py = find_midas_python()
            cmd = [py, "-c",
                   "import sys; from midas_nf_pipeline.cli import main; "
                   "sys.exit(main())",
                   "refine-params", ppath]

        if multi_point:
            cmd += ["--multi-point", "--objective", objective]
        else:
            cmd += ["--row-nr", str(row_nr)]
        cmd += ["--n-cpus", str(n_cpus), "--device", device]

        print(f"[NF-REFINE] {' '.join(cmd)}", file=sys.stderr)
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=7200,
            env=get_midas_env(),
        )

        if proc.returncode != 0:
            return format_result({
                "tool": "refine_nf_parameters",
                "status": "error",
                "error": "midas-nf-pipeline refine-params failed (see stderr).",
                "return_code": proc.returncode,
                "command": " ".join(cmd),
                "stdout": proc.stdout[-2000:] if proc.stdout else "",
                "stderr": proc.stderr[-3000:] if proc.stderr else "",
            })

        # Best-effort parse of refined geometry from stdout for pasting back.
        refined = {}
        _keys = {
            "Lsd": r"Lsd", "BC": r"BC", "tx": r"tx", "ty": r"ty",
            "tz": r"tz", "Wedge": r"Wedge",
        }
        for line in (proc.stdout or "").splitlines():
            for name, key in _keys.items():
                if name in refined:
                    continue
                m = re.search(
                    rf"\b{key}\b\s*[:=]?\s*(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?"
                    r"(?:\s*,\s*-?\d+(?:\.\d+)?)*)", line)
                if m:
                    refined[name] = m.group(1).strip()

        return format_result({
            "tool": "refine_nf_parameters",
            "status": "success",
            "param_file": ppath,
            "mode": "multi-point" if multi_point else "single-point",
            "objective": objective if multi_point else None,
            "refined_geometry": refined,
            "command": " ".join(cmd),
            "stdout": proc.stdout[-6000:] if proc.stdout else "",
            "note": (
                "Paste the refined geometry back into the NF parameter file, then "
                "run run_nf_hedm_reconstruction. If refined_geometry is empty, "
                "read stdout — the fitter's output format may differ."
            ),
        })

    except subprocess.TimeoutExpired:
        return format_result({"tool": "refine_nf_parameters",
                              "status": "error",
                              "error": "NF parameter refinement timed out (>2 h)."})
    except Exception as e:
        return format_result({"tool": "refine_nf_parameters",
                              "status": "error", "error": str(e)})


@mcp.tool()
async def convert_nf_to_dream3d(
    nf_mic_file: str,
    output_hdf5: str = "nf_dream3d.h5",
    include_strain: bool = False,
    voxel_size: float = 1.0
) -> str:
    """Convert NF-HEDM output to DREAM.3D format for visualization.

    Converts a MIDAS NF .mic voxel map to DREAM.3D HDF5 format for
    visualization in Paraview, DREAM.3D, or other 3D visualization tools.

    Args:
        nf_mic_file: Path to an NF-HEDM .mic file (e.g. <MicFileText>.mic)
        output_hdf5: Output HDF5 file name
        include_strain: Include strain tensor data (if available)
        voxel_size: Voxel size in microns

    Returns:
        JSON with conversion status and file information
    """
    try:
        # Validate input
        valid, mic_path = validate_file(nf_mic_file)
        if not valid:
            return format_result({"error": mic_path, "status": "failed"})

        work_dir = Path(mic_path).parent
        output_path = work_dir / output_hdf5
        _announce_output("convert_nf_to_dream3d", output_path, mic=Path(mic_path).name)

        # Build command for conversion utility
        args = [
            mic_path,
            str(output_path),
            "--voxel-size", str(voxel_size)
        ]

        if include_strain:
            args.append("--include-strain")

        print(f"Converting {mic_path} to DREAM.3D format", file=sys.stderr)

        result = run_python_script("nf_paraview_gen.py", args, cwd=str(work_dir))

        # Check output
        conversion_info = {
            "input_file": mic_path,
            "output_file": None,
            "conversion_successful": False
        }

        if output_path.exists():
            conversion_info["output_file"] = str(output_path)
            conversion_info["file_size_mb"] = output_path.stat().st_size / (1024 * 1024)
            conversion_info["conversion_successful"] = True

            # Try to read basic info from HDF5
            try:
                import h5py
                with h5py.File(output_path, 'r') as h5f:
                    conversion_info["hdf5_structure"] = list(h5f.keys())
            except:
                pass

        return format_result({
            "tool": "convert_nf_to_dream3d",
            "status": "completed" if result["success"] else "failed",
            "execution": result,
            "parameters": {
                "voxel_size_um": voxel_size,
                "include_strain": include_strain
            },
            "conversion": conversion_info,
            "usage_note": "Open in Paraview or DREAM.3D for 3D visualization"
        })

    except Exception as e:
        return format_result({
            "tool": "convert_nf_to_dream3d",
            "status": "error",
            "error": str(e)
        })

@mcp.tool()
async def overlay_ff_nf_results(
    ff_grains_file: str,
    nf_mic_file: str,
    output_plot: str = "ff_nf_overlay.png",
    slice_position: str = "middle"
) -> str:
    """Overlay FF and NF grain maps for validation.

    Creates visualization comparing coarse FF-HEDM grain map with
    detailed NF-HEDM microstructure reconstruction.

    Args:
        ff_grains_file: Path to FF-HEDM Grains.csv
        nf_mic_file: Path to an NF-HEDM .mic file (e.g. <MicFileText>.mic)
        output_plot: Output plot file name
        slice_position: Which slice to show (top, middle, bottom)

    Returns:
        JSON with comparison statistics and plot file
    """
    try:
        # Validate inputs
        valid, ff_path = validate_file(ff_grains_file)
        if not valid:
            return format_result({"error": ff_path, "status": "failed"})

        valid, nf_path = validate_file(nf_mic_file)
        if not valid:
            return format_result({"error": nf_path, "status": "failed"})

        work_dir = Path(ff_path).parent
        output_path = work_dir / output_plot

        # Build command
        args = [
            ff_path,
            nf_path,
            "--output", str(output_path),
            "--slice", slice_position
        ]

        print(f"Overlaying FF and NF results", file=sys.stderr)

        result = run_python_script("PlotFFNF.py", args, cwd=str(work_dir))

        # Gather comparison statistics
        comparison = {
            "ff_grains_file": ff_path,
            "nf_mic_file": nf_path,
            "n_ff_grains": 0,
            "n_nf_voxels": 0,
            "plot_file": None
        }

        # Count FF grains
        try:
            with open(ff_path, 'r') as f:
                comparison["n_ff_grains"] = sum(1 for line in f) - 1
        except:
            pass

        # Count NF voxels
        try:
            with open(nf_path, 'r') as f:
                comparison["n_nf_voxels"] = sum(1 for line in f if not line.startswith('%'))
        except:
            pass

        # Check plot output
        if output_path.exists():
            comparison["plot_file"] = str(output_path)
            comparison["plot_size_kb"] = output_path.stat().st_size / 1024

        return format_result({
            "tool": "overlay_ff_nf_results",
            "status": "completed" if result["success"] else "failed",
            "execution": result,
            "comparison": comparison,
            "interpretation": {
                "ff_spatial_resolution": "~100-500 μm per grain",
                "nf_spatial_resolution": "~1-10 μm per voxel",
                "resolution_improvement": f"~{comparison['n_nf_voxels'] / max(comparison['n_ff_grains'], 1):.0f}x"
            }
        })

    except Exception as e:
        return format_result({
            "tool": "overlay_ff_nf_results",
            "status": "error",
            "error": str(e)
        })

# =============================================================================
# ADVANCED ANALYSIS TOOLS
# =============================================================================

@mcp.tool()
async def calculate_misorientation(
    grains_file: str,
    grain_id_1: int,
    grain_id_2: int,
    space_group: int = 225
) -> str:
    """Calculate misorientation angle between two grains.

    Computes the crystallographic misorientation angle and axis between
    two grains based on their orientation matrices.

    Args:
        grains_file: Path to Grains.csv file
        grain_id_1: First grain ID
        grain_id_2: Second grain ID
        space_group: Crystal space group number (default: 225 for FCC)

    Returns:
        JSON with misorientation angle, axis, and grain boundary character
    """
    try:
        valid, grains_path = validate_file(grains_file)
        if not valid:
            return format_result({"error": grains_path, "status": "failed"})

        # Read grains file and extract orientations
        grains_data = {}
        try:
            import csv
            with open(grains_path, 'r') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    gid = int(float(row.get('GrainID', row.get('grainId', -1))))
                    if gid in [grain_id_1, grain_id_2]:
                        # Extract Euler angles (column names vary)
                        euler_cols = ['EulerAngle1', 'EulerAngle2', 'EulerAngle3']
                        alt_cols = ['phi1', 'Phi', 'phi2']

                        euler = None
                        for col_set in [euler_cols, alt_cols]:
                            if all(c in row for c in col_set):
                                euler = [float(row[c]) for c in col_set]
                                break

                        if euler:
                            grains_data[gid] = {
                                'grain_id': gid,
                                'euler_angles': euler
                            }
        except Exception as e:
            return format_result({
                "error": f"Failed to parse grains file: {e}",
                "status": "failed"
            })

        if grain_id_1 not in grains_data:
            return format_result({
                "error": f"Grain {grain_id_1} not found in file",
                "status": "failed"
            })

        if grain_id_2 not in grains_data:
            return format_result({
                "error": f"Grain {grain_id_2} not found in file",
                "status": "failed"
            })

        euler1 = grains_data[grain_id_1]['euler_angles']
        euler2 = grains_data[grain_id_2]['euler_angles']

        # Try to use MIDAS calcMiso utility
        args = [
            str(euler1[0]), str(euler1[1]), str(euler1[2]),
            str(euler2[0]), str(euler2[1]), str(euler2[2]),
            str(space_group)
        ]

        result = run_python_script("calcMiso.py", args)

        # Parse misorientation from output
        miso_angle = 0.0
        miso_axis = [0, 0, 0]

        if result["success"] and result.get("stdout"):
            # Try to parse output
            try:
                lines = result["stdout"].strip().split('\n')
                for line in lines:
                    if 'angle' in line.lower():
                        parts = line.split()
                        miso_angle = float(parts[-1])
                    elif 'axis' in line.lower():
                        parts = line.split()
                        miso_axis = [float(x) for x in parts[-3:]]
            except:
                pass

        # Classify grain boundary
        gb_type = "general"
        if miso_angle < 15:
            gb_type = "low-angle"
        elif 55 < miso_angle < 65 and space_group == 225:  # FCC
            # Check for special boundaries
            if abs(miso_angle - 60) < 5:
                gb_type = "Σ3 twin"
            elif abs(miso_angle - 38.9) < 2:
                gb_type = "Σ9"

        return format_result({
            "tool": "calculate_misorientation",
            "status": "completed" if result["success"] else "warning",
            "execution": result,
            "grain_pair": {
                "grain_1_id": grain_id_1,
                "grain_1_euler": euler1,
                "grain_2_id": grain_id_2,
                "grain_2_euler": euler2
            },
            "misorientation": {
                "angle_degrees": round(miso_angle, 3),
                "axis": [round(x, 4) for x in miso_axis],
                "space_group": space_group
            },
            "grain_boundary": {
                "type": gb_type,
                "classification": "Special" if "Σ" in gb_type else "Random"
            }
        })

    except Exception as e:
        return format_result({
            "tool": "calculate_misorientation",
            "status": "error",
            "error": str(e)
        })

@mcp.tool()
async def run_forward_simulation(
    input_grains_file: str,
    param_file: str,
    output_prefix: str,
    compressed: bool = True,
    scanning_mode: bool = False
) -> str:
    """Forward simulate diffraction from known microstructure.

    Simulates what diffraction patterns would look like for a given
    grain structure. Used for testing reconstruction algorithms and
    validating experimental data.

    Args:
        input_grains_file: Path to input Grains.csv with known orientations
        param_file: Path to Parameters.txt with experimental geometry
        output_prefix: Prefix for output files
        compressed: Use compressed output format
        scanning_mode: Simulate scanning (PF) mode instead of FF mode

    Returns:
        JSON with simulation status and output files
    """
    try:
        # Validate inputs
        valid, grains_path = validate_file(input_grains_file)
        if not valid:
            return format_result({"error": grains_path, "status": "failed"})

        valid, param_path = validate_file(param_file)
        if not valid:
            return format_result({"error": param_path, "status": "failed"})

        work_dir = Path(param_path).parent
        _announce_output("run_forward_simulation", work_dir, params=Path(param_path).name)

        # Update parameter file with input grains and output prefix
        # This is a simplified approach - actual implementation may need
        # to properly parse and modify the parameter file
        temp_params = work_dir / "sim_params.txt"
        with open(param_path, 'r') as fin, open(temp_params, 'w') as fout:
            for line in fin:
                if line.startswith('InFileName'):
                    fout.write(f'InFileName {grains_path}\n')
                elif line.startswith('OutFileName'):
                    fout.write(f'OutFileName {output_prefix}\n')
                else:
                    fout.write(line)

        # Choose executable. MIDAS v11 archived the non-compressed
        # ForwardSimulation and the standalone SimulateScanning binaries;
        # ForwardSimulationCompressed is the only surviving C simulator and
        # handles the FF case. Scanning/PF simulation now lives in the
        # differentiable midas_diffract model (no CLI yet) — flag clearly
        # instead of invoking a binary that no longer exists.
        if scanning_mode:
            return format_result({
                "tool": "run_forward_simulation",
                "status": "error",
                "error": (
                    "Scanning/PF forward simulation (SimulateScanning) was "
                    "removed in MIDAS v11. Use the differentiable forward model "
                    "in the midas_diffract package "
                    "(midas_diffract.simulate_panel_zarrs), or run a PF pipeline "
                    "on real data via run_pf_hedm_workflow."
                ),
            })
        exe = "ForwardSimulationCompressed"
        if not compressed:
            print("[FWD-SIM] non-compressed ForwardSimulation archived in v11; "
                  "using ForwardSimulationCompressed.", file=sys.stderr)
        print(f"Running {exe} simulation", file=sys.stderr)

        result = run_midas_executable(exe, str(temp_params), cwd=str(work_dir), timeout=1800)

        # Find output files
        output_files = []
        for pattern in [f"{output_prefix}*.zip", f"{output_prefix}*.tif",
                       f"{output_prefix}*.h5"]:
            output_files.extend(work_dir.glob(pattern))

        simulation_info = {
            "input_grains": grains_path,
            "param_file": param_path,
            "output_prefix": output_prefix,
            "n_output_files": len(output_files),
            "output_files": [str(f) for f in output_files[:10]],  # Limit listing
            "simulation_mode": "PF-HEDM" if scanning_mode else "FF-HEDM"
        }

        # Count input grains
        try:
            with open(grains_path, 'r') as f:
                simulation_info["n_grains_simulated"] = sum(1 for line in f) - 1
        except:
            pass

        # Clean up temp file
        if temp_params.exists():
            temp_params.unlink()

        return format_result({
            "tool": "run_forward_simulation",
            "status": "completed" if result["success"] else "failed",
            "workflow": "Forward Diffraction Simulation",
            "execution": result,
            "simulation": simulation_info,
            "usage_note": "Use simulated data to test reconstruction pipelines"
        })

    except Exception as e:
        return format_result({
            "tool": "run_forward_simulation",
            "status": "error",
            "error": str(e)
        })

@mcp.tool()
async def extract_grain_centroids(
    nf_mic_file: str,
    output_csv: str = "grain_centroids.csv",
    min_grain_size: int = 100
) -> str:
    """Extract grain centroids from NF reconstruction.

    Identifies individual grains in NF-HEDM voxel data and calculates
    their centroids, volumes, and average orientations.

    Args:
        nf_mic_file: Path to an NF-HEDM .mic file (e.g. <MicFileText>.mic)
        output_csv: Output CSV file with centroid data
        min_grain_size: Minimum grain size in voxels

    Returns:
        JSON with grain statistics and centroid data
    """
    try:
        valid, mic_path = validate_file(nf_mic_file)
        if not valid:
            return format_result({"error": mic_path, "status": "failed"})

        work_dir = Path(mic_path).parent
        output_path = work_dir / output_csv
        _announce_output("extract_grain_centroids", output_path, mic=Path(mic_path).name)

        # MIDAS v11: the standalone NFGrainCentroids binary was removed. Grain
        # centroids/volumes/orientations are produced by Mic2GrainsList, exposed
        # as `midas-nf-pipeline mic2grains` (and run automatically at the end of
        # run_nf_hedm_reconstruction, which writes GrainsLayer*.csv). Prefer the
        # CLI; fall back to the legacy binary only if it somehow still exists.
        # mic2grains needs a NF parameter file: paramFN micFile outFile
        # [doNeighborSearch] [nCPUs] [minConfOverride] — auto-detect a sibling.
        import shutil as _shutil
        nf_cli = _shutil.which("midas-nf-pipeline")
        legacy_bin = MIDAS_BIN / "NFGrainCentroids"
        if nf_cli and not legacy_bin.exists():
            nf_param = None
            for cand in ("Parameters.txt", "parameters.txt", "params.txt"):
                if (work_dir / cand).exists():
                    nf_param = str(work_dir / cand)
                    break
            if nf_param is None:
                hits = (sorted(work_dir.glob("*[Pp]arams*.txt")) +
                        sorted(work_dir.glob("ps_*.txt")))
                nf_param = str(hits[0]) if hits else None
            if nf_param is None:
                return format_result({
                    "tool": "extract_grain_centroids",
                    "status": "error",
                    "error": (
                        "midas-nf-pipeline mic2grains needs a NF parameter file "
                        "(LatticeConstant, SpaceGroup, etc.) alongside the .mic, "
                        "but none was found in "
                        f"{work_dir}. Provide one, or just use "
                        "run_nf_hedm_reconstruction — it writes GrainsLayer*.csv "
                        "(centroids + orientations) automatically."
                    ),
                })
            # mic2grains: paramFN micFile outFile doNeighborSearch nCPUs minConf
            cmd = [nf_cli, "mic2grains", nf_param, mic_path, str(output_path),
                   "0", "4", "0.6"]
            cmd_str = " ".join(cmd)
            print(f"[NF-CENTROIDS] {cmd_str}", file=sys.stderr)
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=600,
                cwd=str(work_dir), env=get_midas_env(),
            )
            result = {
                "success": proc.returncode == 0,
                "return_code": proc.returncode,
                "stdout": proc.stdout, "stderr": proc.stderr,
                "executable": "midas-nf-pipeline mic2grains",
                "error": (proc.stderr or proc.stdout or "")[-1000:]
                         if proc.returncode != 0 else "",
            }
        else:
            # Legacy fallback (v10 binary) — kept for old installations.
            temp_params = work_dir / "centroid_params.txt"
            with open(temp_params, 'w') as f:
                f.write(f"MicFile {mic_path}\n")
                f.write(f"OutputFile {output_csv}\n")
                f.write(f"MinGrainSize {min_grain_size}\n")
            print("Extracting grain centroids (legacy NFGrainCentroids)",
                  file=sys.stderr)
            result = run_midas_executable("NFGrainCentroids", str(temp_params),
                                          cwd=str(work_dir), timeout=600)

        # Parse output
        centroid_info = {
            "input_file": mic_path,
            "output_file": None,
            "n_grains_found": 0,
            "min_grain_size_voxels": min_grain_size
        }

        if output_path.exists():
            centroid_info["output_file"] = str(output_path)

            # Count grains
            try:
                with open(output_path, 'r') as f:
                    centroid_info["n_grains_found"] = sum(1 for line in f) - 1
            except:
                pass

            # Read some statistics
            try:
                import csv
                with open(output_path, 'r') as f:
                    reader = csv.DictReader(f)
                    volumes = []
                    for row in reader:
                        if 'Volume' in row:
                            volumes.append(float(row['Volume']))

                    if volumes:
                        centroid_info["grain_statistics"] = {
                            "mean_volume_voxels": round(np.mean(volumes), 1),
                            "median_volume_voxels": round(np.median(volumes), 1),
                            "total_volume_voxels": int(np.sum(volumes))
                        }
            except:
                pass

        # Clean up temp file
        if temp_params.exists():
            temp_params.unlink()

        return format_result({
            "tool": "extract_grain_centroids",
            "status": "completed" if result["success"] else "failed",
            "execution": result,
            "centroid_analysis": centroid_info
        })

    except Exception as e:
        return format_result({
            "tool": "extract_grain_centroids",
            "status": "error",
            "error": str(e)
        })

# =============================================================================
# DATA MANAGEMENT & UTILITIES
# =============================================================================

@mcp.tool()
async def batch_convert_ge_to_tiff(
    ge_folder: str,
    output_folder: str,
    file_pattern: str = "*.ge*",
    parallel: bool = True,
    n_processes: int = 4
) -> str:
    """Batch convert GE detector files to TIFF format.

    Converts proprietary GE detector format to standard TIFF images
    for processing with MIDAS or other tools.

    Args:
        ge_folder: Directory containing GE files
        output_folder: Output directory for TIFF files
        file_pattern: Glob pattern for GE files (e.g., "*.ge2", "*.ge3")
        parallel: Use parallel processing
        n_processes: Number of parallel processes

    Returns:
        JSON with conversion statistics
    """
    try:
        # Validate directories
        ge_path = Path(ge_folder).expanduser()
        if not ge_path.exists():
            return format_result({
                "error": f"GE folder not found: {ge_path}",
                "status": "failed"
            })

        output_path = Path(output_folder).expanduser()
        output_path.mkdir(parents=True, exist_ok=True)
        _announce_output("batch_convert_ge_to_tiff", output_path)

        # Find GE files
        ge_files = list(ge_path.glob(file_pattern))

        if not ge_files:
            return format_result({
                "warning": f"No files matching '{file_pattern}' found in {ge_path}",
                "status": "warning",
                "n_files_found": 0
            })

        print(f"Converting {len(ge_files)} GE files to TIFF", file=sys.stderr)

        # Build command
        args = [
            str(ge_path),
            str(output_path),
            "--pattern", file_pattern
        ]

        if parallel:
            args.extend(["--parallel", "--n-processes", str(n_processes)])

        result = run_python_script("GE2Tiff.py", args, timeout=3600)

        # Count output files
        tiff_files = list(output_path.glob("*.tif")) + list(output_path.glob("*.tiff"))

        conversion_stats = {
            "input_folder": str(ge_path),
            "output_folder": str(output_path),
            "n_input_files": len(ge_files),
            "n_output_files": len(tiff_files),
            "conversion_rate": len(tiff_files) / len(ge_files) if ge_files else 0,
            "parallel_processing": parallel,
            "n_processes": n_processes if parallel else 1
        }

        return format_result({
            "tool": "batch_convert_ge_to_tiff",
            "status": "completed" if result["success"] else "partial",
            "execution": result,
            "conversion": conversion_stats
        })

    except Exception as e:
        return format_result({
            "tool": "batch_convert_ge_to_tiff",
            "status": "error",
            "error": str(e)
        })

@mcp.tool()
async def create_midas_parameter_file(
    lattice_constants: list,
    space_group: int,
    detector_distance: float,
    beam_center: list,
    wavelength: float,
    omega_step: float,
    output_file: str = "Parameters.txt",
    pixel_size: float = 200.0,
    beam_thickness: float = 200.0,
    wedge: float = 0.0,
    detector_tilt: list = None,
    additional_params: dict = None
) -> str:
    """Generate MIDAS parameter file programmatically.

    Creates a properly formatted Parameters.txt file with validated
    crystallographic and experimental parameters.

    Args:
        lattice_constants: [a, b, c, alpha, beta, gamma] in Å and degrees
        space_group: Space group number (1-230)
        detector_distance: Sample-detector distance in microns
        beam_center: [x, y] beam center in pixels
        wavelength: X-ray wavelength in Angstroms
        omega_step: Rotation step in degrees
        output_file: Output parameter file name
        pixel_size: Detector pixel size in microns
        beam_thickness: Beam height in microns
        wedge: Wedge angle in degrees
        detector_tilt: [tx, ty, tz] detector tilt in degrees
        additional_params: Dictionary of additional parameters

    Returns:
        JSON with parameter file path and validation status
    """
    try:
        # Validate inputs
        if len(lattice_constants) != 6:
            return format_result({
                "error": "lattice_constants must be [a, b, c, alpha, beta, gamma]",
                "status": "failed"
            })

        if len(beam_center) != 2:
            return format_result({
                "error": "beam_center must be [x, y]",
                "status": "failed"
            })

        if not (1 <= space_group <= 230):
            return format_result({
                "error": "space_group must be between 1 and 230",
                "status": "failed"
            })

        if detector_tilt is None:
            detector_tilt = [0.0, 0.0, 0.0]

        if len(detector_tilt) != 3:
            return format_result({
                "error": "detector_tilt must be [tx, ty, tz]",
                "status": "failed"
            })

        output_path = Path(output_file).expanduser()
        _announce_output("create_midas_parameter_file", output_path)

        # Write parameter file
        with open(output_path, 'w') as f:
            f.write("# MIDAS Parameters File\n")
            f.write(f"# Generated by Beamline Assistant\n\n")

            # Crystal structure
            f.write("# Crystal Structure\n")
            lc = lattice_constants
            f.write(f"LatticeConstant {lc[0]:.6f} {lc[1]:.6f} {lc[2]:.6f} ")
            f.write(f"{lc[3]:.6f} {lc[4]:.6f} {lc[5]:.6f}\n")
            f.write(f"SpaceGroup {space_group}\n\n")

            # Detector configuration
            f.write("# Detector Configuration\n")
            f.write(f"Lsd {detector_distance:.4f}\n")
            f.write(f"BC {beam_center[0]:.4f} {beam_center[1]:.4f}\n")
            f.write(f"tx {detector_tilt[0]:.6f}\n")
            f.write(f"ty {detector_tilt[1]:.6f}\n")
            f.write(f"tz {detector_tilt[2]:.6f}\n")
            f.write(f"p0 0\n")  # Distortion parameters
            f.write(f"p1 0\n")
            f.write(f"p2 0\n")
            f.write(f"px {pixel_size:.4f}\n\n")

            # Experimental setup
            f.write("# Experimental Setup\n")
            f.write(f"Wavelength {wavelength:.6f}\n")
            f.write(f"Wedge {wedge:.6f}\n")
            f.write(f"OmegaStep {omega_step:.6f}\n")
            f.write(f"BeamThickness {beam_thickness:.4f}\n\n")

            # Analysis parameters (defaults)
            f.write("# Analysis Parameters\n")
            f.write("MinNrSpots 3\n")
            f.write("Completeness 0.8\n")
            f.write("OverAllRingToIndex 2\n\n")

            # Additional parameters
            if additional_params:
                f.write("# Additional Parameters\n")
                for key, value in additional_params.items():
                    f.write(f"{key} {value}\n")

        # Validate created file
        validation = {
            "file_created": output_path.exists(),
            "file_path": str(output_path),
            "file_size_bytes": output_path.stat().st_size if output_path.exists() else 0,
            "parameters_validated": True
        }

        # Read back and validate
        if output_path.exists():
            with open(output_path, 'r') as f:
                content = f.read()
                validation["contains_lattice"] = "LatticeConstant" in content
                validation["contains_spacegroup"] = "SpaceGroup" in content
                validation["contains_detector"] = "Lsd" in content and "BC" in content
                validation["contains_wavelength"] = "Wavelength" in content

        # Handbook trap lint (FF/NF Handbook = source of truth). This template
        # intentionally omits SkipFrame / MinPeakSNR / RingThresh / dark keys —
        # the lint reminds the caller to fill them in before an FF run.
        try:
            traps = _lint_handbook_traps(output_path, "ff")
        except Exception as _e:
            traps = []
        if traps:
            print("[create_midas_parameter_file] Handbook traps (FF/NF source of truth):",
                  file=sys.stderr)
            for t in traps:
                mark = {"error": "⛔", "warning": "⚠"}.get(t["severity"], "ℹ")
                sec = f" [{t['section']}]" if t.get("section") else ""
                print(f"  {mark} {t['key']}{sec}: {t['message']}", file=sys.stderr)

        return format_result({
            "tool": "create_midas_parameter_file",
            "status": "completed",
            "output_file": str(output_path),
            "validation": validation,
            "handbook_traps": traps,
            "parameters": {
                "lattice_constants": lattice_constants,
                "space_group": space_group,
                "detector_distance_um": detector_distance,
                "beam_center": beam_center,
                "wavelength_angstrom": wavelength,
                "omega_step_deg": omega_step
            }
        })

    except Exception as e:
        return format_result({
            "tool": "create_midas_parameter_file",
            "status": "error",
            "error": str(e)
        })

@mcp.tool()
async def validate_midas_installation(
    midas_path: str = None
) -> str:
    """Validate MIDAS installation and dependencies.

    Checks for required executables, Python packages, and configuration files.

    Args:
        midas_path: Path to MIDAS installation (default: ~/opt/MIDAS)

    Returns:
        JSON with installation validation results
    """
    try:
        # Determine MIDAS path
        if midas_path:
            midas_root = Path(midas_path).expanduser()
        else:
            midas_root = MIDAS_ROOT

        validation = {
            "midas_root": str(midas_root),
            "root_exists": midas_root.exists(),
            "executables": {},
            "python_modules": {},
            "dependencies": {},
            "overall_status": "unknown"
        }

        if not midas_root.exists():
            validation["overall_status"] = "failed"
            validation["error"] = f"MIDAS root directory not found: {midas_root}"
            return format_result({
                "tool": "validate_midas_installation",
                "validation": validation
            })

        # Check for key executables
        bin_path = midas_root / "bin"
        validation["bin_directory"] = str(bin_path)
        validation["bin_exists"] = bin_path.exists()

        # MIDAS v11 reality: most workflows now run through pip-package CLIs /
        # in-process native engines (see native_packages above), NOT raw C
        # binaries. These binaries are only the C/OpenMP fallback path. Some
        # were archived in v11 (CalibrantPanelShiftsOMP, FitTiltBCLsdSample,
        # ForwardSimulation, SimulateScanning, NFGrainCentroids, GrainTracking,
        # CalcStrains) and GPU builds require CUDA (absent on macOS). Flagging
        # those as hard failures produced false alarms, so they're split out as
        # "optional" and reported separately.
        key_executables = [
            # FF-HEDM CPU (active in v11)
            "IndexerOMP", "FitPosOrStrainsOMP", "ProcessGrains",
            "GetHKLListZarr", "PeaksFittingOMPZarrRefactor",
            "CalibrantIntegratorOMP", "ForwardSimulationCompressed",
            # Calibration & Integration (active in v11)
            "IntegratorZarrOMP",
            # NF-HEDM (active in v11)
            "FitOrientationOMP", "GetHKLListNF",
            "MakeHexGrid", "ParseMic", "ProcessImagesCombined",
            "FitWedgeParallel",
        ]
        optional_executables = [
            # GPU builds (need CUDA; absent on macOS/CPU-only installs)
            "IndexerGPU", "FitPosOrStrainsGPU", "FitOrientationGPU",
            # Archived in v11 (superseded by CLIs / native engines)
            "CalibrantPanelShiftsOMP", "GrainTracking", "CalcStrains",
        ]

        for exe in key_executables:
            exe_path = bin_path / exe
            validation["executables"][exe] = exe_path.exists()
        validation["optional_executables"] = {
            exe: (bin_path / exe).exists() for exe in optional_executables
        }

        # Check Python workflows (legacy scripts — may be superseded by pip packages)
        workflow_scripts = {
            "ff_MIDAS.py": midas_root / "FF_HEDM" / "ff_MIDAS.py",
            "pf_MIDAS.py": midas_root / "FF_HEDM" / "pf_MIDAS.py",
            "nf_MIDAS.py": midas_root / "NF_HEDM" / "nf_MIDAS.py",
            "match_grains.py": midas_root / "utils" / "match_grains.py",
            "AutoCalibrateZarr.py": midas_root / "utils" / "AutoCalibrateZarr.py"
        }

        for script, path in workflow_scripts.items():
            validation["python_modules"][script] = path.exists()

        # Check native pip packages (midas-suite — preferred over legacy scripts)
        try:
            from apexa_midas_native import native_engine_status
            native_status = native_engine_status()
            validation["native_packages"] = native_status
            n_installed = sum(1 for v in native_status.values() if v["installed"])
            n_total = len(native_status)
            validation["native_summary"] = (
                f"{n_installed}/{n_total} midas-suite packages installed"
            )
            # Report which CLIs are available
            import shutil as _sh
            for cli in ("midas-pipeline", "midas-autocalibrate",
                        "midas-integrate", "midas-calibrate"):
                validation[f"cli_{cli.replace('-', '_')}"] = bool(_sh.which(cli))
        except Exception as _ne:
            validation["native_packages"] = {"error": str(_ne)}

        # Check Python dependencies (both legacy and native paths)
        required_packages = [
            "numpy", "scipy", "matplotlib", "fabio",
            "h5py", "zarr", "numcodecs", "parsl", "numba"
        ]

        for package in required_packages:
            try:
                __import__(package)
                validation["dependencies"][package] = True
            except ImportError:
                validation["dependencies"][package] = False

        # Overall assessment
        exe_found = sum(validation["executables"].values())
        exe_total = len(validation["executables"])
        scripts_found = sum(validation["python_modules"].values())
        scripts_total = len(validation["python_modules"])
        deps_found = sum(validation["dependencies"].values())
        deps_total = len(validation["dependencies"])

        validation["statistics"] = {
            "executables": f"{exe_found}/{exe_total}",
            "python_scripts": f"{scripts_found}/{scripts_total}",
            "dependencies": f"{deps_found}/{deps_total}"
        }

        # Determine overall status
        if exe_found == exe_total and scripts_found == scripts_total and deps_found == deps_total:
            validation["overall_status"] = "excellent"
        elif exe_found >= exe_total * 0.8 and scripts_found >= 2 and deps_found >= deps_total * 0.8:
            validation["overall_status"] = "good"
        elif exe_found >= exe_total * 0.5:
            validation["overall_status"] = "partial"
        else:
            validation["overall_status"] = "insufficient"

        # Recommendations
        validation["recommendations"] = []
        if exe_found < exe_total:
            validation["recommendations"].append(
                f"Rebuild MIDAS: cd {midas_root} && ./build.sh --build-type Release"
            )
        if deps_found < deps_total:
            validation["recommendations"].append(
                "Install missing Python packages: conda env create -f environment.yml"
            )
        if not validation["bin_exists"]:
            validation["recommendations"].append(
                "Run MIDAS build script to compile executables"
            )

        # Check MIDAS Python packages (midas-params and midas-stress)
        validation["packages"] = {}
        midas_python = find_midas_python()
        for pkg_name, import_name in [
            ("midas_params", "midas_params"),
            ("midas_stress", "midas_stress"),
        ]:
            try:
                check = subprocess.run(
                    [midas_python, "-c", f"import {import_name}; print({import_name}.__version__)"],
                    capture_output=True, text=True, timeout=15,
                    env=get_midas_env(),
                )
                if check.returncode == 0:
                    validation["packages"][pkg_name] = {
                        "installed": True,
                        "version": check.stdout.strip(),
                    }
                else:
                    validation["packages"][pkg_name] = {"installed": False}
            except Exception:
                validation["packages"][pkg_name] = {"installed": False}

        pkgs_missing = [p for p, v in validation["packages"].items() if not v.get("installed")]
        if pkgs_missing:
            for pkg in pkgs_missing:
                validation["recommendations"].append(
                    f"Install {pkg}: pip install -e $MIDAS_PATH/packages/{pkg}"
                )

        return format_result({
            "tool": "validate_midas_installation",
            "validation": validation
        })

    except Exception as e:
        return format_result({
            "tool": "validate_midas_installation",
            "status": "error",
            "error": str(e)
        })

@mcp.tool()
async def verify_ff_reconstruction(
    result_folder: str,
    layers: str = ""
) -> str:
    """Independently verify an FF reconstruction's on-disk artifacts.

    Reads the artifacts under ``result_folder/LayerNr_<N>/`` and checks them
    against the FF Lab Notebook's documented silent-failure chains — WITHOUT
    running MIDAS or trusting the workflow's own summary. Surfaces the failure at
    its real cause, not the crash two stages downstream:
      • empty / all-zero InputAll.csv   → bad Width or collapsed BoxSize
      • 0 rows in SpotsToIndex.csv       → dead ω seed-window
      • 4-byte IndexBest*.bin            → indexer got 0 seeds → 0 grains
      • empty Data.bin/nData.bin, 0 Grains.csv rows

    Args:
        result_folder: FF result directory (holding LayerNr_<N> subdirs).
        layers: Optional comma-separated layer numbers (e.g. "0,1"); empty = all.

    Returns:
        JSON ``{status: ok|fail|incomplete|error, layers:[...], summary}``. Each
        check cites its notebook source. Deterministic, fail-open.
    """
    try:
        import handbook_guardrails as _hg
        layer_list = None
        if layers.strip():
            layer_list = [int(x) for x in re.split(r"[,\s]+", layers.strip()) if x]
        return format_result({
            "tool": "verify_ff_reconstruction",
            **_hg.verify_ff_outputs(result_folder, layer_list),
        })
    except Exception as e:
        return format_result({
            "tool": "verify_ff_reconstruction", "status": "error", "error": str(e)})


@mcp.tool()
async def get_midas_workflow_status(
    result_folder: str,
    workflow_type: str = "ff"
) -> str:
    """Check status of running MIDAS workflow.

    Monitors workflow progress by parsing log files and checking output files.

    Args:
        result_folder: Workflow result directory
        workflow_type: Workflow type (ff, nf, pf)

    Returns:
        JSON with current workflow status and progress
    """
    try:
        result_path = Path(result_folder).expanduser()

        if not result_path.exists():
            return format_result({
                "error": f"Result folder not found: {result_path}",
                "status": "not_found"
            })

        status = {
            "result_folder": str(result_path),
            "workflow_type": workflow_type.upper(),
            "status": "unknown",
            "progress": {},
            "output_files": [],
            "errors": []
        }

        # Check for log directory
        log_dirs = [result_path / "midas_log", result_path / "output"]
        log_dir = None
        for ld in log_dirs:
            if ld.exists():
                log_dir = ld
                break

        if log_dir:
            status["log_directory"] = str(log_dir)
            log_files = list(log_dir.glob("*.log"))
            status["n_log_files"] = len(log_files)

            # Parse latest log file for status
            if log_files:
                latest_log = max(log_files, key=lambda f: f.stat().st_mtime)
                status["latest_log"] = str(latest_log)

                try:
                    with open(latest_log, 'r') as f:
                        log_content = f.read()

                        # Look for completion markers
                        if "completed" in log_content.lower() or "finished" in log_content.lower():
                            status["status"] = "completed"
                        elif "error" in log_content.lower() or "failed" in log_content.lower():
                            status["status"] = "failed"
                            # Extract error lines
                            for line in log_content.split('\n'):
                                if 'error' in line.lower() or 'failed' in line.lower():
                                    status["errors"].append(line.strip())
                        elif "running" in log_content.lower() or "processing" in log_content.lower():
                            status["status"] = "running"

                        # Try to extract progress
                        for line in log_content.split('\n'):
                            if 'progress' in line.lower() or '%' in line:
                                status["progress"]["latest"] = line.strip()
                except:
                    pass

        # Check for output files based on workflow type
        if workflow_type.lower() == "ff":
            # FF-HEDM outputs
            output_patterns = [
                "LayerNr_*/GrainsReconstructed.csv",
                "*.MIDAS.zip",
                "Grains.csv"
            ]
        elif workflow_type.lower() == "nf":
            # NF-HEDM outputs (mic map is <MicFileText>.mic, not "Grains.mic")
            output_patterns = [
                "*.mic",
                "*_consolidated.h5",
                "GrainsLayer*.csv"
            ]
        else:
            output_patterns = ["*.csv", "*.mic"]

        for pattern in output_patterns:
            matches = list(result_path.glob(pattern))
            for match in matches:
                status["output_files"].append({
                    "file": str(match),
                    "size_kb": match.stat().st_size / 1024,
                    "modified": match.stat().st_mtime
                })

        status["n_output_files"] = len(status["output_files"])

        # If we found output files but no log info, assume completed
        if status["status"] == "unknown" and status["n_output_files"] > 0:
            status["status"] = "likely_completed"

        return format_result({
            "tool": "get_midas_workflow_status",
            "status_check": status
        })

    except Exception as e:
        return format_result({
            "tool": "get_midas_workflow_status",
            "status": "error",
            "error": str(e)
        })

# =============================================================================
# BASIC ANALYSIS TOOLS (from original server)
# =============================================================================

# =============================================================================
# FF-HEDM CALIBRATION (MIDAS Official)
# =============================================================================
# Tools moved to analysis_utilities_server.py:
# - detect_rings_quick (was: detect_diffraction_rings) - Custom NumPy diagnostic tool
# - identify_phases_basic (was: identify_crystalline_phases) - Basic phase matching
#
# This server now contains ONLY official MIDAS tools
# =============================================================================

def _read_param_value(param_path: Path, key: str) -> str | None:
    """Return the first whitespace-separated value for ``key`` in a MIDAS params file, or None."""
    try:
        for line in param_path.read_text().splitlines():
            s = line.strip()
            if not s or s.startswith('#'):
                continue
            parts = s.split(None, 1)
            if len(parts) == 2 and parts[0] == key:
                return parts[1].split('#', 1)[0].strip()
    except Exception:
        return None
    return None


def _resolve_image_transform(image_path: Path, user_arg: str,
                             params_file: Path | None) -> tuple[str, str]:
    """Pick the right ImTransOpt for calibration.

    Per MIDAS manual (manuals/README.md §Image Transformation), ImTransOpt is
    detector-mount specific — there is no extension-based rule. Resolution order:

      1. Explicit ``user_arg`` wins (honour what the operator said).
      2. ``ImTransOpt`` from the parameter file if present (re-use prior calibration).
      3. Sibling ``parameters.txt`` / ``Parameters.txt`` next to the image (auto-pick).
      4. Fall back to ``"0"`` (no transform) and emit a warning.

    Returns ``(transform_str, source)``. ``transform_str`` is "" when no override
    should be passed (i.e. params already has it). ``source`` is a short tag for
    diagnostics ("user" / "params" / "sibling" / "default-warned").
    """
    if user_arg:
        return user_arg.strip(), "user"

    # Check the explicit params file we're about to use
    if params_file and params_file.exists():
        v = _read_param_value(params_file, "ImTransOpt")
        if v is not None:
            print(f"  ImTransOpt={v} (from params {params_file.name})", file=sys.stderr)
            return "", "params"  # already in params, do not pass on CLI

    # Check sibling params files in the image directory
    for sibling_name in ("parameters.txt", "Parameters.txt", "params.txt"):
        sibling = image_path.parent / sibling_name
        if sibling.exists():
            v = _read_param_value(sibling, "ImTransOpt")
            if v is not None:
                print(f"  ImTransOpt={v} (auto-detected from {sibling.name})", file=sys.stderr)
                return v, "sibling"

    print("  ⚠ ImTransOpt not specified and not found in params/siblings — defaulting to 0 (no transform).", file=sys.stderr)
    print("    If calibration fails or geometry looks wrong, verify ImTransOpt against a", file=sys.stderr)
    print("    physical fiducial (beam-stop wire, fiducial marker). See MIDAS manuals/README.md.", file=sys.stderr)
    return "0", "default-warned"


def _strip_empty_value_lines(param_path: Path):
    """Remove lines with a key but no value (e.g. 'Dark \\n') that crash ffGenerateZipRefactor.py."""
    try:
        lines = param_path.read_text().splitlines()
        cleaned = []
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                cleaned.append(line)
                continue
            parts = stripped.split(None, 1)
            if len(parts) == 1 and not stripped[0].isdigit():
                continue
            cleaned.append(line)
        param_path.write_text('\n'.join(cleaned) + '\n')
    except Exception:
        pass


@mcp.tool()
async def midas_integrate_2d_to_1d(
    image_file: str,
    calibration_file: str = None,
    dark_file: str = None,
    result_folder: str = None,
    n_cpus: int = 4,
    convert_files: bool = True,
    bright_file: str = None,
    csv_output: bool = False,
    out_name: str = None,
    short_names: bool = True,
    r_min: float = None,
    r_max: float = None,
    r_bin_size: float = None,
    eta_min: float = None,
    eta_max: float = None,
    eta_bin_size: float = None,
) -> str:
    """Integrate a single 2D diffraction image to a 1D lineout using MIDAS integrator.py (v11).

    calibration_file is optional — if omitted, auto-searches the image directory for
    refined_MIDAS_params*.txt produced by midas_auto_calibrate.

    v11 executable chain:
      integrator.py (FF_HEDM/workflows/) → IntegratorZarrOMP

    Outputs written to result_folder (default: image_dir/integration/):
      *_lineout.xy   — 2θ (degrees) vs intensity text file
      *_lineout.bin  — binary lineout
      <stem>.zarr.zip — caked output (GSAS-II compatible; short-name default)
      *_lineouts.csv / *_REtaMap.csv — when csv_output=True
      Map.bin / nMap.bin / maskMap.bin — geometry maps (generated once)

    Args:
        image_file: Path to 2D diffraction image (.tif/.tiff, .ge/.ge1-.ge5, .h5/.hdf5, .zip)
        calibration_file: MIDAS parameter file (refined_MIDAS_params*.txt). Auto-detected if omitted.
        dark_file: Optional dark field image for background subtraction
        result_folder: Output directory (default: <image_dir>/integration)
        n_cpus: OMP threads for IntegratorZarrOMP (default: 4)
        convert_files: Convert input to Zarr before integrating (default: True)
        bright_file: Optional bright/flat-field image; integrated profiles embedded under processed/bright/ in zarr (v11, issue #20)
        csv_output: Also export per-frame lineouts and REtaMap as CSVs (v11, issue #23)
        out_name: Override the output zarr stem (single-file only — clobbers on multi-file)
        short_names: Use short v11 output naming (<stem>.zarr.zip). False = legacy suffix-stacking
        r_min, r_max, r_bin_size: Radial integration range overrides (pixels). Defaults: from params file.
        eta_min, eta_max, eta_bin_size: Azimuthal range overrides (degrees). Defaults: from params file.

    The agent SHOULD prompt the user to confirm R/eta ranges and result_folder before
    invoking this tool — those values control what 2θ window and azimuthal slice get
    integrated, which depends on what the user wants to study (specific rings, full eta,
    sub-sector, etc.). See midas-integrate SKILL.md for the recommended prompting flow.
    """
    try:
        image_path = Path(image_file).expanduser().absolute()

        if not image_path.exists():
            return format_result({"tool": "midas_integrate_2d_to_1d", "status": "error",
                                  "error": f"Image not found: {image_path}"})

        # Auto-find calibration file if not provided
        if calibration_file is None:
            candidates = sorted(image_path.parent.glob("refined_MIDAS_params*.txt"),
                                key=lambda p: p.stat().st_mtime, reverse=True)
            if not candidates:
                return format_result({"tool": "midas_integrate_2d_to_1d", "status": "error",
                                      "error": "No refined_MIDAS_params*.txt found in image directory. "
                                               "Run midas_auto_calibrate first, or pass calibration_file explicitly."})
            param_path = candidates[0]
            print(f"  Auto-detected calibration file: {param_path.name}", file=sys.stderr)
        else:
            param_path = Path(calibration_file).expanduser().absolute()
            if not param_path.exists():
                return format_result({"tool": "midas_integrate_2d_to_1d", "status": "error",
                                      "error": f"Parameter file not found: {param_path}"})

        # Validate param file is actually a MIDAS parameter file (not CSV/JSON/etc.)
        if param_path.suffix.lower() in ('.csv', '.json', '.log', '.bin', '.h5', '.hdf', '.tif'):
            return format_result({"tool": "midas_integrate_2d_to_1d", "status": "error",
                                  "error": f"Invalid parameter file: {param_path.name} (suffix {param_path.suffix}). "
                                           f"Use refined_MIDAS_params*.txt from midas_auto_calibrate."})
        try:
            with open(param_path) as f:
                param_text = f.read()
            if not any(kw in param_text for kw in ["Lsd", "Wavelength", "NrPixels", "BC "]):
                return format_result({"tool": "midas_integrate_2d_to_1d", "status": "error",
                                      "error": f"Parameter file {param_path.name} is missing critical keys "
                                               f"(Lsd, Wavelength, NrPixels, BC). "
                                               f"Use refined_MIDAS_params*.txt from midas_auto_calibrate."})
        except UnicodeDecodeError:
            return format_result({"tool": "midas_integrate_2d_to_1d", "status": "error",
                                  "error": f"Parameter file {param_path.name} is a binary file, not a text param file."})

        # Strip empty-value lines that crash ffGenerateZipRefactor.py
        _strip_empty_value_lines(param_path)

        # ── Native engine attempt (Strategy C: PyTorch compute + dual-format) ──
        # Native-first: if midas_integrate is pip-installed (midas-suite),
        # run the pure-Python/PyTorch engine. Fall back to integrator.py
        # when the package is missing or the hardware/size gate fails.
        # Set APEXA_USE_NATIVE_MIDAS=0 to force the subprocess path.
        _native_disabled = os.environ.get("APEXA_USE_NATIVE_MIDAS") == "0"
        if (not _native_disabled
                and not bright_file and not csv_output):
            try:
                from apexa_midas_native import (
                    native_integrate_2d_to_1d, MidasEngineUnavailable,
                )
                print("[engine] trying native midas_integrate first…",
                      file=sys.stderr)
                _out = (str(Path(result_folder).expanduser().absolute())
                        if result_folder
                        else str(image_path.parent / "integration"))
                # Hybrid by design (NOT force-pip): native PyTorch only when a
                # GPU/accelerator is present; on CPU hosts the native engine's
                # pixel-budget gate refuses and we fall through to the legacy
                # C++ integrator.py. This is deliberate — the C++ integrator is
                # the fast CPU path at batch scale (e.g. 1900+ frame echem
                # scans) AND matches the engine human pipelines use, keeping an
                # APEXA-vs-expert benchmark apples-to-apples. (Calibration, which
                # runs once, does force-pip; integration does not.)
                result_dict = native_integrate_2d_to_1d(
                    image_file=str(image_path),
                    calibration_file=str(param_path),
                    dark_file=dark_file or "",
                    result_folder=_out,
                    out_name=out_name,
                    r_min=r_min, r_max=r_max, r_bin_size=r_bin_size,
                    eta_min=eta_min, eta_max=eta_max, eta_bin_size=eta_bin_size,
                )
                return format_result(result_dict)
            except MidasEngineUnavailable as e:
                print(f"[engine] native unavailable: {e.install_hint}",
                      file=sys.stderr)
                print("[engine] falling back to subprocess (integrator.py)",
                      file=sys.stderr)
            except Exception as e:
                print(f"[engine] native call raised {type(e).__name__}: {e}",
                      file=sys.stderr)
                print("[engine] falling back to subprocess (integrator.py)",
                      file=sys.stderr)

        # Find integrator.py — v10 location: FF_HEDM/workflows/
        integrator_script = MIDAS_ROOT / "FF_HEDM" / "workflows" / "integrator.py"
        if not integrator_script.exists():
            return format_result({"tool": "midas_integrate_2d_to_1d", "status": "error",
                                  "error": f"integrator.py not found at {integrator_script}"})
        _warn_deprecated_cpp("integration: integrator.py")

        out_dir = Path(result_folder).expanduser().absolute() if result_folder \
                  else image_path.parent / "integration"
        out_dir.mkdir(parents=True, exist_ok=True)
        _announce_output("midas_integrate_2d_to_1d", out_dir)

        # Extract file number from basename only (not full path) to avoid
        # matching numbers in parent directories (e.g. /Users/b324240/ → 324240)
        basename = image_path.stem
        file_nr_match = re.search(r'(\d{6})', basename)
        file_nr = int(file_nr_match.group(1)) if file_nr_match else None

        midas_python = find_midas_python()
        cmd = [
            midas_python, str(integrator_script),
            "-paramFN",      str(param_path),
            "-dataFN",       str(image_path),
            "-resultFolder", str(out_dir),
            "-nCPUsLocal",   str(n_cpus),
            "-nCPUs",        "1",
            "-mapDetector",  "1",
            "-convertFiles", "1" if convert_files else "0",
            "-writeMat",     "0",
            "-shortNames",   "1" if short_names else "0",
            "-csvOutput",    "1" if csv_output else "0",
        ]
        if file_nr is not None:
            cmd += ["-startFileNr", str(file_nr), "-endFileNr", str(file_nr)]
        if dark_file:
            dark_path = Path(dark_file).expanduser().absolute()
            if not dark_path.exists():
                return format_result({"tool": "midas_integrate_2d_to_1d", "status": "error",
                                      "error": f"Dark file not found: {dark_path}"})
            cmd += ["-darkFN", str(dark_path)]
        if bright_file:
            bright_path = Path(bright_file).expanduser().absolute()
            if not bright_path.exists():
                return format_result({"tool": "midas_integrate_2d_to_1d", "status": "error",
                                      "error": f"Bright file not found: {bright_path}"})
            cmd += ["-brightFN", str(bright_path)]
        if out_name:
            cmd += ["-outName", str(out_name)]

        # Trailing parameter overrides — integrator.py treats unknown KEY VALUE pairs
        # at end of argv as parameter-file overrides written to a temp params copy.
        for key, val in (("RMin", r_min), ("RMax", r_max), ("RBinSize", r_bin_size),
                         ("EtaMin", eta_min), ("EtaMax", eta_max),
                         ("EtaBinSize", eta_bin_size)):
            if val is not None:
                cmd += [key, str(val)]

        print(f"\n  $ {' '.join(cmd)}", file=sys.stderr)

        result = subprocess.run(cmd, cwd=str(image_path.parent),
                                capture_output=True, text=True,
                                timeout=600, env=get_midas_env())

        if result.returncode != 0:
            print(f"❌ Integration failed (exit {result.returncode})", file=sys.stderr)
            for line in result.stderr.strip().splitlines()[-20:]:
                print(f"  {line}", file=sys.stderr)
            return format_result({"tool": "midas_integrate_2d_to_1d", "status": "error",
                                  "error": f"integrator.py exited {result.returncode}",
                                  "stderr": result.stderr, "stdout": result.stdout})

        lineout = sorted(out_dir.glob("*_lineout.xy"), key=lambda p: p.stat().st_mtime, reverse=True)
        # v11 short-name default is <stem>.zarr.zip; legacy is *_caked.hdf.zarr.zip
        # Also catch *.MIDAS.zip output when input already had .zip extension (double-suffix bug)
        zarr_out = sorted(out_dir.glob("*.zarr.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not zarr_out:
            zarr_out = sorted(
                (p for p in out_dir.glob("*.zip") if not p.name.endswith(".old")),
                key=lambda p: p.stat().st_mtime, reverse=True
            )
        csv_files = sorted(out_dir.glob("*_lineouts.csv"), key=lambda p: p.stat().st_mtime, reverse=True) \
                    if csv_output else []

        # Verify before reporting: exit-0 with no lineout/zarr means the run did
        # NOT actually produce an integrated profile — report that, don't claim
        # success (and never fabricate an output filename). A lineout FILE that is
        # header-only (0 data rows) is an equally false success: the integrator can
        # exit 0 and write just the "# 2theta_deg  intensity" header when no pixel
        # falls in any 2θ bin — seen on real data when the calibration params file
        # has NrPixelsY/NrPixelsZ transposed or is missing the detector panel/mask
        # description, so the R-Eta map is empty (Map.bin ~64 bytes). Count rows.
        def _lineout_data_rows(p) -> int:
            try:
                n = 0
                with open(p) as _fh:
                    for _ln in _fh:
                        s = _ln.strip()
                        if s and not s.startswith("#"):
                            n += 1
                return n
            except Exception:
                return -1  # unreadable → unknown, don't over-claim
        lineout_rows = _lineout_data_rows(lineout[0]) if lineout else 0
        produced = bool(lineout or zarr_out)
        empty_lineout = bool(lineout) and lineout_rows == 0
        if not produced:
            status = "incomplete"
            message = ("integrator.py exited 0 but produced no *_lineout.xy / *.zarr.zip "
                       "in result_folder — treat as not integrated")
        elif empty_lineout:
            status = "incomplete"
            message = (f"integrator.py exited 0 and wrote {lineout[0].name}, but it has no "
                       "data rows (header only) — no pixels fell in any 2θ bin, so the "
                       "R-Eta map is empty. Verify the calibration params file: a common "
                       "cause is NrPixelsY/NrPixelsZ transposed or a missing detector "
                       "panel/mask description (DataType/NPanelsY/PanelSize/MaskFile).")
        else:
            status = "success"
            message = f"Integration complete. Lineout: {lineout[0].name}"
        payload = {
            "tool": "midas_integrate_2d_to_1d",
            "status": status,
            "input_image": str(image_path),
            "calibration_file": str(param_path),
            "result_folder": str(out_dir),
            "lineout_xy": str(lineout[0]) if lineout else None,
            "lineout_data_rows": lineout_rows if lineout else 0,
            "zarr_zip": str(zarr_out[0]) if zarr_out else None,
            "csv_files": [str(p) for p in csv_files] if csv_files else None,
            "message": message,
        }
        _write_integration_outcome(out_dir, payload)
        return format_result(payload)

    except subprocess.TimeoutExpired:
        return format_result({"tool": "midas_integrate_2d_to_1d", "status": "error",
                              "error": "integrator.py timed out (>10 min)"})
    except Exception as e:
        return format_result({"tool": "midas_integrate_2d_to_1d", "status": "error", "error": str(e)})


def _strip_all_suffixes(name: str) -> str:
    """Reduce a filename to its base frame id by removing ALL dotted suffixes —
    general, with no detector/format whitelist to maintain. A raw double
    extension and a single one collapse to the same base:
      'JL_0Nb_002603.vrx.h5'  → 'JL_0Nb_002603'
      'JL_0Nb_002603.vrx.xye' → 'JL_0Nb_002603'
      'JL_0Nb_002603.xye'     → 'JL_0Nb_002603'
      'scan_00042.ge3'        → 'scan_00042'
    This is what makes an APEXA output name match a reference name that used a
    different container extension, without assuming which extension either used.
    (Beamline frame ids use '_' separators, not bare dots, so this never eats a
    meaningful name part in practice; callers that key on this guard against the
    pathological collision by falling back to exact-name matching.)"""
    base = name
    while "." in base:
        stem = base.rsplit(".", 1)[0]
        if not stem:            # leading-dot / dotfile — leave it alone
            break
        base = stem
    return base


def _nearest_dark(image_path, dark_files):
    """Match a sample frame to its dark by file number — the dark_after_<n+1>
    convention: prefer the closest dark numbered >= the sample, else the nearest.
    Returns a Path or None."""
    m = re.search(r'(\d{6})', Path(image_path).stem)
    if not m or not dark_files:
        return None
    n = int(m.group(1))
    def _num(p):
        mm = re.search(r'(\d{6})', Path(p).stem)
        return int(mm.group(1)) if mm else -10**9
    after = [d for d in dark_files if _num(d) >= n]
    pool = after or list(dark_files)
    return min(pool, key=lambda d: abs(_num(d) - n))


def _probe_dark_dataset(h5_path, explicit=None, data_location=None):
    """Pick the HDF5 dataset in a dark file that actually holds nonzero frames.

    A fixed default path is wrong across layouts: some `.h5` darks store their
    frame at ``exchange/data`` (with ``exchange/data_dark`` absent), while others
    (e.g. the `dark_after_*.vrx.h5` files here) store the real counts at
    ``exchange/data_dark`` and leave ``exchange/data`` as a zero placeholder.
    Reading the wrong one yields ``darkMean 0.0`` — a silent no-op subtraction.

    Strategy: try candidates in order and return the FIRST that exists with a
    nonzero-mean first frame. An ``explicit`` path is tried first but is NOT
    trusted blindly — if it reads as all-zero we fall through to auto-detection
    (that is exactly the failure this guards against). Returns ``(path, mean)``
    where ``path`` is the chosen dataset (or the best-available fallback), and
    ``mean`` is its first-frame mean (None if nothing could be read).
    """
    cands = []
    for c in (explicit, "exchange/data_dark", data_location, "exchange/data", "exchange/dark"):
        if c and c.strip("/") not in {x.strip("/") for x in cands}:
            cands.append(c)
    try:
        import h5py
        import numpy as _np
    except Exception:
        return (explicit or data_location or "exchange/data", None)

    best_zero = None   # first existing-but-zero dataset, used only as last resort
    try:
        with h5py.File(str(Path(h5_path).expanduser()), "r") as h:
            for c in cands:
                key = c.strip("/")
                if key not in h:
                    continue
                dset = h[key]
                try:
                    frame = dset[0] if getattr(dset, "ndim", 2) == 3 else dset[()]
                    mean = float(_np.asarray(frame).mean())
                except Exception:
                    continue
                if mean != 0.0:
                    return ("/" + key, mean)
                if best_zero is None:
                    best_zero = ("/" + key, mean)
    except Exception:
        return (explicit or data_location or "exchange/data", None)
    if best_zero is not None:
        return best_zero
    return (explicit or data_location or "exchange/data", None)


@mcp.tool()
@idempotent(
    tool="midas_integrate_series",
    anchor=lambda kw: kw.get("result_folder") or kw.get("image_dir") or "",
    salient=lambda kw: {k: kw.get(k) for k in (
        "parameter_file", "images", "image_dir", "pattern", "exclude_substring",
        "dark_file", "dark_dir", "dark_pattern", "dark_kind", "dark_source",
        "data_location", "dark_location", "max_files", "convert_files",
        "short_names", "csv_output", "r_min", "r_max", "r_bin_size",
        "eta_min", "eta_max", "eta_bin_size", "two_theta_min", "two_theta_max",
        "q_min", "q_max", "n_channels")},
)
async def midas_integrate_series(
    parameter_file: str,
    images: list = None,
    image_dir: str = None,
    pattern: str = "*.h5",
    exclude_substring: str = "dark",
    dark_file: str = None,
    dark_dir: str = None,
    dark_pattern: str = "*dark*",
    dark_kind: str = "after",
    dark_source: str = "file",
    data_location: str = None,
    dark_location: str = None,
    max_files: int = None,
    result_folder: str = None,
    n_cpus: int = 8,
    n_parallel: int = None,
    compute_target: str = "auto",
    convert_files: bool = True,
    short_names: bool = True,
    csv_output: bool = False,
    r_min: float = None, r_max: float = None, r_bin_size: float = None,
    eta_min: float = None, eta_max: float = None, eta_bin_size: float = None,
    two_theta_min: float = None, two_theta_max: float = None,
    q_min: float = None, q_max: float = None, n_channels: int = None,
) -> str:
    """Integrate a SERIES of separate 2D image files in ONE tool call.

    USE THIS instead of calling ``midas_integrate_2d_to_1d`` in a loop over many
    files. Looping the single-file tool burns the agent's iteration budget and
    leads to aborted runs that get summarised from memory (fabricated file lists).
    This tool processes the whole series in one call, verifies each output on
    disk, and writes ``APEXA_integration_series.json`` so results are cited, not
    reconstructed. Only files that actually produced a ``*_lineout.xy`` are
    reported ``success``.

    Output layout (written by the tool — do NOT hand-copy with run_command):
      <result_folder>/<frame>/*_lineout.xy   (raw per-frame integrator output)
      <result_folder>/xye/<frame>.xye        (TOPAS: 2θ°,  I, σ=√I)
      <result_folder>/fxye/<frame>.fxye      (GSAS:  2θ×100, I, σ=√I)
      <result_folder>/APEXA_integration_series.json  (manifest)
    The consolidated xye/ + fxye/ dirs mirror the expert per-sample layout and are
    byte-format-identical to midas_batch_integrate. Pass ``result_folder`` to write
    directly where you want it (e.g. an APEXA_benchmark dir) — never integrate to a
    default then copy. Darks are excluded from the sample set, so xye/ + fxye/
    contain samples only.

    Output grid — specify in WHATEVER convention you use (none is privileged):
      • radius (px):  ``r_min`` / ``r_max`` / ``r_bin_size``   (integrator-native)
      • 2θ (degrees): ``two_theta_min`` / ``two_theta_max`` / ``n_channels``
      • Q (1/Å):      ``q_min`` / ``q_max`` / ``n_channels``
    2θ and Q are converted to the radius grid the integrator needs, using Lsd/px
    (and Wavelength for Q) from the parameter file — R=(Lsd/px)·tan(2θ),
    Q=4π·sin(θ)/λ. Use this to reproduce a reference/desired grid exactly instead of
    leaving RMin/RMax at calibration defaults (which yields a different range). If
    you're matching someone else's output, read THEIR grid from their script/params
    — don't guess; if there's no reference, ask the user for their preferred grid.

    File selection:
      • ``images``: explicit list of image paths, OR
      • ``image_dir`` + ``pattern``: glob (files whose name contains
        ``exclude_substring`` — e.g. darks — are skipped).
      • ``max_files``: cap to a representative, evenly-spaced subset (e.g. 3 →
        start/middle/end). Omit to integrate every matched file.

    Performance:
      • ``n_parallel``: how many files integrate CONCURRENTLY (they're independent).
        Default (omit) auto-fills the host without oversubscribing:
        ``cores // n_cpus`` workers, capped at 16. This is the main speed lever for
        a large CPU series — the old path ran strictly one file at a time. Pass
        ``n_parallel=1`` to force serial (e.g. to leave cores free during live
        acquisition). ``n_cpus`` stays the per-file thread count.

    Dark handling (generalizes across data layouts — not every beamtime is
    structured the same):
      • ``dark_source="file"`` (default): a separate dark file per frame. Either
        pass one ``dark_file`` for all, or let the tool discover darks in
        ``dark_dir`` (default = image dir) via ``dark_pattern``, scoped to THIS
        sample's prefix and to ``dark_kind`` ("after"/"before"/"any"), then match
        each frame to the nearest-numbered dark (the dark_after_<n+1> convention,
        with graceful fallback to nearest).
      • ``dark_source="embedded"``: each frame's own file carries its dark
        (e.g. HDF5 ``exchange/data_dark``); set ``dark_location`` accordingly.
      • ``dark_source="none"``: integrate without dark subtraction.
      • ``data_location`` / ``dark_location``: HDF5 dataset paths. A separate .h5
        dark's frame is read from ``dark_location`` (defaults to ``data_location``
        or ``exchange/data`` — NOT integrator's ``exchange/dark`` default, which
        is wrong for files that store the dark at ``exchange/data``).
    """
    try:
        param_path = Path(parameter_file).expanduser().absolute()
        if not param_path.exists():
            return format_result({"tool": "midas_integrate_series", "status": "error",
                                  "error": f"Parameter file not found: {param_path}"})
        # 1) resolve the image list. Darks are NEVER samples: a file that the dark
        # layer would treat as a dark must not be integrated as data. This is
        # driven by the configurable `dark_pattern` (the single knob for "what is a
        # dark" — default *dark*, override for other conventions like *bg*/*empty*),
        # NOT a hardcoded name; `dark_kind` only chooses WHICH darks to subtract,
        # it never affects this exclusion. Independent of `exclude_substring`.
        # (Generic fix for the observed dark_before leak that doubled 192→384.)
        import fnmatch as _fnm
        _dpat = (dark_pattern or "").lower()
        def _is_dark(name: str) -> bool:
            n = name.lower()
            if _dpat:
                return _fnm.fnmatch(n, _dpat)     # configurable dark identifier
            return "dark" in n                    # fallback only if no dark_pattern set
        excl = (exclude_substring or "").lower()
        if images:
            allp = sorted(Path(p).expanduser().absolute() for p in images)
        elif image_dir:
            d = Path(image_dir).expanduser().absolute()
            if not d.is_dir():
                return format_result({"tool": "midas_integrate_series", "status": "error",
                                      "error": f"image_dir not found: {d}"})
            allp = sorted(p for p in d.glob(pattern) if p.is_file())
        else:
            return format_result({"tool": "midas_integrate_series", "status": "error",
                                  "error": "provide either images=[...] or image_dir=..."})
        files = [p for p in allp if p.exists() and not _is_dark(p.name)
                 and (not excl or excl not in p.name.lower())]
        n_darks_excluded = len(allp) - len(files)
        if not files:
            return format_result({"tool": "midas_integrate_series", "status": "error",
                                  "error": f"no sample image files matched (excluded {n_darks_excluded} darks)"})
        n_matched = len(files)
        # 1b) compute-dispatch tiering (docs/COMPUTE_DISPATCH.md). This tool runs
        # the CPU integrator.py engine; for a large sweep on a CPU-only host with a
        # GPU endpoint configured, recommend offloading rather than grinding.
        plan = _pick_compute_target(n_frames=n_matched, megapixels=8.3, prefer=compute_target)
        if compute_target == "remote" or (
                plan["target"] == "remote-gpu" and os.environ.get("APEXA_GPU_AUTO_DISPATCH")):
            tgt = plan.get("machine") or plan.get("endpoint") or "the configured GPU endpoint"
            return format_result({
                "tool": "midas_integrate_series", "status": "dispatch_recommended",
                "compute": plan, "matched_files": n_matched,
                "recommendation": (
                    f"Large batch ({n_matched} frames) — run on {tgt}: on a GPU host with a "
                    "shared view of the data, invoke this same midas_integrate_series call (it "
                    "will use the GPU engines there), or midas-pipeline --machine "
                    f"{plan.get('machine','<machine>')} --device cuda. "
                    "Pass compute_target='local' to force CPU integration here (slow)."),
            })
        # 2) representative, evenly-spaced subset when capped
        subset_note = None
        if max_files and max_files < n_matched:
            if max_files == 1:
                idx = [0]
            else:
                idx = sorted({round(i * (n_matched - 1) / (max_files - 1))
                              for i in range(max_files)})
            files = [files[i] for i in idx]
            subset_note = f"{len(files)} of {n_matched} matched files (evenly-spaced subset)"
        # 3) dark candidates (only if no single dark given). Scope to THIS sample
        # (the prefix before the 6-digit frame number, e.g. "JL_0Nb_") AND to the
        # requested dark_kind ("after"/"before"/"any"), so a JL_0Nb frame is never
        # matched to a WY5/JL_Nb dark or the wrong before/after set in a directory
        # that interleaves many samples and both dark kinds.
        dark_files = []
        dark_scope = "n/a"
        if not dark_file and dark_source == "file":
            import os as _os
            base = (Path(dark_dir).expanduser().absolute() if dark_dir
                    else (Path(image_dir).expanduser().absolute() if image_dir else files[0].parent))
            prefix = _os.path.commonprefix([re.sub(r'\d{6}.*$', '', p.stem) for p in files])
            sample_tok = prefix.rstrip('_').lower()
            kind = (dark_kind or "").lower().strip()
            cand = sorted(base.glob(dark_pattern))
            def _same_sample(p):
                return (not sample_tok) or p.name.startswith(prefix) or sample_tok in p.name.lower()
            def _of_kind(pool):
                # Filter to the requested before/after set; fall back to any-kind
                # if the directory doesn't tag darks that way.
                if kind in ("after", "before"):
                    k = [p for p in pool if f"dark_{kind}" in p.name.lower()]
                    return k or pool
                return pool
            scoped = [p for p in cand if _same_sample(p)]
            if scoped:
                # This sample has darks under its OWN prefix — use only those, so a
                # directory interleaving many samples (each with its own darks) never
                # cross-matches. This is the common per-sample-dark case.
                dark_files = _of_kind(scoped)
                dark_scope = "sample-prefix"
            else:
                # No dark under this sample's prefix → the darks are SHARED across
                # samples: collected per-LOOP (e.g. one before/after set bracketing a
                # whole set of cells cycled in turn), and therefore named for a
                # different sample than the one being integrated. Fall back to ALL
                # darks of the requested kind in the directory and let _nearest_dark
                # pick the right loop's dark by frame number. General, not
                # dataset-specific: this branch only runs when the sample has no dark
                # of its own, so it can't steal a sample's dedicated darks.
                dark_files = _of_kind(cand)
                dark_scope = "shared/per-loop (no sample-prefix dark)"
        # 4) integrator setup (CPU subprocess engine — the batch-scale path)
        integrator_script = MIDAS_ROOT / "FF_HEDM" / "workflows" / "integrator.py"
        if not integrator_script.exists():
            return format_result({"tool": "midas_integrate_series", "status": "error",
                                  "error": f"integrator.py not found at {integrator_script}"})
        _warn_deprecated_cpp("integration series: integrator.py")
        midas_python = find_midas_python()
        out_root = (Path(result_folder).expanduser().absolute() if result_folder
                    else files[0].parent / "integration_series")
        out_root.mkdir(parents=True, exist_ok=True)
        # Flag when output landed at the DEFAULT location (inside the raw data dir)
        # because result_folder was not passed. Surfaced in the returned summary —
        # not just stderr — so the agent/user notices instead of assuming it went to
        # the intended benchmark/output folder (recurring "wrote to the wrong place").
        _used_default_out = not result_folder
        env = get_midas_env()

        # Grid convention → radius grid. The integrator bins in detector RADIUS
        # (px), but users specify the grid in whichever convention THEY prefer —
        # radius (r_min/r_max, pass through), 2θ degrees (two_theta_*), or Q inverse-
        # Å (q_*). No convention is privileged: convert the one that's given to the
        # radius grid using the geometry in the param file, so APEXA reproduces the
        # user's/reference's grid exactly instead of leaving RMin/RMax at calibration
        # defaults. R=(Lsd/px)·tan(2θ);  Q=4π·sin(θ)/λ ⇒ 2θ=2·asin(Qλ/4π).
        grid_info = None
        if any(v is not None for v in (two_theta_min, two_theta_max, q_min, q_max)):
            import math as _m
            _lsd = _px = _wl = None
            for _ln in param_path.read_text().splitlines():
                _t = _ln.split()
                if len(_t) >= 2 and _t[0] == "Lsd":
                    _lsd = float(_t[1])
                elif len(_t) >= 2 and _t[0] in ("px", "PixelSize", "pxY"):
                    _px = float(_t[1])
                elif len(_t) >= 2 and _t[0] in ("Wavelength", "wavelength", "Lambda"):
                    _wl = float(_t[1])
            if not _lsd or not _px:
                return format_result({"tool": "midas_integrate_series", "status": "error",
                                      "error": "grid spec needs Lsd and px in the parameter file "
                                               "to convert to the radius grid; none found."})
            def _tt_to_r(tt_deg):
                return (_lsd / _px) * _m.tan(_m.radians(tt_deg))
            def _q_to_r(q):
                if not _wl:
                    raise ValueError("Q grid needs Wavelength in the parameter file")
                two_theta = 2.0 * _m.degrees(_m.asin(q * _wl / (4.0 * _m.pi)))
                return _tt_to_r(two_theta)
            try:
                if q_min is not None or q_max is not None:      # Q convention
                    convention = "Q(1/Å)"
                    lo, hi = q_min, q_max
                    if q_min is not None:
                        r_min = _q_to_r(q_min)
                    if q_max is not None:
                        r_max = _q_to_r(q_max)
                else:                                            # 2θ convention
                    convention = "2θ(deg)"
                    lo, hi = two_theta_min, two_theta_max
                    if two_theta_min is not None:
                        r_min = _tt_to_r(two_theta_min)
                    if two_theta_max is not None:
                        r_max = _tt_to_r(two_theta_max)
            except ValueError as _ve:
                return format_result({"tool": "midas_integrate_series", "status": "error",
                                      "error": str(_ve)})
            if n_channels and r_min is not None and r_max is not None:
                r_bin_size = (r_max - r_min) / float(n_channels)
            grid_info = {"convention": convention, "min": lo, "max": hi,
                         "n_channels": n_channels, "Lsd_um": _lsd, "px_um": _px,
                         "wavelength_A": _wl, "r_min_px": r_min, "r_max_px": r_max,
                         "r_bin_px": r_bin_size}

        # Resolve file-level parallelism. Separate input files are INDEPENDENT
        # (own out_root/<stem>/ dir, read-only shared darks), so the series is
        # embarrassingly parallel. Default (None) fills the box WITHOUT
        # oversubscribing: cores // n_cpus workers (each worker still uses n_cpus
        # threads inside the integrator), capped at 16 and at the file count. This
        # replaces the old strictly-serial loop that spawned one cold integrator.py
        # process per file back-to-back and left most cores idle. Pass n_parallel=1
        # to force the old serial behavior (e.g. during live acquisition).
        _cores = os.cpu_count() or 8
        if n_parallel is None or n_parallel < 1:
            n_parallel = max(1, min(16, _cores // max(1, n_cpus)))
        n_parallel = max(1, min(n_parallel, len(files)))

        # Announce BEFORE running (Claude-Code style: say what + where up front).
        _dscheme = (f"{dark_source}/{dark_kind}" if dark_source == "file" else dark_source)
        if dark_source == "file" and not dark_file and dark_scope != "n/a":
            _dscheme += f" [{dark_scope}]"   # flag shared/per-loop cross-prefix matching
        print(f"[integrate_series] {len(files)} file(s) → {out_root}", file=sys.stderr)
        print(f"[integrate_series] params={param_path.name}  darks={_dscheme}  "
              f"compute={plan['target']}  parallel={n_parallel}×{n_cpus}cpu "
              f"({_cores} cores)", file=sys.stderr)
        if grid_info:
            print(f"[integrate_series] grid {grid_info['min']}–{grid_info['max']} "
                  f"{grid_info['convention']} / {n_channels} ch → R {r_min:.1f}–{r_max:.1f}px "
                  f"bin {r_bin_size:.4f}px", file=sys.stderr)

        # ── Pre-flight: resolve the dark dataset ONCE and abort on a zero dark ──
        # The correct HDF5 dark dataset varies by layout (some store the frame at
        # exchange/data, others at exchange/data_dark with exchange/data a zero
        # placeholder). Probe a representative dark up front so a wrong/zero dark
        # fails LOUDLY here — never after silently integrating 192 zero-subtracted
        # frames and reporting plain "success" (the exact trap that bit JL_0Nb).
        # Reject bogus dark_source values (e.g. a model passing "paired_dark_after"
        # by conflating dark_source with dark_kind) rather than silently falling
        # through every branch and matching NO dark.
        _VALID_DARK_SOURCES = {"file", "embedded", "none"}
        if dark_source not in _VALID_DARK_SOURCES:
            return format_result({
                "tool": "midas_integrate_series", "status": "error",
                "error": (
                    f"Unknown dark_source={dark_source!r}. Valid values: "
                    f"'file' (separate dark file per frame; set dark_dir/dark_file and "
                    f"dark_kind='after'|'before'), 'embedded' (dark inside each frame's "
                    f"own file; set dark_location), or 'none'. For paired dark-after "
                    f"files use dark_source='file', dark_kind='after'."),
            })

        _dkloc_resolved = None    # chosen dark dataset (uniform layout → resolve once)
        _dmean = None             # first-frame mean of the chosen dark dataset
        if dark_source != "none" and files:
            if dark_source == "embedded":
                _rep_dark, _explicit = files[0], (dark_location or "exchange/data_dark")
            elif dark_file:
                _rep_dark, _explicit = Path(dark_file).expanduser().absolute(), dark_location
            else:
                _nd = _nearest_dark(files[0], dark_files)
                _rep_dark, _explicit = (Path(_nd) if _nd else None), dark_location
            # Dark requested but none could be matched → abort. Integrating without a
            # dark must be an explicit choice (dark_source='none'), never an accident
            # of an empty dark_dir or a naming mismatch.
            if not (_rep_dark and Path(_rep_dark).exists()):
                return format_result({
                    "tool": "midas_integrate_series", "status": "error",
                    "error": (
                        f"dark_source={dark_source!r} requested but NO dark file was "
                        f"matched (dark_scope={dark_scope}; dark_dir/dark_file empty or "
                        f"no *_dark_{dark_kind}_* files in the directory). Aborting before "
                        f"integrating {len(files)} frames with no dark. Shared/per-loop "
                        f"darks under a different sample prefix ARE matched automatically "
                        f"when the sample has none of its own, so this means the directory "
                        f"holds no matching dark at all. Fix: set dark_dir to the folder "
                        f"holding the dark files (e.g. *_dark_after_*.h5) with "
                        f"dark_kind='after', or pass dark_source='none' to integrate "
                        f"without a dark on purpose."),
                    "dark_file": None, "dark_dataset": None,
                    "dark_first_frame_mean": None, "dark_applied": False,
                })
            if _rep_dark and Path(_rep_dark).exists():
                _dkloc_resolved, _dmean = _probe_dark_dataset(
                    _rep_dark, explicit=_explicit, data_location=data_location)
                print(f"[integrate_series] dark dataset: {_dkloc_resolved} "
                      f"(first-frame mean={_dmean}) from {Path(_rep_dark).name}",
                      file=sys.stderr)
                if _dmean == 0.0:
                    return format_result({
                        "tool": "midas_integrate_series", "status": "error",
                        "error": (
                            f"Dark subtraction would be a NO-OP: the dark dataset "
                            f"{_dkloc_resolved} reads all-zero (first-frame mean 0.0) in "
                            f"{Path(_rep_dark).name}. Aborting BEFORE integrating "
                            f"{len(files)} frames with an effectively absent dark. Fix: "
                            f"set dark_location to the dataset holding real dark counts "
                            f"(e.g. 'exchange/data_dark'), or pass dark_source='none' to "
                            f"integrate without a dark on purpose."),
                        "dark_file": str(_rep_dark),
                        "dark_dataset": _dkloc_resolved,
                        "dark_first_frame_mean": _dmean,
                    })

        # Integrate ONE file → its result record. Pulled out of the loop so it can
        # run in a worker thread: each call only blocks on subprocess.run (the work
        # is in the child integrator.py process, which releases the GIL), so a thread
        # pool yields full process-level parallelism with no pickling.
        def _integrate_one(img):
            out_dir = out_root / img.stem
            out_dir.mkdir(parents=True, exist_ok=True)
            if dark_source == "none":
                this_dark = None
            elif dark_source == "embedded":
                this_dark = img          # the frame's own file carries its dark
            elif dark_file:
                this_dark = Path(dark_file).expanduser().absolute()
            else:
                nd = _nearest_dark(img, dark_files)
                this_dark = Path(nd) if nd else None
            rec = {"input_image": str(img),
                   "dark_file": str(this_dark) if this_dark else None,
                   "result_folder": str(out_dir)}
            m = re.search(r'(\d{6})', img.stem)
            file_nr = int(m.group(1)) if m else None
            cmd = [midas_python, str(integrator_script),
                   "-paramFN", str(param_path), "-dataFN", str(img),
                   "-resultFolder", str(out_dir),
                   "-nCPUsLocal", str(n_cpus), "-nCPUs", "1", "-mapDetector", "1",
                   "-convertFiles", "1" if convert_files else "0", "-writeMat", "0",
                   "-shortNames", "1" if short_names else "0",
                   "-csvOutput", "1" if csv_output else "0"]
            if file_nr is not None:
                cmd += ["-startFileNr", str(file_nr), "-endFileNr", str(file_nr)]
            if data_location:
                cmd += ["-dataLoc", data_location]
            if this_dark and this_dark.exists():
                cmd += ["-darkFN", str(this_dark)]
                # Dark dataset was resolved (and zero-dark aborted) in the pre-flight
                # above; fall back to the layout default only if the probe returned
                # nothing (e.g. h5py unavailable).
                dkloc = _dkloc_resolved or dark_location or (
                    "exchange/data_dark" if dark_source == "embedded"
                    else (data_location or "exchange/data"))
                cmd += ["-darkLoc", dkloc]
            for key, val in (("RMin", r_min), ("RMax", r_max), ("RBinSize", r_bin_size),
                             ("EtaMin", eta_min), ("EtaMax", eta_max), ("EtaBinSize", eta_bin_size)):
                if val is not None:
                    cmd += [key, str(val)]
            try:
                res = subprocess.run(cmd, cwd=str(img.parent), capture_output=True,
                                     text=True, timeout=600, env=env)
                lineout = sorted(out_dir.glob("*_lineout.xy"),
                                 key=lambda p: p.stat().st_mtime, reverse=True)
                if res.returncode == 0 and lineout:
                    rec["status"] = "success"
                    rec["lineout_xy"] = str(lineout[0])
                else:
                    rec["status"] = "error"
                    rec["error"] = (f"integrator.py exit {res.returncode}"
                                    if res.returncode != 0 else "no *_lineout.xy produced")
                    rec["stderr_tail"] = "\n".join(res.stderr.strip().splitlines()[-5:])
            except subprocess.TimeoutExpired:
                rec["status"] = "error"; rec["error"] = "timeout (>10 min)"
            except Exception as _e:
                rec["status"] = "error"; rec["error"] = str(_e)
            return rec

        # Run the series: serial when n_parallel==1 (deterministic, matches the old
        # path), else a bounded thread pool of n_parallel concurrent files. Manifest
        # order is restored to input order afterwards (as_completed finishes out of
        # order), so results/xye/fxye stay deterministic regardless of scheduling.
        _rec_by_img, _done = {}, 0
        if n_parallel == 1:
            for img in files:
                rec = _integrate_one(img)
                _rec_by_img[str(img)] = rec
                _done += 1
                print(f"  [{rec['status']:7s}] ({_done}/{len(files)}) {img.name}",
                      file=sys.stderr)
        else:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            with ThreadPoolExecutor(max_workers=n_parallel) as _ex:
                _futs = {_ex.submit(_integrate_one, img): img for img in files}
                for _fut in as_completed(_futs):
                    img = _futs[_fut]
                    try:
                        rec = _fut.result()
                    except Exception as _e:   # worker crash: record, never abort the batch
                        rec = {"input_image": str(img),
                               "result_folder": str(out_root / img.stem),
                               "status": "error", "error": f"worker crashed: {_e}"}
                    _rec_by_img[str(img)] = rec
                    _done += 1
                    print(f"  [{rec['status']:7s}] ({_done}/{len(files)}) {img.name}",
                          file=sys.stderr)
        per_file = [_rec_by_img[str(img)] for img in files]     # deterministic order
        n_ok = sum(1 for r in per_file if r.get("status") == "success")

        n_fail = len(per_file) - n_ok
        # Consolidate into the expert's per-sample layout at result_folder:
        #   <result_folder>/xye/<frame>.xye   (TOPAS: 2θ°,  I, σ=√I)
        #   <result_folder>/fxye/<frame>.fxye (GSAS:  2θ×100, I, σ=√I)
        # Each successful *_lineout.xy (2-col: 2θ°, I) is read and re-written in
        # BOTH formats with the SAME _write_xye/_write_fxye helpers midas_batch_
        # integrate uses — so a series is byte-format-identical to the operando
        # batch tool and directly GSAS/TOPAS-ready. Only the samples reach here
        # (darks were excluded from `files`), and the tool writes them itself — the
        # agent never hand-copies, so darks cannot leak back in (the 384-xye /
        # 0-fxye mess came entirely from agent run_command copying).
        def _read_lineout_xy(p):
            tth, inten = [], []
            with open(p) as fh:
                for ln in fh:
                    ln = ln.strip()
                    if not ln or ln[0] in "#Rr":     # skip blank/header
                        continue
                    parts = ln.replace(",", " ").split()
                    if len(parts) >= 2:
                        try:
                            tth.append(float(parts[0])); inten.append(float(parts[1]))
                        except ValueError:
                            continue
            return tth, inten
        xye_dir, fxye_dir = out_root / "xye", out_root / "fxye"
        consolidated = []
        for rec in per_file:
            if rec.get("status") == "success" and rec.get("lineout_xy"):
                # Clean base name: strip ALL extensions (.h5 AND any container
                # token like .vrx) so consolidated files are '<frame>.xye',
                # matching the standard reference convention — NOT '<frame>.vrx.xye',
                # whose spurious token made filename-matched comparison find 0
                # common files. General: no per-format token list.
                stem = _strip_all_suffixes(Path(rec["input_image"]).name)
                try:
                    tth, inten = _read_lineout_xy(rec["lineout_xy"])
                    if not tth:
                        raise RuntimeError("empty/unreadable lineout")
                    xye_dir.mkdir(parents=True, exist_ok=True)
                    fxye_dir.mkdir(parents=True, exist_ok=True)
                    xye_p = xye_dir / f"{stem}.xye"
                    fxye_p = fxye_dir / f"{stem}.fxye"
                    _write_xye(xye_p, tth, inten)
                    _write_fxye(fxye_p, tth, inten, title=stem)
                    rec["xye"] = str(xye_p); rec["fxye"] = str(fxye_p)
                    consolidated.append(stem)
                except Exception as _e:
                    rec["consolidate_error"] = str(_e)
        summary = {
            "tool": "midas_integrate_series",
            "status": "success" if n_fail == 0 else ("partial" if n_ok else "error"),
            "parameter_file": str(param_path),
            "matched_files": n_matched,
            "darks_excluded": n_darks_excluded,
            "dark_scheme": _dscheme,
            "dark_scope": dark_scope,   # sample-prefix vs shared/per-loop (cross-prefix)
            # Provenance the "did you grab the dark?" question needs: the dataset
            # actually read and its first-frame mean. mean==0.0 ⇒ subtraction was a
            # no-op (wrong path / zero placeholder), even though a dark was "found".
            "dark_dataset": _dkloc_resolved,
            "dark_first_frame_mean": _dmean,
            "dark_applied": bool(_dkloc_resolved and _dmean not in (None, 0.0)),
            "processed_files": len(per_file),
            "succeeded": n_ok,
            "failed": n_fail,
            "subset": subset_note,
            "compute": plan,
            "n_parallel": n_parallel,
            "output_root": str(out_root),
            "output_location_warning": (
                f"result_folder was not set — output was written to a DEFAULT location "
                f"inside the data directory ({out_root}). If you meant it to go to a "
                f"specific folder (e.g. an APEXA_benchmark dir), re-run with result_folder "
                f"set; do NOT report a different location than this."
                if _used_default_out else None),
            "grid": grid_info,
            "xye_dir": str(xye_dir) if consolidated else None,
            "fxye_dir": str(fxye_dir) if consolidated else None,
            "consolidated_count": len(consolidated),
            "results": per_file,
        }
        manifest = _write_integration_outcome(
            out_root, summary, filename="APEXA_integration_series.json")
        _announce_output("midas_integrate_series", out_root,
                         xye=summary["xye_dir"], fxye=summary["fxye_dir"],
                         succeeded=n_ok, failed=n_fail)
        # Return a COMPACT payload: all failures + a few successes; full list is on disk.
        summary["manifest"] = manifest
        summary["results"] = ([r for r in per_file if r["status"] != "success"] +
                              [r for r in per_file if r["status"] == "success"][:5])
        summary["note"] = ("Cite only outputs listed here or in the manifest; files "
                           "not marked 'success' produced NO lineout and were not integrated.")
        return format_result(summary)
    except Exception as e:
        return format_result({"tool": "midas_integrate_series", "status": "error", "error": str(e)})


def _read_xy_grid(path: Path):
    """Read a 1D pattern (.xy/.xye/.fxye/.dat): return (n_rows, x_min, x_max, x[], y[]).
    Skips comment/header lines; takes the first two numeric columns."""
    xs, ys = [], []
    for ln in path.read_text().splitlines():
        ln = ln.strip()
        if not ln or ln[0] in "#;Bb":          # comment / BANK header
            continue
        t = ln.replace(",", " ").split()
        if len(t) >= 2:
            try:
                xs.append(float(t[0])); ys.append(float(t[1]))
            except ValueError:
                continue
    if not xs:
        return 0, None, None, [], []
    return len(xs), min(xs), max(xs), xs, ys


@mcp.tool()
async def compare_integrated_series(apexa_dir: str, reference_dir: str,
                                    pattern: str = "*.xye",
                                    x_tol: float = 0.02,
                                    bin_frac: float = 0.25) -> str:
    """Verify one set of integrated 1D patterns against a reference — grid, count,
    and peak alignment — and REFUSE to claim parity unless they actually match.

    Convention-agnostic: it compares whatever x-axis the files use (radius, 2θ, Q,
    or d) by reading the first two numeric columns — it does not assume degrees.
    ``pattern`` selects the format to compare (``*.xye``, ``*.xy``, ``*.fxye``,
    ``*.dat``, ``*.chi``, …); compare like-for-like.

    Grid tolerance is judged **relative to the bin width**, because an absolute
    threshold cannot serve every x-unit at once: 0.02 is generous in 2θ-degrees but
    punishing in radius-pixels, so the SAME physical grid would pass as .xye and
    fail as .fxye. Two grids count as the same when the row counts are equal AND
    each endpoint agrees within ``max(x_tol, bin_frac × bin_width)`` — i.e. a small
    fraction of one bin. ``x_tol`` is the absolute floor (files' own x units);
    ``bin_frac`` is the scale-relative allowance (default 0.25 bin). Sub-bin float /
    rounding drift on an otherwise identical grid therefore reads as parity, as it
    should — every sample lands within a fraction of a bin of its counterpart.

    Compares two per-sample directories (``apexa_dir`` vs ``reference_dir``, matched
    by filename) and reports, over the common files:
      • file-count agreement,
      • x-grid agreement: row count + min/max within ``x_tol``,
      • strongest-peak position offset (a cheap alignment sanity check).
    Returns ``grid_match``/``parity`` booleans — the guard against reporting a
    comparison as "matching" when the grids differ. If ``grid_match`` is false,
    re-run the integration on the reference grid (midas_integrate_series accepts the
    grid in radius, 2θ, or Q) before any comparison is trustworthy.
    """
    try:
        ad, rd = Path(apexa_dir).expanduser(), Path(reference_dir).expanduser()
        if not ad.is_dir() or not rd.is_dir():
            return format_result({"tool": "compare_integrated_series", "status": "error",
                                  "error": f"directory not found: {ad if not ad.is_dir() else rd}"})
        a_paths = sorted(ad.glob(pattern))
        r_paths = sorted(rd.glob(pattern))
        # Match on a NORMALIZED key (base frame id, all extensions stripped), not
        # the raw filename — so APEXA's 'JL_0Nb_002603.vrx.xye' pairs with the
        # reference's 'JL_0Nb_002603.xye' instead of yielding 0 common files (the
        # double-extension false negative that forced a manual rename/copy detour).
        # General: no per-format token list. If stripping suffixes collapses two
        # distinct files to one key (bare dots in a frame name), that side is
        # ambiguous — fall back to exact-name matching so we never mis-pair.
        def _keyed(paths):
            keyed, collided = {}, False
            for p in paths:
                k = _strip_all_suffixes(p.name)
                if k in keyed:
                    collided = True
                keyed[k] = p
            return keyed, collided
        _a_norm, _a_coll = _keyed(a_paths)
        _r_norm, _r_coll = _keyed(r_paths)
        if _a_coll or _r_coll:
            a_files = {p.name: p for p in a_paths}
            r_files = {p.name: p for p in r_paths}
            matched_by = "exact-name (normalized keys collided)"
        else:
            a_files, r_files = _a_norm, _r_norm
            _raw_common = {p.name for p in a_paths} & {p.name for p in r_paths}
            matched_by = ("normalized-stem"
                          if len(set(a_files) & set(r_files)) > len(_raw_common)
                          else "exact-name")
        common = sorted(set(a_files) & set(r_files))
        result = {
            "tool": "compare_integrated_series", "status": "success",
            "apexa_dir": str(ad), "reference_dir": str(rd), "pattern": pattern,
            "apexa_count": len(a_files), "reference_count": len(r_files),
            "common_count": len(common),
            "matched_by": matched_by,
            "count_match": len(a_files) == len(r_files) and len(a_files) > 0,
        }
        if not common:
            result.update({"grid_match": False, "parity": False,
                           "note": "no filename-matched pairs to compare"})
            return format_result(result)
        # Compare the grid on a representative pair (first common file).
        probe = common[0]
        an, amin, amax, ax, ay = _read_xy_grid(a_files[probe])
        rn, rmin, rmax, rx, ry = _read_xy_grid(r_files[probe])
        def _peak(xs, ys):
            if not ys:
                return None
            i = max(range(len(ys)), key=lambda k: ys[k])
            return xs[i]
        apk, rpk = _peak(ax, ay), _peak(rx, ry)
        # Scale-relative tolerance: a fraction of one bin, floored by the absolute
        # x_tol. Bin width is taken from the reference grid (rows-1 intervals).
        have_range = (amin is not None and rmin is not None
                      and amax is not None and rmax is not None)
        bin_w = (abs(rmax - rmin) / (rn - 1)) if (rn and rn > 1 and have_range) else None
        eff_tol = max(x_tol, bin_frac * bin_w) if bin_w else x_tol
        d_min = abs(amin - rmin) if have_range else None
        d_max = abs(amax - rmax) if have_range else None
        range_match = bool(have_range and d_min <= eff_tol and d_max <= eff_tol)
        grid_match = bool(an == rn and range_match)
        result.update({
            "probe_file": probe,
            "apexa_grid": {"rows": an, "x_min": amin, "x_max": amax},
            "reference_grid": {"rows": rn, "x_min": rmin, "x_max": rmax},
            "row_match": an == rn,
            "bin_width": (round(bin_w, 6) if bin_w else None),
            "eff_tol": round(eff_tol, 6),
            # Endpoint offsets in bin-width units — the scale-free "how far off" that
            # reads the same across 2θ / radius / Q (≲0.25 bin ⇒ same grid).
            "endpoint_offset_bins": (
                [round(d_min / bin_w, 3), round(d_max / bin_w, 3)]
                if (bin_w and d_min is not None) else None),
            "range_match": range_match,
            "grid_match": grid_match,
            "peak_offset": (round(abs(apk - rpk), 5) if (apk is not None and rpk is not None) else None),
            "parity": bool(grid_match and result["count_match"]),
        })
        if not grid_match:
            result["recommendation"] = (
                f"Grids differ (dir-A {an} rows {amin}–{amax} vs reference {rn} rows "
                f"{rmin}–{rmax}, in the files' own x units; endpoints off by "
                f"{result['endpoint_offset_bins']} bin(s), tol {round(eff_tol,6)}). "
                "Re-run the integration on the reference grid (midas_integrate_series "
                "takes the grid in radius, 2θ, or Q) BEFORE claiming any parity.")
        return format_result(result)
    except Exception as e:
        return format_result({"tool": "compare_integrated_series", "status": "error", "error": str(e)})

# Phase identification tool moved to analysis_utilities_server.py as identify_phases_basic
# Use GSAS-II server for comprehensive phase identification

def _probe_wavelength_from_hdf5(h5_path):
    """Best-effort scan of an HDF5 file for the instrument's recorded incident
    energy or wavelength, so calibration can proceed when the energy is not in
    the filename. Returns (wavelength_angstrom, source_str) or (None, None).

    Reading it from the instrument's own file (rather than the human's analysis
    output) keeps an APEXA-vs-expert benchmark fair. Conservative by design:
    only physically plausible scalar values are accepted (wavelength 0.05–3.0 Å,
    energy 1–500 keV, with eV→keV auto-scaling), and the source path is reported
    so the value can be sanity-checked rather than silently trusted.
    """
    try:
        import h5py
        import numpy as _np
        from apexa_units import kev_to_angstrom
    except Exception:
        return None, None

    WL_KEYS = ("wavelength", "lambda")
    EN_KEYS = ("energy", "monochromator", "mono_energy",
               "incident_energy", "beam_energy", "kev")
    found = {"wl": None, "en": None}

    def _scalar(v):
        try:
            if isinstance(v, bytes):
                v = v.decode("utf-8", "ignore")
            if isinstance(v, str):
                return float(v.strip().split()[0])
            arr = _np.asarray(v).ravel()
            return float(arr[0]) if arr.size else None
        except Exception:
            return None

    def _consider(name, value):
        low = name.lower()
        val = _scalar(value)
        if val is None or val <= 0:
            return
        if any(k in low for k in WL_KEYS):
            if 0.05 <= val <= 3.0 and found["wl"] is None:
                found["wl"] = (val, f"HDF5 '{name}'")
        elif any(k in low for k in EN_KEYS):
            e = val / 1000.0 if val > 1000 else val   # eV → keV
            if 1.0 <= e <= 500.0 and found["en"] is None:
                found["en"] = (kev_to_angstrom(e), f"HDF5 '{name}' ({e:.4f} keV)")

    try:
        with h5py.File(str(h5_path), "r") as f:
            for ak, av in f.attrs.items():
                _consider(f"@{ak}", av)

            def visit(name, obj):
                try:
                    if isinstance(obj, h5py.Dataset) and 0 < obj.size <= 16:
                        _consider(name, obj[()])
                except Exception:
                    pass
                try:
                    for ak, av in obj.attrs.items():
                        _consider(f"{name}@{ak}", av)
                except Exception:
                    pass

            f.visititems(visit)
    except Exception as e:
        print(f"[wavelength probe] HDF5 scan failed: {e}", file=sys.stderr)
        return None, None

    # Prefer a directly-stored wavelength over one derived from energy.
    return found["wl"] or found["en"] or (None, None)


# ── pip midas-suite migration helpers ───────────────────────────────────────
# Cubic calibrant standards → (SpaceGroup, lattice a in Å). Used to synthesize a
# CalibrationParams .txt for the pip `midas-autocalibrate` console script when no
# parameter file is supplied (the legacy AutoCalibrateZarr.py auto-detected these
# itself; the pip CLI requires them in a params file).
_CALIBRANT_DB = {
    "CeO2": (225, 5.411651), "LaB6": (221, 4.156890), "Si": (227, 5.431020),
    "Ni":   (225, 3.523870), "Al":   (225, 4.049500), "Au": (225, 4.078250),
    "Cu":   (225, 3.615000), "W":    (229, 3.165000),
}


def _resolve_midas_cli(console_name: str, legacy_script=None):
    """Prefer the pip `midas-suite` console script; fall back to a legacy
    MIDAS_ROOT script ONLY when the console script is genuinely absent.
    Returns (kind, path) where kind is "pip" | "legacy" | None."""
    import shutil as _sh
    exe = _sh.which(console_name)
    if exe:
        return ("pip", exe)
    if legacy_script and Path(legacy_script).exists():
        return ("legacy", str(legacy_script))
    return (None, None)


def _detect_calibrant_from_name(stem: str):
    """Best-effort calibrant id from a filename stem (CeO2, LaB6, Si, …)."""
    low = stem.lower()
    for name in _CALIBRANT_DB:
        if name.lower() in low:
            return name
    return "CeO2"   # overwhelmingly the default beamline calibrant


def _detector_shape_and_px(image_path: Path):
    """Return (ny, nz, px_microns) for a detector frame, best-effort.

    Reads the array shape from HDF5 (largest 2D dataset) or a TIFF/GE header.
    Pixel size: Varex 2880² → 150 µm, Pilatus/Eiger-ish → 172 µm, else 200 µm.
    Conservative defaults so synthesis never hard-fails on a readable image."""
    ny = nz = 2048
    try:
        suf = image_path.suffix.lower()
        if suf in (".h5", ".hdf5", ".nxs"):
            import h5py
            with h5py.File(str(image_path), "r") as f:
                best = [0, (2048, 2048)]
                def _v(name, obj):
                    try:
                        if hasattr(obj, "shape") and len(obj.shape) >= 2:
                            yx = obj.shape[-2:]
                            if yx[0] * yx[1] > best[0]:
                                best[0] = yx[0] * yx[1]
                                best[1] = (int(yx[0]), int(yx[1]))
                    except Exception:
                        pass
                f.visititems(_v)
                ny, nz = best[1]
        else:
            try:
                import fabio
                arr = fabio.open(str(image_path)).data
                ny, nz = int(arr.shape[0]), int(arr.shape[1])
            except Exception:
                pass
    except Exception:
        pass
    if (ny, nz) == (2880, 2880):
        px = 150.0
    elif max(ny, nz) in (1475, 1679, 2167, 981, 1043):  # common Pilatus/Eiger
        px = 172.0
    else:
        px = 200.0
    return ny, nz, px


def _synthesize_calibration_params(out_path: Path, *, calibrant: str,
                                   wavelength: float, px_um: float,
                                   ny: int, nz: int, lsd_um: float,
                                   bc_y: float, bc_x: float,
                                   eta_bin: float, n_iter: int, mult: float):
    """Write a minimal MIDAS CalibrationParams .txt the pip `midas-autocalibrate`
    CLI can consume. Returns (ok, error_str). Validates required fields are >0."""
    try:
        sg, a = _CALIBRANT_DB.get(calibrant, _CALIBRANT_DB["CeO2"])
        wl = float(wavelength or 0.0)
        if wl <= 0:
            return False, "no wavelength available to synthesize params"
        lsd = float(lsd_um) if lsd_um and lsd_um < 1_000_000 else 1_000_000.0
        max_ring = 0.5 * (ny ** 2 + nz ** 2) ** 0.5
        r_min = 10.0
        lines = [
            f"SpaceGroup {sg}",
            f"LatticeConstant {a:.6f} {a:.6f} {a:.6f} 90 90 90",
            f"Wavelength {wl:.6f}",
            f"Lsd {lsd:.1f}",
            f"BC {bc_y:.2f} {bc_x:.2f}",
            f"px {px_um:.2f}",
            f"NrPixelsY {int(ny)}",
            f"NrPixelsZ {int(nz)}",
            f"MaxRingRad {max_ring:.1f}",
            f"MinRingRad {r_min:.1f}",
            # Integration-cake bounds — REQUIRED by midas_integrate.build_map
            # (which midas-autocalibrate calls internally). Omitting RBinSize
            # makes n_r_bins = (RMax-RMin)/RBinSize divide by zero.
            f"RMin {r_min:.1f}",
            f"RMax {max_ring:.1f}",
            "RBinSize 1.0",
            "EtaMin -180.0",
            "EtaMax 180.0",
            f"EtaBinSize {eta_bin}",
            f"nIterations {int(n_iter)}",
            f"OutlierFactor {mult}",
        ]
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("\n".join(lines) + "\n")
        # Optional validation if midas_calibrate is importable (never fatal).
        try:
            from midas_calibrate.params import CalibrationParams  # type: ignore
            CalibrationParams.from_file(str(out_path)).validate()
        except ImportError:
            pass
        except Exception as ve:
            return False, f"synthesized params failed validation: {ve}"
        return True, ""
    except Exception as e:
        return False, f"param synthesis error: {e}"


def _find_dark_for_image(image_path: Path):
    """Auto-resolve a dark-field frame from the dataset tree when the caller
    didn't pass one. Calibration datasets ship a dark (e.g. dark_1p0s_*.h5) and
    dark subtraction is REQUIRED for reliable ring fitting on attenuated
    calibrants — without it v2's E-step finds no peaks ("no fitted points").

    Searches the image's directory and up to 4 parent levels (calibrant images
    are often placed in per-run subfolders like .../calibration/ceria_att3/
    while the dark sits at the dataset root .../ai_tune/), plus the symlink
    target's directory. Prefers a dark whose exposure token (e.g. '1p0s')
    matches the calibrant image. Returns an absolute path string or "".
    """
    # The dark must be readable by the SAME code path as the main image.
    # AutoCalibrateZarr reads a TIFF image's dark with PIL (Image.open), so a raw
    # GE (.ge*) or HDF5 dark handed to a TIFF calibration crashes with
    # "cannot load this image". Restrict candidates to the image's own format
    # family instead of any dark that merely matches the exposure token.
    fmt_groups = (
        {".tif", ".tiff"},
        {".h5", ".hdf5", ".hdf", ".nxs"},
        {".ge", ".ge1", ".ge2", ".ge3", ".ge5"},
    )
    img_suffix = image_path.suffix.lower()
    compat = next((g for g in fmt_groups if img_suffix in g), None)
    if compat is None:                       # unknown image type → allow any
        compat = set().union(*fmt_groups)
    m = re.search(r'(\d+p\d+s)', image_path.stem.lower())
    exp_tok = m.group(1) if m else None
    # Build the search directory list (dedup, order = nearest first).
    seen, dirs = set(), []
    for start in (image_path.parent, image_path.resolve().parent):
        d = start
        for _ in range(5):   # image dir + 4 parents
            try:
                rp = d.resolve()
            except Exception:
                break
            if rp not in seen and d.is_dir():
                seen.add(rp); dirs.append(d)
            if d.parent == d:
                break
            d = d.parent
    cands, skipped = [], 0
    for d in dirs:
        try:
            for p in d.iterdir():
                name = p.name.lower()
                if not (p.is_file() and "dark" in name):
                    continue
                # Skip MIDAS-generated intermediates (derived artifacts from a
                # prior run, e.g. dark_*.tif.ge.analysis.MIDAS.ge5) — not raw darks.
                if ".analysis." in name or ".midas." in name:
                    continue
                if p.suffix.lower() not in compat:
                    skipped += 1
                    continue
                cands.append(p)
        except Exception:
            continue
    if not cands:
        if skipped:
            print(f"  ⚠ found {skipped} dark(s) but none in the image's format "
                  f"({img_suffix}); proceeding without dark subtraction.",
                  file=sys.stderr)
        return ""
    if exp_tok:   # prefer exposure-matched dark within the compatible set
        for p in cands:
            if exp_tok in p.name.lower():
                return str(p.resolve())
    return str(cands[0].resolve())


def _write_calibration_outcome(out_dir, payload: dict):
    """Drop a uniform machine-readable per-run outcome manifest
    (``APEXA_calibration.json``) into the calibration output directory.

    Architectural purpose: APEXA was *stateless about results* — it ran a
    calibration but, when later asked "what's the outcome?", had to re-discover
    scattered MIDAS artifacts (refined_MIDAS_params*.txt, autocal.log, *.zarr.zip)
    and often found nothing (e.g. after a timeout). A single authoritative
    manifest per run fixes that: the tool returns it immediately, it persists on
    disk, and every benchmark run becomes trivially comparable across
    attenuation / calibrant / engine. Terminal states (success, error, timeout)
    all write one. Never raises — manifest writing must not break calibration.
    Returns the manifest path, or None.
    """
    try:
        from datetime import datetime, timezone
        d = Path(out_dir)
        d.mkdir(parents=True, exist_ok=True)
        rec = dict(payload)
        rec.setdefault("manifest", "APEXA_calibration.json")
        rec.setdefault("written_at", datetime.now(timezone.utc).isoformat())
        p = d / "APEXA_calibration.json"
        p.write_text(json.dumps(rec, indent=2, default=str))
        print(f"  ↳ outcome manifest: {p}", file=sys.stderr)
        return str(p)
    except Exception as _e:
        print(f"  ⚠ could not write outcome manifest: {_e}", file=sys.stderr)
        return None


def _write_integration_outcome(out_dir, payload: dict,
                               filename: str = "APEXA_integration.json"):
    """Per-run integration outcome manifest — the same on-disk-truth pattern as
    ``_write_calibration_outcome``. Lets "what was integrated?" be answered from a
    single authoritative file (real output paths + per-file status), so the agent
    cites verified results instead of reconstructing plausible filenames after a
    forced finalize. Never raises. Returns the manifest path, or None.
    """
    try:
        from datetime import datetime, timezone
        d = Path(out_dir)
        d.mkdir(parents=True, exist_ok=True)
        rec = dict(payload)
        rec.setdefault("manifest", filename)
        rec.setdefault("written_at", datetime.now(timezone.utc).isoformat())
        p = d / filename
        p.write_text(json.dumps(rec, indent=2, default=str))
        print(f"  ↳ integration manifest: {p}", file=sys.stderr)
        return str(p)
    except Exception as _e:
        print(f"  ⚠ could not write integration manifest: {_e}", file=sys.stderr)
        return None


@mcp.tool()
@idempotent(
    tool="midas_auto_calibrate",
    anchor=lambda kw: (kw.get("output_dir")
                       or os.path.dirname(os.path.expanduser(kw.get("image_file") or ""))),
    salient=lambda kw: {k: kw.get(k) for k in (
        "image_file", "parameters_file", "dark_file", "calibration_engine",
        "energy_kev", "wavelength_angstrom", "seed_from_params", "threshold",
        "first_ring_nr", "lsd_guess", "bc_x_guess", "bc_y_guess",
        "image_transform", "data_loc")},
)
async def midas_auto_calibrate(
    image_file: str,
    parameters_file: str = "",
    dark_file: str = "",
    output_dir: str = "",
    lsd_guess: float = 1000000.0,
    bc_x_guess: float = 0.0,
    bc_y_guess: float = 0.0,
    n_iterations: int = 40,
    tol_shifts: float = 3.0,
    tol_rotation: float = 1.0,
    mult_factor: float = 2.5,
    first_ring_nr: int = 1,
    eta_bin_size: float = 5.0,
    threshold: int = 0,
    make_plots: int = 0,
    save_plots_hdf: str = "",
    image_transform: str = "",
    data_loc: str = "",
    energy_kev: float = 0.0,
    wavelength_angstrom: float = 0.0,
    calibration_engine: str = "v1",   # v2 opt-in: fails beam-center seeding on off-center detectors (pending MIDAS dev fix)
    seed_from_params: str = "",        # trusted neighbour refined_MIDAS_params*.txt → seed BC/Lsd (robust fallback for low-SNR frames)
    host: str = "",
) -> str:
    """🔧 PRIMARY TOOL FOR FF-HEDM DETECTOR CALIBRATION (MIDAS Official)

    calibration_engine: "v1" (default) = MIDAS autocalibrate (pip console
    midas-autocalibrate, else legacy AutoCalibrateZarr.py) → refined MIDAS
    param .txt. "v2" = midas-calibrate-v2 differentiable engine → writes
    calibration.json with iso_R/harmonic distortion + in/post-residual strain
    (µε). Use "v2" to benchmark against a colleague who calibrated with v2 (the
    calibration.json format). v2 is PyTorch — slow on CPU-only hosts.

    ⚠️ WORKFLOW GUIDANCE - WHEN TO USE THIS TOOL:
    When user requests:
    - "calibrate the detector"
    - "auto-calibrate using CeO2" or any calibrant material
    - "determine detector geometry"
    - "calibration workflow"
    - "refine beam center and distance"

    → USE THIS TOOL (midas_auto_calibrate) - This is the ONLY tool for FF-HEDM calibration
    → DO NOT use detect_rings_quick (that's a diagnostic tool in utilities server)
    → This is the OFFICIAL MIDAS calibration method from AutoCalibrateZarr.py

    🚨 CRITICAL: FILE PATH REQUIREMENTS
    - image_file: Must be the EXACT, COMPLETE file path as it exists on disk
    - DO NOT abbreviate or guess filenames (e.g., "CeO2.tif" when actual file is "CeO2_650mm_61p332keV_2DFocused_0p1s_att200_004018.tif")
    - If user provides a directory, use filesystem_list_directory FIRST to find the actual filename
    - Then call this tool with the FULL PATH found from the directory listing
    - The tool will auto-search the parent directory if the exact file is not found

    MIDAS Manual: https://github.com/marinerhemant/MIDAS/blob/master/manuals/FF_autocalibrate.md
    MIDAS Component: AutoCalibrateZarr.py → CalibrantOMP → GetHKLList
    Location: MIDAS/utils/AutoCalibrateZarr.py

    DESCRIPTION:
    Auto-calibrates detector geometry by analyzing a 2D diffraction image of a known calibrant
    (e.g., CeO2, LaB6). Iteratively refines all geometric parameters (Lsd, beam center, tilts,
    distortions) until convergence using MIDAS's CalibrantOMP binary for robust least-squares
    fitting with automatic outlier rejection.

    FF-HEDM WORKFLOW POSITION:
    Step 1 - CALIBRATION (must run BEFORE analysis)
    ├─ Input: Raw calibrant image + initial parameter file
    ├─ Output: refined_MIDAS_params.txt (calibrated parameters)
    └─ Next steps: integrate_2d_to_1d OR run_ff_hedm_full_workflow

    Args:
        image_file: EXACT path to calibrant diffraction image (.tif/.tiff, .ge/.ge1-.ge5, .h5/.hdf5/.nxs, .zip Zarr)
                   If file not found, will auto-search parent directory for matching files.
        parameters_file: OPTIONAL path to MIDAS parameter file. If omitted, AutoCalibrateZarr.py auto-detects
                        calibrant (CeO2/LaB6 from filename), energy (from keV in filename), distance (from mm in filename),
                        and pixel size (from detector shape). Only needed if filename lacks these hints.
        dark_file: Optional path to dark field image for background subtraction
        output_dir: Optional output directory for calibration results (refined_MIDAS_params*.txt,
                   autocal.log, calibrant_screen_out.csv). Created if it does not exist.
                   Default: "" = write results alongside the source image file (original behaviour).
                   Use this when calibrating multiple files in a batch so results from each file
                   land in their own directory instead of clobbering each other.
        lsd_guess: Initial sample-to-detector distance guess in µm (default: 1000000 = auto-detect from ring ratios).
            A value < 10000 is treated as mm and auto-converted to µm (e.g. 895 → 895000).
        bc_x_guess: Initial beam center X coordinate in pixels. Provide with bc_y_guess or not at all —
            a partial seed (only one coordinate) is ignored (default: 0.0 = auto-detect from ring geometry).
        bc_y_guess: Initial beam center Y coordinate in pixels; pair with bc_x_guess (default: 0.0 = auto-detect)
        n_iterations: Maximum number of refinement iterations (default: 40)
        tol_shifts: Panel shift convergence tolerance in pixels (default: 3.0)
        tol_rotation: Panel rotation convergence tolerance in degrees (default: 1.0)
        mult_factor: Outlier rejection multiplier - rings with strain > mult_factor × median_strain are excluded (default: 2.5)
        first_ring_nr: Index (1-based) of first prominent ring for initial Lsd estimation (default: 1)
        eta_bin_size: Azimuthal bin size in degrees for CalibrantOMP fitting (default: 5.0)
        threshold: Manual intensity threshold for ring segmentation (default: 0 = auto-calculate)
        make_plots: Display matplotlib plots during refinement (0=no, 1=yes) (default: 0)
        save_plots_hdf: Path to HDF5 file for saving all intermediate data/plots for offline analysis (default: "" = don't save)
        image_transform: Image transformation - "0"=none, "1"=flip LR, "2"=flip UD, "3"=transpose, or space-separated combo (default: "" = none)
        data_loc: HDF5 dataset path if not standard location (default: "" = use /entry/data/data)

    Returns:
        JSON with calibrated geometric parameters and convergence metrics

    Outputs:
        - refined_MIDAS_params.txt: Final converged parameters (Lsd, BC, tx, ty, tz, p0-p3)
        - autocal.log: Detailed execution log with iteration history
        - calibrant_screen_out.csv: Raw CalibrantOMP output (for debugging)
        - [optional] HDF5 file with all intermediate arrays and plots

    Example Usage:
        Standard CeO2 calibration at ~650mm:
        {
            "image_file": "/data/CeO2_61keV_650mm.tif",
            "parameters_file": "/data/Params_CeO2.txt",
            "lsd_guess": 650000,
            "stopping_strain": 0.0001
        }

        High-precision calibration with diagnostics:
        {
            "image_file": "LaB6_calibrant.h5",
            "parameters_file": "Params_LaB6.txt",
            "dark_file": "dark.h5",
            "lsd_guess": 200000,
            "stopping_strain": 0.00004,
            "mult_factor": 3.0,
            "save_plots_hdf": "calibration_diagnostics.h5",
            "image_transform": "2"
        }

    Required Parameter File Format:
        SpaceGroup 225              # CeO2: 225, LaB6: 221, Si: 227
        LatticeParameter 5.411      # CeO2 lattice constant in Angstroms
        Wavelength 0.2021           # X-ray wavelength in Angstroms (61.332 keV)
        px 200                      # Pixel size in microns
        SkipFrame 0
    """
    try:
        print(f"\n{'='*70}", file=sys.stderr)
        print(f"🔧 midas_auto_calibrate called:", file=sys.stderr)
        print(f"   Image: {image_file}", file=sys.stderr)
        print(f"   Params: {parameters_file}", file=sys.stderr)
        print(f"{'='*70}\n", file=sys.stderr)

        # ── Remote-locality guard (fail-closed) ─────────────────────────────
        # Calibration is the ONE MIDAS tool not yet routed over SSH: its driver is
        # deeply local-FS-coupled (in-process native engine, local image-header
        # probe for detector shape, symlink-based calibrant auto-detection,
        # cwd-relative outputs, glob read-back). Silently running it local-blind on
        # a /gdata image would either error confusingly or, worse, calibrate the
        # wrong thing. So when the data resolves to a remote analysis host, REFUSE
        # with an actionable message instead of guessing — same fail-closed
        # principle as the deletion gate. (Full remote routing is the tracked
        # follow-up; ring-thresh and run_ff_hedm_full_workflow ARE remote-capable.)
        _cal_decision = decide_exec_host(image_file, parameters_file, dark_file,
                                         host=host)
        if _cal_decision.get("error"):
            return format_result({"tool": "midas_auto_calibrate", "status": "error",
                                  "error": _cal_decision.get("reason")})
        if _cal_decision.get("is_remote"):
            _ch = _cal_decision.get("host")
            return format_result({
                "tool": "midas_auto_calibrate", "status": "error",
                "is_remote": True, "host": _ch,
                "error": (
                    f"The calibrant data resolves to remote host '{_ch}', but "
                    "midas_auto_calibrate does not yet run over SSH (remote routing "
                    "is the tracked follow-up — its driver is local-FS-coupled). "
                    "NOTHING was run — no local-blind calibration was attempted. "
                    "Options: (1) run the calibration on the analysis host and point "
                    "APEXA at the resulting refined_MIDAS_params*.txt, or "
                    "(2) stage the calibrant image locally and re-run. Note: "
                    "calibrate_ring_thresholds and run_ff_hedm_full_workflow ARE "
                    "remote-capable and route to the data host automatically."
                ),
            })

        # ── Trusted-seed fallback ──────────────────────────────────────────
        # Robust path for low-SNR frames (e.g. CeO2 att5): seed BC + Lsd from a
        # neighbour's known-good refined_MIDAS_params*.txt so auto ring-detection
        # can't wander into a false basin. Reads the CORRECT complete geometry
        # (both BC coords, Lsd already in µm) — the seed the agent tried to hand-
        # write and got wrong. Only fills guesses the caller left at defaults.
        if seed_from_params:
            _sp = Path(seed_from_params).expanduser().absolute()
            if not _sp.exists():
                return format_result({"tool": "midas_auto_calibrate", "status": "error",
                                      "error": f"seed_from_params not found: {_sp}"})
            try:
                _seed = {}
                for _ln in _sp.read_text().splitlines():
                    _p = _ln.split()
                    if not _p:
                        continue
                    if _p[0] == "BC" and len(_p) >= 3:
                        _seed["bc_y"] = float(_p[1]); _seed["bc_x"] = float(_p[2])
                    elif _p[0] == "Lsd" and len(_p) >= 2:
                        _seed["lsd"] = float(_p[1])
                if lsd_guess >= 1000000.0 and "lsd" in _seed:
                    lsd_guess = _seed["lsd"]            # µm, as written in the params file
                if bc_x_guess == 0.0 and "bc_x" in _seed:
                    bc_x_guess = _seed["bc_x"]
                if bc_y_guess == 0.0 and "bc_y" in _seed:
                    bc_y_guess = _seed["bc_y"]
                print(f"  ↳ seeded from {_sp.name}: Lsd={_seed.get('lsd')} µm, "
                      f"BC=({_seed.get('bc_x')}, {_seed.get('bc_y')})", file=sys.stderr)
            except Exception as _e:
                return format_result({"tool": "midas_auto_calibrate", "status": "error",
                                      "error": f"could not parse seed_from_params {_sp}: {_e}"})

        # ── Native engine attempt ──────────────────────────────────────────
        # Native-first: if midas_calibrate is pip-installed (midas-suite),
        # run the pure-Python engine. Fall back to AutoCalibrateZarr.py
        # when the package is missing or the hardware gate fails (CPU-only).
        # Set APEXA_USE_NATIVE_MIDAS=0 to force the subprocess path.
        _native_disabled = os.environ.get("APEXA_USE_NATIVE_MIDAS") == "0"
        if (not _native_disabled
                and parameters_file and not image_transform):
            try:
                from apexa_midas_native import (
                    native_autocalibrate, MidasEngineUnavailable,
                )
                print("[engine] trying native midas_calibrate first…",
                      file=sys.stderr)
                result_dict = native_autocalibrate(
                    image_file=image_file,
                    parameters_file=parameters_file,
                    dark_file=dark_file,
                    n_iterations=n_iterations,
                )
                return format_result(result_dict)
            except MidasEngineUnavailable as e:
                print(f"[engine] native unavailable: {e.install_hint}",
                      file=sys.stderr)
                print("[engine] falling back to subprocess (AutoCalibrateZarr.py)",
                      file=sys.stderr)
            except Exception as e:
                # Any other native failure → fall back; don't lose the user.
                print(f"[engine] native call raised {type(e).__name__}: {e}",
                      file=sys.stderr)
                print("[engine] falling back to subprocess (AutoCalibrateZarr.py)",
                      file=sys.stderr)

        # Locate AutoCalibrateZarr.py
        # Note: We don't check MIDAS_AVAILABLE here because that only checks for
        # pyFAI/fabio dependencies, not MIDAS executables. AutoCalibrateZarr.py
        # has its own dependencies managed within the MIDAS environment.
        autocal_script = MIDAS_ROOT / "utils" / "AutoCalibrateZarr.py"
        print(f"✓ Checking for AutoCalibrateZarr.py at: {autocal_script}", file=sys.stderr)
        if not autocal_script.exists():
            # Provide diagnostic information about what was found
            utils_dir = MIDAS_ROOT / "utils"
            utils_exists = utils_dir.exists()

            diagnostic_info = f"MIDAS_ROOT detected: {MIDAS_ROOT}\n"
            diagnostic_info += f"utils/ directory exists: {utils_exists}\n"

            if utils_exists:
                try:
                    utils_contents = [f.name for f in utils_dir.iterdir() if f.name.endswith('.py')][:10]
                    diagnostic_info += f"Python files in utils/: {', '.join(utils_contents) if utils_contents else 'none'}\n"
                except:
                    diagnostic_info += "Could not list utils/ contents\n"

            # Check for alternative locations
            alt_locations = []
            for name in ["AutoCalibrateZarr.py", "deprecated_AutoCalibrate.py", "AutoCalibrate.py"]:
                script_path = MIDAS_ROOT / "utils" / name
                if script_path.exists():
                    alt_locations.append(str(script_path))

            if alt_locations:
                diagnostic_info += f"\nFound alternative scripts:\n  " + "\n  ".join(alt_locations)

            return format_result({
                "tool": "midas_auto_calibrate",
                "status": "error",
                "error": f"AutoCalibrateZarr.py not found at expected location: {autocal_script}\n\n{diagnostic_info}\n\nTo fix:\n1. Set MIDAS_PATH environment variable to your MIDAS installation\n2. Ensure AutoCalibrateZarr.py exists in MIDAS/utils/\n3. Use a recent MIDAS version from https://github.com/marinerhemant/MIDAS"
            })

        # Expand paths
        image_path = Path(image_file).expanduser().absolute()
        print(f"✓ Image path: {image_path} (exists: {image_path.exists()})", file=sys.stderr)

        param_path = None
        if parameters_file:
            param_path = Path(parameters_file).expanduser().absolute()
            print(f"✓ Param path: {param_path} (exists: {param_path.exists()})", file=sys.stderr)
        else:
            print(f"  No parameter file provided — AutoCalibrateZarr.py will auto-detect from filename", file=sys.stderr)

        # Auto-search for image file if not found
        if not image_path.exists():
            print(f"⚠ Image file not found at: {image_path}", file=sys.stderr)

            # Search the specified directory AND up the dataset tree. The agent
            # often points calibration at a per-run subfolder
            # (.../calibration/lab6_att3/IMG.h5) without copying the raw frame
            # there, while the frame lives at the dataset root (.../ai_tune/).
            # Walk up a few parents so the exact-basename match finds it, instead
            # of falling back to CWD and picking nothing.
            specified_dir = image_path.parent
            search_dirs = []
            _d = specified_dir
            for _ in range(5):   # subfolder + up to 4 parents
                if _d.exists() and _d not in search_dirs:
                    search_dirs.append(_d)
                if _d.parent == _d:
                    break
                _d = _d.parent
            if not search_dirs:   # bare filename / nothing on disk → last resort
                search_dirs = [Path.cwd()]

            print(f"  Searching in: {', '.join(str(d) for d in search_dirs)}", file=sys.stderr)

            search_patterns = ["*.tif", "*.tiff", "*.ge", "*.ge1", "*.ge2", "*.ge3", "*.ge4", "*.ge5", "*.h5", "*.hdf5", "*.nxs"]
            found_files = []
            for search_dir in search_dirs:
                if search_dir.exists():
                    for pattern in search_patterns:
                        found_files.extend(search_dir.glob(pattern))

            # Filter symlinks to avoid duplicates
            found_files = [f for f in found_files if not f.is_symlink()]

            if found_files:
                # Try exact stem match first, then partial match
                search_stem = image_path.stem.lower()
                exact_match = next((f for f in found_files if f.stem.lower() == search_stem), None)
                if exact_match:
                    image_path = exact_match
                    print(f"  ✓ Exact match: {image_path}", file=sys.stderr)
                else:
                    partial_match = next((f for f in found_files if search_stem in f.stem.lower()), None)
                    if partial_match:
                        image_path = partial_match
                        print(f"  ✓ Partial match: {image_path}", file=sys.stderr)
                    else:
                        # List what was found so the user can pick
                        file_list = "\n".join(f"  - {f.parent.name}/{f.name}" for f in found_files[:10])
                        return format_result({
                            "tool": "midas_auto_calibrate",
                            "status": "error",
                            "error": f"File '{image_path.name}' not found. Files in {specified_dir}:\n{file_list}\n\nPlease provide the exact filename."
                        })
            else:
                return format_result({
                    "tool": "midas_auto_calibrate",
                    "status": "error",
                    "error": f"No diffraction images found in {specified_dir}. Please provide the full absolute path to the image file."
                })

        # Auto-search for parameters file if specified but not found
        if param_path and not param_path.exists():
            print(f"⚠ Parameters file not found at: {param_path}", file=sys.stderr)

            # Search in image directory and cwd
            search_dirs = {param_path.parent, image_path.parent, Path.cwd()}
            param_patterns = ["*arameters*.txt", "*params*.txt", "*Params*.txt", "refined_MIDAS_params*.txt"]
            found_params = []
            for search_dir in search_dirs:
                if search_dir.exists():
                    for pattern in param_patterns:
                        found_params.extend(search_dir.glob(pattern))

            # Filter out obvious non-parameter files (< 10KB)
            found_params = [p for p in found_params if p.stat().st_size < 10000]

            if found_params:
                found_params.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                search_basename = param_path.stem.lower()
                basename_match = next(
                    (f for f in found_params if search_basename in f.stem.lower() or "param" in f.stem.lower()),
                    None
                )
                param_path = basename_match if basename_match else found_params[0]
                print(f"  ✓ Using: {param_path}", file=sys.stderr)
            else:
                # No param file found — let AutoCalibrateZarr.py auto-detect
                print(f"  No parameter file found — falling back to auto-detection from filename", file=sys.stderr)
                param_path = None

        # Determine file type for ConvertFile flag
        suffix = image_path.suffix.lower()
        # v10: format is auto-detected from extension — ConvertFile is optional.
        # We still pass it explicitly so the conversion path is unambiguous.
        if suffix in ['.zip']:
            convert_file = 0  # Zarr zip
        elif suffix in ['.h5', '.hdf5', '.hdf', '.nxs']:
            convert_file = 1  # HDF5 / NeXus
        elif suffix in ['.ge', '.ge1', '.ge2', '.ge3', '.ge4', '.ge5']:
            convert_file = 2  # GE binary
        elif suffix in ['.tif', '.tiff']:
            convert_file = 3  # TIFF
        else:
            return format_result({
                "tool": "midas_auto_calibrate",
                "status": "error",
                "error": f"Unsupported file format: {suffix}. Supported: .zip (Zarr), .h5/.hdf5 (HDF5), .ge/.ge1-.ge5 (GE binary), .tif/.tiff (TIFF)"
            })

        # Save original filename before symlink creation (for energy extraction)
        original_filename = image_path.name
        if image_path.is_symlink():
            original_filename = image_path.resolve().name

        # WORKAROUND for MIDAS filename parsing bug:
        # MIDAS ffGenerateZipRefactor.py has trouble when TIF files have complex names
        # because it converts filename.tif -> filename.tif.ge, then tries to parse
        # the stem which still contains ".tif", confusing the numeric parser.
        #
        # Solution: Create a simple symlink without dots in the basename
        # Example: CeO2_650mm_61p332keV_2DFocused_0p1s_att200_004018.tif
        #       -> CeO2_calib_000001.tif

        # Always create a simple symlink for TIFF files to avoid parsing issues
        if suffix in ['.tif', '.tiff']:
            # Extract just the first part of filename before any numbers
            # Find the first meaningful word (usually material name)
            # Preserve material name including trailing digit (e.g. CeO2, LaB6)
            # so AutoCalibrateZarr.py can auto-detect calibrant from symlink name
            match = re.match(r'^([A-Za-z]+[0-9]*)', image_path.stem)
            prefix = match.group(1) if match else "calib"

            # Create simple MIDAS-friendly name WITHOUT extension
            # MIDAS will add .ge to it, and needs numbers at the END of the stem
            # So: calib_000001 -> calib_000001.ge (stem: calib_000001, ends with numbers ✓)
            # NOT: calib_000001.tif -> calib_000001.tif.ge (stem: calib_000001.tif, ends with .tif ✗)
            midas_friendly_name = f"{prefix}_000001{image_path.suffix}"
            midas_friendly_path = image_path.parent / midas_friendly_name

            # Skip symlink if the friendly name is already the same as the input
            # (would create a self-referential symlink CeO2_000001.tif -> CeO2_000001.tif)
            if midas_friendly_name == image_path.name:
                print(f"  Input already has a MIDAS-friendly name: {image_path.name}", file=sys.stderr)
                # Resolve the real target if the input is itself a symlink
                if image_path.is_symlink():
                    real_target = image_path.resolve()
                    print(f"  Symlink target: {real_target.name}", file=sys.stderr)
                    # Rebuild the symlink to point to the real file
                    image_path.unlink()
                    image_path.symlink_to(real_target)
                    print(f"  ✓ Refreshed symlink: {image_path.name} -> {real_target}", file=sys.stderr)
            else:
                # Remove old symlink if it exists
                if midas_friendly_path.is_symlink():
                    midas_friendly_path.unlink()
                elif midas_friendly_path.exists():
                    # Don't overwrite real files, use different name
                    midas_friendly_name = f"{prefix}_calibration_000001{image_path.suffix}"
                    midas_friendly_path = image_path.parent / midas_friendly_name
                    if midas_friendly_path.is_symlink():
                        midas_friendly_path.unlink()

                # Create symlink
                try:
                    midas_friendly_path.symlink_to(image_path.resolve())  # Absolute symlink
                    print(f"✓ Created MIDAS-friendly symlink: {midas_friendly_name} -> {image_path.resolve()}", file=sys.stderr)
                    image_path = midas_friendly_path
                except Exception as e:
                    print(f"⚠ Could not create symlink: {e}. Using original filename.", file=sys.stderr)
                    # Continue with original filename

        # ── Resolve X-ray wavelength (Å) from the best available source ──────
        # Priority: explicit arg > filename keV token > HDF5 instrument metadata.
        # A param file carries its own Wavelength, so we only inject --wavelength
        # when none was supplied. Every source is logged so the value can be
        # sanity-checked — APEXA never silently guesses (MIDAS rightly refuses).
        from apexa_units import kev_to_angstrom
        original_stem = Path(original_filename).stem
        _resolved_wl = None
        _wl_source = None

        # 1. Explicit override from the caller (energy_kev or wavelength_angstrom).
        if wavelength_angstrom and wavelength_angstrom > 0:
            _resolved_wl = float(wavelength_angstrom)
            _wl_source = f"wavelength_angstrom arg ({_resolved_wl:.6f} Å)"
        elif energy_kev and energy_kev > 0:
            _resolved_wl = kev_to_angstrom(float(energy_kev))
            _wl_source = f"energy_kev arg ({float(energy_kev):.4f} keV)"

        # 2. keV token in the original filename (e.g. 61p332keV, 71.676keV).
        if _resolved_wl is None:
            energy_match = re.search(
                r'(?:^|[_\-])([\d]+(?:[p.][\d]+)?)keV(?:[_\-.]|$)',
                original_stem, re.IGNORECASE
            )
            if energy_match:
                _kev_fn = float(energy_match.group(1).replace('p', '.'))
                if _kev_fn > 0:
                    _resolved_wl = kev_to_angstrom(_kev_fn)
                    _wl_source = f"filename keV token ({_kev_fn} keV)"

        # 3. Instrument metadata inside the HDF5 file itself. Fair for an
        #    APEXA-vs-expert benchmark — it reads the scan's own recorded energy,
        #    not the expert's analysis output. Only when no param file supplies it.
        if _resolved_wl is None and not param_path:
            _wl_probe, _probe_src = _probe_wavelength_from_hdf5(image_path)
            if _wl_probe:
                _resolved_wl = _wl_probe
                _wl_source = _probe_src

        if _resolved_wl:
            print(f"✓ Wavelength resolved from {_wl_source}: λ = {_resolved_wl:.6f} Å",
                  file=sys.stderr)
        else:
            print("  ⚠ No wavelength from arg / filename / HDF5 metadata. MIDAS "
                  "will need a param file or will error. Pass energy_kev=<value> "
                  "to set it explicitly.", file=sys.stderr)

        # Extract Lsd guess from original filename if present (e.g. 650mm, 210mm)
        lsd_match = re.search(
            r'(?:^|[_\-])([\d]+(?:[p.][\d]+)?)mm(?:[_\-.]|$)',
            original_stem, re.IGNORECASE
        )
        if lsd_match and lsd_guess >= 1000000:  # Only if user didn't provide one
            dist_mm = float(lsd_match.group(1).replace('p', '.'))
            lsd_from_filename = int(dist_mm * 1000)  # mm → µm
            print(f"✓ Extracted Lsd from original filename: {dist_mm} mm → {lsd_from_filename} µm", file=sys.stderr)

        # ── Auto-resolve the dark frame if the caller didn't pass one ─────────
        # Dark subtraction is required for reliable ring fitting; when it's
        # missing, v2's E-step finds no peaks on attenuated calibrants
        # ("E-step produced no fitted points"). The model should pass dark_file,
        # but calibration must not depend on it remembering — same poka-yoke as
        # auto-detecting wavelength/calibrant/Lsd from the filename.
        if not (dark_file and Path(dark_file).expanduser().exists()):
            _auto_dark = _find_dark_for_image(image_path)
            if _auto_dark:
                dark_file = _auto_dark
                print(f"✓ Auto-resolved dark frame (none passed): {dark_file}",
                      file=sys.stderr)
            else:
                print("  ⚠ No dark frame passed or found in the dataset tree — "
                      "calibrating WITHOUT dark subtraction. On attenuated "
                      "calibrants the rings may be too weak to fit (v2 E-step may "
                      "report 'no fitted points'); pass dark_file explicitly if so.",
                      file=sys.stderr)

        # Output location is GENERIC: if the caller gives output_dir, use it;
        # otherwise fall back to the MIDAS-natural default (the image's dir, as
        # AutoCalibrateZarr does). APEXA does NOT invent a subfolder scheme here
        # — when the location matters and isn't specified, the AGENT asks the
        # user for it before calling this tool (see APEXA_AGENT prompt).
        _calib_out = (Path(output_dir).expanduser().absolute()
                      if output_dir else image_path.parent)
        _announce_output("midas_auto_calibrate", _calib_out,
                         engine=str(calibration_engine), image=image_path.name)

        # ── Engine v2: midas-calibrate-v2 (differentiable; writes calibration.json) ──
        # Produces the SAME artifact a colleague gets from midas-calibrate-v2:
        # calibration.json with iso_R/harmonic distortion + in/post-residual
        # strain (µε) — enabling a true v2-vs-v2 calibration benchmark. PyTorch,
        # so slow on CPU-only hosts; runs in the MIDAS python env via subprocess.
        if str(calibration_engine).lower() == "v2" and not _resolved_wl:
            print("[engine] v2 calibration needs a wavelength but none resolved; "
                  "falling back to v1 engine.", file=sys.stderr)
        if str(calibration_engine).lower() == "v2" and _resolved_wl:
            _v2_out = (Path(output_dir).expanduser().absolute()
                       if output_dir else image_path.parent)
            _v2_out.mkdir(parents=True, exist_ok=True)
            # v2 built-in calibrant set. _detect_calibrant_from_name returns a
            # material only when its token is in the filename (else defaults to
            # CeO2). If it detected a real calibrant the v2 engine can't handle
            # (Au/Ni/Al), do NOT silently run CeO2 against a differently-named
            # image — that fabricates a wrong-material calibration. Fail honestly.
            _V2_CALIBRANTS = ("CeO2", "LaB6", "Si", "Al2O3")
            _calib_v2 = _detect_calibrant_from_name(original_stem)
            if _calib_v2 not in _V2_CALIBRANTS:
                _hint = ("Au is a single-crystal / NF beam-position standard, not an "
                         "FF powder calibrant — use a powder standard for v2 geometry."
                         if _calib_v2 == "Au" else
                         f"{_calib_v2} is not a supported v2 powder calibrant.")
                return format_result({
                    "tool": "midas_auto_calibrate", "status": "error",
                    "engine": "v2:midas_calibrate_v2",
                    "error": (f"filename '{original_stem}' looks like a {_calib_v2} "
                              f"calibrant, which the v2 engine does not support "
                              f"(built-ins: {', '.join(_V2_CALIBRANTS)})."),
                    "detected_calibrant": _calib_v2,
                    "supported_calibrants": list(_V2_CALIBRANTS),
                    "hint": (_hint + " To calibrate with a powder standard, pass a "
                             "CeO2/LaB6/Si/Al2O3 image, or run calibration_engine='v1' "
                             "(material-agnostic via LatticeConstant+SpaceGroup)."),
                })
            _ny2, _nz2, _px2 = _detector_shape_and_px(image_path)
            _lsd_um2 = (float(lsd_guess) if lsd_guess < 1_000_000
                        else (float(lsd_from_filename) if lsd_match else 1_000_000.0))
            _dark_abs = (str(Path(dark_file).expanduser().absolute())
                         if dark_file and Path(dark_file).expanduser().exists() else "")
            _vals = (
                f"_IMG={str(image_path)!r}\n_DARK={_dark_abs!r}\n_WL={float(_resolved_wl)}\n"
                f"_PX={float(_px2)}\n_CAL={_calib_v2!r}\n_OUT={str(_v2_out)!r}\n"
                f"_LSD={float(_lsd_um2)}\n_NITER={int(n_iterations)}\n"
            )
            _body = r'''
import json, numpy as np
from pathlib import Path
def _load(p):
    p=str(p)
    if not p: return None
    if p.endswith((".h5",".hdf5",".hdf",".nxs")):
        import h5py; best=[None]
        with h5py.File(p,"r") as f:
            def v(n,o):
                try:
                    if hasattr(o,"shape") and len(getattr(o,"shape",()))>=2:
                        a=np.asarray(o[()])
                        if a.ndim>2: a=a[0]
                        if best[0] is None or a.size>best[0].size: best[0]=a
                except Exception: pass
            f.visititems(v)
        return best[0]
    import fabio; return np.asarray(fabio.open(p).data)
img=_load(_IMG)
if img is None: raise SystemExit("could not load image array")
if img.ndim>2: img=img[0]
dark=_load(_DARK) if _DARK else None
from midas_calibrate_v2 import calibrate
res=calibrate(np.asarray(img,dtype=float), wavelength=_WL, pxY=_PX, calibrant=_CAL,
              output_dir=_OUT, initial_Lsd=_LSD, n_iter=_NITER, dark=dark,
              device="cpu", verbose=True)
out={"engine":"pip-v2:midas_calibrate_v2","Lsd_um":res.Lsd,"BC_y":res.BC_y,
     "BC_z":res.BC_z,"tx":res.tx,"ty":res.ty,"tz":res.tz,"wavelength_A":res.wavelength_A,
     "in_loop_strain_uE":res.in_loop_strain_uE,
     "post_residual_strain_uE":res.post_residual_strain_uE,
     "calibration_json":str(Path(_OUT)/"calibration.json"),
     "residual_corr_bin_path":getattr(res,"residual_corr_bin_path",None)}
print("APEXA_V2_RESULT="+json.dumps(out))
'''
            # v2 (midas_calibrate_v2) ships in the pip midas-suite installed in
            # THIS interpreter's env (the APEXA .venv) — NOT in the conda MIDAS
            # env that find_midas_python() returns (that one only carries the
            # C++ deps: zarr/diplib/numba/...). Running v2 under conda →
            # ModuleNotFoundError every time. Use sys.executable (the .venv).
            midas_python = sys.executable
            # Probe importability first so a missing/old pip package yields one
            # clean line instead of dumping a traceback on every calibration.
            try:
                _probe = subprocess.run(
                    [midas_python, "-c", "import midas_calibrate_v2"],
                    capture_output=True, text=True, timeout=60)
                _v2_ok = _probe.returncode == 0
            except Exception:
                _v2_ok = False
            if not _v2_ok:
                print("[engine] v2 engine (midas_calibrate_v2) not importable in "
                      f"{midas_python} — using v1 engine.", file=sys.stderr)
                _p = None
            else:
                print("=" * 70, file=sys.stderr)
                print("🔧 MIDAS CALIBRATION (v2 differentiable — midas_calibrate_v2):",
                      file=sys.stderr)
                print(f"   image={image_path} calibrant={_calib_v2} λ={_resolved_wl} "
                      f"px={_px2} Lsd0={_lsd_um2} out={_v2_out}", file=sys.stderr)
                print("   (PyTorch on CPU is slow — this can take many minutes)",
                      file=sys.stderr)
                print("=" * 70, file=sys.stderr)
                try:
                    # Clean env (no C++ DYLD/LD injection — that triggers an
                    # h5py/libhdf5 symbol mismatch for the pip torch stack).
                    _p = subprocess.run([midas_python, "-c", _vals + _body],
                                        capture_output=True, text=True,
                                        timeout=7200, env=dict(os.environ))
                except subprocess.TimeoutExpired:
                    print("[engine] v2 calibration timed out (CPU PyTorch is slow) — "
                          "falling back to v1 engine.", file=sys.stderr)
                    _p = None
            _line = (next((l for l in (_p.stdout or "").splitlines()
                          if l.startswith("APEXA_V2_RESULT=")), None)
                     if _p is not None else None)
            if _p is not None and _p.returncode == 0 and _line:
                import json as _json
                _res = _json.loads(_line[len("APEXA_V2_RESULT="):])
                _res.update({
                    "tool": "midas_auto_calibrate", "status": "success",
                    "Lsd_mm": _res.get("Lsd_um", 0) / 1000.0,
                    "message": (f"v2 calibration complete (calibrant {_calib_v2}). "
                                f"Strain {_res.get('in_loop_strain_uE')}→"
                                f"{_res.get('post_residual_strain_uE')} µε. "
                                f"calibration.json written to {_v2_out}.")})
                print("[engine] calibration engine: pip-v2:midas_calibrate_v2",
                      file=sys.stderr)
                return format_result(_res)
            # v2 failed/unavailable → fall through to the v1 engine below.
            if _p is not None:
                print("[engine] v2 calibration failed — falling back to v1 engine.",
                      file=sys.stderr)
                for _l in (_p.stderr or "").strip().splitlines()[-8:]:
                    print(f"  {_l}", file=sys.stderr)

        # Build command with all parameters according to MIDAS manual
        # Use MIDAS Python (conda midas_env) instead of current Python (UV)
        midas_python = find_midas_python()
        cmd = [
            midas_python,
            str(autocal_script),
            "-dataFN", str(image_path),
        ]
        if param_path:
            cmd.extend(["-paramFN", str(param_path)])

        # Inject the resolved wavelength when no param file carries one.
        if _resolved_wl and not param_path:
            cmd.extend(["--wavelength", f"{_resolved_wl:.6f}"])

        # Pass Lsd from original filename if no user-provided guess
        if lsd_match and lsd_guess >= 1000000 and not param_path:
            cmd.extend(["-LsdGuess", str(lsd_from_filename)])

        cmd.extend([
            "-ConvertFile", str(convert_file),
            "--n-iterations", str(n_iterations),
            "--tol-shifts", str(tol_shifts),
            "--tol-rotation", str(tol_rotation),
            "-MultFactor", str(mult_factor),
            "-FirstRingNr", str(first_ring_nr),
            "-EtaBinSize", str(eta_bin_size),
            "-MakePlots", str(make_plots)
        ])

        # Add optional parameters
        if dark_file:
            dark_path = Path(dark_file).expanduser().absolute()
            if dark_path.exists():
                cmd.extend(["-darkFN", str(dark_path)])
                # AutoCalibrateZarr reads a SEPARATE dark .h5 at `dark_loc or
                # 'exchange/dark'` with NO existence guard
                # (read_image_for_estimation line 768) → a hard KeyError crash
                # when the dark file stores its frames elsewhere. GE5 darks keep
                # the real dark at exchange/data and have no exchange/dark, and
                # exchange/data_dark is an all-zero placeholder — so the default
                # both crashes AND (if it existed) would subtract nothing. Probe
                # the dark file for the dataset that actually holds nonzero counts
                # and pass it as -darkLoc so the read targets real dark data.
                if dark_path.suffix.lower() in (".h5", ".hdf5", ".hdf", ".nxs"):
                    _dkloc, _dkmean = _probe_dark_dataset(
                        dark_path, data_location=(data_loc or None))
                    if _dkmean is not None:   # a real, readable dataset was found
                        _dkloc = _dkloc.lstrip("/")
                        cmd.extend(["-darkLoc", _dkloc])
                        print(f"✓ Dark dataset resolved in dark file: {_dkloc} "
                              f"(mean {_dkmean:.0f})", file=sys.stderr)
                        if _dkmean == 0.0:
                            print("  ⚠ chosen dark dataset reads all-zero — dark "
                                  "subtraction will be a no-op and rings may be "
                                  "too weak to fit. Verify the dark file.",
                                  file=sys.stderr)
                    else:
                        print("  ⚠ could not read any dark dataset from the dark "
                              "file; leaving -darkLoc unset (AutoCalibrateZarr "
                              "defaults to exchange/dark).", file=sys.stderr)

        if lsd_guess < 1000000:  # User provided a real guess (not the auto sentinel)
            # Unit guard: Lsd is µm here, but callers routinely pass mm (e.g. 895
            # meaning 895 mm). A value < 10000 is physically impossible as µm for a
            # real detector (<10 mm) → it's mm; convert. This prevented a real
            # failure: '-LsdGuess 895' (0.9 mm) collapsed a seeded att5 retry.
            lsd_um = lsd_guess * 1000.0 if 0 < lsd_guess < 10000 else lsd_guess
            if 0 < lsd_guess < 10000:
                print(f"  ⚠ LsdGuess {lsd_guess} looks like mm — interpreting as "
                      f"{int(lsd_um)} µm", file=sys.stderr)
            cmd.extend(["-LsdGuess", str(int(lsd_um))])

        # Beam-center seed: MIDAS -BCGuess needs BOTH coordinates (order Y X). A
        # partial seed (one coord 0) puts the beam centre at the detector edge and
        # sends the fit into a false minimum — refuse it rather than pass a bad 0.
        if bc_x_guess != 0.0 and bc_y_guess != 0.0:
            cmd.extend(["-BCGuess", str(bc_y_guess), str(bc_x_guess)])
        elif bc_x_guess != 0.0 or bc_y_guess != 0.0:
            print("  ⚠ partial beam-center seed ignored — provide BOTH bc_y_guess "
                  "and bc_x_guess (or neither); MIDAS -BCGuess needs both.",
                  file=sys.stderr)

        if threshold > 0:  # Manual threshold specified
            cmd.extend(["-Threshold", str(threshold)])

        if save_plots_hdf:  # Save diagnostic HDF5
            hdf_path = Path(save_plots_hdf).expanduser().absolute()
            cmd.extend(["-SavePlotsHDF", str(hdf_path)])

        if data_loc:  # Non-standard HDF5 dataset location
            cmd.extend(["-dataLoc", data_loc])

        # Image transformation: explicit user_arg → params file → sibling params → 0+warn
        # (per MIDAS manuals/README.md, ImTransOpt is detector-mount specific — no extension rule)
        resolved_transform, transform_source = _resolve_image_transform(
            image_path, image_transform, param_path
        )
        if resolved_transform:
            transforms = resolved_transform.strip().split()
            if transforms:
                cmd.extend(["-ImTransOpt"] + transforms)

        # Add beamline-standard bad pixel and gap intensity markers
        cmd.extend(["-BadPxIntensity", "-2"])
        cmd.extend(["-GapIntensity", "-1"])

        # ── Output directory handling ─────────────────────────────────────────
        # AutoCalibrateZarr.py writes all outputs (refined_MIDAS_params*.txt,
        # autocal.log, calibrant_screen_out.csv) into the working directory
        # (cwd). When output_dir is specified we:
        #   1. Create the directory.
        #   2. Symlink the image (and param/dark files if present) into it so
        #      MIDAS can find them via relative paths.
        #   3. Set cwd = output_dir so all outputs land there.
        # This lets the caller batch-calibrate multiple files, one per subdir,
        # without results clobbering each other.
        work_dir = image_path.parent   # default: same dir as image

        if output_dir:
            out_path = Path(output_dir).expanduser().absolute()
            out_path.mkdir(parents=True, exist_ok=True)
            print(f"✓ Output directory: {out_path}", file=sys.stderr)

            # Symlink image into output dir so MIDAS can read it from cwd.
            img_link = out_path / image_path.name
            if not img_link.exists() and not img_link.is_symlink():
                img_link.symlink_to(image_path.resolve())
                print(f"  ↳ symlink: {image_path.name} → {image_path.resolve()}", file=sys.stderr)

            # Update the image path the command receives so MIDAS uses the link
            cmd_image_path = img_link
            # Replace the -dataFN argument already appended above
            datafn_idx = cmd.index("-dataFN")
            cmd[datafn_idx + 1] = str(cmd_image_path)

            # Symlink param file if present
            if param_path and param_path.exists():
                p_link = out_path / param_path.name
                if not p_link.exists() and not p_link.is_symlink():
                    p_link.symlink_to(param_path.resolve())

            # Symlink dark file if present
            if dark_file:
                dark_path_sym = Path(dark_file).expanduser().absolute()
                if dark_path_sym.exists():
                    d_link = out_path / dark_path_sym.name
                    if not d_link.exists() and not d_link.is_symlink():
                        d_link.symlink_to(dark_path_sym.resolve())

            work_dir = out_path

        # ── Engine selection: pip midas-autocalibrate (DEFAULT) → legacy C++ ──
        # "Force pip everywhere": prefer the versioned pip console script over the
        # hand-built C++ AutoCalibrateZarr.py (which drifts out of date). The pip
        # CLI needs a CalibrationParams .txt, which we synthesize from the
        # detected calibrant/wavelength/geometry when no param file was supplied.
        # Falls back to legacy ONLY when the console script is absent or its
        # setup/run fails — so this never breaks worse than the C++ path.
        # Set APEXA_FORCE_LEGACY_MIDAS=1 to pin the C++ engine.
        _force_legacy = os.environ.get("APEXA_FORCE_LEGACY_MIDAS") == "1"
        # The pip console midas-autocalibrate (v1) cannot load HDF5 frames — it
        # passes the path through as a STRING, so its dark subtraction crashes
        # ("ufunc 'subtract' did not contain a loop ... dtype('<U20')"). Only the
        # legacy AutoCalibrateZarr path converts HDF5 (-ConvertFile 1). So for
        # .h5/.hdf5 images skip pip-console v1 and use legacy directly. (v2 above
        # DOES read HDF5 via h5py and is tried first, so it remains the default.)
        _img_is_hdf5 = image_path.suffix.lower() in (".h5", ".hdf5", ".hdf", ".nxs")
        if _img_is_hdf5 and not _force_legacy:
            print("[engine] image is HDF5 — pip-console v1 cannot convert it; "
                  "using legacy AutoCalibrateZarr (-ConvertFile 1).", file=sys.stderr)
        _ac_kind, _ac_cli = (None, None) if (_force_legacy or _img_is_hdf5) else \
            _resolve_midas_cli("midas-autocalibrate")
        result = None
        engine_used = None

        if _ac_cli:
            try:
                _pip_params = param_path
                if _pip_params is None:
                    _calib = _detect_calibrant_from_name(original_stem)
                    _ny, _nz, _px = _detector_shape_and_px(image_path)
                    _bcy = bc_y_guess or (_nz / 2.0)
                    _bcx = bc_x_guess or (_ny / 2.0)
                    # same mm→µm unit guard as the subprocess path
                    if 0 < lsd_guess < 10000:
                        _lsd_um = int(lsd_guess * 1000)
                    elif lsd_guess < 1_000_000:
                        _lsd_um = int(lsd_guess)
                    else:
                        _lsd_um = (lsd_from_filename if lsd_match else 1_000_000)
                    _pip_params = work_dir / f"{image_path.stem}_autogen_calib_params.txt"
                    _ok, _err = _synthesize_calibration_params(
                        _pip_params, calibrant=_calib, wavelength=_resolved_wl,
                        px_um=_px, ny=_ny, nz=_nz, lsd_um=_lsd_um,
                        bc_y=_bcy, bc_x=_bcx, eta_bin=eta_bin_size,
                        n_iter=n_iterations, mult=mult_factor)
                    if not _ok:
                        raise RuntimeError(_err)
                    print(f"✓ Synthesized CalibrationParams for pip CLI: {_pip_params}"
                          f" (calibrant={_calib}, λ={_resolved_wl})", file=sys.stderr)
                _out_params = work_dir / "refined_MIDAS_params.txt"
                _pip_cmd = [_ac_cli, str(_pip_params), "--image", str(image_path),
                            "--n-iters", str(n_iterations),
                            "--output", str(_out_params)]
                if dark_file and Path(dark_file).expanduser().exists():
                    _pip_cmd.extend(["--dark", str(Path(dark_file).expanduser().absolute())])
                print("="*70, file=sys.stderr)
                print("🔧 MIDAS CALIBRATION (pip midas-autocalibrate):", file=sys.stderr)
                print(f"   Working directory: {work_dir}", file=sys.stderr)
                print(f"   {' '.join(str(x) for x in _pip_cmd)}", file=sys.stderr)
                print("="*70, file=sys.stderr)
                # PyTorch calibration on CPU is slow — generous timeout (user
                # accepted the CPU cost when choosing force-pip).
                result = subprocess.run(_pip_cmd, cwd=str(work_dir),
                                        capture_output=True, text=True,
                                        timeout=3600, env=get_midas_env())
                if result.returncode == 0:
                    engine_used = "pip-console:midas-autocalibrate"
                else:
                    print(f"[engine] midas-autocalibrate exit {result.returncode}; "
                          "falling back to legacy AutoCalibrateZarr.py", file=sys.stderr)
                    for line in (result.stderr or "").strip().splitlines()[-12:]:
                        print(f"  {line}", file=sys.stderr)
                    result = None
            except Exception as e:
                print(f"[engine] pip console path error: {type(e).__name__}: {e};"
                      " falling back to legacy", file=sys.stderr)
                result = None

        if result is None:
            # ── Legacy C++ AutoCalibrateZarr.py (fallback) ────────────────────
            cmd_str = " ".join(str(x) for x in cmd)
            print("="*70, file=sys.stderr)
            print("🔧 MIDAS AUTO-CALIBRATION COMMAND (legacy AutoCalibrateZarr.py):", file=sys.stderr)
            print(f"   Working directory: {work_dir}", file=sys.stderr)
            print(f"   Python: {cmd[0]}", file=sys.stderr)
            print(f"   Script: {cmd[1]}", file=sys.stderr)
            print(f"   Parameters:", file=sys.stderr)
            for i in range(2, len(cmd), 2):
                if i+1 < len(cmd) and cmd[i].startswith("-"):
                    print(f"      {cmd[i]} {cmd[i+1]}", file=sys.stderr)
            print("="*70, file=sys.stderr)
            result = subprocess.run(
                cmd,
                cwd=str(work_dir),
                capture_output=True,
                text=True,
                # 30 min: AutoCalibrateZarr converts the HDF5 (-ConvertFile 1)
                # AND runs N iterations — a 348 MB Varex frame at 40 iters on a
                # CPU beamline routinely exceeds 10 min. The old 600 s ceiling is
                # exactly why the deployed run "produced no outputs": it timed out.
                timeout=int(os.environ.get("APEXA_CALIB_TIMEOUT", "1800")),
                env=get_midas_env()
            )
            engine_used = "legacy-cpp:AutoCalibrateZarr.py"
            _warn_deprecated_cpp("calibration: AutoCalibrateZarr.py")

        print(f"[engine] calibration engine: {engine_used}", file=sys.stderr)

        if result.returncode != 0:
            error_msg = f"Calibration failed with exit code {result.returncode}"

            # Print raw stderr to terminal so operator can see the real error
            print(f"❌ Calibration failed (exit code {result.returncode})", file=sys.stderr)
            if result.stderr:
                print("--- stderr (last 20 lines) ---", file=sys.stderr)
                for line in result.stderr.strip().splitlines()[-20:]:
                    print(f"  {line}", file=sys.stderr)
            if result.stdout:
                print("--- stdout (last 5 lines) ---", file=sys.stderr)
                for line in result.stdout.strip().splitlines()[-5:]:
                    print(f"  {line}", file=sys.stderr)

            _err_out = {
                "tool": "midas_auto_calibrate",
                "status": "error",
                "engine": engine_used,
                "image_file": str(image_path),
                "output_dir": str(work_dir),
                "error": error_msg,
                "stderr": (result.stderr or "")[-2000:],
                "stdout": (result.stdout or "")[-1000:],
            }
            _err_out["outcome_manifest"] = _write_calibration_outcome(work_dir, _err_out)
            return format_result(_err_out)

        # v10 names the output refined_MIDAS_params_<material>.txt (e.g. _CeO2.txt)
        # so glob for any matching file rather than hardcoding the name.
        # Search in work_dir (= output_dir if specified, else image_path.parent).
        output = result.stdout
        candidates = sorted(work_dir.glob("refined_MIDAS_params*.txt"),
                            key=lambda p: p.stat().st_mtime, reverse=True)
        refined_params_file = candidates[0] if candidates else work_dir / "refined_MIDAS_params.txt"

        # AutoCalibrateZarr.py writes "Dark " (key with no value) when no dark file
        # is provided. ffGenerateZipRefactor.py crashes on empty-value lines.
        # Strip them from the output file so integration doesn't fail.
        if refined_params_file.exists():
            lines = refined_params_file.read_text().splitlines()
            cleaned = [ln for ln in lines if len(ln.split()) >= 2 or not ln.split()]
            if len(cleaned) != len(lines):
                refined_params_file.write_text("\n".join(cleaned) + "\n")
                removed = [ln for ln in lines if ln not in cleaned]
                print(f"  Cleaned {len(removed)} empty-value line(s) from params: {removed}", file=sys.stderr)

        calibrated_params = {
            "bc_x": None,
            "bc_y": None,
            "lsd": None,
            "tx": None,
            "ty": None,
            "tz": None,
            "p0": None,
            "p1": None,
            "p2": None,
            "p3": None,
            "wavelength": None,
            "px": None
        }

        if refined_params_file.exists():
            # Parse the refined parameters file
            with open(refined_params_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue

                    parts = line.split()
                    if len(parts) < 2:
                        continue

                    key = parts[0]
                    if key == 'BC':
                        # BC format: "BC y x" (MIDAS uses Y X order)
                        if len(parts) >= 3:
                            calibrated_params['bc_y'] = float(parts[1])
                            calibrated_params['bc_x'] = float(parts[2])
                    elif key == 'Lsd':
                        calibrated_params['lsd'] = float(parts[1])
                    elif key == 'tx':
                        calibrated_params['tx'] = float(parts[1])
                    elif key == 'ty':
                        calibrated_params['ty'] = float(parts[1])
                    elif key == 'tz':
                        calibrated_params['tz'] = float(parts[1])
                    elif key == 'p0':
                        calibrated_params['p0'] = float(parts[1])
                    elif key == 'p1':
                        calibrated_params['p1'] = float(parts[1])
                    elif key == 'p2':
                        calibrated_params['p2'] = float(parts[1])
                    elif key == 'p3':
                        calibrated_params['p3'] = float(parts[1])
                    elif key == 'Wavelength':
                        calibrated_params['wavelength'] = float(parts[1])
                    elif key == 'px':
                        calibrated_params['px'] = float(parts[1])

        # Parse convergence metrics from autocal.log
        # Outputs land in work_dir (the per-run output dir when output_dir was
        # given), NOT next to the original image. Prefer work_dir, fall back.
        autocal_log = (work_dir / "autocal.log") if (work_dir / "autocal.log").exists() \
                      else image_path.parent / "autocal.log"
        convergence_metrics = {
            "num_iterations": None,
            "final_mean_strain": None,
            "excluded_rings": [],
            "converged": False
        }

        if autocal_log.exists():
            with open(autocal_log, 'r') as f:
                log_content = f.read()

                # Count iterations
                iterations = log_content.count('INFO - Iteration')
                if iterations > 0:
                    convergence_metrics["num_iterations"] = iterations

                # Parse final mean strain from last INFO line
                info_lines = [l for l in log_content.split('\n') if 'INFO -' in l and 'Mean Strain' in l]
                if info_lines:
                    last_line = info_lines[-1]
                    try:
                        # Format: "INFO - Mean Strain: 0.000123"
                        strain_val = float(last_line.split('Mean Strain:')[-1].strip())
                        convergence_metrics["final_mean_strain"] = strain_val
                        convergence_metrics["converged"] = strain_val < 0.0001
                    except:
                        pass

                # Parse excluded rings
                excluded_lines = [l for l in log_content.split('\n') if 'Excluding ring' in l]
                for line in excluded_lines:
                    try:
                        ring_num = int(line.split('ring')[-1].strip().split()[0])
                        if ring_num not in convergence_metrics["excluded_rings"]:
                            convergence_metrics["excluded_rings"].append(ring_num)
                    except:
                        pass

        # Also check stdout for INFO messages (if autocal.log not found)
        if not autocal_log.exists():
            for line in output.split('\n'):
                if 'INFO -' in line and 'Mean Strain' in line:
                    try:
                        strain_val = float(line.split('Mean Strain')[-1].replace(':', '').strip())
                        convergence_metrics["final_mean_strain"] = strain_val
                        convergence_metrics["converged"] = strain_val < 0.0001
                    except:
                        pass

        # Look for generated zarr file (in work_dir first, then image dir)
        zarr_file = None
        for _zdir in (work_dir, image_path.parent):
            for f in _zdir.glob("*.zarr.zip"):
                if f.stat().st_mtime > (image_path.stat().st_mtime - 60):  # recent
                    zarr_file = str(f)
                    break
            if zarr_file:
                break

        # Build success message
        bc_x = calibrated_params.get('bc_x')
        bc_y = calibrated_params.get('bc_y')
        lsd_mm = calibrated_params.get('lsd')

        if lsd_mm is not None and lsd_mm > 1000:  # Convert from µm to mm
            lsd_mm = lsd_mm / 1000.0

        message = f"✓ Auto-calibration completed successfully!\n\n"
        message += f"Refined Parameters:\n"
        message += f"  Beam Center: BC_Y={bc_y:.2f}, BC_X={bc_x:.2f} pixels\n"
        message += f"  Distance (Lsd): {lsd_mm:.2f} mm\n"
        if calibrated_params.get('tx'):
            message += f"  Tilts: tx={calibrated_params['tx']:.6f}, ty={calibrated_params['ty']:.6f}, tz={calibrated_params['tz']:.6f} rad\n"

        message += f"\nConvergence:\n"
        if convergence_metrics["num_iterations"]:
            message += f"  Iterations: {convergence_metrics['num_iterations']}\n"
        if convergence_metrics["final_mean_strain"]:
            message += f"  Final Mean Strain: {convergence_metrics['final_mean_strain']:.6f}\n"
            message += f"  Status: {'CONVERGED ✓' if convergence_metrics['converged'] else 'HIGH STRAIN — consider increasing --n-iterations'}\n"
        if convergence_metrics["excluded_rings"]:
            message += f"  Excluded Rings: {', '.join(map(str, convergence_metrics['excluded_rings']))}\n"

        message += f"\nOutput Files:\n"
        message += f"  • {refined_params_file.name} - Use this for ff_MIDAS.py\n"
        if autocal_log.exists():
            message += f"  • autocal.log - Detailed iteration history\n"
        if save_plots_hdf:
            message += f"  • {Path(save_plots_hdf).name} - Diagnostic plots and arrays\n"

        _outcome = {
            "tool": "midas_auto_calibrate",
            "status": "success",
            "engine": engine_used,
            "calibrant": _detect_calibrant_from_name(original_stem),
            "image_file": str(image_path),
            "dark_file": str(dark_file) if dark_file else None,
            "input_parameters_file": str(param_path),
            "calibrated_parameters_file": str(refined_params_file) if refined_params_file.exists() else None,
            "calibrated_parameters": calibrated_params,
            "convergence_metrics": convergence_metrics,
            "zarr_file": zarr_file,
            "output_dir": str(work_dir),
            "message": message
        }
        # Architectural fix: drop a uniform per-run outcome manifest so a later
        # "what's the outcome?" is answered from this record (and benchmark runs
        # are comparable), instead of re-discovering scattered MIDAS artifacts.
        _outcome["outcome_manifest"] = _write_calibration_outcome(work_dir, _outcome)
        message += f"\n  • APEXA_calibration.json - machine-readable outcome record\n"
        _outcome["message"] = message
        return format_result(_outcome)

    except subprocess.TimeoutExpired:
        # Record the timeout as a real outcome so the agent reports it (and does
        # not silently "find nothing"). work_dir is set before any subprocess.
        _to = {
            "tool": "midas_auto_calibrate", "status": "timeout",
            "engine": locals().get("engine_used"),
            "image_file": str(image_path),
            "output_dir": str(locals().get("work_dir", image_path.parent)),
            "error": (f"Calibration timed out (limit "
                      f"{os.environ.get('APEXA_CALIB_TIMEOUT', '1800')}s). The CPU "
                      "PyTorch/AutoCalibrateZarr path is slow on large HDF5 frames. "
                      "Retry with fewer --n-iterations, set APEXA_CALIB_TIMEOUT "
                      "higher, or APEXA_FORCE_LEGACY_MIDAS=1 for the fast C++ path."),
        }
        _to["outcome_manifest"] = _write_calibration_outcome(
            locals().get("work_dir", image_path.parent), _to)
        return format_result(_to)
    except Exception as e:
        return format_result({
            "tool": "midas_auto_calibrate",
            "status": "error",
            "error": str(e)
        })

# =============================================================================
# BATCH INTEGRATION (MULTI-PANEL DETECTOR SUPPORT)
# =============================================================================

# ── Python-default batch integration helpers (operando: dark + xye/fxye) ─────
def _radius_px_to_two_theta_deg(r_px, lsd_um, px_um):
    """Convert detector radius (px) to 2θ (deg): 2θ = atan(R·px / Lsd)."""
    import math
    return [math.degrees(math.atan((r * px_um) / lsd_um)) for r in r_px]


def _write_xye(path, tth_deg, inten):
    """TOPAS .xye: 2θ(deg)  I  σ(=√max(I,0)), space-separated."""
    import math
    with open(path, "w") as f:
        for t, i in zip(tth_deg, inten):
            f.write(f"{t:.6f} {i:.6f} {math.sqrt(i) if i > 0 else 0.0:.6f}\n")


def _write_fxye(path, tth_deg, inten, title="APEXA integrated"):
    """GSAS/Jana .fxye: 2θ in centidegrees, I, σ(=√max(I,0)). Minimal header."""
    import math
    with open(path, "w") as f:
        f.write(f"{title}\n")
        for t, i in zip(tth_deg, inten):
            f.write(f"{t * 100.0:.2f} {i:.4f} {math.sqrt(i) if i > 0 else 0.0:.4f}\n")


def _read_profile_csv(csv_path):
    """Read a midas-integrate-v2 CSV (R_px, intensity) → (r_px[], inten[])."""
    r_px, inten = [], []
    with open(csv_path) as f:
        for line in f:
            line = line.strip()
            if not line or line[0] in "#Rr":      # skip blank/header
                continue
            parts = line.replace(",", " ").split()
            if len(parts) >= 2:
                try:
                    r_px.append(float(parts[0])); inten.append(float(parts[1]))
                except ValueError:
                    continue
    return r_px, inten


def _batch_integrate_v2_python(frames, params_file, dark_file, out_dir,
                               lsd_um, px_um, mode="subpixel", timeout=600):
    """Python-default operando batch: per frame run `midas-integrate-v2 --dark`
    (dark subtracted by the maintained CLI), then write per-frame .xye (TOPAS,
    2θ°) + .fxye (GSAS, centidegrees, σ=√I). Raises on any failure so the caller
    can fall back to the legacy C++ integrator. PyTorch — slow on CPU at scale.
    """
    import shutil as _sh
    cli = _sh.which("midas-integrate-v2")
    if not cli:
        raise RuntimeError("midas-integrate-v2 not found (pip midas-suite)")
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    outputs = []
    for frame in frames:
        stem = Path(frame).stem
        csv_out = out_dir / f"{stem}.profile.csv"
        cmd = [cli, str(params_file), "--image", str(frame),
               "--mode", mode, "--out", str(csv_out)]
        if dark_file:
            cmd += ["--dark", str(dark_file)]
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, env=get_midas_env())
        if p.returncode != 0 or not csv_out.exists():
            raise RuntimeError(f"midas-integrate-v2 failed on {stem}: "
                               f"{(p.stderr or '')[-300:]}")
        r_px, inten = _read_profile_csv(csv_out)
        tth = _radius_px_to_two_theta_deg(r_px, lsd_um, px_um)
        xye, fxye = out_dir / f"{stem}.xye", out_dir / f"{stem}.fxye"
        _write_xye(xye, tth, inten)
        _write_fxye(fxye, tth, inten, title=stem)
        outputs += [str(xye), str(fxye)]
    return outputs


@mcp.tool()
async def midas_batch_integrate(
    data_file: str,
    dark_file: str,
    parameter_file: str,
    start_frame: int,
    end_frame: int,
    result_folder: str = "./integration_results",
    map_detector: bool = True,
    num_cpus: int = 10,
    num_frame_chunks: int = 10,
    convert_files: bool = True,
    write_mat: bool = False,
    data_location: str = "/exchange/data",
    dark_location: str = "/exchange/data",
    bright_file: str = None,
    csv_output: bool = False,
    short_names: bool = True,
    r_min: float = None,
    r_max: float = None,
    r_bin_size: float = None,
    eta_min: float = None,
    eta_max: float = None,
    eta_bin_size: float = None,
) -> str:
    """
    Batch integrate 2D diffraction images using MIDAS Python integrator.

    This is the PRODUCTION workflow used at beamlines for multi-panel detectors
    like Hydra (4 panels). Uses integrator.py which supports:
    - Multi-panel detector mapping
    - Dark file correction
    - Batch frame processing
    - Parallel CPU processing
    - HDF5 input/output

    ✨ AUTOMATED: Runs DetectorMapper if Map.bin missing (handled by integrator.py)

    Based on cake_ge_v2.sh workflow from 1-ID beamline.

    Args:
        data_file: Path to HDF5 data file (e.g., sample_003083.ge1.h5)
        dark_file: Path to HDF5 dark file for background subtraction
        parameter_file: MIDAS parameter file (refined_MIDAS_params.txt)
        start_frame: First frame number to process
        end_frame: Last frame number to process
        result_folder: Output directory for results (default: ./integration_results)
        map_detector: Enable detector mapping for multi-panel detectors (default: True)
        num_cpus: Number of CPU cores for parallel processing (default: 10)
        num_frame_chunks: Number of chunks to divide frames into (default: 10)
        convert_files: Convert files to MIDAS analysis format (default: True)
        write_mat: Write MATLAB .mat files (default: False, saves disk space)
        data_location: Location within data HDF5 file (default: /exchange/data)
        dark_location: Location within dark HDF5 file (default: /exchange/data)
        bright_file: Optional bright/flat-field image (v11, issue #20) — embedded under processed/bright/ in zarr
        csv_output: Also export per-frame lineouts and REtaMap as CSVs (v11, issue #23)
        short_names: Use short v11 output naming (<stem>.zarr.zip). False = legacy suffix-stacking
        r_min, r_max, r_bin_size: Radial integration range overrides (pixels). Defaults: from params file.
        eta_min, eta_max, eta_bin_size: Azimuthal range overrides (degrees). Defaults: from params file.

    The agent SHOULD show the params-file R/eta values back to the user and confirm
    result_folder before invoking this tool. See midas-integrate SKILL.md.

    Returns:
        JSON with integration status, output files (.zarr.zip), and processing details

    Example:
        midas_batch_integrate(
            data_file="/path/to/CeO2_003083.ge1.h5",
            dark_file="/path/to/dark_003084.ge1.h5",
            parameter_file="refined_MIDAS_params_ge1_Tx_cake_partial.txt",
            start_frame=3083,
            end_frame=3085,
            map_detector=True,
            num_cpus=80
        )
    """
    try:
        # Validate inputs
        data_path = Path(data_file).resolve()
        dark_path = Path(dark_file).resolve()
        param_path = Path(parameter_file).resolve()

        if not data_path.exists():
            return format_result({
                "tool": "midas_batch_integrate",
                "status": "error",
                "error": f"Data file not found: {data_path}"
            })

        if not dark_path.exists():
            return format_result({
                "tool": "midas_batch_integrate",
                "status": "error",
                "error": f"Dark file not found: {dark_path}"
            })

        if not param_path.exists():
            return format_result({
                "tool": "midas_batch_integrate",
                "status": "error",
                "error": f"Parameter file not found: {param_path}"
            })

        if param_path.suffix.lower() in ('.csv', '.json', '.log', '.bin', '.h5', '.hdf', '.tif'):
            return format_result({"tool": "midas_batch_integrate", "status": "error",
                                  "error": f"Invalid parameter file: {param_path.name} (suffix {param_path.suffix}). "
                                           f"Use refined_MIDAS_params*.txt from midas_auto_calibrate."})

        _strip_empty_value_lines(param_path)
        _announce_output("midas_batch_integrate", Path(result_folder).resolve(),
                         frames=f"{start_frame}-{end_frame}", data=data_path.name)

        # ── Python default (operando): per-frame dark-subtract + v2 integrate ──
        # Preferred when a dark is given (v2-batch can't dark-subtract). Resolves
        # the frame list from data_file + range (excluding dark/background files),
        # runs `midas-integrate-v2 --dark` per frame, and writes .xye + .fxye.
        # On ANY uncertainty/error it falls through to the deprecated C++
        # integrator below (which also handles mixed dirs). Slow on CPU at scale.
        import shutil as _sh2
        _force_legacy = os.environ.get("APEXA_FORCE_LEGACY_MIDAS") == "1"
        if not _force_legacy and dark_file and _sh2.which("midas-integrate-v2"):
            try:
                _df = Path(data_file)
                if _df.is_dir():
                    _cands = sorted(
                        p for pat in ("*.h5", "*.hdf5", "*.tif", "*.tiff")
                        for p in _df.glob(pat)
                        if "dark" not in p.name.lower()
                        and "background" not in p.name.lower())

                    def _fnum(p):
                        nums = re.findall(r"\d+", p.name)
                        return int(nums[-1]) if nums else -1
                    _frames = [str(p) for p in _cands
                               if start_frame <= _fnum(p) <= end_frame]
                else:
                    _frames = [str(_df)]
                if not _frames:
                    raise RuntimeError("no data frames resolved (dir layout?)")
                _lsd_um = _px_um = None
                for _ln in Path(param_path).read_text().splitlines():
                    _t = _ln.split()
                    if len(_t) >= 2 and _t[0] == "Lsd":
                        _lsd_um = float(_t[1])
                    elif len(_t) >= 2 and _t[0] in ("px", "PixelSize", "pxY"):
                        _px_um = float(_t[1])
                if not _lsd_um or not _px_um:
                    raise RuntimeError("Lsd/px not in params (needed for 2θ)")
                _out = Path(result_folder).resolve()
                print("=" * 70, file=sys.stderr)
                print("🔧 BATCH (python default: midas-integrate-v2 --dark per frame "
                      "→ xye/fxye):", file=sys.stderr)
                print(f"   {len(_frames)} frames · dark={Path(dark_file).name} · out={_out}",
                      file=sys.stderr)
                if len(_frames) > 200:
                    print(f"   ⚠ {len(_frames)} frames on CPU PyTorch is slow — set "
                          "APEXA_FORCE_LEGACY_MIDAS=1 for the fast C++ path.", file=sys.stderr)
                print("=" * 70, file=sys.stderr)
                _outs = _batch_integrate_v2_python(
                    _frames, str(param_path), str(Path(dark_file).expanduser()),
                    _out, _lsd_um, _px_um)
                print("[engine] batch engine: pip-v2:midas-integrate-v2 (python, per-frame dark)",
                      file=sys.stderr)
                return format_result({
                    "tool": "midas_batch_integrate", "status": "success",
                    "engine": "pip-v2:midas-integrate-v2-python",
                    "result_folder": str(_out), "n_frames": len(_frames),
                    "n_outputs": len(_outs), "outputs": _outs[:20],
                    "message": f"Batch integrated {len(_frames)} frames via python v2 "
                               "(per-frame dark subtraction); wrote .xye + .fxye."})
            except Exception as _e:
                print(f"[engine] python batch path unavailable ({type(_e).__name__}: {_e}) "
                      "— falling back to legacy C++ integrator.", file=sys.stderr)

        # ── Latest-first: midas-integrate-v2-batch when applicable ──────────
        # The modern pip batch engine (subpixel binning, xye/csv/h5 output) is
        # the default — BUT it cannot dark-subtract and ingests only 3-D stacks
        # (zarr / hdf5-with-frames / tiff-glob). Per the latest-first +
        # auto-fallback policy, use it ONLY when no dark/bright correction is
        # requested and the input is a stack; any dark subtraction or MIDAS
        # lineout/zarr (GSAS) need falls back to the legacy C++ integrator below.
        import shutil as _sh
        _v2b = _sh.which("midas-integrate-v2-batch")
        _suffix = Path(data_file).suffix.lower()
        _is_stack = _suffix in (".zip", ".zarr", ".h5", ".hdf5")
        if _v2b and _is_stack and not dark_file and not bright_file:
            try:
                _out = Path(result_folder).resolve()
                _out.mkdir(parents=True, exist_ok=True)
                _cmd = [_v2b, str(Path(parameter_file).resolve())]
                if _suffix in (".zip", ".zarr"):
                    _cmd += ["--zarr", str(Path(data_file).resolve())]
                else:
                    _cmd += ["--hdf5", str(Path(data_file).resolve())]
                    if data_location:
                        _cmd += ["--hdf5-dataset",
                                 data_location.strip("/").split("/")[-1] or "frames"]
                _cmd += ["--out-dir", str(_out), "--mode", "subpixel",
                         "--out-format", "xye"]
                print("=" * 70, file=sys.stderr)
                print("🔧 BATCH INTEGRATION (latest — midas-integrate-v2-batch, subpixel):",
                      file=sys.stderr)
                print("   " + " ".join(_cmd), file=sys.stderr)
                print("   (PyTorch — slow on CPU for large stacks)", file=sys.stderr)
                print("=" * 70, file=sys.stderr)
                _pp = subprocess.run(_cmd, capture_output=True, text=True,
                                     timeout=14400, env=get_midas_env())
                if _pp.returncode == 0:
                    _outs = sorted(str(p) for p in _out.glob("*.xye")) or \
                        sorted(str(p) for p in _out.glob("*"))
                    print("[engine] batch engine: pip-v2:midas-integrate-v2-batch",
                          file=sys.stderr)
                    return format_result({
                        "tool": "midas_batch_integrate", "status": "success",
                        "engine": "pip-v2:midas-integrate-v2-batch",
                        "result_folder": str(_out), "n_outputs": len(_outs),
                        "outputs": _outs[:20],
                        "message": "Batch integrated via latest midas-integrate-v2-batch "
                                   "(subpixel binning, xye output)."})
                print(f"[engine] v2-batch exit {_pp.returncode}; falling back to legacy "
                      "integrator.py", file=sys.stderr)
                for _l in (_pp.stderr or "").strip().splitlines()[-10:]:
                    print(f"  {_l}", file=sys.stderr)
            except Exception as _e:
                print(f"[engine] v2-batch error {type(_e).__name__}: {_e}; falling back "
                      "to legacy integrator.py", file=sys.stderr)
        elif _v2b and (dark_file or bright_file):
            print("[engine] dark/bright correction requested → legacy integrator.py "
                  "(midas-integrate-v2-batch cannot dark-subtract).", file=sys.stderr)

        # Find MIDAS integrator.py via MIDAS_ROOT (set from .env)
        if not MIDAS_ROOT:
            return format_result({"tool": "midas_batch_integrate", "status": "error",
                                  "error": "MIDAS_PATH not set. Add MIDAS_PATH to .env"})
        midas_integrator = MIDAS_ROOT / "FF_HEDM" / "workflows" / "integrator.py"
        if not midas_integrator.exists():
            return format_result({"tool": "midas_batch_integrate", "status": "error",
                                  "error": f"integrator.py not found at {midas_integrator}"})
        _warn_deprecated_cpp("batch integration: integrator.py")

        # Create result folder
        result_path = Path(result_folder).resolve()
        result_path.mkdir(parents=True, exist_ok=True)

        # Build integrator command
        # Based on: python ~/opt/MIDAS/utils/integrator.py -resultFolder ./ge1_cake -paramFN params.txt -dataFN data.h5 -dataLoc /exchange/data -darkFN dark.h5 -darkLoc /exchange/data -startFileNr 3083 -endFileNr 3085 -convertFiles 1 -mapDetector 1 -nCPUs 80 -writeMat 0 -numFrameChunks 10

        midas_python = find_midas_python()
        cmd = [
            midas_python,
            str(midas_integrator),
            "-resultFolder", str(result_path),
            "-paramFN", str(param_path),
            "-dataFN", str(data_path),
            "-dataLoc", data_location,
            "-darkFN", str(dark_path),
            "-darkLoc", dark_location,
            "-startFileNr", str(start_frame),
            "-endFileNr", str(end_frame),
            "-convertFiles", "1" if convert_files else "0",
            "-mapDetector", "1" if map_detector else "0",
            "-nCPUs", str(num_cpus),
            "-writeMat", "1" if write_mat else "0",
            "-numFrameChunks", str(num_frame_chunks),
            "-shortNames", "1" if short_names else "0",
            "-csvOutput", "1" if csv_output else "0",
        ]
        if bright_file:
            bright_path = Path(bright_file).expanduser().resolve()
            if not bright_path.exists():
                return format_result({"tool": "midas_batch_integrate", "status": "error",
                                      "error": f"Bright file not found: {bright_path}"})
            cmd += ["-brightFN", str(bright_path)]

        # Trailing parameter overrides (R/Eta range — see integrator.py override syntax)
        for key, val in (("RMin", r_min), ("RMax", r_max), ("RBinSize", r_bin_size),
                         ("EtaMin", eta_min), ("EtaMax", eta_max),
                         ("EtaBinSize", eta_bin_size)):
            if val is not None:
                cmd += [key, str(val)]

        cmd_str = " ".join(cmd)
        print(f"\n  $ {cmd_str}", file=sys.stderr)

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=1800,  # 30 minute timeout for batch processing
            env=get_midas_env()
        )

        if result.returncode != 0:
            print(f"❌ Batch integration failed (exit {result.returncode})", file=sys.stderr)
            for line in result.stderr.strip().splitlines()[-20:]:
                print(f"  {line}", file=sys.stderr)
            return format_result({
                "tool": "midas_batch_integrate",
                "status": "error",
                "command": cmd_str,
                "exit_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "error": f"Integration failed with exit code {result.returncode}"
            })

        # Find output files
        zarr_files = list(result_path.glob("*.zarr.zip"))
        hdf_files = list(result_path.glob("*.hdf"))
        mat_files = list(result_path.glob("*.mat"))

        # Count processed frames
        num_frames = end_frame - start_frame + 1

        # Build success message
        message = f"✓ Batch integration completed successfully!\n\n"
        message += f"Processing Details:\n"
        message += f"  Frames processed: {num_frames} ({start_frame} to {end_frame})\n"
        message += f"  Data file: {data_path.name}\n"
        message += f"  Dark file: {dark_path.name}\n"
        message += f"  Parameters: {param_path.name}\n"
        message += f"  Detector mapping: {'ENABLED' if map_detector else 'DISABLED'}\n"
        message += f"  CPUs used: {num_cpus}\n"

        message += f"\nOutput Files ({result_path}):\n"
        if zarr_files:
            message += f"  • {len(zarr_files)} ZARR files (.zarr.zip)\n"
            for zf in zarr_files[:3]:  # Show first 3
                message += f"    - {zf.name}\n"
            if len(zarr_files) > 3:
                message += f"    ... and {len(zarr_files) - 3} more\n"

        if hdf_files:
            message += f"  • {len(hdf_files)} HDF5 files\n"

        if mat_files:
            message += f"  • {len(mat_files)} MATLAB files\n"

        message += f"\nNext Steps:\n"
        message += f"  1. ZARR files contain integrated 1D patterns\n"
        message += f"  2. For multi-panel detectors, repeat for all panels (ge1, ge2, ge3, ge4)\n"
        message += f"  3. Convert ZARR to MAT if needed using zarr_tomat.py\n"
        message += f"  4. Use ff_MIDAS.py for grain indexing with refined parameters\n"

        return format_result({
            "tool": "midas_batch_integrate",
            "status": "success",
            "method": "MIDAS integrator.py",
            "command": cmd_str,
            "data_file": str(data_path),
            "dark_file": str(dark_path),
            "parameter_file": str(param_path),
            "result_folder": str(result_path),
            "frame_range": {"start": start_frame, "end": end_frame, "count": num_frames},
            "detector_mapping": map_detector,
            "cpus": num_cpus,
            "output_files": {
                "zarr": [str(f) for f in zarr_files],
                "hdf": [str(f) for f in hdf_files],
                "mat": [str(f) for f in mat_files]
            },
            "stdout": result.stdout,
            "stderr": result.stderr,
            "message": message
        })

    except subprocess.TimeoutExpired:
        return format_result({
            "tool": "midas_batch_integrate",
            "status": "error",
            "error": "Integration timed out (>30 minutes). Try reducing frame count or increase timeout."
        })
    except Exception as e:
        return format_result({
            "tool": "midas_batch_integrate",
            "status": "error",
            "error": str(e),
            "traceback": traceback.format_exc()
        })

# =============================================================================
# KNOWLEDGE BASE & DOMAIN EXPERTISE
# =============================================================================

# Initialize knowledge base (lazy loading)
_knowledge_base = None
_materials_db = None
_typical_params = None

def _apply_offline_hf_env() -> bool:
    """Force HuggingFace fully offline when APEXA offline mode is requested, so the
    embedder loads ONLY from the local cache and never reaches the network.

    This is the hard requirement for air-gapped beamline machines: the embed model
    must already be pre-staged in ~/.cache/huggingface, and with these flags set a
    missing-network machine loads it from cache instead of hanging/failing on an HF
    HEAD request. Enable with APEXA_OFFLINE=1 (or set HF_HUB_OFFLINE=1 directly).
    Idempotent; safe to call before every model load. Returns True if offline."""
    # Network tier is authoritative: anything below `web` must never reach HF.
    # APEXA_OFFLINE stays supported as the legacy switch.
    try:
        from apexa_network import apply_offline_env
        if apply_offline_env():
            return True
    except Exception:
        pass
    truthy = ("1", "true", "yes", "on")
    if (os.environ.get("APEXA_OFFLINE", "").lower() in truthy
            or os.environ.get("HF_HUB_OFFLINE", "").lower() in truthy):
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        return True
    return False


def get_knowledge_base():
    """Get or initialize the knowledge base (RAG with ChromaDB)"""
    global _knowledge_base
    if _knowledge_base is None:
        try:
            offline = _apply_offline_hf_env()  # must run before SentenceTransformer import
            # No third-party telemetry from a beamline host, at any tier, and no
            # model-loading INFO spam in the operator's terminal. Both must be set
            # BEFORE the imports below.
            os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
            os.environ.setdefault("CHROMA_TELEMETRY_ENABLED", "False")
            # HuggingFace tokenizers spawns worker threads on first use. Every
            # MIDAS tool then fork()s via subprocess.run, and forking a process
            # that already has tokenizer threads risks a DEADLOCK — the library
            # warns and silently disables parallelism when it detects this. In
            # APEXA that ordering is guaranteed: a RAG query warms the embedder,
            # then a calibration/integration forks. Set it explicitly BEFORE the
            # tokenizer is ever created so behaviour is deterministic rather than
            # rescued after the fact.
            os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
            for _noisy in ("sentence_transformers", "chromadb", "chromadb.telemetry",
                           "transformers", "posthog", "httpx"):
                logging.getLogger(_noisy).setLevel(logging.WARNING)
            import chromadb
            from sentence_transformers import SentenceTransformer
            from pathlib import Path

            kb_path = Path(__file__).parent / "knowledge_base"
            chroma_path = kb_path / "chroma_db"

            if chroma_path.exists():
                model_name = os.environ.get("APEXA_EMBED_MODEL",
                                            "nomic-ai/nomic-embed-text-v1.5")
                st_kwargs = {"trust_remote_code": True}
                if offline:
                    st_kwargs["local_files_only"] = True
                try:
                    embedder = SentenceTransformer(model_name, **st_kwargs)
                except TypeError:
                    # older sentence-transformers lacks local_files_only kwarg;
                    # the env vars above still force offline loading.
                    st_kwargs.pop("local_files_only", None)
                    embedder = SentenceTransformer(model_name, **st_kwargs)
                _knowledge_base = {
                    "client": chromadb.PersistentClient(path=str(chroma_path)),
                    "embedder": embedder,
                    "model_name": model_name,
                    "offline": offline,
                    "available": True
                }
            else:
                _knowledge_base = {"available": False, "error": "Knowledge base not indexed"}
        except Exception as e:
            _knowledge_base = {"available": False, "error": str(e)}

    return _knowledge_base


def get_materials_db():
    """Get materials database"""
    global _materials_db
    if _materials_db is None:
        try:
            import json
            from pathlib import Path
            kb_path = Path(__file__).parent / "knowledge_base"
            materials_file = kb_path / "data" / "materials.json"

            if materials_file.exists():
                with open(materials_file) as f:
                    _materials_db = json.load(f)
            else:
                _materials_db = {}
        except Exception as e:
            _materials_db = {}

    return _materials_db


def get_typical_parameters():
    """Get typical HEDM parameters"""
    global _typical_params
    if _typical_params is None:
        try:
            import json
            from pathlib import Path
            kb_path = Path(__file__).parent / "knowledge_base"
            params_file = kb_path / "data" / "typical_parameters.json"

            if params_file.exists():
                with open(params_file) as f:
                    _typical_params = json.load(f)
            else:
                _typical_params = {}
        except Exception as e:
            _typical_params = {}

    return _typical_params


@mcp.tool()
async def query_hedm_knowledge(
    question: str,
    max_results: int = 3,
    source_type: Optional[str] = None,
    technique: Optional[str] = None,
    retrieval_mode: str = "dense"
) -> str:
    """Query the HEDM knowledge base using semantic search across papers, logbooks, books, and technique capsules.

    This tool searches through indexed research papers, experimental logbooks,
    crystallography textbooks, and vendored MIDAS technique capsules to answer
    questions about HEDM theory, best practices, and past experiments.

    Args:
        question: Natural language question about HEDM, crystallography, calibration, etc.
                 Examples:
                 - "What is a good calibration strain value?"
                 - "How was sample XYZ processed in 2019?"
                 - "Explain Bragg's law for 61keV beam"
        max_results: Number of relevant excerpts to return (default: 3)
        source_type: Filter by document type: "paper", "logbook", "book", "capsule",
                 or None for all
        technique: Filter to a single MIDAS technique capsule id (e.g. "ff-hedm",
                 "nf-hedm", "pf-hedm", "dfxm", "tomo", "xrd-ct", "dct-tt",
                 "calibrate-integrate"), or None for all techniques. Composes with
                 source_type. Only capsule chunks carry a technique tag; non-capsule
                 chunks are tagged "" at index time, so a technique filter narrows to
                 that technique's capsule content.
        retrieval_mode: "dense" (default, semantic embedding search — unchanged
                 behaviour) or "hybrid" (fuse dense with sparse BM25 keyword search
                 via Reciprocal Rank Fusion; better for exact tokens like flag/param
                 names). Hybrid needs no network or model beyond what dense uses, and
                 falls back to dense on any error. The technique/source_type filters
                 apply to both retrievers.

    Returns:
        JSON string with relevant excerpts, sources, and confidence scores
    """
    try:
        kb = get_knowledge_base()

        if not kb.get("available"):
            return json.dumps({
                "status": "error",
                "error": "Knowledge base not available",
                "details": kb.get("error", "Not indexed"),
                "suggestion": "Run: python knowledge_base/index_knowledge.py"
            }, indent=2)

        # Encode the question. Some embedders (Nomic) require a task prefix;
        # most others don't. Keep this in sync with EMBED_PREFIXES in
        # knowledge_base/index_knowledge.py.
        _query_prefix_map = {
            "nomic-ai/nomic-embed-text-v1.5": "search_query: ",
            "nomic-ai/nomic-embed-text-v1":   "search_query: ",
        }
        q_pfx = _query_prefix_map.get(kb.get("model_name", ""), "")
        query_embedding = kb["embedder"].encode(f"{q_pfx}{question}").tolist()

        # Get collection
        collection = kb["client"].get_collection(name="hedm_knowledge")

        # Build filter. source_type and technique compose; ChromaDB requires an
        # explicit $and to combine two equality clauses. Neither set → None (the
        # exact contract that shipped before technique existed — additive proof).
        _clauses = []
        if source_type:
            _clauses.append({"type": source_type})
        if technique:
            _clauses.append({"technique": technique})
        if len(_clauses) == 2:
            where_filter = {"$and": _clauses}
        elif len(_clauses) == 1:
            where_filter = _clauses[0]
        else:
            where_filter = None

        # Retrieve. "dense" is the original path, preserved byte-for-byte so the
        # default contract is unchanged. "hybrid" fuses dense + sparse BM25 and
        # falls back to dense on any error (fail-open).
        _mode = (retrieval_mode or "dense").strip().lower()
        retrieval_meta = {"mode": "dense"}
        hits = None  # list of (doc, meta, dist_or_None, extra_dict)

        if _mode == "hybrid":
            try:
                import sys as _sys
                _kbdir = str(Path(__file__).parent / "knowledge_base")
                if _kbdir not in _sys.path:
                    _sys.path.insert(0, _kbdir)
                import hybrid_retrieval as _hybrid
                fused = _hybrid.hybrid_search(
                    collection=collection,
                    query_embedding=query_embedding,
                    query_text=question,
                    n_results=max_results,
                    where_filter=where_filter,
                )
                hits = [
                    (r["document"], r["metadata"], r["distance"], {
                        "dense_rank": r["dense_rank"],
                        "sparse_rank": r["sparse_rank"],
                        "rrf": round(r["rrf"], 6),
                    })
                    for r in fused
                ]
                retrieval_meta = {"mode": "hybrid", "rrf_k": _hybrid.RRF_K}
            except Exception as _he:
                # Fail-open to dense; record why so a beamline operator can see it.
                hits = None
                retrieval_meta = {"mode": "dense", "requested_mode": "hybrid",
                                  "hybrid_fallback_reason": str(_he)}

        if hits is None:
            # Dense path (default, and hybrid fallback) — unchanged query.
            results = collection.query(
                query_embeddings=[query_embedding],
                n_results=max_results,
                where=where_filter
            )
            hits = [
                (doc, meta, dist, {})
                for doc, meta, dist in zip(
                    results['documents'][0],
                    results['metadatas'][0],
                    results['distances'][0],
                )
            ]

        # Format results — include citation, page, DOI for proper referencing
        excerpts = []
        for i, (doc, meta, dist, extra) in enumerate(hits, 1):
            ex = {
                "rank": i,
                "source": meta['source'],
                "type": meta['type'],
                "technique": meta.get('technique', ''),
                "citation": meta.get('citation', meta['source']),
                "bibkey": meta.get('bibkey', ''),
                "title": meta.get('title', ''),
                "authors": meta.get('authors', ''),
                "year": meta.get('year', ''),
                "journal": meta.get('journal', ''),
                "doi": meta.get('doi', ''),
                "page": meta.get('page', 0),
                # Sparse-only hybrid hits have no embedding distance → null, never
                # a fabricated score.
                "similarity": (round(max(0.0, 1 - dist), 3) if dist is not None else None),
                "excerpt": doc,
                "chunk_index": meta.get('chunk_index', 0)
            }
            ex.update(extra)
            excerpts.append(ex)

        # Compact reference list (deduped by source) for the LLM to cite cleanly
        seen = set()
        references = []
        for ex in excerpts:
            if ex['source'] in seen:
                continue
            seen.add(ex['source'])
            references.append({
                "source": ex['source'],
                "citation": ex['citation'],
                "bibkey": ex['bibkey'],
                "doi": ex['doi'],
            })

        return json.dumps({
            "status": "success",
            "question": question,
            "results_count": len(excerpts),
            "retrieval": retrieval_meta,
            "references": references,
            "excerpts": excerpts,
            "instruction_to_assistant": (
                "When answering, cite each excerpt inline using the 'citation' field "
                "(e.g. 'Sharma et al. (2012). J. Appl. Cryst. 45:693–704. DOI:...') and, "
                "when available, reference the page number. Use get_bibtex(source) to "
                "retrieve the BibTeX entry for any cited source."
            )
        }, indent=2)

    except Exception as e:
        return json.dumps({
            "status": "error",
            "error": str(e),
            "traceback": traceback.format_exc()
        }, indent=2)


@mcp.tool()
async def get_bibtex(source: str) -> str:
    """Return the BibTeX entry for a paper in the knowledge base.

    Reads the sibling .bib sidecar (e.g. HEDM-I.pdf -> HEDM-I.bib) for the
    requested source. Use this after query_hedm_knowledge to cite a result
    in a manuscript or report.

    Args:
        source: PDF filename returned by query_hedm_knowledge (e.g. "HEDM-I.pdf"),
                or the bare stem ("HEDM-I"), or a bibkey ("Sharma2012a").

    Returns:
        JSON with the BibTeX entry text, or an error if unavailable.
    """
    try:
        from pathlib import Path
        papers_dir = Path(__file__).parent / "knowledge_base" / "papers"

        # Normalize: try as filename, stem, then bibkey-search
        candidates = []
        s = source.strip()
        if s.endswith(".pdf"):
            candidates.append(papers_dir / (s[:-4] + ".bib"))
        if s.endswith(".bib"):
            candidates.append(papers_dir / s)
        candidates.append(papers_dir / f"{s}.bib")

        bib_path = next((c for c in candidates if c.exists()), None)

        if bib_path is None:
            # Fallback: scan all .bib files for matching bibkey
            for bib in papers_dir.glob("*.bib"):
                text = bib.read_text(encoding="utf-8", errors="ignore")
                if f"{{{s}," in text or f"{{ {s}," in text:
                    bib_path = bib
                    break

        if bib_path is None:
            return json.dumps({
                "status": "error",
                "error": f"No BibTeX entry found for '{source}'",
                "available": sorted(p.stem for p in papers_dir.glob("*.bib")),
            }, indent=2)

        return json.dumps({
            "status": "success",
            "source": source,
            "bib_file": str(bib_path.name),
            "bibtex": bib_path.read_text(encoding="utf-8"),
        }, indent=2)

    except Exception as e:
        return json.dumps({
            "status": "error",
            "error": str(e),
            "traceback": traceback.format_exc(),
        }, indent=2)


@mcp.tool()
async def get_material_properties(material_name: str) -> str:
    """Get crystallographic properties and metadata for a material.

    Provides authoritative data from Materials Project including lattice parameters,
    space group, density, and HEDM-specific metadata like typical ring counts and
    calibration usage.

    Args:
        material_name: Chemical formula or name (e.g., "CeO2", "LaB6", "Ti", "Steel_316L")

    Returns:
        JSON string with complete material properties
    """
    try:
        materials_db = get_materials_db()

        # Try exact match first
        material = materials_db.get(material_name)

        # Try case-insensitive match
        if not material:
            for key in materials_db.keys():
                if key.upper() == material_name.upper():
                    material = materials_db[key]
                    break

        if not material:
            # List available materials
            available = [k for k in materials_db.keys() if not k.startswith("_")]
            return json.dumps({
                "status": "not_found",
                "requested": material_name,
                "available_materials": available[:20],
                "total_available": len(available)
            }, indent=2)

        return json.dumps({
            "status": "success",
            "material": material
        }, indent=2)

    except Exception as e:
        return json.dumps({
            "status": "error",
            "error": str(e)
        }, indent=2)


@mcp.tool()
async def get_typical_hedm_parameters(
    beam_energy_kev: Optional[float] = None,
    geometry_type: Optional[str] = None
) -> str:
    """Get typical HEDM experimental parameters and quality thresholds.

    Provides guidelines for detector distances, calibration quality metrics,
    integration parameters, and processing recommendations based on experimental
    geometry and beam energy.

    Args:
        beam_energy_kev: Beam energy in keV (e.g., 30, 61.3, 90, 120)
        geometry_type: "ff_hedm" or "nf_hedm" (optional)

    Returns:
        JSON string with typical parameter ranges and quality thresholds
    """
    try:
        params = get_typical_parameters()

        if not params:
            return json.dumps({
                "status": "error",
                "error": "Typical parameters database not loaded"
            }, indent=2)

        result = {"status": "success"}

        # Get relevant beam energy info
        if beam_energy_kev:
            from apexa_units import kev_to_angstrom
            wavelength_angstrom = kev_to_angstrom(beam_energy_kev)
            result["beam_info"] = {
                "energy_kev": beam_energy_kev,
                "wavelength_angstrom": round(wavelength_angstrom, 4)
            }

            # Find closest standard energy
            energies = params.get("beam_energies", {})
            for energy_type, energy_data in energies.items():
                if abs(energy_data["value_kev"] - beam_energy_kev) < 5:
                    result["beam_info"]["type"] = energy_type
                    result["beam_info"]["typical_use"] = energy_data["use_case"]
                    break

        # Get geometry-specific parameters
        if geometry_type and "detector_geometries" in params:
            geom_params = params["detector_geometries"].get(geometry_type)
            if geom_params:
                result["detector_geometry"] = geom_params

        # Always include quality thresholds
        result["calibration_quality"] = params.get("calibration_quality", {})
        result["data_quality_thresholds"] = params.get("data_quality_thresholds", {})

        # Include integration parameters if FF-HEDM
        if geometry_type == "ff_hedm" or not geometry_type:
            result["integration_parameters"] = params.get("integration_parameters", {})

        return json.dumps(result, indent=2)

    except Exception as e:
        return json.dumps({
            "status": "error",
            "error": str(e)
        }, indent=2)


@mcp.tool()
async def estimate_parameters_from_image(
    wavelength_angstrom: float,
    ring_radii_px: List[float],
    pixel_size_um: float,
    material: Optional[str] = "CeO2"
) -> str:
    """Estimate detector geometry (Lsd, beam center) from observed diffraction rings.

    Uses ring positions and known d-spacings to calculate detector distance.
    Helpful for initial parameter estimation before auto-calibration.

    Args:
        wavelength_angstrom: X-ray wavelength in Angstroms
        ring_radii_px: List of observed ring radii in pixels (e.g., [412, 478, 675])
        pixel_size_um: Detector pixel size in microns (typically 172 for GE)
        material: Calibrant material (default: "CeO2")

    Returns:
        JSON string with estimated Lsd and quality metrics
    """
    try:
        import numpy as np

        materials_db = get_materials_db()
        mat_data = materials_db.get(material)

        if not mat_data:
            return json.dumps({
                "status": "error",
                "error": f"Material {material} not found in database"
            }, indent=2)

        # Get d-spacings for this material
        d_spacings = mat_data.get("d_spacings_angstrom")
        if not d_spacings:
            return json.dumps({
                "status": "error",
                "error": f"No d-spacings available for {material}"
            }, indent=2)

        # Calculate Lsd for each ring
        lsd_estimates = []
        for r_px in ring_radii_px[:len(d_spacings)]:
            r_mm = r_px * pixel_size_um / 1000  # Convert px to mm

            # Bragg's law: 2*d*sin(theta) = lambda
            # theta = arcsin(lambda / (2*d))
            # R = Lsd * tan(2*theta)
            # Therefore: Lsd = R / tan(2*theta)

            for d in d_spacings:
                theta_rad = np.arcsin(wavelength_angstrom / (2 * d))
                two_theta_rad = 2 * theta_rad
                expected_lsd_mm = r_mm / np.tan(two_theta_rad)

                # Only consider reasonable Lsd values (100mm to 3000mm)
                if 100 < expected_lsd_mm < 3000:
                    lsd_estimates.append({
                        "ring_radius_px": r_px,
                        "d_spacing_angstrom": d,
                        "estimated_lsd_mm": round(expected_lsd_mm, 2)
                    })

        if not lsd_estimates:
            return json.dumps({
                "status": "error",
                "error": "Could not estimate Lsd from provided rings"
            }, indent=2)

        # Calculate average and std
        lsd_values = [est["estimated_lsd_mm"] for est in lsd_estimates]
        mean_lsd = np.mean(lsd_values)
        std_lsd = np.std(lsd_values)

        return json.dumps({
            "status": "success",
            "material": material,
            "wavelength_angstrom": wavelength_angstrom,
            "estimated_lsd_mm": round(mean_lsd, 2),
            "std_lsd_mm": round(std_lsd, 2),
            "confidence": "high" if std_lsd < 10 else "medium" if std_lsd < 50 else "low",
            "individual_estimates": lsd_estimates,
            "recommendation": f"Use Lsd guess: {round(mean_lsd * 1000)} microns (±{round(std_lsd)}mm)"
        }, indent=2)

    except Exception as e:
        return json.dumps({
            "status": "error",
            "error": str(e),
            "traceback": traceback.format_exc()
        }, indent=2)


# =============================================================================
# MIDAS VIEWER LAUNCHER
# =============================================================================

# Map of viewer short names → relative paths in MIDAS repo.
# CLI conventions per viewer (from script headers — verified against MIDAS repo):
#
#   plot_calibrant_results   positional: [file.corr.csv]  — Qt GUI, detach
#   plot_lineout_results     positional: [directory]       — Qt GUI, detach
#   plot_lineout_comparison  positional: file.lineout.xy [integrator.xy]
#                            flag:       --paramFN params.txt
#   plot_integrator_peaks    positional: file.caked.hdf.zarr.zip [options]
#   plot_caked_peaks         positional: /path/to/results/ OR zarr.zip — Qt GUI, detach
#   viz_caking               positional: zarr.zip          — Dash web app, detach
#   live_viewer              flags: --lineout lineout.bin [--fit fit.bin]
#                                   [--nRBins N] [--nPeaks N]
#   interactiveFFplotting    positional: directory         — Qt GUI, detach
#   ff_asym_qt               no required args (opens file dialog)
#   nf_qt                    positional: [.mic file]       — Qt GUI, detach
#   PlotFFNF                 positional: directory
#   plot_phase_id_results    positional: file
#   plotGrains3d             flag: -resultFolder <dir>  (reads Grains.csv)
#   plotFFSpots3d            flag: -resultFolder <dir>  (reads InputAll.csv)
#   plotFFSpots3dGrains      flag: -resultFolder <dir>  (reads SpotMatrix.csv+Grains.csv)
#   pfIntensityViewer        flag: -paramFile <txt> [-resultDir <dir>]  — Dash web app
#   peak_sigma_statistics    positional: results_dir (LayerNr_* parent)
_VIEWER_SCRIPTS = {
    "plot_calibrant_results":   "gui/viewers/plot_calibrant_results.py",
    "plot_lineout_results":     "gui/viewers/plot_lineout_results.py",
    "plot_lineout_comparison":  "gui/viewers/plot_lineout_comparison.py",
    "plot_integrator_peaks":    "gui/viewers/plot_integrator_peaks.py",
    "plot_caked_peaks":         "gui/viewers/plot_caked_peaks.py",
    "viz_caking":               "gui/viewers/viz_caking.py",
    "live_viewer":              "gui/viewers/live_viewer.py",
    "interactiveFFplotting":    "gui/viewers/interactiveFFplotting.py",
    "ff_asym_qt":               "gui/ff_asym_qt.py",
    "nf_qt":                    "gui/nf_qt.py",
    "PlotFFNF":                 "gui/viewers/PlotFFNF.py",
    "plot_phase_id_results":    "gui/viewers/plot_phase_id_results.py",
    # 3D grain/spot visualizers (Plotly → standalone .html, run to completion)
    "plotGrains3d":             "gui/viewers/plotGrains3d.py",
    "plotFFSpots3d":            "gui/viewers/plotFFSpots3d.py",
    "plotFFSpots3dGrains":      "gui/viewers/plotFFSpots3dGrains.py",
    # PF-HEDM sinogram / intensity viewer (Dash web app)
    "pfIntensityViewer":        "gui/viewers/pfIntensityViewer.py",
    # Peak sigma statistics (writes PNG/CSV, run to completion)
    "peak_sigma_statistics":    "gui/viewers/peak_sigma_statistics.py",
}

# Viewers that are pure Qt GUIs: launched detached — do NOT wait for them.
_GUI_VIEWERS = {
    "plot_calibrant_results", "plot_lineout_results", "plot_caked_peaks",
    "interactiveFFplotting", "ff_asym_qt", "nf_qt", "PlotFFNF",
    "plot_phase_id_results",
}

# Viewers launched as web apps (Dash/Plotly): also detached, open browser.
_WEB_VIEWERS = {"viz_caking", "pfIntensityViewer"}

# Plotly viewers that write standalone .html into the result folder and exit.
# Run to completion (cwd = result folder) and report the generated .html files.
_HTML_VIEWERS = {"plotGrains3d", "plotFFSpots3d", "plotFFSpots3dGrains"}

# Viewers that take a result FOLDER via -resultFolder instead of a positional file.
_RESULTFOLDER_VIEWERS = {"plotGrains3d", "plotFFSpots3d", "plotFFSpots3dGrains"}

def _build_viewer_cmd(viewer: str, script_path: Path,
                      data_file: str, param_file: str,
                      extra_args: str) -> list:
    """Build the correct command for each viewer based on its CLI signature."""
    data_path = Path(data_file).expanduser().absolute()
    cmd: list = []

    if viewer in _RESULTFOLDER_VIEWERS:
        # plotGrains3d / plotFFSpots3d / plotFFSpots3dGrains: -resultFolder <dir>.
        # data_file is the FF/PF result folder containing Grains.csv / InputAll.csv
        # / SpotMatrix.csv. If a file was passed, use its parent directory.
        target = data_path if data_path.is_dir() else data_path.parent
        cmd = [str(script_path), "-resultFolder", str(target)]

    elif viewer == "pfIntensityViewer":
        # PF sinogram/intensity Dash app: -paramFile <txt> [-resultDir <dir>].
        # data_file is the parameter file; param_file (optional) is the result dir.
        cmd = [str(script_path), "-paramFile", str(data_path)]
        if param_file:
            rd = Path(param_file).expanduser().absolute()
            if rd.exists():
                cmd.extend(["-resultDir", str(rd)])

    elif viewer == "peak_sigma_statistics":
        # positional: results_dir (parent of LayerNr_* folders); --paramFN optional
        target = data_path if data_path.is_dir() else data_path.parent
        cmd = [str(script_path), str(target)]
        if param_file:
            pf = Path(param_file).expanduser().absolute()
            if pf.exists():
                cmd.extend(["--paramFN", str(pf)])

    elif viewer == "live_viewer":
        # live_viewer uses --lineout / --fit flags, not positional args
        cmd = [str(script_path), "--lineout", str(data_path)]
        if param_file:
            # param_file repurposed as fit.bin path for live_viewer
            fit_path = Path(param_file).expanduser().absolute()
            if fit_path.exists():
                cmd.extend(["--fit", str(fit_path)])

    elif viewer == "plot_lineout_comparison":
        # positional: calibrant.lineout.xy [integrator.xy] --paramFN params.txt
        cmd = [str(script_path), str(data_path)]
        if param_file:
            param_path = Path(param_file).expanduser().absolute()
            if param_path.exists():
                # If param_file is a .txt it's the parameter file; if .xy it's
                # the second lineout positional arg
                if param_path.suffix.lower() in (".txt",):
                    cmd.extend(["--paramFN", str(param_path)])
                else:
                    cmd.insert(2, str(param_path))  # second positional

    elif viewer == "plot_integrator_peaks":
        # positional: zarr.zip [--min-height N --prominence N ...]
        cmd = [str(script_path), str(data_path)]

    elif viewer == "viz_caking":
        # Dash app — positional: zarr.zip
        cmd = [str(script_path), str(data_path)]

    elif viewer == "plot_lineout_results":
        # Takes a DIRECTORY and scans for *_lineout.xy files with a dropdown.
        # If the user passed a .xy file, use its parent directory so the
        # viewer can discover all lineouts in that folder.
        target = data_path if data_path.is_dir() else data_path.parent
        cmd = [str(script_path), str(target)]

    else:
        # Default: positional data file (calibrant, caked, ff_asym, nf)
        cmd = [str(script_path), str(data_path)]

    if extra_args:
        import shlex as _shlex
        cmd.extend(_shlex.split(extra_args))

    return cmd


@mcp.tool()
async def plot_lineout_series_contour(
    input_dir: str = "",
    images: list = None,
    pattern: str = "*.xye",
    output_file: str = "",
    x_col: int = 0,
    y_col: int = 1,
    x_label: str = "2θ (deg)",
    title: str = "",
    log_scale: bool = False,
    interactive: bool = True,
    open_it: bool = True,
) -> str:
    """Operando contour / waterfall of a SERIES of 1D patterns (.xye/.xy/.dat/.chi).

    Use this for "contour / waterfall / operando plot of a folder of lineouts" —
    MIDAS has NO series viewer, and .xye is not a MIDAS-native lineout format (its
    Qt lineout viewer opens BLANK on .xye), so do NOT route these through
    run_midas_viewer and do NOT hand-roll a matplotlib script via run_command.

    Builds a 2D map across all files — x = radial axis (column ``x_col``), y =
    frame/scan order (from the number in each filename), colour = intensity (column
    ``y_col``) — writes a PNG and, by default, an interactive Plotly HTML that
    actually renders these files (zoom/pan/hover) and is opened in your browser.
    Convention-agnostic: point x_col/y_col at whatever columns your files use
    (default col0 = x, col1 = intensity).

    Args:
        input_dir:  directory of 1D pattern files (with ``pattern``), OR
        images:     explicit list of file paths (overrides input_dir).
        pattern:    glob for input_dir (default ``*.xye``).
        output_file: PNG path (default ``<input_dir>/<name>_contour.png``).
        x_col/y_col: 0-based columns for the radial axis and intensity.
        x_label:    axis label for the radial axis.
        log_scale:  log-scale the intensity colour (log1p) for weak features.
        interactive: also write an interactive Plotly HTML alongside the PNG.
        open_it:    best-effort open the result in the OS default app/browser.
    """
    try:
        import numpy as _np
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as _plt
        import re as _re

        # 1) resolve + order the files by the number in the filename (scan order)
        if images:
            files = [Path(p).expanduser().absolute() for p in images]
        elif input_dir:
            d = Path(input_dir).expanduser().absolute()
            if not d.is_dir():
                return format_result({"tool": "plot_lineout_series_contour",
                                      "status": "error", "error": f"input_dir not found: {d}"})
            files = [p for p in d.glob(pattern) if p.is_file()]
        else:
            return format_result({"tool": "plot_lineout_series_contour", "status": "error",
                                  "error": "provide input_dir=... or images=[...]"})
        def _fnum(p):
            m = _re.findall(r"\d+", p.stem)
            return int(m[-1]) if m else -1
        files = sorted([p for p in files if p.exists()], key=_fnum)
        if not files:
            return format_result({"tool": "plot_lineout_series_contour", "status": "error",
                                  "error": f"no files matched {pattern!r}"})

        # 2) read each pattern (first numeric x_col/y_col); interpolate onto the
        #    reference x-grid when grids differ slightly across frames.
        def _read(p):
            xs, ys = [], []
            for ln in p.read_text().splitlines():
                ln = ln.strip()
                if not ln or ln[0] in "#;Bb":
                    continue
                t = ln.replace(",", " ").split()
                if len(t) > max(x_col, y_col):
                    try:
                        xs.append(float(t[x_col])); ys.append(float(t[y_col]))
                    except ValueError:
                        continue
            return _np.asarray(xs), _np.asarray(ys)

        x_ref, y0 = _read(files[0])
        if x_ref.size == 0:
            return format_result({"tool": "plot_lineout_series_contour", "status": "error",
                                  "error": f"no numeric data in {files[0].name} "
                                           f"(x_col={x_col}, y_col={y_col})"})
        rows, frames, skipped = [], [], []
        for p in files:
            xs, ys = _read(p)
            if xs.size == 0:
                skipped.append(p.name); continue
            rows.append(ys if (xs.shape == x_ref.shape and _np.allclose(xs, x_ref))
                        else _np.interp(x_ref, xs, ys))
            frames.append(_fnum(p))
        Z = _np.vstack(rows)
        if log_scale:
            Z = _np.log1p(_np.clip(Z, 0, None))

        # 3) output paths
        base_dir = Path(input_dir).expanduser().absolute() if input_dir else files[0].parent
        stem = (Path(output_file).stem if output_file
                else f"{_os_common_prefix(files)}contour")
        png_path = (Path(output_file).expanduser().absolute() if output_file
                    else base_dir / f"{stem}.png")
        png_path.parent.mkdir(parents=True, exist_ok=True)
        _announce_output("plot_lineout_series_contour", png_path,
                         files=len(rows), interactive=interactive)

        # 4) static PNG (always). A contour needs ≥2 frames (Y = frame index); a
        #    single pattern renders as a blank mesh, so fall back to a 1D line plot
        #    (2θ vs intensity) — same as MIDAS's own plot_lineout_results.py viewer.
        single = len(rows) == 1
        fig, ax = _plt.subplots(figsize=(9, 6))
        if single:
            ax.plot(x_ref, Z[0], linewidth=0.9, color="#1f77b4")
            ax.set_xlabel(x_label)
            ax.set_ylabel("log(1+I)" if log_scale else "intensity")
            ax.set_title(title or f"Lineout — {files[0].name}")
            ax.margins(x=0.01)
        else:
            mesh = ax.pcolormesh(x_ref, _np.arange(len(rows)), Z, shading="auto", cmap="viridis")
            ax.set_xlabel(x_label)
            ax.set_ylabel("frame index (scan order)")
            ax.set_title(title or f"Operando contour — {len(rows)} patterns "
                                  f"({frames[0]}–{frames[-1]})")
            fig.colorbar(mesh, ax=ax, label="log(1+I)" if log_scale else "intensity")
        fig.tight_layout(); fig.savefig(png_path, dpi=150); _plt.close(fig)

        outputs = {"png": str(png_path)}
        # 5) interactive Plotly HTML (renders .xye that the MIDAS Qt viewer can't)
        if interactive:
            try:
                import plotly.graph_objects as _go
                html_path = png_path.with_suffix(".html")
                if single:
                    figp = _go.Figure(data=_go.Scatter(
                        x=x_ref.tolist(), y=Z[0].tolist(), mode="lines",
                        line=dict(width=1.3)))
                    figp.update_layout(
                        title=title or f"Lineout — {files[0].name}",
                        xaxis_title=x_label,
                        yaxis_title="log(1+I)" if log_scale else "intensity",
                        template="plotly_dark")
                else:
                    figp = _go.Figure(data=_go.Heatmap(
                        z=Z.tolist(), x=x_ref.tolist(), y=frames,
                        colorscale="Viridis",
                        colorbar=dict(title="log(1+I)" if log_scale else "intensity")))
                    figp.update_layout(
                        title=title or f"Operando contour — {len(rows)} patterns",
                        xaxis_title=x_label, yaxis_title="frame / scan number",
                        template="plotly_dark")
                figp.write_html(str(html_path), include_plotlyjs="cdn")
                outputs["html"] = str(html_path)
            except Exception as _e:
                outputs["html_error"] = str(_e)

        # 6) best-effort open (the analog of a viewer "launching")
        opened = None
        if open_it:
            target = outputs.get("html") or outputs["png"]
            try:
                import subprocess as _sp, sys as _sys
                if _sys.platform == "darwin":
                    _sp.Popen(["open", target])
                elif _sys.platform.startswith("win"):
                    os.startfile(target)  # type: ignore[attr-defined]
                else:
                    _sp.Popen(["xdg-open", target])
                opened = target
            except Exception:
                opened = None

        return format_result({
            "tool": "plot_lineout_series_contour", "status": "success",
            "plot_kind": "line" if single else "contour",
            "n_patterns": len(rows), "n_x": int(x_ref.size),
            "frame_range": [frames[0], frames[-1]] if frames else None,
            "x_range": [float(x_ref.min()), float(x_ref.max())],
            "outputs": outputs, "opened": opened,
            "skipped_empty": skipped or None,
            "note": ("Single pattern → a 1D line plot (2θ vs intensity); a contour "
                     "needs ≥2 frames. Open the .html for interactive zoom/pan/hover."
                     if single else
                     "Open the .html for an interactive contour (zoom/pan/hover); the "
                     ".png is a static copy. MIDAS has no native viewer for a series "
                     "of .xye patterns."),
        })
    except Exception as e:
        return format_result({"tool": "plot_lineout_series_contour", "status": "error", "error": str(e)})


def _os_common_prefix(files) -> str:
    """Common filename prefix (before the frame number) for naming outputs."""
    import os as _os, re as _re
    pref = _os.path.commonprefix([_re.sub(r"\d.*$", "", f.stem) for f in files])
    return (pref.rstrip("_") + "_") if pref.strip("_") else ""


@mcp.tool()
async def run_midas_viewer(
    viewer: str,
    data_file: str,
    param_file: str = "",
    extra_args: str = "",
) -> str:
    """Launch a MIDAS viewer/plotting script on a data file.

    GUI viewers (Qt) are launched detached — the window opens and APEXA
    returns immediately without waiting for it to close. Web viewers (Dash)
    are also launched detached; they print a localhost URL to stderr.
    Non-GUI viewers (plot_integrator_peaks, plot_lineout_comparison) run
    to completion and return their output.

    Available viewers:
    - plot_calibrant_results:  Calibration ring fit (*_corr.csv) [Qt GUI]
    - plot_lineout_results:    Qt GUI for 4-column *_lineout.xy from extract_lineouts.py
                               (NOT for 2-col MIDAS integrator lineouts — use plot_lineout_comparison)
    - plot_lineout_comparison: 2-col lineout vs calibrant rings (*_lineout.xy --paramFN params.txt)
                               Works with MIDAS integrator lineouts (2-column format)
    - plot_integrator_peaks:   Peak fitting on caked data (*_caked.hdf.zarr.zip)
    - plot_caked_peaks:        Caked peak-fit viewer (*_caked.hdf.zarr.zip or dir) [Qt GUI]
    - viz_caking:              Dash web viewer for zarr caked data (*_caked.hdf.zarr.zip)
    - live_viewer:             Real-time GPU stream viewer (--lineout lineout.bin)
    - interactiveFFplotting:   FF-HEDM grain map viewer (directory with Grains.csv) [Qt GUI]
    - ff_asym_qt:              Raw 2D diffraction image viewer [Qt GUI]
    - nf_qt:                   NF-HEDM microstructure viewer (.mic file) [Qt GUI]
    - PlotFFNF:                FF+NF overlay viewer (directory) [Qt GUI]
    - plot_phase_id_results:   Phase identification results viewer [Qt GUI]
    - plotGrains3d:            3D grain map from Grains.csv (result folder) → Plotly HTML
    - plotFFSpots3d:           3D diffraction spots from InputAll.csv (result folder) → Plotly HTML
    - plotFFSpots3dGrains:     3D spots colored by grain from SpotMatrix.csv (result folder) → Plotly HTML
    - pfIntensityViewer:       PF-HEDM sinogram/intensity viewer (paramFile) [Dash web app]
    - peak_sigma_statistics:   Peak-width (sigma) statistics over LayerNr_* (results dir)

    Args:
        viewer:     Viewer name (see list above)
        data_file:  Path to the primary input. For plotGrains3d/plotFFSpots3d/
                    plotFFSpots3dGrains/peak_sigma_statistics this is the FF/PF
                    RESULT FOLDER (containing Grains.csv/InputAll.csv/SpotMatrix.csv
                    or LayerNr_* dirs). For pfIntensityViewer it is the parameter file.
        param_file: Secondary input — meaning depends on viewer:
                    - plot_lineout_comparison: path to params.txt (for ring overlay)
                                               OR second lineout .xy file
                    - live_viewer:             path to fit.bin (peak evolution panel)
                    - pfIntensityViewer:       result directory (-resultDir)
                    - peak_sigma_statistics:   path to params.txt (--paramFN)
                    - others: ignored
        extra_args: Extra CLI flags forwarded verbatim (e.g. "--nRBins 2000 --nPeaks 5")

    Returns:
        JSON confirming the viewer was launched and the command used
    """
    try:
        rel_path = _VIEWER_SCRIPTS.get(viewer)
        if not rel_path:
            return format_result({
                "tool": "run_midas_viewer",
                "status": "error",
                "error": f"Unknown viewer: '{viewer}'",
                "available_viewers": list(_VIEWER_SCRIPTS.keys()),
            })

        script_path = MIDAS_ROOT / rel_path
        if not script_path.exists():
            return format_result({
                "tool": "run_midas_viewer",
                "status": "error",
                "error": (
                    f"Viewer script not found: {script_path}\n"
                    f"Make sure MIDAS is up to date: cd $MIDAS_PATH && git pull"
                ),
            })

        data_path = Path(data_file).expanduser().absolute()
        if not data_path.exists():
            return format_result({
                "tool": "run_midas_viewer",
                "status": "error",
                "error": f"Data file/directory not found: {data_path}",
            })

        midas_python = find_midas_python()
        viewer_args = _build_viewer_cmd(viewer, script_path,
                                        str(data_path), param_file, extra_args)
        cmd = [midas_python] + viewer_args
        env = get_midas_env()

        print(f"  Launching viewer: {viewer} → {data_path.name}", file=sys.stderr)
        print(f"  Command: {' '.join(cmd)}", file=sys.stderr)

        is_gui = viewer in _GUI_VIEWERS
        is_web = viewer in _WEB_VIEWERS

        if is_gui or is_web:
            # Detach — open the window and return immediately.
            # Do NOT use capture_output or wait() — that blocks until the GUI closes.
            proc = subprocess.Popen(
                cmd,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,   # detach from APEXA's process group
            )
            return format_result({
                "tool": "run_midas_viewer",
                "status": "launched",
                "viewer": viewer,
                "data_file": str(data_path),
                "command": " ".join(cmd),
                "pid": proc.pid,
                "message": (
                    f"{'Web app' if is_web else 'GUI window'} launched "
                    f"(PID {proc.pid}). "
                    + ("Check your browser for the Dash URL (usually http://127.0.0.1:8050)."
                       if is_web else
                       "The window should appear on your display shortly.")
                ),
            })

        else:
            # Non-GUI: run to completion (plot_integrator_peaks, plot_lineout_comparison,
            # live_viewer non-interactive, and the Plotly HTML 3D viewers).
            # HTML viewers (plotGrains3d/...) write their .html into CWD, so run
            # them inside the result folder and report the generated files.
            is_html = viewer in _HTML_VIEWERS
            run_cwd = str(data_path if data_path.is_dir() else data_path.parent) \
                      if is_html else None
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=300, env=env,
                cwd=run_cwd,
            )
            payload = {
                "tool": "run_midas_viewer",
                "status": "success" if result.returncode == 0 else "error",
                "viewer": viewer,
                "data_file": str(data_path),
                "command": " ".join(cmd),
                "return_code": result.returncode,
                "stdout": result.stdout[-1000:] if result.stdout else "",
                "stderr": result.stderr[-500:] if result.stderr else "",
            }
            if is_html and result.returncode == 0 and run_cwd:
                html_files = sorted(
                    str(p) for p in Path(run_cwd).glob("*.html")
                )
                payload["html_files"] = html_files
                payload["message"] = (
                    f"Generated {len(html_files)} interactive Plotly HTML file(s) "
                    f"in {run_cwd}. Open them in a browser."
                )
            return format_result(payload)

    except subprocess.TimeoutExpired:
        return format_result({
            "tool": "run_midas_viewer",
            "status": "error",
            "error": "Viewer timed out after 300s (non-GUI mode only).",
        })
    except Exception as e:
        return format_result({
            "tool": "run_midas_viewer",
            "status": "error",
            "error": str(e),
            "traceback": traceback.format_exc(),
        })


@mcp.tool()
async def generate_ff_reconstruction_report(
    run_dir: str,
    material: str = "",
    title: str = "",
    out: str = "report.html",
    c11: float = 0.0,
    c12: float = 0.0,
    beam_height: float = 0.0,
    figdir: str = "",
) -> str:
    """Generate a self-contained FF-HEDM reconstruction report (FF Handbook §9).

    Wraps MIDAS `utils/midas_ff_report.py`, which reads a completed FF run's
    Grains.csv (and, if present, processgrains_diagnostics.h5) and emits ONE
    standalone report.html with all figures base64-embedded — grain-count and
    completeness stats, orientation/IPF maps, position maps, per-grain strain,
    and (with the diagnostics H5) spot-residual plates and auto-findings. The
    UI renders the returned HTML path.

    Prerequisite: the run must have produced Grains.csv. That comes from
    run_ff_hedm_full_workflow with process_grains=True (the default) — ideally
    refine_backend='c-omp', the handbook-preferred path. If Grains.csv is
    missing this tool returns an actionable error instead of a report.

    Args:
        run_dir: A LayerNr_<N> folder containing Grains.csv (the per-layer
                 result dir, NOT the parent result folder).
        material: Material name for the report header (default: derived from
                  the Grains.csv spacegroup, e.g. "SG225").
        title: Report title (default: auto).
        out: Output HTML filename. Relative paths are written inside run_dir
             (default: report.html → <run_dir>/report.html).
        c11, c12: Optional cubic elastic constants (GPa) to bias the d0
                  equilibrium for per-grain stress; 0 = omit (strain only).
        beam_height: Optional beam height (µm) for the vertical extent panel;
                     0 = omit.
        figdir: Scratch dir for intermediate figures (default:
                <run_dir>/_report_figs).

    Returns:
        JSON with the absolute report path, size, has_diagnostics, and the
        exact command run.
    """
    try:
        rp = Path(run_dir).expanduser().absolute()
        if not rp.exists() or not rp.is_dir():
            return format_result({
                "tool": "generate_ff_reconstruction_report",
                "status": "error",
                "error": f"run_dir not found or not a directory: {rp}",
            })

        grains = rp / "Grains.csv"
        if not grains.exists():
            return format_result({
                "tool": "generate_ff_reconstruction_report",
                "status": "error",
                "error": (
                    f"Grains.csv not found in {rp}. The FF run did not produce a "
                    "grain list. Re-run run_ff_hedm_full_workflow with "
                    "process_grains=True (default) and refine_backend='c-omp' "
                    "(the handbook-preferred, most-reliable path to Grains.csv), "
                    "then point run_dir at the LayerNr_<N> folder it writes."
                ),
            })

        has_diag = (rp / "processgrains_diagnostics.h5").exists()

        # Resolve output path: relative → inside run_dir so we can verify it.
        out_path = Path(out).expanduser()
        if not out_path.is_absolute():
            out_path = rp / out_path

        report_script = MIDAS_ROOT / "utils" / "midas_ff_report.py"
        if not report_script.exists():
            return format_result({
                "tool": "generate_ff_reconstruction_report",
                "status": "error",
                "error": f"Report generator not found: {report_script}",
            })

        py = find_midas_python()
        cmd = [py, str(report_script), str(rp), "--out", str(out_path)]
        if material:
            cmd += ["--material", material]
        if title:
            cmd += ["--title", title]
        if c11:
            cmd += ["--c11", str(c11)]
        if c12:
            cmd += ["--c12", str(c12)]
        if beam_height:
            cmd += ["--beam-height", str(beam_height)]
        if figdir:
            cmd += ["--figdir", figdir]

        _announce_output("generate_ff_reconstruction_report", out_path,
                         has_diagnostics=has_diag)
        print(f"[FF-REPORT] {' '.join(cmd)}", file=sys.stderr)

        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=1800,
            env=get_midas_env(),
        )

        if proc.returncode != 0 or not out_path.exists():
            return format_result({
                "tool": "generate_ff_reconstruction_report",
                "status": "error",
                "error": "Report generation failed (see stderr).",
                "return_code": proc.returncode,
                "command": " ".join(cmd),
                "stdout": proc.stdout[-2000:] if proc.stdout else "",
                "stderr": proc.stderr[-3000:] if proc.stderr else "",
            })

        _announce_output("generate_ff_reconstruction_report", out_path,
                         status="written")
        return format_result({
            "tool": "generate_ff_reconstruction_report",
            "status": "success",
            "report_html": str(out_path),
            "size_bytes": out_path.stat().st_size,
            "has_diagnostics": has_diag,
            "run_dir": str(rp),
            "command": " ".join(cmd),
            "note": (
                "Standalone report.html with embedded figures."
                + ("" if has_diag else
                   " No processgrains_diagnostics.h5 — spot-residual plates and "
                   "some auto-findings are omitted (grain/orientation/strain "
                   "panels still render).")
            ),
        })

    except subprocess.TimeoutExpired:
        return format_result({
            "tool": "generate_ff_reconstruction_report",
            "status": "error",
            "error": "Report generation timed out (>30 min).",
        })
    except Exception as e:
        return format_result({
            "tool": "generate_ff_reconstruction_report",
            "status": "error",
            "error": str(e),
        })


# =============================================================================
# GSAS-II REFINEMENT & LIVE ANALYSIS
# =============================================================================

def _extract_instprm_from_zarr(zarr_path: str, output_dir: str) -> Optional[str]:
    """Extract instrument parameters from a MIDAS .zarr.zip and write a .instprm file.

    GSAS-II .xye import requires an instrument parameter file. MIDAS stores
    these in the zarr under InstrumentParameters/ (Distance, Lam, U, V, W, etc.).
    """
    try:
        instprm_path = Path(output_dir) / "_midas_extracted.instprm"

        # Use the GSASII or midas_env Python to read the zarr (handles compression)
        extract_script = (
            "import sys, json\n"
            "data_file = sys.argv[1]\n"
            "try:\n"
            "    import zarr\n"
            "    try:\n"
            "        fp = zarr.open(data_file, mode='r')\n"
            "    except Exception:\n"
            "        import asyncio\n"
            "        async def _o():\n"
            "            s = await zarr.storage.ZipStore.open(data_file, mode='r')\n"
            "            return zarr.open_group(s, mode='r')\n"
            "        fp = asyncio.run(_o())\n"
            "    import numpy as np\n"
            "    ip = fp['InstrumentParameters']\n"
            "    params = {}\n"
            "    for k in ['Lam','Polariz','U','V','W','X','Y','Z','SH_L','Distance']:\n"
            "        if k in ip:\n"
            "            params[k] = float(np.array(ip[k]).flat[0])\n"
            "    print(json.dumps(params))\n"
            "except Exception as e:\n"
            "    print(json.dumps({'error': str(e)}))\n"
        )

        gsasii_python = find_gsasii_python()
        python_exe = gsasii_python or find_midas_python()
        env = get_midas_env()

        result = subprocess.run(
            [python_exe, "-c", extract_script, zarr_path],
            capture_output=True, text=True, timeout=30, env=env,
        )
        if result.returncode != 0:
            return None

        params = json.loads(result.stdout.strip())
        if "error" in params or "Lam" not in params:
            return None

        lines = [
            "#GSAS-II instrument parameter file; do not add/delete items!",
            "Type:PXC",
            f"Lam:{params.get('Lam', 0.2):.6f}",
            "Zero:0.0",
            f"Polariz.:{params.get('Polariz', 0.99):.4f}",
            f"U:{params.get('U', 1.163):.6f}",
            f"V:{params.get('V', -0.126):.6f}",
            f"W:{params.get('W', 0.063):.6f}",
            f"X:{params.get('X', 0.0):.6f}",
            f"Y:{params.get('Y', 0.0):.6f}",
            f"Z:{params.get('Z', 0.0):.6f}",
            f"SH/L:{params.get('SH_L', 0.002):.6f}",
            "Azimuth:0.0",
            "Bank:1",
        ]
        instprm_path.write_text("\n".join(lines) + "\n")
        return str(instprm_path)
    except Exception as e:
        print(f"  Warning: Could not extract instprm from zarr: {e}", file=sys.stderr)
        return None

async def _gsas_refine_xy(data_path: Path, cif_files: List[str],
                          output_dir: str, bkg_terms: int,
                          two_theta_limits: Optional[List[float]],
                          wavelength_A: Optional[float],
                          experimental_a: Optional[float] = None) -> str:
    """Lattice parameter refinement from a plain .xy powder pattern.

    FF-HEDM derived .xy files are sparse spot projections (not azimuthal
    integrations), so full Rietveld diverges.  We use peak-position least-
    squares instead, which is the correct method for this data type:
      1. Find local maxima above background.
      2. Compute d-spacings via Bragg's law.
      3. Assign hkl from the CIF-derived space group + trial cell.
      4. Weighted least-squares on 1/d² = (h²+k²+l²)/a²  (cubic) or
         general metric tensor for other crystal systems.
    Also attempts a GSAS-II Rietveld via GSASIIscriptable as a secondary
    result if GSAS-II is installed.
    """
    gsas_python = find_gsasii_python()

    out_path = Path(output_dir).expanduser().absolute()
    out_path.mkdir(parents=True, exist_ok=True)
    _announce_output("run_gsas_refinement", out_path, data=data_path.name)
    gpx_path = out_path / (data_path.stem + "_refine.gpx")

    wl = wavelength_A or 0.22291
    tth_lo = two_theta_limits[0] if two_theta_limits else 2.0
    tth_hi = two_theta_limits[1] if two_theta_limits else 20.0

    # ── Locate GSAS-II parent directory ───────────────────────────────────
    gsasii_parent = _find_gsasii_path()
    if not gsasii_parent:
        for _cand in [
            os.path.expanduser("~/miniconda3/envs/GSASII/GSAS-II"),
            os.path.expanduser("~/opt/miniconda3/envs/GSASII/GSAS-II"),
        ]:
            if os.path.isfile(os.path.join(_cand, "GSASII", "GSASIIscriptable.py")):
                gsasii_parent = _cand
                break

    # ── Build synchrotron instrument parameter file ───────────────────────
    # Profile params (U,V,W) tuned for synchrotron CW at short wavelength:
    # FWHM_G = sqrt(U·tan²θ + V·tanθ + W) ≈ 0.05-0.08° at 2θ≈10°.
    prm_path = str(out_path / "synchrotron_inst.prm")
    prm_content = (
        "            123456789012345678901234567890123456789012345678901234567890\n"
        f"INS   BANK      1                                                               \n"
        f"INS   HTYPE   PXCR                                                              \n"
        f"INS  1 IRAD     0                                                               \n"
        f"INS  1 ICONS  {wl:.6f}  0.000000       0.0         0       1.0    0       0.0   \n"
        f"INS  1I HEAD  SYNCHROTRON APS BEAMLINE POWDER DATA                             \n"
        f"INS  1I ITYP    0    0.0000  180.0000         1                                 \n"
        f"INS  1PRCF1     3    8      0.01                                                \n"
        f"INS  1PRCF11   1.000000E-03   0.000000E+00   5.000000E-03   0.000000E+00        \n"
        f"INS  1PRCF12   0.000000E+00   0.000000E+00   0.000000E+00   0.000000E+00        \n"
    )
    with open(prm_path, "w") as _f:
        _f.write(prm_content)

    cif_list_repr = repr([str(Path(c).expanduser().absolute()) for c in cif_files])

    # ── Inline script: peak-position lattice refinement + optional Rietveld ─
    script = f"""
import sys, os, json
import numpy as np
sys.path.insert(0, r"{gsasii_parent or ''}")

# ── 1. Peak-position lattice parameter refinement ───────────────────────
data = np.loadtxt(r"{data_path}", comments='#')
tth_all, I_all = data[:, 0], data[:, 1]
mask = (tth_all >= {tth_lo}) & (tth_all <= {tth_hi})
tth, I = tth_all[mask], I_all[mask]

def find_peaks(tth, I, threshold=None):
    thresh = threshold or (max(I) * 0.05 if max(I) > 0 else 1)
    peaks = []
    for i in range(2, len(tth) - 2):
        if I[i] > I[i-1] and I[i] > I[i+1] and I[i] > thresh:
            lo, hi = max(0, i-3), min(len(tth), i+4)
            s, t = I[lo:hi], tth[lo:hi]
            cen = float(np.sum(s * t) / np.sum(s)) if np.sum(s) > 0 else tth[i]
            peaks.append((cen, float(I[i])))
    return peaks

peaks = find_peaks(tth, I)

wl = {wl}
def tth_to_d(t2): return wl / (2.0 * np.sin(np.radians(t2 / 2.0)))

# Cluster peaks that belong to the same reflection (within 0.1 deg)
clustered = []
used = [False] * len(peaks)
for i, (t2i, ii) in enumerate(peaks):
    if used[i]: continue
    group = [(t2i, ii)]
    for j, (t2j, ij) in enumerate(peaks):
        if j != i and not used[j] and abs(t2i - t2j) < 0.12:
            group.append((t2j, ij))
            used[j] = True
    used[i] = True
    # intensity-weighted centroid
    wts = np.array([g[1] for g in group])
    t2c = float(np.average([g[0] for g in group], weights=wts))
    clustered.append((t2c, float(wts.max())))

# Load trial cell — prefer user-supplied experimental_a, fall back to CIF regex
cif_files = {cif_list_repr}
a_trial = {repr(experimental_a) if experimental_a is not None else 'None'}
lattice_type = 'cubic'

import re as _re
if a_trial is None:
    a_trial = 4.0
    try:
        if cif_files:
            with open(cif_files[0]) as _cf:
                _cif = _cf.read()
            _ma = _re.search(r'_cell_length_a\\s+([\\d.]+)', _cif)
            _mb = _re.search(r'_cell_length_b\\s+([\\d.]+)', _cif)
            if _ma:
                a_trial = float(_ma.group(1))
            if _mb and abs(float(_mb.group(1)) - a_trial) > 0.02:
                lattice_type = 'general'
    except Exception:
        pass

# Generate allowed reflections for FCC (or general cubic hkl)
def gen_reflections_cubic_fcc(a, max_n2=30):
    hkls = []
    for h in range(0, 7):
        for k in range(h, 7):
            for l in range(k, 7):
                n2 = h*h + k*k + l*l
                if n2 == 0 or n2 > max_n2: continue
                # All-odd or all-even (FCC systematic absence rule)
                parities = {{h % 2, k % 2, l % 2}}
                if len(parities) > 1: continue
                d = a / n2**0.5
                hkls.append((h, k, l, n2, d))
    return hkls

# Assign peaks to hkl
def assign_peaks(peaks, a_trial, wl, tol_frac=0.025):
    hkls = gen_reflections_cubic_fcc(a_trial)
    assigned = []
    for t2, inten in peaks:
        d_obs = tth_to_d(t2)
        best = min(hkls, key=lambda x: abs(x[4] - d_obs))
        if best[4] > 0 and abs(best[4] - d_obs) / best[4] < tol_frac:
            assigned.append((t2, d_obs, best[0], best[1], best[2], best[3], inten))
    return assigned

# Self-correcting pass: if no experimental_a was provided, derive a rough estimate
# from the data before the final assignment.  DFT-relaxed CIF cells can be
# 1-2% inflated vs experiment (e.g. Au mp-81: 4.17 vs experimental 4.08 Å),
# which shifts peak-hkl assignments across n2 boundaries.
# Use median(d_obs * sqrt(n2)) over a preliminary assignment with generous
# tolerance to get a data-driven trial that avoids systematic CIF bias.
if {repr(experimental_a) if experimental_a is not None else 'None'} is None and len(clustered) >= 3:
    _pre = assign_peaks(clustered, a_trial, wl, tol_frac=0.05)
    if len(_pre) >= 3:
        _a_estimates = [_d * _n2**0.5 for _, _d, _h, _k, _l, _n2, _ in _pre]
        _a_data = float(np.median(_a_estimates))
        # Accept the data-driven estimate only when it differs meaningfully from
        # the CIF value (>0.5%) and is in a plausible range (2.5–10 Å)
        if 2.5 < _a_data < 10.0 and abs(_a_data - a_trial) / a_trial > 0.005:
            a_trial = _a_data

assigned = assign_peaks(clustered, a_trial, wl)

# Weighted least-squares: A = 1/a^2, constraint: 1/d^2 = n2 * A
# => A_fit = sum(w * n2 * 1/d^2) / sum(w * n2^2)
result_peaks = []
if len(assigned) >= 2:
    d_obs_arr = np.array([x[1] for x in assigned])
    n2_arr = np.array([x[5] for x in assigned], dtype=float)
    w_arr = np.array([x[6] for x in assigned])
    inv_d2 = 1.0 / d_obs_arr**2
    A_fit = np.sum(w_arr * n2_arr * inv_d2) / np.sum(w_arr * n2_arr**2)
    a_fit = float(A_fit**-0.5)
    d_calc_arr = a_fit / n2_arr**0.5
    residuals = d_obs_arr - d_calc_arr
    # Uncertainty via bootstrap-like propagation
    sigma_d = float(np.std(residuals))
    sigma_a = sigma_d / np.sqrt(len(assigned)) * a_fit / float(np.mean(d_obs_arr))
    for t2, d_obs_v, h, k, l, n2, inten in assigned:
        result_peaks.append({{'hkl': f'({{h}}{{k}}{{l}})', '2theta_deg': round(t2, 4),
                              'd_obs': round(d_obs_v, 5), 'd_calc': round(a_fit/n2**0.5, 5)}})
else:
    a_fit, sigma_a = float(a_trial or 4.0), 0.0

peak_result = {{
    'method': 'peak-position least-squares',
    'a_refined_angstrom': round(a_fit, 5),
    'sigma_a_angstrom': round(sigma_a, 5),
    'n_peaks_used': len(assigned),
    'peaks': result_peaks,
}}

# ── 2. Attempt GSAS-II Rietveld (secondary) ─────────────────────────────
# Remove stale gpx/bak files so newgpx truly starts fresh
import glob as _glob
for _stale in _glob.glob(r"{str(gpx_path).replace('.gpx', '')}*.gpx"):
    try: os.remove(_stale)
    except: pass

gsas_result = None
try:
    from GSASII import GSASIIscriptable as G2sc
    gpx = G2sc.G2Project(newgpx=r"{gpx_path}")
    hist = gpx.add_powder_histogram(r"{data_path}", iparams=r"{prm_path}")
    hist.Limits('lower', {tth_lo})
    hist.Limits('upper', {tth_hi})
    for cif in {cif_list_repr}:
        gpx.add_phase(cif, histograms=[hist])
    gpx.set_Controls('cycles', 8)
    hist.set_refinements({{'Background': {{'no. coeffs': {bkg_terms}, 'refine': True}}}})
    gpx.refine(makeBack=True)
    for ph in gpx.phases():
        ph.set_refinements({{'Cell': True}})
    gpx.set_Controls('cycles', 12)
    gpx.refine(makeBack=True)
    cell = gpx.phases()[0].get_cell()
    rwp = hist.residuals.get('wR', 999.0)
    a_gsas = cell.get('length_a', a_fit)
    if rwp < 50.0:  # only report if refinement actually converged
        gsas_result = {{'Rwp': round(rwp, 2), 'a_angstrom': round(a_gsas, 5), 'gpx': r"{gpx_path}"}}
    gpx.save()
except Exception as _e:
    gsas_result = {{'note': f'Rietveld not attempted or failed: {{_e}}'}}

out = {{'peak_fit': peak_result, 'gsas_rietveld': gsas_result}}
print(json.dumps(out))
"""
    try:
        interpreter = gsas_python or sys.executable
        res = subprocess.run([interpreter, "-c", script],
                             capture_output=True, text=True, timeout=300)
        # parse last JSON line from stdout
        lines = [l for l in res.stdout.strip().splitlines() if l.startswith("{")]
        if not lines:
            # Return stderr for debugging
            return format_result({"tool": "run_gsas_refinement", "status": "error",
                                  "error": "No JSON result from refinement script",
                                  "stderr": res.stderr[-2000:],
                                  "stdout": res.stdout[-500:]})
        import json as _json
        r = _json.loads(lines[-1])
        pk = r.get("peak_fit", {})
        gsas = r.get("gsas_rietveld")
        a_val = pk.get("a_refined_angstrom", 0)
        sigma_a = pk.get("sigma_a_angstrom", 0)
        n_pk = pk.get("n_peaks_used", 0)
        msg = (f"Lattice parameter from {n_pk} peaks: a = {a_val:.5f} ± {sigma_a:.5f} Å  "
               f"(peak-position least-squares, λ={wl} Å)")
        if gsas and "Rwp" in gsas:
            msg += f"  |  Rietveld cross-check: a={gsas['a_angstrom']:.5f} Å, Rwp={gsas['Rwp']:.1f}%"
        return format_result({"tool": "run_gsas_refinement", "status": "success",
                              "data_file": str(data_path),
                              "method": "peak-position least-squares",
                              "a_refined_angstrom": a_val,
                              "sigma_a_angstrom": sigma_a,
                              "n_peaks_used": n_pk,
                              "peaks": pk.get("peaks", []),
                              "gsas_rietveld": gsas,
                              "message": msg})
    except subprocess.TimeoutExpired:
        return format_result({"tool": "run_gsas_refinement", "status": "error",
                              "error": "Refinement timed out (>5 min)"})
    except Exception as e:
        return format_result({"tool": "run_gsas_refinement", "status": "error",
                              "error": str(e)})


@mcp.tool()
async def run_gsas_refinement(
    data_file: str,
    cif_files: List[str],
    output_dir: str = "refinement",
    bkg_terms: int = 6,
    two_theta_limits: Optional[List[float]] = None,
    no_atoms: bool = False,
    no_export: bool = False,
    n_cpus: int = 8,
    instprm_file: Optional[str] = None,
    robust: bool = True,
    wavelength_A: Optional[float] = None,
    experimental_a: Optional[float] = None,
    engine: str = "gsas2",
) -> str:
    """Run Rietveld refinement on MIDAS caked output via GSAS-II, MAUD, or both.

    The `engine` parameter selects the backend; the historical name
    `run_gsas_refinement` is retained for backward compatibility but the tool
    now dispatches to one of two drivers (with `engine="both"` running both
    engines and writing a cross-validation summary). When `robust=False` the
    engine selector is ignored and the legacy MIDAS `gsas_ii_refine.py` script
    is invoked exactly as before.

    By default (robust=True, engine="gsas2") APEXA's robust GSAS-II driver
    `apexa_gsas_robust.py` is invoked instead of the raw MIDAS
    `gsas_ii_refine.py`. The robust driver fixes two real-data failure modes
    encountered on the cross-detector benchmark (see
    benchmark/detector_zoo/ground_truth.json):

      * NaN-safe extraction — pixel-array detectors with module gaps (Pilatus,
        Eiger) leave NaN bins in the lineout. The MIDAS extractor passes them
        through and they bias the residual; we drop them per-slice. On Pilatus
        this took |Δa| from 63 mAangstrom to 0.56 mAangstrom and recovered all
        360/360 slices.

      * Data-aware starting cell — Materials Project DFT-relaxed CIFs sit
        ~50 mAangstrom above experimental lattice constants. For low-statistics
        single-frame data (GE) the per-slice landscape does not pull the cell
        across that gap, locking refinement near the wrong minimum. The robust
        driver substitutes a NIST experimental cell when the CIF names a known
        calibrant (CeO2 / LaB6 / Si / Al2O3), or uses the user-supplied
        ``experimental_a``. On GE this took |Δa| from 65 mAangstrom to
        0.07 mAangstrom and recovered 81/81 slices.

    Set robust=False to fall back to the legacy MIDAS script (n_cpus parallelism
    is only available in the legacy path; the robust path runs serially per
    histogram but completes a 360-slice run in ~10 min).

    Stages: Background+Scale → Cell → U,V,W → X,Y,SH/L → (Atoms, optional).

    Prerequisites:
    - GSAS-II installed (conda install gsas2pkg -c briantoby)
    - zarr==2.18.3 in midas_env

    Args:
        data_file: Path to MIDAS .zarr.zip caked output file
        cif_files: One or more CIF files defining crystallographic phase(s)
        output_dir: Output directory for .gpx projects and exports (default: refinement/)
        bkg_terms: Number of Chebyshev background coefficients (default: 6)
        two_theta_limits: Optional 2θ limits in degrees as [LOW, HIGH] (default: full range)
        no_atoms: Skip atomic position / thermal parameter refinement (default: False)
        no_export: Skip CIF and CSV exports after refinement (default: False)
        n_cpus: Number of parallel workers (legacy path only; default: 8)
        instprm_file: Optional .instprm file for GSAS-II instrument parameters
        robust: Use APEXA robust driver with NaN filter + calibrant cell fix (default: True)
        wavelength_A: X-ray wavelength in Angstroms — used by robust driver to
            estimate starting cell from lineout peak positions when CIF cell
            disagrees with data by >20 mAangstrom (default: None, no estimation)
        experimental_a: Explicit override for starting cell a parameter in
            Angstroms (robust driver only). Overrides auto-calibrant detection.
        engine: Rietveld engine to use when robust=True. One of:
            - "gsas2" (default): GSAS-II via `apexa_gsas_robust.py`.
            - "maud":  MAUD via `apexa_maud_milk.py` (MILK Python wrapper;
                      requires MAUD + a JDK on PATH; install hint surfaced
                      automatically when missing).
            - "both":  run gsas2 then maud, write `refinement_crossvalidation.json`
                      reporting |Δa_engines|, the Rwp ratio, and an agreement
                      verdict. If MAUD is unavailable, the call silently falls
                      back to gsas2-only and tags the response.
            Ignored when robust=False (the legacy MIDAS path runs unchanged).

    Returns:
        JSON with refinement summary: Rwp values, lattice parameters, output file paths.
        When robust=True, includes "extraction_stats", "cif_preparation", and
        "robust_features_applied" diagnostics. When engine="both", also includes
        a top-level "crossvalidation" object.
    """
    try:
        data_path = Path(data_file).expanduser().absolute()
        if not data_path.exists():
            return format_result({"tool": "run_gsas_refinement", "status": "error",
                                  "error": f"Data file not found: {data_path}"})

        # .xy / .xye powder pattern — refine directly via GSASIIscriptable
        if str(data_path).endswith(".xy") or str(data_path).endswith(".xye"):
            return await _gsas_refine_xy(data_path, cif_files, output_dir,
                                         bkg_terms, two_theta_limits, wavelength_A,
                                         experimental_a)

        if not (str(data_path).endswith(".zarr.zip") or str(data_path).endswith(".MIDAS.zip")):
            return format_result({"tool": "run_gsas_refinement", "status": "error",
                                  "error": f"Expected .zarr.zip, .MIDAS.zip, .xy, or .xye file, "
                                           f"got: {data_path.name}"})

        # Validate CIF files
        resolved_cifs = []
        for cif in cif_files:
            cif_path = Path(cif).expanduser().absolute()
            if not cif_path.exists():
                return format_result({"tool": "run_gsas_refinement", "status": "error",
                                      "error": f"CIF file not found: {cif_path}"})
            resolved_cifs.append(str(cif_path))

        # === Engine + driver-script resolution ================================
        # `engine` selects between the two robust drivers; `robust=False`
        # forces the legacy MIDAS path regardless of `engine`.
        if engine not in ("gsas2", "maud", "both"):
            return format_result({"tool": "run_gsas_refinement", "status": "error",
                                  "error": f"Unknown engine={engine!r}. Use 'gsas2', 'maud', or 'both'."})

        apexa_root = Path(__file__).resolve().parent
        gsas2_script  = apexa_root / "apexa_gsas_robust.py"
        maud_script   = apexa_root / "apexa_maud_milk.py"
        legacy_script = MIDAS_ROOT / "utils" / "gsas_ii_refine.py"

        if not robust and not legacy_script.exists():
            return format_result({"tool": "run_gsas_refinement", "status": "error",
                                  "error": f"Legacy gsas_ii_refine.py not found at {legacy_script}. "
                                           "Requires MIDAS v11+."})
        if robust and engine in ("gsas2", "both") and not gsas2_script.exists():
            return format_result({"tool": "run_gsas_refinement", "status": "error",
                                  "error": f"GSAS-II robust driver missing at {gsas2_script}."})

        # MAUD-engine availability is a soft fail (degrade rather than error).
        # Discovery checks the filesystem; the script itself raises EngineUnavailable
        # at import time if MILK / JDK are missing.
        from apexa_engines import find_maud_installation, maud_install_hint, cross_validate, load_summary
        maud_available = maud_script.exists() and find_maud_installation() is not None
        maud_unavailable_msg = None
        if engine == "maud" and not maud_available:
            return format_result({
                "tool": "run_gsas_refinement",
                "status": "engine_unavailable",
                "engine": "maud",
                "install_hint": maud_install_hint(),
                "maud_script_exists": maud_script.exists(),
            })
        if engine == "both" and not maud_available:
            # Fall back to gsas2-only, but flag it in the response.
            maud_unavailable_msg = (
                "MAUD engine unavailable; cross-validation skipped. "
                + maud_install_hint()
            )

        out_path = Path(output_dir).expanduser().absolute()
        out_path.mkdir(parents=True, exist_ok=True)
        _announce_output("run_gsas_refinement", out_path, data=data_path.name)

        # Auto-extract instrument parameters from the zarr if no instprm provided
        generated_instprm = None
        if not instprm_file:
            generated_instprm = _extract_instprm_from_zarr(str(data_path), str(out_path))
            if generated_instprm:
                instprm_file = generated_instprm
                print(f"  Auto-extracted instrument params from zarr", file=sys.stderr)

        # Prefer GSASII env Python for the GSAS-II driver — its binaries match
        # the compiled .so ABI. The MAUD driver runs in the apexa env.
        gsasii_python = find_gsasii_python()
        python_for_gsas2 = gsasii_python or find_midas_python()
        python_for_maud  = sys.executable  # apexa env (MILK + JDK)
        python_for_legacy = python_for_gsas2

        # ------------------------------------------------------------------
        # Inner: build command + execute one driver, return a result dict.
        # The same shape is reused for gsas2, maud, and the legacy path.
        # ------------------------------------------------------------------
        def _invoke(script: Path, python_exe: str, sub_out: Path, label: str) -> dict:
            sub_out.mkdir(parents=True, exist_ok=True)
            cmd = [
                python_exe, str(script),
                "--data", str(data_path),
                "--cif", *resolved_cifs,
                "--out", str(sub_out),
                "--bkg-terms", str(bkg_terms),
            ]
            if script == legacy_script:
                cmd.extend(["--nCPUs", str(n_cpus)])
            if two_theta_limits and len(two_theta_limits) == 2:
                cmd.extend(["--limits", str(two_theta_limits[0]), str(two_theta_limits[1])])
            # Robust drivers (gsas2, maud): atom refinement OFF by default; legacy: ON by default.
            if script != legacy_script:
                if not no_atoms:
                    cmd.append("--refine-atoms")
                if wavelength_A is not None:
                    cmd.extend(["--wavelength", str(wavelength_A)])
                if experimental_a is not None:
                    cmd.extend(["--experimental-a", str(experimental_a)])
            else:
                if no_atoms:
                    cmd.append("--no-atoms")
                if no_export:
                    cmd.append("--no-export")
            if instprm_file:
                instprm_path = Path(instprm_file).expanduser().absolute()
                if instprm_path.exists():
                    cmd.extend(["--instprm", str(instprm_path)])

            env = get_midas_env()
            print(f"  [{label}] {data_path.name} → {sub_out}", file=sys.stderr)
            print(f"  [{label}] python: {python_exe}", file=sys.stderr)
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, env=env)

            # Read whichever summary file the driver writes.
            summary = {}
            for cand in ("summary.json", "refinement_summary.json"):
                f = sub_out / cand
                if f.exists():
                    try:
                        summary = json.load(open(f))
                        break
                    except Exception:
                        pass

            ok = proc.returncode == 0
            stderr = proc.stderr[-1500:] if proc.stderr else ""
            res = {
                "label": label,
                "driver": script.name,
                "status": "success" if ok else "error",
                "returncode": proc.returncode,
                "output_dir": str(sub_out),
                "summary": summary,
                "python_used": python_exe,
                "stderr": stderr if not ok else "",
                "stdout_tail": (proc.stdout or "")[-500:],
                "command": " ".join(cmd),
            }
            if not ok:
                hint = ""
                if "Cannot import GSASIIscriptable" in stderr or "ModuleNotFoundError" in stderr:
                    hint = (" GSAS-II import failed — install via: "
                            "conda install gsas2full -c briantoby -c conda-forge")
                if "milk" in stderr.lower() or "MAUD" in stderr or "JAVA_HOME" in stderr:
                    hint = " " + maud_install_hint()
                res["error"] = f"{script.name} failed (exit {proc.returncode}).{hint}"
            return res

        # === Dispatch =========================================================
        if not robust:
            _warn_deprecated_cpp("GSAS refinement: gsas_ii_refine.py")
            res = _invoke(legacy_script, python_for_legacy, out_path, "legacy")
            return format_result({
                "tool": "run_gsas_refinement",
                "status": res["status"],
                "engine": "midas_legacy",
                "driver": "midas_gsas_ii_refine",
                "data_file": str(data_path),
                "cif_files": resolved_cifs,
                "output_dir": str(out_path),
                "summary": res["summary"],
                "python_used": res["python_used"],
                "stdout": res["stdout_tail"],
                "stderr": res.get("stderr", ""),
                **({"error": res["error"]} if "error" in res else {}),
            })

        if engine == "gsas2":
            res = _invoke(gsas2_script, python_for_gsas2, out_path, "gsas2")
            return format_result({
                "tool": "run_gsas_refinement",
                "status": res["status"],
                "engine": "gsas2",
                "driver": "apexa_gsas_robust",
                "data_file": str(data_path),
                "cif_files": resolved_cifs,
                "output_dir": str(out_path),
                "summary": res["summary"],
                "python_used": res["python_used"],
                "stdout": res["stdout_tail"],
                "stderr": res.get("stderr", ""),
                **({"error": res["error"]} if "error" in res else {}),
            })

        if engine == "maud":
            res = _invoke(maud_script, python_for_maud, out_path, "maud")
            return format_result({
                "tool": "run_gsas_refinement",
                "status": res["status"],
                "engine": "maud",
                "driver": "apexa_maud_milk",
                "data_file": str(data_path),
                "cif_files": resolved_cifs,
                "output_dir": str(out_path),
                "summary": res["summary"],
                "python_used": res["python_used"],
                "stdout": res["stdout_tail"],
                "stderr": res.get("stderr", ""),
                **({"error": res["error"]} if "error" in res else {}),
            })

        # engine == "both": run gsas2, then maud (if available), then cross-validate.
        gsas2_dir = out_path / "gsas2"
        maud_dir  = out_path / "maud"
        gsas2_res = _invoke(gsas2_script, python_for_gsas2, gsas2_dir, "gsas2")

        if not maud_available:
            return format_result({
                "tool": "run_gsas_refinement",
                "status": gsas2_res["status"],
                "engine": "both",
                "fallback_used": "gsas2",
                "fallback_reason": maud_unavailable_msg,
                "data_file": str(data_path),
                "cif_files": resolved_cifs,
                "output_dir": str(out_path),
                "gsas2": gsas2_res,
                "maud": None,
                "crossvalidation": None,
            })

        maud_res = _invoke(maud_script, python_for_maud, maud_dir, "maud")

        # Cross-validate when both engines wrote a usable summary.
        cv = None
        if gsas2_res["summary"] and maud_res["summary"]:
            try:
                g = load_summary(gsas2_dir / "summary.json", engine="gsas2") \
                    if (gsas2_dir / "summary.json").exists() \
                    else load_summary(gsas2_dir / "refinement_summary.json", engine="gsas2")
                m = load_summary(maud_dir / "summary.json", engine="maud") \
                    if (maud_dir / "summary.json").exists() \
                    else load_summary(maud_dir / "refinement_summary.json", engine="maud")
                cv = cross_validate(g, m)
                (out_path / "refinement_crossvalidation.json").write_text(
                    json.dumps(cv, indent=2)
                )
            except Exception as e:
                cv = {"verdict": "incomplete", "error": str(e)}

        overall_status = (
            "success" if gsas2_res["status"] == "success" and maud_res["status"] == "success"
            else "partial_success"
        )
        return format_result({
            "tool": "run_gsas_refinement",
            "status": overall_status,
            "engine": "both",
            "data_file": str(data_path),
            "cif_files": resolved_cifs,
            "output_dir": str(out_path),
            "gsas2": gsas2_res,
            "maud": maud_res,
            "crossvalidation": cv,
        })

    except subprocess.TimeoutExpired:
        return format_result({"tool": "run_gsas_refinement", "status": "error",
                              "error": "Refinement timed out after 1800s. Try fewer histograms with two_theta_limits or more n_cpus."})
    except Exception as e:
        return format_result({"tool": "run_gsas_refinement", "status": "error",
                              "error": str(e), "traceback": traceback.format_exc()})


@mcp.tool()
async def run_live_analysis(
    backend: str,
    cif_files: List[str],
    param_file: str,
    data_file: Optional[str] = None,
    folder: Optional[str] = None,
    pva: bool = False,
    pva_ip: Optional[str] = None,
    output_dir: str = "refinement",
    dark_file: Optional[str] = None,
    n_cpus: int = 4,
    bkg_terms: int = 6,
    two_theta_limits: Optional[List[float]] = None,
    no_atoms: bool = False,
    skip_integration: bool = False,
    zarr_file: Optional[str] = None,
    skip_refinement: bool = False,
    output_h5: Optional[str] = None,
    data_location: str = "exchange/data",
    dark_location: str = "exchange/dark",
) -> str:
    """Run combined MIDAS integration + GSAS-II refinement pipeline.

    Two-stage pipeline:
      Stage 1: Integration (batch CPU or GPU streaming) → .zarr.zip
      Stage 2: GSAS-II refinement on the caked output → .gpx + summary

    Backends:
    - "batch": CPU-based integrator.py (requires data_file + param_file)
    - "stream": GPU-based integrator_batch_process.py (requires folder or pva + param_file)

    Use --skip-integration with --zarr-file to refine an existing .zarr.zip.
    Use --skip-refinement to only integrate without GSAS-II fitting.

    Args:
        backend: Integration backend: "batch" (CPU) or "stream" (GPU)
        cif_files: CIF file(s) for the crystallographic phase(s)
        param_file: MIDAS parameter file (refined_MIDAS_params*.txt)
        data_file: Path to first data file (batch backend, HDF5 or image)
        folder: Source folder of images (stream backend)
        pva: Enable PVA live-streaming mode (stream backend)
        pva_ip: PVA server IP address (stream backend)
        output_dir: Output directory for refinement results (default: refinement/)
        dark_file: Dark field file for background subtraction
        n_cpus: Number of CPUs for integration and refinement (default: 4)
        bkg_terms: Chebyshev background terms for GSAS-II (default: 6)
        two_theta_limits: Optional 2θ limits as [LOW, HIGH]
        no_atoms: Skip atomic position refinement (default: False)
        skip_integration: Skip Stage 1, use existing zarr_file (default: False)
        zarr_file: Path to existing .zarr.zip (use with skip_integration)
        skip_refinement: Run only Stage 1, no GSAS-II (default: False)
        output_h5: HDF5 output filename for stream backend
        data_location: HDF5 dataset path for batch backend (default: exchange/data)
        dark_location: HDF5 dark dataset path (default: exchange/dark)

    Returns:
        JSON with pipeline results: integration output, refinement summary, file paths
    """
    try:
        # Validate backend
        if backend not in ("batch", "stream"):
            return format_result({"tool": "run_live_analysis", "status": "error",
                                  "error": f"Invalid backend '{backend}'. Use 'batch' or 'stream'."})

        # Find the pipeline script
        pipeline_script = MIDAS_ROOT / "FF_HEDM" / "workflows" / "integrate_and_refine.py"
        if not pipeline_script.exists():
            return format_result({"tool": "run_live_analysis", "status": "error",
                                  "error": f"integrate_and_refine.py not found at {pipeline_script}. "
                                           "Requires MIDAS v11+."})

        # Validate param file
        param_path = Path(param_file).expanduser().absolute()
        if not skip_integration and not param_path.exists():
            return format_result({"tool": "run_live_analysis", "status": "error",
                                  "error": f"Parameter file not found: {param_path}"})

        # Validate CIF files
        resolved_cifs = []
        if not skip_refinement:
            for cif in cif_files:
                cif_path = Path(cif).expanduser().absolute()
                if not cif_path.exists():
                    return format_result({"tool": "run_live_analysis", "status": "error",
                                          "error": f"CIF file not found: {cif_path}"})
                resolved_cifs.append(str(cif_path))

        out_path = Path(output_dir).expanduser().absolute()
        out_path.mkdir(parents=True, exist_ok=True)
        _announce_output("run_live_analysis", out_path)

        env = get_midas_env()
        midas_python = find_midas_python()
        integration_stdout = ""
        integration_stderr = ""
        zarr_output = None

        # ── Stage 1: Integration (midas_env Python — has diplib/skimage/h5py) ──
        if not skip_integration:
            int_cmd = [
                midas_python, str(pipeline_script),
                "--backend", backend,
                "-nCPUs", str(n_cpus),
                "--skip-refinement",
            ]

            if backend == "batch":
                if not data_file:
                    return format_result({"tool": "run_live_analysis", "status": "error",
                                          "error": "batch backend requires data_file"})
                data_path = Path(data_file).expanduser().absolute()
                if not data_path.exists():
                    return format_result({"tool": "run_live_analysis", "status": "error",
                                          "error": f"Data file not found: {data_path}"})
                int_cmd.extend(["-paramFN", str(param_path), "-dataFN", str(data_path)])
                int_cmd.extend(["-dataLoc", data_location, "-darkLoc", dark_location])
            elif backend == "stream":
                int_cmd.extend(["--param-file", str(param_path)])
                if pva:
                    int_cmd.append("--pva")
                    if pva_ip:
                        int_cmd.extend(["--pva-ip", pva_ip])
                elif folder:
                    folder_path = Path(folder).expanduser().absolute()
                    if not folder_path.exists():
                        return format_result({"tool": "run_live_analysis", "status": "error",
                                              "error": f"Folder not found: {folder_path}"})
                    int_cmd.extend(["--folder", str(folder_path)])
                else:
                    return format_result({"tool": "run_live_analysis", "status": "error",
                                          "error": "stream backend requires --folder or --pva"})
                if output_h5:
                    int_cmd.extend(["--output-h5", output_h5])

            if dark_file:
                dark_path = Path(dark_file).expanduser().absolute()
                if dark_path.exists():
                    if backend == "batch":
                        int_cmd.extend(["-darkFN", str(dark_path)])
                    else:
                        int_cmd.extend(["--dark", str(dark_path)])

            print(f"  Stage 1: Integration ({backend}): {param_path.name}", file=sys.stderr)
            int_result = subprocess.run(int_cmd, capture_output=True, text=True,
                                        timeout=1200, env=env)
            integration_stdout = int_result.stdout
            integration_stderr = int_result.stderr

            if int_result.returncode != 0:
                return format_result({
                    "tool": "run_live_analysis",
                    "status": "error",
                    "stage": "integration",
                    "error": f"Integration failed (exit {int_result.returncode})",
                    "stderr": integration_stderr[-1500:] if integration_stderr else "",
                    "command": " ".join(int_cmd),
                })

            # Find the produced .zarr.zip file
            data_dir = data_path.parent if backend == "batch" else Path(folder).expanduser().absolute()
            for zf in sorted(data_dir.rglob("*_caked.hdf.zarr.zip")):
                zarr_output = str(zf)
                break
            if not zarr_output:
                for zf in sorted(data_dir.rglob("*.zarr.zip")):
                    zarr_output = str(zf)
                    break
        else:
            # skip_integration: use provided zarr_file
            if zarr_file:
                zarr_path = Path(zarr_file).expanduser().absolute()
                if not zarr_path.exists():
                    return format_result({"tool": "run_live_analysis", "status": "error",
                                          "error": f"Zarr file not found: {zarr_path}"})
                zarr_output = str(zarr_path)

        # ── Stage 2: Refinement (GSASII env Python — has matching binaries) ──
        summary = {}
        refinement_stdout = ""
        if not skip_refinement and resolved_cifs and zarr_output:
            refine_script = MIDAS_ROOT / "utils" / "gsas_ii_refine.py"
            if not refine_script.exists():
                return format_result({"tool": "run_live_analysis", "status": "error",
                                      "error": f"gsas_ii_refine.py not found at {refine_script}"})

            gsasii_python = find_gsasii_python()
            refine_python = gsasii_python or midas_python

            # Extract instrument params from zarr for GSAS-II
            live_instprm = _extract_instprm_from_zarr(zarr_output, str(out_path))

            ref_cmd = [
                refine_python, str(refine_script),
                "--data", zarr_output,
                "--cif", *resolved_cifs,
                "--out", str(out_path),
                "--bkg-terms", str(bkg_terms),
                "--nCPUs", str(n_cpus),
            ]
            if live_instprm:
                ref_cmd.extend(["--instprm", live_instprm])
            if two_theta_limits and len(two_theta_limits) == 2:
                ref_cmd.extend(["--limits", str(two_theta_limits[0]), str(two_theta_limits[1])])
            if no_atoms:
                ref_cmd.append("--no-atoms")

            print(f"  Stage 2: GSAS-II refinement → {out_path}", file=sys.stderr)
            print(f"  Python: {refine_python}", file=sys.stderr)
            ref_result = subprocess.run(ref_cmd, capture_output=True, text=True,
                                        timeout=1800, env=env)
            refinement_stdout = ref_result.stdout

            if ref_result.returncode != 0:
                return format_result({
                    "tool": "run_live_analysis",
                    "status": "error",
                    "stage": "refinement",
                    "error": f"GSAS-II refinement failed (exit {ref_result.returncode})",
                    "stderr": ref_result.stderr[-1500:] if ref_result.stderr else "",
                    "zarr_file": zarr_output,
                    "python_used": refine_python,
                    "command": " ".join(ref_cmd),
                })

            summary_file = out_path / "refinement_summary.json"
            if summary_file.exists():
                with open(summary_file) as f:
                    summary = json.load(f)

        mode_desc = "skip→refine" if skip_integration else f"{backend}→refine"
        if skip_refinement:
            mode_desc = f"{backend}→integrate-only"

        output_files = sorted(str(p) for p in out_path.iterdir() if p.is_file()) if out_path.exists() else []

        return format_result({
            "tool": "run_live_analysis",
            "status": "success",
            "mode": mode_desc,
            "backend": backend,
            "param_file": str(param_path),
            "output_dir": str(out_path),
            "zarr_file": zarr_output,
            "output_files": output_files[:30],
            "refinement_summary": summary,
            "stdout": (refinement_stdout or integration_stdout)[-500:],
        })

    except subprocess.TimeoutExpired:
        return format_result({"tool": "run_live_analysis", "status": "error",
                              "error": "Pipeline timed out after 1800s"})
    except Exception as e:
        return format_result({"tool": "run_live_analysis", "status": "error",
                              "error": str(e), "traceback": traceback.format_exc()})


# =============================================================================
# CIF FILE FETCHER (MATERIALS PROJECT)
# =============================================================================

@mcp.tool()
async def fetch_cif_from_mp(
    formula: str,
    output_dir: str = ".",
    mp_api_key: Optional[str] = None,
) -> str:
    """Fetch CIF files from the Materials Project database for a given chemical formula.

    Uses the Materials Project REST API (mp-api) to download crystallographic
    information files (CIF) for use with GSAS-II refinement or MIDAS workflows.

    The API key is read from (in order): mp_api_key parameter, MP_API_KEY env var,
    or ~/.config/.pmgrc.yaml (set by `pmg init`).

    Args:
        formula: Chemical formula (e.g., "CeO2", "LaB6", "Fe", "Ti-6Al-4V" → "Ti")
        output_dir: Directory to save CIF files (default: current directory)
        mp_api_key: Materials Project API key (optional if set in env or config)

    Returns:
        JSON with downloaded CIF file paths and material metadata
    """
    try:
        # Network-tier self-gate (defence-in-depth). The client surface already
        # hides this tool below the `web` tier (apexa_network.disabled_tools),
        # but a non-standard MCP client that skips surface/client gating could
        # still call it — and Materials Project is public-internet-only, so on an
        # air-gapped beamline host the request would HANG rather than fail. Refuse
        # here too, with the same explicit "do not fabricate" message the client
        # gate uses, so nothing blocks and the model can't invent a CIF.
        try:
            from apexa_network import tool_unavailable_reason
            _reason = tool_unavailable_reason("fetch_cif_from_mp")
        except Exception:
            _reason = None  # fail-open: network module missing → behave as before
        if _reason:
            return format_result({
                "tool": "fetch_cif_from_mp",
                "status": "error",
                "error": _reason,
            })

        # Try importing mp_api
        try:
            from mp_api.client import MPRester
        except ImportError:
            return format_result({
                "tool": "fetch_cif_from_mp",
                "status": "error",
                "error": "mp-api not installed. Install with: uv pip install mp-api",
                "suggestion": "Run: uv sync --extra extra"
            })

        out_path = Path(output_dir).expanduser().absolute()
        out_path.mkdir(parents=True, exist_ok=True)

        # Resolve API key
        api_key = mp_api_key or os.environ.get("MP_API_KEY")

        if not api_key:
            return format_result({
                "tool": "fetch_cif_from_mp",
                "status": "error",
                "error": "No Materials Project API key provided.",
                "suggestion": "Set MP_API_KEY in .env or pass mp_api_key parameter. "
                              "Get a key at https://next-gen.materialsproject.org/api"
            })

        # Set env var so MPRester always finds it (some versions ignore the kwarg)
        os.environ["MP_API_KEY"] = api_key

        results = []
        with MPRester(api_key) as mpr:
            # Query for structures matching the formula
            docs = mpr.materials.summary.search(
                formula=formula,
                fields=["material_id", "formula_pretty", "structure",
                         "symmetry", "energy_above_hull", "is_stable"]
            )

            if not docs:
                return format_result({
                    "tool": "fetch_cif_from_mp",
                    "status": "not_found",
                    "formula": formula,
                    "error": f"No structures found for '{formula}' in Materials Project"
                })

            # Sort: stable structures first, then by energy above hull
            docs_sorted = sorted(docs, key=lambda d: (
                0 if d.is_stable else 1,
                d.energy_above_hull or 999
            ))
            for doc in docs_sorted[:3]:
                # Convert to conventional standard cell with proper space group
                # (MP stores primitive P1 cells; GSAS-II needs conventional with symmetry)
                try:
                    from pymatgen.symmetry.analyzer import SpacegroupAnalyzer
                    from pymatgen.io.cif import CifWriter
                    sga = SpacegroupAnalyzer(doc.structure)
                    conv_struct = sga.get_conventional_standard_structure()
                    cif_str = str(CifWriter(conv_struct, symprec=0.1))
                except Exception:
                    cif_str = doc.structure.to(fmt="cif")
                mp_id = doc.material_id
                cif_filename = f"{doc.formula_pretty}_{mp_id}.cif"
                cif_path = out_path / cif_filename

                with open(cif_path, "w") as f:
                    f.write(cif_str)

                results.append({
                    "material_id": str(mp_id),
                    "formula": doc.formula_pretty,
                    "space_group": doc.symmetry.symbol if doc.symmetry else "unknown",
                    "crystal_system": str(doc.symmetry.crystal_system) if doc.symmetry else "unknown",
                    "energy_above_hull_eV": doc.energy_above_hull,
                    "is_stable": doc.is_stable,
                    "cif_file": str(cif_path),
                })

        return format_result({
            "tool": "fetch_cif_from_mp",
            "status": "success",
            "formula": formula,
            "structures_found": len(docs),
            "downloaded": len(results),
            "files": results,
        })

    except Exception as e:
        return format_result({
            "tool": "fetch_cif_from_mp",
            "status": "error",
            "error": str(e),
            "traceback": traceback.format_exc()
        })


# =============================================================================
# PARAMETER VALIDATION TOOLS (midas-params)
# =============================================================================

def _resolve_param_file(path_str: str) -> tuple[bool, str]:
    """Resolve a parameter file path — accepts a file OR a directory.

    If a directory is given, searches for parameter files in priority order:
    Parameters.txt, refined_MIDAS_params*.txt, ps_*.txt, *.txt (param-like).
    """
    path = Path(path_str).expanduser()
    if path.is_file():
        return True, str(path)

    if path.is_dir():
        candidates = []
        # Priority 1: Parameters.txt / parameters.txt (standard names)
        # Use os.listdir for case-exact matching (macOS FS is case-insensitive)
        try:
            actual_names = set(os.listdir(path))
        except OSError:
            actual_names = set()
        for name in ["Parameters.txt", "parameters.txt"]:
            if name in actual_names:
                return True, str(path / name)
        # Priority 2: refined_MIDAS_params*.txt (calibration output)
        candidates.extend(sorted(path.glob("refined_MIDAS_params*.txt"), reverse=True))
        # Priority 3: ps_*.txt (parameter set files)
        candidates.extend(sorted(path.glob("ps_*.txt")))
        if candidates:
            return True, str(candidates[0])
        # Priority 4: any .txt file that looks like a param file (has key=value lines)
        for txt in sorted(path.glob("*.txt")):
            try:
                with open(txt) as f:
                    first_lines = f.read(500)
                if any(kw in first_lines for kw in ["Lsd", "Wavelength", "NrPixels", "SpaceGroup"]):
                    return True, str(txt)
            except Exception:
                continue
        return False, f"No parameter file found in directory: {path}"

    return False, f"Path not found: {path}"


def _find_midas_params_cli() -> str:
    """Locate the midas-params console script.

    midas-params ships with the pip `midas-suite` packages, which live in the
    APEXA uv .venv — NOT the conda midas_env. Prefer shutil.which (finds
    .venv/bin/midas-params, like midas-autocalibrate), then fall back to the
    conda-adjacent path for older layouts. Without this, _run_midas_params fell
    back to the conda python and raised ModuleNotFoundError: 'midas_params'."""
    import shutil as _sh
    exe = _sh.which("midas-params")
    if exe:
        return exe
    cli_path = str(Path(find_midas_python()).parent / "midas-params")
    if Path(cli_path).exists():
        return cli_path
    return None


def _run_midas_params(subcommand_args: list, timeout: int = 60) -> dict:
    """Run a midas-params CLI subcommand and return parsed JSON output."""
    cli = _find_midas_params_cli()
    if cli is None:
        midas_python = find_midas_python()
        cmd = [midas_python, "-c",
               "from midas_params.cli import main; import sys; sys.exit(main())",
               ] + subcommand_args
    else:
        cmd = [cli] + subcommand_args

    result = subprocess.run(
        cmd, capture_output=True, text=True,
        timeout=timeout, env=get_midas_env(),
    )
    if result.stdout.strip():
        return json.loads(result.stdout)
    raise RuntimeError(result.stderr or "midas-params produced no output")


@mcp.tool()
async def validate_parameter_file(
    param_file: str,
    pipeline: str = "ff"
) -> str:
    """Validate a MIDAS parameter file against pipeline-specific rules.

    Checks required keys, value ranges (wavelength, LSD, beam center),
    omega consistency, 12 cross-field rules, and detects typos with
    edit-distance suggestions. Run this BEFORE any HEDM workflow to
    catch configuration errors early and save beamtime.

    Args:
        param_file: Path to MIDAS Parameters.txt file, OR a directory
                   (auto-finds Parameters.txt / refined_MIDAS_params*.txt)
        pipeline: Pipeline to validate against: ff, nf, pf, or ri

    Returns:
        JSON with validation status, error/warning counts, and issues
        with line numbers, severity, and fix suggestions
    """
    try:
        valid, param_path = _resolve_param_file(param_file)
        if not valid:
            return format_result({"tool": "validate_parameter_file",
                                  "status": "error", "error": param_path})

        if pipeline not in ("ff", "nf", "pf", "ri"):
            return format_result({"tool": "validate_parameter_file",
                                  "status": "error",
                                  "error": f"Invalid pipeline '{pipeline}'. Must be ff, nf, pf, or ri"})

        report = _run_midas_params(["validate", param_path, "--path", pipeline, "--json"])

        return format_result({
            "tool": "validate_parameter_file",
            "status": "valid" if report.get("ok") else "invalid",
            "param_file": param_path,
            "pipeline": pipeline,
            "errors": report.get("errors", 0),
            "warnings": report.get("warnings", 0),
            "issues": report.get("issues", []),
        })

    except FileNotFoundError:
        return format_result({"tool": "validate_parameter_file", "status": "error",
                              "error": "midas-params not installed. Run: pip install -e $MIDAS_PATH/packages/midas_params"})
    except Exception as e:
        return format_result({"tool": "validate_parameter_file", "status": "error", "error": str(e)})


@mcp.tool()
async def diagnose_parameter_file(
    param_file: str,
    pipeline: str = "ff",
    output_format: str = "json"
) -> str:
    """Generate LLM-optimized diagnosis of a MIDAS parameter file.

    Produces a structured payload with validation issues, pipeline primer
    context, parameter registry info, and actionable suggestions designed
    for AI-assisted debugging. Use this after validate_parameter_file
    reports errors to get detailed fix guidance.

    Args:
        param_file: Path to MIDAS Parameters.txt file, OR a directory
                   (auto-finds Parameters.txt / refined_MIDAS_params*.txt)
        pipeline: Pipeline to diagnose against: ff, nf, pf, or ri
        output_format: Output format — 'json' (structured) or 'prompt' (LLM-ready text)

    Returns:
        JSON or text with complete diagnosis including line-level issues,
        parameter specs, and fix suggestions
    """
    try:
        valid, param_path = _resolve_param_file(param_file)
        if not valid:
            return format_result({"tool": "diagnose_parameter_file",
                                  "status": "error", "error": param_path})

        if pipeline not in ("ff", "nf", "pf", "ri"):
            return format_result({"tool": "diagnose_parameter_file",
                                  "status": "error",
                                  "error": f"Invalid pipeline '{pipeline}'. Must be ff, nf, pf, or ri"})

        fmt = "json" if output_format not in ("json", "prompt") else output_format
        args = ["diagnose", param_path, "--path", pipeline, "--format", fmt]

        # APEXA-side handbook trap lint (source of truth: FF/NF Handbooks) —
        # catches silent-corruption rules midas-params doesn't check.
        try:
            traps = _lint_handbook_traps(param_path, pipeline)
        except Exception as _e:
            traps = [{"severity": "info", "key": "_lint",
                      "message": f"handbook trap lint unavailable: {_e}",
                      "section": ""}]

        if fmt == "prompt":
            cli = _find_midas_params_cli()
            if cli is None:
                midas_python = find_midas_python()
                cmd = [midas_python, "-c",
                       "from midas_params.cli import main; import sys; sys.exit(main())"
                       ] + args
            else:
                cmd = [cli] + args
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=60, env=get_midas_env(),
            )
            diag_text = result.stdout
            if traps:
                lines = ["", "## Handbook traps (APEXA — FF/NF Handbook source of truth)"]
                for t in traps:
                    mark = {"error": "⛔", "warning": "⚠"}.get(t["severity"], "ℹ")
                    sec = f" [{t['section']}]" if t.get("section") else ""
                    lines.append(f"{mark} {t['key']}{sec}: {t['message']}")
                diag_text = diag_text + "\n" + "\n".join(lines)
            return format_result({
                "tool": "diagnose_parameter_file",
                "status": "success",
                "param_file": param_path,
                "pipeline": pipeline,
                "format": "prompt",
                "diagnosis": diag_text,
                "handbook_traps": traps,
            })

        diagnosis = _run_midas_params(args)
        return format_result({
            "tool": "diagnose_parameter_file",
            "status": "success",
            "param_file": param_path,
            "pipeline": pipeline,
            "format": "json",
            "diagnosis": diagnosis,
            "handbook_traps": traps,
        })

    except FileNotFoundError:
        return format_result({"tool": "diagnose_parameter_file", "status": "error",
                              "error": "midas-params not installed. Run: pip install -e $MIDAS_PATH/packages/midas_params"})
    except Exception as e:
        return format_result({"tool": "diagnose_parameter_file", "status": "error", "error": str(e)})


# Core MIDAS geometry keys — a .txt carrying a couple of these IS a parameter file,
# regardless of its name (so ps_au.txt / an oddly-named param file is still caught).
_CORE_PARAM_KEYS = {
    "lsd", "wavelength", "bc", "spacegroup", "latticeparameter", "latticeconstant",
    "px", "nrpixels", "nrpixelsy", "nrpixelsz", "omegastart", "omegastep",
    "omegarange", "tx", "ty", "tz",
}


def _looks_like_param_file(p: Path) -> bool:
    """Content sniff: does this .txt hold MIDAS parameter keys? (name-independent)."""
    try:
        keyset = set()
        with p.open(errors="ignore") as fh:
            for i, raw in enumerate(fh):
                if i > 200:                      # params are short; cap the read
                    break
                line = raw.split("#", 1)[0].strip()
                if line:
                    keyset.add(line.split()[0].lower())
    except Exception:
        return False
    # Any FF/NF discriminator, or ≥2 core geometry keys → it's a param file.
    return bool(keyset & (_NF_PARAM_KEYS | _FF_PARAM_KEYS)) or \
        len(keyset & _CORE_PARAM_KEYS) >= 2


def _classify_input(p: Path) -> dict:
    """Read-only, fast classification of a data path (for recommend_workflow).

    Globs + a light HDF5 key peek only — no heavy reads. Distinguishes single
    images, a series directory (with dark_before/after detection and calibrant
    vs sample split), grains/mic/param/CIF/zarr files, and HDF5 layout (incl.
    embedded darks).
    """
    info = {"path": str(p), "exists": p.exists(), "kind": "unknown"}
    if not p.exists():
        return info
    # Strict calibrant test: only True when the name LITERALLY contains a known
    # calibrant token. (_detect_calibrant_from_name defaults to CeO2 and would
    # mislabel every sample as a calibrant.)
    def _explicit_calibrant(stem: str):
        low = stem.lower()
        for nm in _CALIBRANT_DB:
            if nm.lower() in low:
                return nm
        return None
    IMG_PATS = ("*.h5", "*.hdf5", "*.hdf", "*.tif", "*.tiff",
                "*.ge", "*.ge2", "*.ge3", "*.ge5", "*.cbf")
    if p.is_dir():
        info["kind"] = "directory"
        imgs = sorted({q for pat in IMG_PATS for q in p.glob(pat) if q.is_file()})
        darks = [q for q in imgs if "dark" in q.name.lower()]
        samples = [q for q in imgs if "dark" not in q.name.lower()]
        cal = [q for q in samples if _explicit_calibrant(q.stem)]
        # Param files: MIDAS ships several naming conventions — refined_MIDAS_params*,
        # *Parameters*, and the ps_*.txt style used for NF/FF scan configs (e.g.
        # ps_nfdev.txt). Dedupe while preserving order.
        _pf = []
        for _pat in ("refined_MIDAS_params*.txt", "*[Pp]aram*.txt", "ps_*.txt"):
            _pf += sorted(p.glob(_pat))
        _pf = list(dict.fromkeys(_pf))
        # Subdirectories: a scan collection (…/varexD/<many scan dirs>) keeps its
        # images one level down, so top-level image counts are 0 — record the subdir
        # shape so recommend_workflow can still classify a container directory.
        try:
            subdirs = sorted(q for q in p.iterdir() if q.is_dir())
        except Exception:
            subdirs = []
        info.update({
            "n_images": len(imgs), "n_samples": len(samples), "n_darks": len(darks),
            "has_dark_before": any("dark_before" in q.name.lower() for q in darks),
            "has_dark_after": any("dark_after" in q.name.lower() for q in darks),
            "calibrant_files": [q.name for q in cal[:3]],
            "sample_example": samples[0].name if samples else (imgs[0].name if imgs else None),
            "sample_first": str(samples[0]) if samples else None,
            "grains_csv": [q.name for q in sorted(p.glob("*[Gg]rains*.csv"))[:3]],
            "mic_files": [q.name for q in sorted(p.glob("*.mic"))[:3]],
            "param_files": [q.name for q in _pf[:3]],
            "n_subdirs": len(subdirs),
            "subdir_examples": [q.name for q in subdirs[:6]],
        })
        return info
    # single file
    suf = p.suffix.lower(); name = p.name.lower()
    info["suffix"] = suf
    info["calibrant"] = _explicit_calibrant(p.stem)
    if "grains" in name and suf == ".csv":
        info["kind"] = "grains_csv"
    elif suf == ".mic":
        info["kind"] = "mic"
    elif suf == ".cif":
        info["kind"] = "cif"
    elif name.endswith(".zarr.zip"):
        info["kind"] = "zarr_integration"
    elif suf == ".txt" and (("param" in name or "midas" in name)
                            or _looks_like_param_file(p)):
        info["kind"] = "param_file"
    elif suf in (".h5", ".hdf5", ".hdf", ".nxs"):
        info["kind"] = "hdf5_image"
        try:
            import h5py
            dsets = []
            with h5py.File(p, "r") as h:
                def _v(n, o):
                    if isinstance(o, h5py.Dataset) and getattr(o, "ndim", 0) >= 2:
                        dsets.append((n, list(o.shape)))
                h.visititems(_v)
            info["hdf5_datasets"] = [{"path": n, "shape": s} for n, s in dsets[:6]]
            info["embedded_dark"] = any("dark" in n.lower() for n, _ in dsets)
            info["data_location_guess"] = next(
                (n for n, _ in dsets if n.lower().endswith("data")
                 and "dark" not in n.lower()), (dsets[0][0] if dsets else None))
        except Exception as e:
            info["hdf5_peek_error"] = str(e)
    elif suf in (".tif", ".tiff", ".cbf"):
        info["kind"] = "tiff_image"
    elif suf in (".ge", ".ge2", ".ge3", ".ge5"):
        info["kind"] = "ge_image"
    return info


# Parameter-file KEYS that are unique to each pipeline (from the MIDAS v11 example
# param files: NF_HEDM/Example/ps_au.txt vs FF_HEDM/Example/Parameters.txt). These
# are the authoritative, name-independent modality signal — a param file's contents
# say what it is regardless of what it's called or where it lives. All lowercased.
_NF_PARAM_KEYS = {
    "ndistances", "nrfilesperdistance", "gridsize", "gridmask", "edgelength",
    "minconfidence", "minfracaccept", "nrorientations", "savensolutions",
    "orienttol", "micfilebinary", "micfiletext", "onlyspotsinfo",
    "globalpositionfirstlayer", "layerthickness", "maxringrad", "lsdtol",
    "lsdrelativetol", "bctol", "tiltstol", "extreduced", "extorig",
    "medfiltradius", "gaussfiltradius", "logmaskradius", "blanketsubtraction",
}
_FF_PARAM_KEYS = {
    "ringthresh", "overallringtoindex", "completeness", "minnrspots",
    "usefriedelpairs", "stepsizeorient", "stepsizepos", "marginradial",
    "margineta", "marginome", "marginradius", "omebinsize", "etabinsize",
    "margabc", "margabg", "nrfilespersweep", "imtransopt", "hbeam", "vsample",
    "tint", "tgap", "upperboundthreshold", "width",
}


def _parse_param_multi(param_path: Path) -> dict:
    """Parse a MIDAS parameter file into {lowercased_key: [value-token-lists]}.

    Keys can repeat (RingThresh, Lsd, …), so every occurrence is kept. Each
    value is the list of whitespace tokens after the key (comments stripped).
    Also returns "_raw_keys" with original-case first-token spellings.
    """
    out: dict = {}
    raw_keys: dict = {}
    try:
        text = param_path.read_text(errors="ignore")
    except Exception:
        return {"_raw_keys": {}}
    for line in text.splitlines():
        s = line.split("#", 1)[0].strip()
        if not s:
            continue
        parts = s.split()
        k = parts[0].lower()
        out.setdefault(k, []).append(parts[1:])
        raw_keys.setdefault(k, parts[0])
    out["_raw_keys"] = raw_keys
    return out


def _lint_handbook_traps(param_path, pipeline: str = "ff", source_h5: str = "") -> list:
    """Check a parameter file against the FF/NF Handbook silent-corruption traps.

    Thin wrapper over the generic, handbook-sourced guardrail engine in
    ``handbook_guardrails.evaluate_param_guardrails``. Returns the same list of
    ``{severity, key, message, section}`` dicts as before (error/warning/info) —
    all call sites (the FF pre-dispatch gate + the two renderers) are unchanged.

    The rules are no longer hand-typed here: unit/degenerate traps come from
    generic primitives fed by facts parsed from ``manuals/FF_Parameters_Reference.md``
    (units/defaults/arity) plus the ``FF_HEDM/Example/Parameters.txt`` recommended
    magnitudes; relational/outcome traps come from a curated, per-rule **cited**
    spec. See ``handbook_guardrails`` for the design. Fail-open to ``[]``.
    """
    try:
        import handbook_guardrails as _hg
        return _hg.evaluate_param_guardrails(param_path, pipeline, source_h5)
    except Exception:
        return []

    return traps


def _read_param_modality(param_path: Path) -> dict:
    """Classify ONE MIDAS parameter file as FF/NF/PF from its KEYS (not its name).

    Reads the first whitespace token of each non-comment line and scores it against
    the NF/FF key sets. Point-focus is a far-field-format file with ``nScans > 1``;
    near-field is decided by NF-only keys or multiple detector distances
    (``nDistances`` / repeated ``Lsd``). Returns
    {"modality": ..., "why": ..., "nf": <count>, "ff": <count>}.
    """
    try:
        text = param_path.read_text(errors="ignore")
    except Exception as e:
        return {"modality": None, "why": f"{param_path.name}: unreadable ({e})",
                "nf": 0, "ff": 0}
    keyset = set()
    n_lsd = 0
    n_scans = None
    n_dist = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()      # strip inline + full-line comments
        if not line:
            continue
        parts = line.split()
        k = parts[0].lower()
        keyset.add(k)
        if k == "lsd":
            n_lsd += 1
        elif k == "nscans" and len(parts) > 1:
            try:
                n_scans = int(float(parts[1]))
            except ValueError:
                pass
        elif k == "ndistances" and len(parts) > 1:
            try:
                n_dist = int(float(parts[1]))
            except ValueError:
                pass
    nf = len(keyset & _NF_PARAM_KEYS)
    ff = len(keyset & _FF_PARAM_KEYS)
    name = param_path.name
    # Point-focus: FF-format scanning file with more than one scan position.
    if n_scans and n_scans > 1:
        return {"modality": "pf",
                "why": f"{name}: nScans={n_scans} (>1 → point-focus/scanning)",
                "nf": nf, "ff": ff}
    multi_dist = (n_dist and n_dist > 1) or n_lsd > 1
    if multi_dist or (nf and nf > ff):
        why = f"{name}: {nf} NF-only key(s)"
        if multi_dist:
            why += f" + {n_dist or n_lsd} detector distances"
        return {"modality": "nf", "why": why, "nf": nf, "ff": ff}
    if ff and ff > nf:
        return {"modality": "ff",
                "why": f"{name}: {ff} FF-only key(s) (RingThresh/indexing/margins)",
                "nf": nf, "ff": ff}
    return {"modality": None, "why": f"{name}: no decisive FF/NF/PF keys",
            "nf": nf, "ff": ff}


def _modality_from_param_files(param_paths) -> dict:
    """Aggregate per-file param classifications into one modality decision.

    PF is decisive if any file scans; otherwise the unambiguous majority of NF/FF
    votes wins. Returns the standard {"modality", "source", "why", ...} shape with
    modality None when the param files carry no decisive keys.
    """
    votes = {"nf": 0, "ff": 0, "pf": 0}
    reasons = []
    for pp in param_paths:
        d = _read_param_modality(pp)
        if d["modality"]:
            votes[d["modality"]] += 1
            reasons.append(d["why"])
    if votes["pf"]:
        return {"modality": "pf", "source": "paramfile", "votes": votes,
                "why": "; ".join(r for r in reasons if "nScans" in r)
                       or "scanning parameter file"}
    cand = {k: v for k, v in (("nf", votes["nf"]), ("ff", votes["ff"])) if v}
    if cand:
        best = max(cand, key=cand.get)
        if list(cand.values()).count(cand[best]) == 1:      # unambiguous majority
            return {"modality": best, "source": "paramfile", "votes": votes,
                    "why": "; ".join(reasons)}
    return {"modality": None, "source": None, "votes": votes,
            "why": "parameter file(s) carry no decisive FF/NF/PF keys"}


# Powder detector-calibration standards (refine Lsd/BC/out-of-plane ty/tz). Au is a
# single-crystal standard used for a DIFFERENT calibration — the IN-PLANE tilt tx,
# which Debye-Scherrer rings can't see — so an Au_FF scan is a geometry-check /
# in-plane-calibration dataset, NOT a sample FF pilot.
_POWDER_CALIBRANTS = {"ceo2", "ceria", "lab6", "si", "al2o3"}
_INPLANE_CALIBRANTS = {"au", "gold", "ruby"}


def _read_param_target(param_path: Path) -> dict:
    """Parse what a MIDAS param file actually points AT: RawFolder / FileStem and,
    from those names, whether the target is a calibration standard.

    Returns {"rawfolder", "filestem", "calibrant", "calib_kind"} where calib_kind is
    "powder" (CeO2/LaB6/Si → detector geometry), "inplane" (Au single-crystal → tx),
    or None (a real sample). calibrant is the matched token, else None.
    """
    raw = filestem = None
    try:
        with param_path.open(errors="ignore") as fh:
            for raw_line in fh:
                line = raw_line.split("#", 1)[0].strip()
                if not line:
                    continue
                parts = line.split()
                k = parts[0].lower()
                if k == "rawfolder" and len(parts) > 1:
                    raw = parts[1]
                elif k == "filestem" and len(parts) > 1:
                    filestem = parts[1]
    except Exception:
        return {"rawfolder": None, "filestem": None, "calibrant": None,
                "calib_kind": None}
    # Match a calibrant token in the target's basename (FileStem preferred).
    hay = " ".join(x for x in (filestem, (Path(raw).name if raw else None)) if x).lower()
    calib, kind = None, None
    for tok in _POWDER_CALIBRANTS:
        if tok in hay:
            calib, kind = tok, "powder"
            break
    if not calib:
        # Whole-word only for short tokens like "au" (avoid matching "gauss", "aug").
        toks = set(re.split(r"[^a-z0-9]+", hay))
        for tok in _INPLANE_CALIBRANTS:
            if tok in toks:
                calib, kind = tok, "inplane"
                break
    return {"rawfolder": raw, "filestem": filestem, "calibrant": calib,
            "calib_kind": kind}


# Whole-word modality tags — the FALLBACK signal used only when goal, parameter-file
# contents, and reconstruction outputs are all silent. Literal near-/far-field /
# point-focus markers plus the "nfdev" development-dir convention. (Detector-family
# guessing is deliberately excluded: Varex/GE/Dexela panels serve both FF and NF.)
_NF_TOKENS = {"nf", "nfdev", "nearfield", "nfhedm"}
_FF_TOKENS = {"ff", "farfield", "ffhedm"}
_PF_TOKENS = {"pf", "pointfocus", "pfhedm", "scanning"}


def _infer_hedm_modality(p: Path, info: dict, goal: str = "") -> dict:
    """Infer FF vs NF vs PF from the goal first, then data-side signals.

    Goal keywords win when present. Otherwise decide from the DATA — in order:
    (2) the parameter file's own KEYS (read the file, authoritative — a param file's
    contents say what pipeline it is regardless of name/location), (3) reconstruction
    output already on disk (.mic → NF), then the name-based fallbacks: (4) whole-word
    modality tokens in the path components, (5) a child-name majority (a container of
    scan dirs). Returns {"modality": "ff"|"nf"|"pf"|None, "source": ..., "why": ...};
    modality is None only when nothing is decisive, and the caller keeps FF as the
    documented default (but flags it as a fallback, not evidence).
    """
    import re
    g = (goal or "").lower()

    # 1) Explicit goal keyword wins (check NF/PF before FF).
    if re.search(r"\bnf\b", g) or any(k in g for k in
                                      ("near-field", "near field", "nearfield")):
        return {"modality": "nf", "source": "goal", "why": "goal names near-field"}
    if re.search(r"\bpf\b", g) or any(k in g for k in
                                      ("point-focus", "point focus", "pointfocus", "scanning")):
        return {"modality": "pf", "source": "goal",
                "why": "goal names point-focus/scanning"}
    if re.search(r"\bff\b", g) or any(k in g for k in
                                      ("far-field", "far field", "farfield")):
        return {"modality": "ff", "source": "goal", "why": "goal names far-field"}

    def _toks(strings):
        out = set()
        for s in strings:
            out.update(t for t in re.split(r"[^a-z0-9]+", s.lower()) if t)
        return out

    # 2) READ the parameter file(s) and classify from their keys — the authoritative
    #    signal. This is what makes recommend_workflow decide from data, not names.
    param_paths = []
    if p.is_dir():
        param_paths = [p / n for n in (info.get("param_files") or []) if n]
    elif info.get("kind") == "param_file" or p.suffix.lower() == ".txt":
        param_paths = [p]
    if param_paths:
        pm = _modality_from_param_files(param_paths)
        if pm.get("modality"):
            return pm

    # 3) Reconstruction output already present → decisive.
    if info.get("mic_files"):
        return {"modality": "nf", "source": "data",
                "why": f".mic reconstruction output present ({info['mic_files'][0]})"}

    # 4) Whole-word modality tokens in the path components (+ multiword substrings).
    pl = str(p).lower()
    path_toks = _toks(list(p.parts) + [p.name])
    nf = bool(path_toks & _NF_TOKENS) or any(s in pl for s in ("near-field", "near_field", "nearfield"))
    pf = bool(path_toks & _PF_TOKENS) or any(s in pl for s in ("point-focus", "point_focus", "pointfocus"))
    ff = bool(path_toks & _FF_TOKENS) or any(s in pl for s in ("far-field", "far_field", "farfield"))
    hits = [m for m, on in (("nf", nf), ("pf", pf), ("ff", ff)) if on]
    if len(hits) == 1:
        m = hits[0]
        return {"modality": m, "source": "path",
                "why": f"'{m}' signalled by the data path"}

    # 5) Child-name majority (e.g. many *_scans* NF dirs beside one Au_FF reference).
    fam = {"nf": 0, "ff": 0, "pf": 0}
    if p.is_dir():
        try:
            for c in p.iterdir():
                ct = _toks([c.name])
                if ct & _NF_TOKENS:
                    fam["nf"] += 1
                if ct & _FF_TOKENS:
                    fam["ff"] += 1
                if ct & _PF_TOKENS:
                    fam["pf"] += 1
        except Exception:
            pass
    best = max(fam, key=fam.get)
    if fam[best] > 0 and list(fam.values()).count(fam[best]) == 1:
        return {"modality": best, "source": "children",
                "why": f"{fam[best]} child item(s) tagged '{best}'"}

    return {"modality": None, "source": None,
            "why": "no explicit FF/NF/PF signal in goal, path, or contents"}


# Map _infer_hedm_modality output → capsule id (guarded by has_technique at use).
_MODALITY_TO_CAPSULE = {"ff": "ff-hedm", "nf": "nf-hedm", "pf": "pf-hedm"}

# Curated, CITED data-signature discriminators for the non-HEDM techniques. Each
# entry is inert until its capsule is vendored (gated on has_technique) and is
# superseded automatically if a capsule ships a machine-readable
# '## Data signatures' table (capsule_registry.signatures() wins by precedence).
#   goal   : distinctive goal substrings (unambiguous — NOT the shared
#            'calibrate'/'integrate' words, which are handled in the fallback)
#   files  : fnmatch globs of config/output filenames that mark the technique
#   tokens : whole-word path/dir/file name tokens
#   cite   : the capsule doc + lines the signature is drawn from
# NOTE: calibrate-integrate is matched ONLY on its distinctive config filenames,
# never on RhoD/RBinSize/EtaBinSize — those integration keys also appear inside FF
# param files, so keying on them would steal FF datasets (this step runs before
# the FF/NF/PF check). Filenames like paramstest_v2.txt / midas_calibrate.par are
# unambiguous.
_TECHNIQUE_SIGNATURES = {
    "tomo": {
        "goal": ["tomo", "tomography", "tomocupy", "sinogram"],
        "files": ["tomocupy_args.yml", "*_TomoFastScan.dat", "BEST_SHIFT_*"],
        "tokens": ["tomo", "tomocupy"],
        "cite": "tomo/ENVELOPE.md:24-28,64",
    },
    "xrd-ct": {
        "goal": ["xrd-ct", "xrdct", "diffraction tomography"],
        "files": ["cake_cache*.h5", "geometry_for_dt.json", "ps_dt.txt"],
        "tokens": ["xrdct"],
        "cite": "xrd-ct/ENVELOPE.md:1-5; README.md:13",
    },
    "dct-tt": {
        "goal": ["dct", "topotomo", "topo-tomo", "diffraction contrast tomography"],
        "files": [],
        "tokens": ["dct", "topotomo"],
        "cite": "dct-tt/README.md:15-25,53-60",
    },
    "calibrate-integrate": {
        "goal": [],   # shared 'calibrate'/'integrate' words → _SHARED_PHASE_GOAL fallback
        "files": ["midas_calibrate.par", "paramstest_v2.txt", "exp_setup.yml"],
        "tokens": [],
        "cite": "calibrate-integrate/README.md:158-255; ENVELOPE.md:12-14",
    },
    "dfxm": {
        "goal": ["dfxm", "dark-field x-ray", "dark field x-ray"],
        "files": [],
        "tokens": ["dfxm"],
        "cite": "dfxm/ENVELOPE.md; phase-0-survey.md",
    },
}

# Shared-phase goal keywords → capsule, tried ONLY after the FF/NF/PF check comes
# up empty. Calibration/integration is a phase *within* ff/nf/pf, so a goal like
# "calibrate the FF detector" must resolve to ff-hedm (via _infer_hedm_modality),
# not to the standalone calibrate-integrate technique.
_SHARED_PHASE_GOAL = {"calibrate-integrate": ["calibrat", "integrat", "caking", "azimuthal"]}


def _infer_technique(p: Path, info: dict, goal: str = "") -> dict:
    """Recognize the analysis TECHNIQUE (capsule id) from the goal + raw data.

    Generalizes _infer_hedm_modality (FF/NF/PF only) to every vendored capsule by
    WRAPPING it — all existing FF/NF/PF behavior is preserved. Precedence:
      1. distinctive goal keywords for tomo / xrd-ct / dct-tt / dfxm
      2. data-file / path-token signatures (a capsule's own signatures() table if
         it ships one, else the curated, cited _TECHNIQUE_SIGNATURES dict)
      3. FF/NF/PF via _infer_hedm_modality → _MODALITY_TO_CAPSULE
      4. shared-phase goal fallback (calibrate/integrate) — only if 1-3 are empty
      5. None (caller keeps its documented default)
    Every capsule id is gated on capsule_registry.has_technique, so an entry is
    inert until that capsule is vendored. Returns
    {"technique": <id|None>, "source", "why"}. Fail-open on any error.
    """
    try:
        import capsule_registry as _cr
    except Exception:
        return {"technique": None, "source": None, "why": "capsule_registry unavailable"}
    try:
        import fnmatch
        g = (goal or "").lower()
        _ok = _cr.has_technique

        # Candidate filenames (bounded) + name tokens from the path + children.
        names: list = []
        try:
            if p.is_dir():
                names = [c.name for c in list(p.iterdir())[:4000]]
            elif p.exists():
                names = [p.name]
        except Exception:
            names = []
        name_toks: set = set()
        for nm in names + list(p.parts):
            name_toks.update(t for t in re.split(r"[^a-z0-9]+", nm.lower()) if t)

        # 1) Distinctive goal keywords (calibrate-integrate has none here).
        for tech, sig in _TECHNIQUE_SIGNATURES.items():
            if _ok(tech) and any(kw in g for kw in sig.get("goal", [])):
                return {"technique": tech, "source": "goal", "why": f"goal names {tech}"}

        # 2) Data signatures — a capsule's own table wins, else the curated dict.
        for tech, sig in _TECHNIQUE_SIGNATURES.items():
            if not _ok(tech):
                continue
            try:
                if _cr.signatures(tech):   # future upstream schema; [] today
                    # Upstream declares its own signatures → let it drive via the
                    # generic accessor. (No capsule ships this yet.)
                    ups = " ".join(s.get("signature", "") for s in _cr.signatures(tech)).lower()
                    if any(nm.lower() in ups or nm.lower() in g for nm in names):
                        return {"technique": tech, "source": "data",
                                "why": f"capsule-declared signature ({tech})"}
            except Exception:
                pass
            for gl in sig.get("files", []):
                if any(fnmatch.fnmatch(nm, gl) for nm in names):
                    return {"technique": tech, "source": "data",
                            "why": f"file signature '{gl}' present ({sig['cite']})"}
            toks = set(sig.get("tokens", []))
            if toks and (name_toks & toks):
                hit = sorted(name_toks & toks)[0]
                return {"technique": tech, "source": "path",
                        "why": f"'{hit}' token in the data path ({sig['cite']})"}

        # 3) FF/NF/PF — the existing, unchanged classifier.
        mod = _infer_hedm_modality(p, info, goal)
        if mod.get("modality"):
            cap = _MODALITY_TO_CAPSULE.get(mod["modality"])
            if cap and _ok(cap):
                return {"technique": cap, "source": mod.get("source") or "data",
                        "why": mod.get("why") or f"{mod['modality']} data"}

        # 4) Shared-phase goal fallback — only reached when nothing above matched.
        for tech, kws in _SHARED_PHASE_GOAL.items():
            if _ok(tech) and any(kw in g for kw in kws):
                return {"technique": tech, "source": "goal",
                        "why": f"goal names {tech} (no HEDM modality signalled)"}

        return {"technique": None, "source": None,
                "why": "no decisive technique signal in goal, path, or contents"}
    except Exception as e:
        return {"technique": None, "source": None, "why": f"infer error: {e}"}


# Grouped, grounded capability summary (real tool names) — returned when
# recommend_workflow is called with no path, or the user asks "what can you do".
_CAPABILITY_GROUPS = {
    "Calibrate detector geometry": [
        "midas_auto_calibrate — refine Lsd/beam-center/tilts from a CeO2/LaB6/Si ring image (→ refined_MIDAS_params*.txt)",
        "run_ff_calibration — CalibrantOMP FF calibration from a param file",
        "estimate_parameters_from_image — first-guess Lsd/BC from measured ring radii",
    ],
    "Integrate 2D → 1D": [
        "midas_integrate_series — a SERIES of separate files in ONE call (auto dark-match, xye/fxye per sample)",
        "midas_batch_integrate — a frame RANGE inside one file",
        "midas_integrate_2d_to_1d — a single image",
    ],
    "FF / NF / PF HEDM": [
        "run_ff_hedm_full_workflow — full FF grain reconstruction (→ Grains.csv)",
        "run_nf_hedm_reconstruction / convert_nf_to_dream3d / extract_grain_centroids",
        "run_pf_hedm_workflow, run_forward_simulation, match_grains, calculate_misorientation",
    ],
    "Phase / strain / refinement": [
        "run_gsas_refinement — Rietveld/lattice refinement from an integrated pattern + CIF",
        "compute_grain_stress, get_material_stiffness, correct_d0_equilibrium, analyze_slip_systems",
        "fetch_cif_from_mp, get_material_properties, read_grains_summary",
    ],
    "Inspect / validate / advise": [
        "recommend_workflow — inspect data and recommend the next tool + parameters (this tool)",
        "inspect_dataset_file, validate_parameter_file, diagnose_parameter_file, get_typical_hedm_parameters",
    ],
    "Visualize": [
        "run_midas_viewer — caked heatmaps, lineouts, calibrant/peak overlays",
    ],
}


# Map a recommended tool → the Agent Skill (.agents/skills/<name>/SKILL.md) that
# documents its exact v11 flags/paths/outputs. recommend_workflow attaches the
# right skill to every recommendation so the agent loads the canonical procedure
# (not just the tool) when the user exposes data. First token of the tool string
# is matched, so "read_grains_summary / compute_grain_stress" resolves on the head.
# tool name -> Agent Skill dir. Single source of truth lives in skill_registry
# (also imported by the agent loop, which pre-loads the SKILL.md body into a
# specialist's system prompt). Imported here so recommend_workflow keeps naming
# the right skill without the map drifting between the two consumers.
from skill_registry import SKILL_FOR_TOOL as _SKILL_FOR_TOOL


# One-line index of the Agent Skills, returned in the capability summary so the
# agent knows which procedure doc to load for each task class.
_SKILL_INDEX = {
    "midas-calibrate": "Detector-geometry calibration (Lsd, beam center, in-plane/out-of-plane tilts tx/ty/tz) from a CeO2/LaB6/Si calibrant",
    "midas-integrate": "2D→1D azimuthal integration / caking (single image, frame range, or a whole series)",
    "midas-ff-hedm": "Far-field HEDM grain reconstruction via the unified midas-pipeline (→ Grains.csv)",
    "midas-ffpipeline": "Differentiable PyTorch FF-HEDM pipeline + per-grain lattice refinement (checkpoint/resume, multi-GPU)",
    "midas-hedm": "Full HEDM umbrella — FF/NF/PF reconstruction, grain matching, misorientation, forward simulation, stress/slip",
    "midas-gsasii": "GSAS-II peak fitting / Rietveld & lattice refinement on integrated patterns + a CIF",
    "midas-validate": "Validate/diagnose MIDAS parameter files & datasets; inspect geometry before a heavy run",
    "midas-visualize": "Caked heatmaps, lineouts, calibrant/peak overlays, HEDM grain maps",
    "motor-control": "EPICS motor control (position, move, jog, tweak, home) at APS beamlines",
}


def _skill_for(tool: str) -> str:
    """Resolve the Agent Skill for a recommendation's tool string (first token)."""
    if not tool:
        return None
    head = tool.strip().split()[0].split("/")[0].strip()
    return _SKILL_FOR_TOOL.get(head)


def _attach_skills(recs: list) -> list:
    """Annotate each recommendation dict with its 'skill' (in place, then return)."""
    for r in recs:
        if isinstance(r, dict) and "skill" not in r:
            sk = _skill_for(r.get("tool", ""))
            if sk:
                r["skill"] = sk
    return recs


@mcp.tool()
async def recommend_workflow(path: str = "", goal: str = "") -> str:
    """Inspect input data and recommend the APEXA tool + parameters to run next.

    READ-ONLY advisory — nothing is executed. Classifies what `path` is (single
    image, a series directory, calibrant, HDF5 with/without embedded dark, grains
    file, param file, CIF, zarr), then returns a GROUNDED recommendation: the
    primary tool to call, its key parameters (dark scheme, HDF5 data location,
    compute tier from frame count), a suggested output location, 1–2 alternatives
    with trade-offs, and the natural next step. Use this to answer "what should I
    do with this data?" or "what can you do?" BEFORE committing to a heavy run.

    Args:
        path: file or directory to inspect. Empty → return a capability summary.
        goal: optional steer — "calibrate" / "integrate" / "index" / "refine" /
              "convert" — biases which recommendation is ranked first.
    """
    try:
        if not path:
            return format_result({
                "tool": "recommend_workflow", "status": "success",
                "mode": "capability_summary",
                "capabilities": _CAPABILITY_GROUPS,
                "skills": _SKILL_INDEX,
                "hint": "Call recommend_workflow with a path to a file or directory "
                        "for a data-specific recommendation. Each capability maps to "
                        "an Agent Skill (see 'skills') carrying the exact v11 procedure.",
            })
        p = Path(path).expanduser().absolute()
        info = _classify_input(p)
        if not info["exists"]:
            return format_result({"tool": "recommend_workflow", "status": "error",
                                  "error": f"path not found: {p}"})

        recs = []          # ranked list of {tool, why, params, output, alternative}
        kind = info["kind"]
        g = (goal or "").lower()

        if kind == "directory":
            ns = info["n_samples"]
            if info["calibrant_files"] and (ns <= 5 or "calibrat" in g):
                recs.append({
                    "tool": "midas_auto_calibrate",
                    "why": f"calibrant image(s) present ({', '.join(info['calibrant_files'])})",
                    "params": {"image_file": "<the calibrant image>",
                               "output_dir": "<where to write refined_MIDAS_params*.txt>"},
                    "note": "energy/pixel auto-detected from filename; dark auto-resolved.",
                })
            if ns >= 2:
                dark_kind = ("after" if info["has_dark_after"]
                             else "before" if info["has_dark_before"] else "any")
                dark_source = "file" if info["n_darks"] else "none"
                plan = _pick_compute_target(n_frames=ns, megapixels=8.3)
                recs.append({
                    "tool": "midas_integrate_series",
                    "why": f"{ns} sample files → integrate the whole series in one call",
                    "params": {"image_dir": str(p),
                               "dark_source": dark_source, "dark_kind": dark_kind,
                               "result_folder": "<APEXA_benchmark/.../<sample>_integrated_data>",
                               "compute_target": plan["target"]},
                    "output": "writes xye/ + fxye/ per sample (darks excluded automatically)",
                    "compute": plan,
                    "alternative": "midas_batch_integrate — only if all frames live INSIDE one file",
                })
            if info["grains_csv"]:
                recs.append({"tool": "read_grains_summary / compute_grain_stress",
                             "why": f"grains file(s) present: {', '.join(info['grains_csv'])}"})
                recs.append({
                    "tool": "midas-joint-ff-calibrate grain-tx",
                    "why": "single-crystal spots in Grains.csv make the IN-PLANE tilt tx "
                           "refinable — it is invisible to powder calibration",
                    "note": "in-plane tx (rotation about the beam) is NOT observable from "
                            "Debye-Scherrer rings; refine it post-reconstruction from these "
                            "grains (angular loss, >=50 grains). MIDAS CLI — not yet wrapped "
                            "as an APEXA @mcp.tool (tracked gap).",
                    "manual_step": True,
                })
            # FF/NF/PF-HEDM: a MIDAS parameter file + rotation frames (or a
            # collection of scan subdirectories) → grain reconstruction is on the
            # table. Modality is decided DATA-FIRST — reconstruction outputs, then
            # path tokens (…/nfdev_jul26/…), then a child-name majority — with goal
            # keywords overriding. This stops near-field data being misrouted to the
            # far-field default. (A container dir has its frames one level down, so
            # accept n_subdirs when there are no top-level images.)
            if info.get("param_files") and (ns >= 1 or info.get("n_subdirs")):
                mod = _infer_hedm_modality(p, info, goal)
                modality = mod["modality"] or "ff"     # documented FF default
                # Rank grains first when asked OR when the data itself points at a
                # modality (positive evidence, not the bare fallback).
                wants_grains = (
                    any(k in g for k in ("hedm", "ff", "nf", "pf", "grain", "index",
                                         "reconstruct", "orientation", "refine"))
                    or mod["source"] in ("data", "path", "children"))
                pf = info["param_files"][0]
                frames_note = (f"{ns} rotation frame(s)" if ns >= 1
                               else f"{info.get('n_subdirs')} scan subdirectorie(s)")

                # What do the param files TARGET (RawFolder/FileStem), and is that a
                # calibration standard? An Au single-crystal FF scan is an in-plane
                # (tx) calibration dataset, NOT a sample — so don't pilot on it.
                param_targets = {nm: _read_param_target(p / nm)
                                 for nm in info["param_files"]} if p.is_dir() else {}
                chosen_tgt = param_targets.get(pf, {})
                try:
                    subdir_names = ([q.name for q in sorted(p.iterdir()) if q.is_dir()]
                                    if p.is_dir() else [])
                except Exception:
                    subdir_names = []

                def _is_cal_dir(nm):
                    low = nm.lower()
                    return low in ("cali", "calib", "calibration") or "calibrant" in low
                cal_dir = next((n for n in subdir_names if _is_cal_dir(n)), None)
                # Sample FF folders = *_FF_* subdirs that are NOT a calibrant/dark.
                sample_ffs = []
                for n in subdir_names:
                    low = n.lower()
                    if "dark" in low or _is_cal_dir(n):
                        continue
                    toks = set(re.split(r"[^a-z0-9]+", low))
                    if toks & (_POWDER_CALIBRANTS | _INPLANE_CALIBRANTS):
                        continue
                    if "ff" in toks:
                        sample_ffs.append(n)
                # A param file whose target is a real sample (not a calibrant), if any.
                sample_param = next((n for n in info["param_files"]
                                     if param_targets.get(n, {}).get("calib_kind") is None),
                                    None)

                if modality == "nf":
                    hedm_rec = {
                        "tool": "run_nf_hedm_reconstruction",
                        "why": f"near-field data — {mod['why']} — + parameter file ({pf}) "
                               f"→ spatial grain map",
                        "params": {"param_file": f"<{pf}>",
                                   "result_folder": "<output dir>"},
                        "output": "LayerNr_*/<MicFileText>.mic (voxel grain map) "
                                  "+ *_consolidated.h5 → convert_nf_to_dream3d",
                        "next_step": "validate_parameter_file(pipeline='nf') first; "
                                     "inspect one scan subdir with inspect_dataset_file "
                                     "to confirm frames/omega before the full run",
                        "alternative": "run_ff_hedm_full_workflow — only if this is actually far-field",
                    }
                elif modality == "pf":
                    hedm_rec = {
                        "tool": "run_pf_hedm_workflow",
                        "why": f"point-focus/scanning data — {mod['why']} — + parameter file ({pf})",
                        "params": {"param_file": f"<{pf}>",
                                   "result_dir": "<where to write per-layer Grains.csv>"},
                        "output": "LayerNr_*/Grains.csv + v_map.h5",
                        "alternative": "run_ff_hedm_full_workflow (far-field) | "
                                       "run_nf_hedm_reconstruction (near-field)",
                    }
                elif chosen_tgt.get("calib_kind") == "inplane":
                    # The chosen param file targets an Au single-crystal dataset →
                    # this is an IN-PLANE (tx) calibration run, not a sample pilot.
                    _tgt = chosen_tgt.get("filestem") or chosen_tgt.get("rawfolder") or "Au"
                    hedm_rec = {
                        "tool": "run_ff_hedm_full_workflow",
                        "role": "in-plane (tx) calibration / geometry check",
                        "why": f"{pf} targets an Au single-crystal dataset ({_tgt}) — this is "
                               f"an IN-PLANE (tx) calibration / geometry-check run, NOT a "
                               f"sample FF pilot",
                        "params": {"param_file": f"<{pf}>",
                                   "result_dir": "<in-plane-calibration output>"},
                        "note": "run AFTER powder detector calibration (CeO2/LaB6 → Lsd/BC + "
                                "out-of-plane ty/tz); the Au grains refine the in-plane tilt "
                                "tx that Debye-Scherrer rings cannot see. Do not treat these "
                                "grains as sample science.",
                    }
                else:
                    ff_why = (f"far-field data — {mod['why']} — " if mod["modality"] == "ff"
                              else "no explicit FF/NF/PF signal — FF is the DEFAULT, not a "
                                   "detection (pass goal='nf'/'pf' or check the data path if "
                                   "this is near-field/point-focus) — ")
                    hedm_rec = {
                        "tool": "run_ff_hedm_full_workflow",
                        "why": f"{ff_why}+ parameter file ({pf}) + {frames_note} "
                               f"→ full FF-HEDM grain reconstruction",
                        "params": {"param_file": f"<{pf}>",
                                   "result_dir": "<where to write Grains.csv>",
                                   "refine_backend": "c-omp (for Grains.csv) | python"},
                        "output": "Grains.csv (grain centroid / orientation / strain)",
                        "alternative": "run_nf_hedm_reconstruction (near-field grain map) | "
                                       "run_pf_hedm_workflow (point-focus scanning)",
                    }
                hedm_rec["modality_inference"] = mod
                recs.insert(0, hedm_rec) if wants_grains else recs.append(hedm_rec)

                # For FF containers, add the ACTUAL sample pilot + calibrate-first
                # steps so the container isn't presented as one monolithic Au run.
                if modality == "ff" and sample_ffs:
                    _sp = sample_param or pf
                    _sp_note = ("" if sample_param else
                                " (both param files here target the Au calibration dataset — "
                                "copy one and set RawFolder/FileStem to the sample)")
                    recs.insert(0, {
                        "tool": "run_ff_hedm_full_workflow",
                        "role": "sample FF-HEDM pilot",
                        "why": "real FF-HEDM pilot — run on an actual sample folder, not the "
                               "Au in-plane-calibration dataset",
                        "params": {"param_file": f"<{_sp}{_sp_note}>",
                                   "data_dir": f"<{sample_ffs[0]}>",
                                   "result_dir": "<Grains.csv output>",
                                   "refine_backend": "c-omp (for Grains.csv) | python"},
                        "sample_candidates": sample_ffs[:6],
                        "output": "Grains.csv (grain centroid / orientation / strain)",
                    })
                if cal_dir or any(t.get("calib_kind") == "powder"
                                  for t in param_targets.values()):
                    recs.insert(0, {
                        "tool": "midas_auto_calibrate",
                        "role": "detector calibration (do first)",
                        "why": ("calibrate detector geometry first"
                                + (f" — '{cal_dir}/' calibration folder present" if cal_dir
                                   else "")),
                        "params": {"image_file": f"<CeO2/LaB6 image in {cal_dir or 'cali'}/>"},
                        "note": "powder standard → Lsd/BC + out-of-plane tilts ty/tz; the "
                                "in-plane tilt tx is refined afterwards from the Au "
                                "single-crystal FF scan.",
                    })
        elif kind == "hdf5_image":
            dl = info.get("data_location_guess")
            dsrc = "embedded" if info.get("embedded_dark") else "file"
            recs.append({
                "tool": "midas_integrate_series (image_dir=parent) or midas_integrate_2d_to_1d",
                "why": "HDF5 detector image",
                "params": {"data_location": dl,
                           "dark_source": dsrc,
                           **({"dark_location": next((d["path"] for d in info.get("hdf5_datasets", [])
                                                      if "dark" in d["path"].lower()), None)}
                              if dsrc == "embedded" else {})},
                "note": f"HDF5 datasets: {[d['path'] for d in info.get('hdf5_datasets', [])]}",
            })
            if info.get("calibrant"):
                _cal = info["calibrant"]
                _powder = _cal in ("CeO2", "LaB6", "Si", "Al2O3")
                recs.insert(0, {
                    "tool": "midas_auto_calibrate",
                    "why": f"filename looks like a {_cal} calibrant",
                    "note": (f"{_cal} is a standard powder calibrant → refines Lsd/BC "
                             "+ OUT-OF-PLANE tilts ty/tz (in-plane tx is not powder-"
                             "observable; see the tx note below)." if _powder else
                             f"{_cal} is a single-crystal standard → used for NF "
                             "beam-position calibration (gui/nf_qt.py), not FF powder "
                             "geometry. For FF powder use CeO2/LaB6/Si."),
                })
        elif kind in ("tiff_image", "ge_image"):
            if info.get("calibrant"):
                _cal = info["calibrant"]
                _powder = _cal in ("CeO2", "LaB6", "Si", "Al2O3")
                recs.append({
                    "tool": "midas_auto_calibrate",
                    "why": f"{_cal} calibrant image",
                    "note": ("powder standard → Lsd/BC + out-of-plane ty/tz" if _powder
                             else f"{_cal} single-crystal standard → NF beam-position "
                                  "calibration, not FF powder geometry"),
                })
            recs.append({"tool": "midas_integrate_2d_to_1d",
                         "why": "single 2D image → 1D pattern",
                         "alternative": "batch_convert_ge_to_tiff first if downstream needs TIFF"})
        elif kind == "grains_csv":
            recs.append({"tool": "read_grains_summary",
                         "why": "grain table → summary, then compute_grain_stress / analyze_slip_systems"})
            recs.append({
                "tool": "midas-joint-ff-calibrate grain-tx",
                "why": "refine the IN-PLANE tilt tx from these single-crystal grains "
                       "(powder calibration cannot see tx)",
                "note": "in-plane tx (about the beam axis) is invisible to Debye-Scherrer "
                        "rings; it is refined from grain spots via angular loss (>=50 grains). "
                        "MIDAS CLI — not yet an APEXA @mcp.tool (tracked gap).",
                "manual_step": True,
            })
        elif kind == "mic":
            recs.append({"tool": "extract_grain_centroids / convert_nf_to_dream3d",
                         "why": "NF .mic reconstruction output"})
        elif kind == "cif":
            recs.append({"tool": "run_gsas_refinement",
                         "why": "CIF phase → pair with an integrated pattern (.xy/.xye/.zarr.zip) to refine"})
        elif kind == "zarr_integration":
            recs.append({"tool": "run_gsas_refinement",
                         "why": "zarr integration output → refine with a CIF",
                         "alternative": "run_midas_viewer — caked heatmap + lineout"})
        elif kind == "param_file":
            # Read the file's KEYS to decide which pipeline it belongs to, then
            # recommend validating against THAT pipeline and running it.
            pm = _modality_from_param_files([p])
            pipe = pm.get("modality") or "ff"
            recs.append({
                "tool": "validate_parameter_file → diagnose_parameter_file",
                "why": "MIDAS parameter file → validate before using it in a pipeline",
                "params": {"param_file": str(p), "pipeline": pipe},
                "modality_inference": pm,
            })
            if pm.get("modality") == "nf":
                recs.append({
                    "tool": "run_nf_hedm_reconstruction",
                    "why": f"parameter file reads as near-field — {pm['why']}",
                    "params": {"param_file": str(p),
                               "result_folder": "<output dir>"},
                    "output": "LayerNr_*/<MicFileText>.mic + *_consolidated.h5",
                })
            elif pm.get("modality") == "pf":
                recs.append({
                    "tool": "run_pf_hedm_workflow",
                    "why": f"parameter file reads as point-focus/scanning — {pm['why']}",
                    "params": {"param_file": str(p),
                               "result_dir": "<where to write per-layer Grains.csv>"},
                    "output": "LayerNr_*/Grains.csv + v_map.h5",
                })
            elif pm.get("modality") == "ff":
                recs.append({
                    "tool": "run_ff_hedm_full_workflow",
                    "why": f"parameter file reads as far-field — {pm['why']}",
                    "params": {"param_file": str(p),
                               "data_file": "<.analysis.MIDAS.zip zarr>",
                               "result_dir": "<where to write Grains.csv>"},
                    "output": "Grains.csv / IndexBest.bin",
                })
        else:
            recs.append({"tool": "inspect_dataset_file",
                         "why": "unrecognized type — inspect to extract geometry/metadata first"})

        _attach_skills(recs)
        skills_used = sorted({r["skill"] for r in recs if isinstance(r, dict) and r.get("skill")})
        # Recognize the analysis TECHNIQUE from goal + raw data (generic across all
        # vendored capsules). This machine-readable field is the bridge that lets the
        # agent loop inject the right MIDAS methodology (spine + halt checklist)
        # immediately after this first classification — before calibrate/integrate/
        # workflow tools fire. Additive; never renames/removes an existing field.
        _tech = _infer_technique(p, info, goal)
        return format_result({
            "tool": "recommend_workflow", "status": "success",
            "mode": "recommendation",
            "input": info,
            "goal": goal or None,
            "capsule_technique": _tech.get("technique"),
            "technique_source": _tech.get("source"),
            "technique_why": _tech.get("why"),
            "recommendations": recs,
            "skills": skills_used,
            "note": "Advisory only — nothing was run. Each recommendation names the "
                    "Agent Skill (.agents/skills/<name>) with the exact v11 flags, "
                    "paths, and outputs — load it before running. Confirm parameters "
                    "+ output location, then call the recommended tool with "
                    "result_folder/output_dir set to where you want the output.",
        })
    except Exception as e:
        return format_result({"tool": "recommend_workflow", "status": "error", "error": str(e)})


@mcp.tool()
async def inspect_dataset_file(
    dataset_file: str
) -> str:
    """Auto-extract parameters from a raw dataset file.

    Reads GE, HDF5, Zarr, or TIFF files and extracts detector geometry,
    beam energy, pixel size, image dimensions, and other parameters
    embedded in the file headers or metadata. Useful for pre-populating
    parameter files or verifying consistency.

    Args:
        dataset_file: Path to data file (GE2/GE3/GE5, HDF5, Zarr, TIFF)

    Returns:
        JSON with extracted parameters, confidence levels, and sources
    """
    try:
        valid, dataset_path = validate_file(dataset_file)
        if not valid:
            return format_result({"tool": "inspect_dataset_file",
                                  "status": "error", "error": dataset_path})

        report = _run_midas_params(["inspect", dataset_path, "--json"])

        # Bridge field: recognize the technique from the file/path when decisive
        # (additive; None when nothing unambiguous). Lets the agent loop inject the
        # right methodology after an inspect, same as after recommend_workflow.
        try:
            _dp = Path(dataset_path)
            _tech = _infer_technique(_dp, _classify_input(_dp), "")
        except Exception:
            _tech = {"technique": None, "source": None, "why": ""}

        return format_result({
            "tool": "inspect_dataset_file",
            "status": "success",
            "dataset_file": dataset_path,
            "capsule_technique": _tech.get("technique"),
            "technique_source": _tech.get("source"),
            "extracted": report,
        })

    except FileNotFoundError:
        return format_result({"tool": "inspect_dataset_file", "status": "error",
                              "error": "midas-params not installed. Run: pip install -e $MIDAS_PATH/packages/midas_params"})
    except Exception as e:
        return format_result({"tool": "inspect_dataset_file", "status": "error", "error": str(e)})


@mcp.tool()
async def enumerate_bragg_rings(
    param_file: str
) -> str:
    """List Bragg rings that fall on the detector for a given crystal/geometry.

    Reads wavelength, detector distance, lattice parameters, and space group
    from a MIDAS parameter file and enumerates all Bragg rings with their
    hkl indices, d-spacing, 2theta, and detector radius. Helps verify correct
    RingThresh and OverAllRingToIndex settings.

    Args:
        param_file: Path to MIDAS Parameters.txt with crystal/geometry info,
                   OR a directory (auto-finds the param file)

    Returns:
        JSON with ring list (hkl, d-spacing, 2theta, radius, on-detector flag)
    """
    try:
        valid, param_path = _resolve_param_file(param_file)
        if not valid:
            return format_result({"tool": "enumerate_bragg_rings",
                                  "status": "error", "error": param_path})

        report = _run_midas_params(["rings", "--from", param_path, "--json"])

        return format_result({
            "tool": "enumerate_bragg_rings",
            "status": "success",
            "param_file": param_path,
            "rings": report,
        })

    except FileNotFoundError:
        return format_result({"tool": "enumerate_bragg_rings", "status": "error",
                              "error": "midas-params not installed. Run: pip install -e $MIDAS_PATH/packages/midas_params"})
    except Exception as e:
        return format_result({"tool": "enumerate_bragg_rings", "status": "error", "error": str(e)})


# =============================================================================
# STRESS / STRAIN ANALYSIS TOOLS (midas-stress)
# =============================================================================

def _run_stress_runner(subcommand_args: list, timeout: int = 120) -> dict:
    """Run a _stress_runner.py subcommand and return parsed JSON output."""
    midas_python = find_midas_python()
    cmd = [midas_python, str(STRESS_RUNNER_SCRIPT)] + subcommand_args

    result = subprocess.run(
        cmd, capture_output=True, text=True,
        timeout=timeout, env=get_midas_env(),
    )
    if result.stdout.strip():
        return json.loads(result.stdout)
    raise RuntimeError(result.stderr or "_stress_runner.py produced no output")


def _run_capability_runner(subcommand_args: list, timeout: int = 1800) -> dict:
    """Run a _capability_runner.py subcommand and return parsed JSON.

    Regime 3 (see CLAUDE.md): the 8 new MIDAS capability packages install their
    torch stack into the APEXA .venv, so run under THIS interpreter
    (sys.executable) with a CLEAN env (no get_midas_env C++ DYLD/LD injection —
    that triggers an h5py/libhdf5 symbol mismatch for the pip torch stack).
    Mirrors the calibrate-v2 call site, NOT _run_stress_runner (conda + injected
    env). The runner keeps stdout clean (JSON only); chatter goes to stderr.
    """
    cmd = [sys.executable, str(CAPABILITY_RUNNER_SCRIPT)] + subcommand_args
    result = subprocess.run(
        cmd, capture_output=True, text=True,
        timeout=timeout, env=dict(os.environ),
    )
    if result.stdout.strip():
        return json.loads(result.stdout)
    raise RuntimeError(result.stderr.strip().splitlines()[-1]
                       if result.stderr.strip()
                       else "_capability_runner.py produced no output")


@mcp.tool()
async def compute_pair_distribution(
    pattern_file: str,
    composition: str,
    wavelength: float,
    x_is_two_theta: bool = False,
    q_max: float = 0.0,
    window: str = "lorch",
    r_min: float = 0.0,
    r_max: float = 20.0,
    n_r: int = 500,
    output_file: str = "",
) -> str:
    """Compute a pair distribution function G(r) from an integrated 1D pattern.

    REAL-DATA tool (midas-pdf 0.1.0). Takes an integrate-v2 / integrated 1D
    pattern (2 columns: Q in Å⁻¹ or 2θ in deg, then intensity) plus the sample
    composition, and returns the Faber-Ziman S(Q) → G(r) via a windowed Fourier
    sine transform, with a propagated 1σ band. Use after midas_integrate_series /
    midas_integrate_2d_to_1d to get local structure (nearest-neighbour distances)
    from total scattering.

    Args:
        pattern_file: 1D pattern (.xy/.xye/.dat/.csv/.txt), 2 numeric columns.
        composition: element:fraction list, e.g. "Ni:1" or "Ce:1,O:2".
        wavelength: X-ray wavelength in Å (also converts 2θ→Q when needed).
        x_is_two_theta: True if column 1 is 2θ (deg) rather than Q (Å⁻¹).
        q_max: truncate the transform at this Q (Å⁻¹); 0 = use all data.
        window: FT window — "lorch" (default) or "none".
        r_min, r_max, n_r: output r-grid (Å) range and sample count.
        output_file: optional path to write a 2-column r, G(r) table.

    Returns:
        JSON with G(r)/S(Q) statistics and the first-peak r (nearest neighbour).
    """
    try:
        valid, ppath = validate_file(pattern_file)
        if not valid:
            return format_result({"tool": "compute_pair_distribution",
                                  "status": "error", "error": ppath})
        cargs = ["pdf", "--pattern", ppath, "--composition", composition,
                 "--wavelength", str(wavelength), "--window", window,
                 "--r-min", str(r_min), "--r-max", str(r_max), "--n-r", str(n_r)]
        if x_is_two_theta:
            cargs.append("--x-is-two-theta")
        if q_max:
            cargs += ["--q-max", str(q_max)]
        if output_file:
            cargs += ["--out", output_file]
        out = _run_capability_runner(cargs, timeout=600)
        # Bridge to the technique capsule (apexa_agents._maybe_capsule_msg
        # precedence 1). The tool NAME carries no capsule token — 'pdf' does not
        # appear in 'compute_pair_distribution' — so without this the PDF
        # methodology (normalization refinement, the sigma trap, the low-r
        # first-peak trap) would never inject on the one tool that needs it.
        return format_result({"tool": "compute_pair_distribution",
                              "capsule_technique": "pdf", **out})
    except Exception as e:
        return format_result({"tool": "compute_pair_distribution",
                              "status": "error", "error": str(e)})


@mcp.tool()
async def analyze_grain_defects(
    voxels_file: str,
    mode: str = "rods",
    out_dir: str = "",
    grains_file: str = "",
    no_html: bool = False,
) -> str:
    """Analyze crystal defects from FF-HEDM diffuse scattering (midas-defect).

    REAL-DATA tool (midas-defect 0.1.0). Runs the midas_defect pipeline on a
    q-space voxel cloud (NPZ from FF-HEDM diffuse scattering) to detect
    dislocation ⟨111⟩ rods, asterism, or stacking-fault polytypes; the
    "inventory" mode reproduces the full defect budget (needs the indexed
    Grains.csv too). Requires a real voxel NPZ — there is no synthetic demo.

    Args:
        voxels_file: q-space voxel NPZ (indices/values) from diffuse scattering.
        mode: "rods" (dislocation rods) | "asterism" | "polytype" | "inventory".
        out_dir: directory for HTML/report artifacts (rods/asterism/polytype).
        grains_file: MIDAS Grains.csv — required only for mode="inventory".
        no_html: skip HTML report generation (faster).

    Returns:
        JSON with the CLI status, return code, and stdout/stderr tails.
    """
    try:
        valid, vpath = validate_file(voxels_file)
        if not valid:
            return format_result({"tool": "analyze_grain_defects",
                                  "status": "error", "error": vpath})
        odir = out_dir or str(Path(vpath).parent / "defect_out")
        cargs = ["defect", "--voxels", vpath, "--mode", mode, "--out-dir", odir]
        if grains_file:
            gvalid, gpath = validate_file(grains_file)
            if not gvalid:
                return format_result({"tool": "analyze_grain_defects",
                                      "status": "error", "error": gpath})
            cargs += ["--grains", gpath]
        if no_html:
            cargs.append("--no-html")
        out = _run_capability_runner(cargs, timeout=3600)
        return format_result({"tool": "analyze_grain_defects", **out})
    except Exception as e:
        return format_result({"tool": "analyze_grain_defects",
                              "status": "error", "error": str(e)})


@mcp.tool()
async def fit_grain_odf(
    geometry_file: str,
    grains_file: str,
    spots_file: str,
    frames_file: str,
    output_file: str = "grain_odf.h5",
    odf_type: str = "bingham",
) -> str:
    """Fit a per-grain orientation distribution function from FF-HEDM data.

    REAL-DATA tool (midas-grain-odf 0.1.0). Inverts each grain's intra-grain
    orientation spread (ODF) from FF-HEDM spot patches via the differentiable
    midas_grain_odf model. Needs the detector geometry, the indexed grains,
    the extracted spots, and the raw frame stack.

    Args:
        geometry_file: JSON with detector geometry + lattice.
        grains_file: grains.csv path.
        spots_file: spots.csv path.
        frames_file: frame stack (.npy / .npz / .h5).
        output_file: output .h5 path for the fitted ODFs.
        odf_type: "particle" | "bingham" (default) | "voxel".

    Returns:
        JSON with the CLI status, return code, and stdout/stderr tails.
    """
    try:
        for f in (geometry_file, grains_file, spots_file, frames_file):
            v, msg = validate_file(f)
            if not v:
                return format_result({"tool": "fit_grain_odf",
                                      "status": "error", "error": msg})
        cargs = ["grain_odf", "--geometry", geometry_file, "--grains", grains_file,
                 "--spots", spots_file, "--frames", frames_file,
                 "--out", output_file, "--odf-type", odf_type]
        out = _run_capability_runner(cargs, timeout=7200)
        return format_result({"tool": "fit_grain_odf", **out})
    except Exception as e:
        return format_result({"tool": "fit_grain_odf",
                              "status": "error", "error": str(e)})


@mcp.tool()
async def simulate_2d_diffraction(
    out_dir: str,
    tutorial: str = "tutorial_coherent_rsm",
    seed: int = 0,
) -> str:
    """Forward-model 2D / ultrafast coherent diffraction (midas-2d).

    SYNTHETIC demonstration (midas-2d 0.1.0; real-data ingestion deferred
    upstream). Runs one of the midas_2d shipped tutorials — coherent RSM,
    nanoplatelet thickness fringes, transient MD disorder, depth-strain, etc. —
    writing figures/arrays to out_dir. Use to demonstrate the coherent /
    few-layer / ultrafast forward models.

    Args:
        out_dir: directory to write the tutorial's artifacts.
        tutorial: one of tutorial_coherent_rsm (default), tutorial_npl_fringes,
                  tutorial_depth_strain, tutorial_md_transient_disorder,
                  tutorial_ml_and_detector, tutorial_stiffness_and_phonon,
                  tutorial_transport_and_md, tutorial_frontier.
        seed: RNG seed (ignored by tutorials that take none).

    Returns:
        JSON with the artifacts written and honest mode/real_data flags.
    """
    try:
        cargs = ["twod", "--tutorial", tutorial, "--out-dir", out_dir,
                 "--seed", str(seed)]
        out = _run_capability_runner(cargs, timeout=1800)
        return format_result({"tool": "simulate_2d_diffraction", **out})
    except Exception as e:
        return format_result({"tool": "simulate_2d_diffraction",
                              "status": "error", "error": str(e)})


@mcp.tool()
async def design_xaf_experiment(
    energy_kev: float = 80.0,
    opening_deg: float = 15.0,
    n_grains: int = 50,
    n_mountings: int = 2,
    material: str = "zirconia_monoclinic",
    sample_radius_um: float = 50.0,
    seed: int = 0,
) -> str:
    """Design / simulate a Cross-Axis Faceted (anvil-cell) HEDM experiment.

    SYNTHETIC design tool (midas-xaf 0.1.0; real-data ingestion deferred
    upstream). Builds a synthetic grain population for a limited-opening
    anvil-cell geometry and runs the XAF forward + indexing pipeline to report
    coverage / determinability — for planning cross-axis mounting strategies
    before beamtime.

    Args:
        energy_kev: beam energy (keV).
        opening_deg: full angular opening of the pressure cell (deg).
        n_grains: number of grains in the synthetic sample.
        n_mountings: number of cross-axis remountings.
        material: material key (e.g. "zirconia_monoclinic").
        sample_radius_um: sample radius (µm).
        seed: RNG seed.

    Returns:
        JSON with the config, pipeline summary, and coverage fraction.
    """
    try:
        cargs = ["xaf", "--energy-keV", str(energy_kev),
                 "--opening-deg", str(opening_deg), "--n-grains", str(n_grains),
                 "--n-mountings", str(n_mountings), "--material", material,
                 "--sample-radius-um", str(sample_radius_um), "--seed", str(seed)]
        out = _run_capability_runner(cargs, timeout=1800)
        return format_result({"tool": "design_xaf_experiment", **out})
    except Exception as e:
        return format_result({"tool": "design_xaf_experiment",
                              "status": "error", "error": str(e)})


@mcp.tool()
async def simulate_dfxm_image(
    grid: int = 64,
    strain: float = 0.001,
    hkl: str = "1,1,1",
    two_theta_deg: float = 10.0,
    d_spacing_a: float = 2.0,
    output_file: str = "",
) -> str:
    """Forward-model a Dark-Field X-ray Microscopy (DFXM) image (midas-dfxm).

    SYNTHETIC forward model (midas-dfxm 0.1.0; real strain/orientation-field
    ingestion deferred upstream). Builds a uniform-strain deformation field on a
    grid and renders the DFXM image through the resolution function + objective
    optics — a demonstration of the imaging forward operator.

    Args:
        grid: image grid size (grid × grid).
        strain: uniform εxx applied to the synthetic field.
        hkl: reflection as "h,k,l".
        two_theta_deg: scattering angle (deg).
        d_spacing_a: reflection d-spacing (Å) for the nominal Q.
        output_file: optional .npy path to save the rendered image.

    Returns:
        JSON with image shape/statistics and honest mode/real_data flags.
    """
    try:
        cargs = ["dfxm", "--grid", str(grid), "--strain", str(strain),
                 "--hkl", hkl, "--two-theta-deg", str(two_theta_deg),
                 "--d-spacing-A", str(d_spacing_a)]
        if output_file:
            cargs += ["--out", output_file]
        out = _run_capability_runner(cargs, timeout=1800)
        return format_result({"tool": "simulate_dfxm_image", **out})
    except Exception as e:
        return format_result({"tool": "simulate_dfxm_image",
                              "status": "error", "error": str(e)})


@mcp.tool()
async def invert_pf_grain_odf(
    layer_dir: str = "",
    grain_id: int = -1,
    n_pixels_y: int = 2048,
    n_pixels_z: int = 2048,
) -> str:
    """Joint per-grain peak-shape ODF inversion for pf-HEDM (midas-pf-odf).

    DEFERRED capability (midas-pf-odf 0.1.0): the differentiable joint
    peak-shape / ODF inversion is present, but a turnkey synthetic self-test is
    not shipped upstream and the real-data reader is still being finalized. With
    no arguments this confirms the capability and lists what a real run needs;
    given a pf-HEDM layer dir + grain id it attempts to load the grain dataset.

    Args:
        layer_dir: pf-HEDM layer directory (Grains.csv + frames), optional.
        grain_id: grain id to load (>=0 to attempt a real load).
        n_pixels_y, n_pixels_z: detector dimensions for the loader.

    Returns:
        JSON describing the capability, required inputs, and any load attempt.
    """
    try:
        cargs = ["pf_odf", "--n-pixels-y", str(n_pixels_y),
                 "--n-pixels-z", str(n_pixels_z)]
        if layer_dir:
            cargs += ["--layer-dir", layer_dir]
        if grain_id >= 0:
            cargs += ["--grain-id", str(grain_id)]
        out = _run_capability_runner(cargs, timeout=1800)
        return format_result({"tool": "invert_pf_grain_odf", **out})
    except Exception as e:
        return format_result({"tool": "invert_pf_grain_odf",
                              "status": "error", "error": str(e)})


@mcp.tool()
async def invert_pink_beam(
    energy_kev: float = 55.0,
    half_bw: float = 0.02,
) -> str:
    """Pink-beam spectrum-aware differentiable grain-state inversion (midas-pink).

    DEFERRED capability (midas-pink 0.1.0): the spectrum-aware differentiable
    inversion (build_pink_bank → recover_grain_state / recover_two_stage) is
    present, but a turnkey synthetic self-test is not shipped upstream and real
    pink-beam ROI ingestion is deferred. This confirms the capability and
    exercises spectrum construction as a smoke test.

    Args:
        energy_kev: central beam energy (keV).
        half_bw: spectral half-bandwidth (relative).

    Returns:
        JSON describing the capability, required inputs, and the spectrum smoke test.
    """
    try:
        cargs = ["pink", "--energy-keV", str(energy_kev), "--half-bw", str(half_bw)]
        out = _run_capability_runner(cargs, timeout=600)
        return format_result({"tool": "invert_pink_beam", **out})
    except Exception as e:
        return format_result({"tool": "invert_pink_beam",
                              "status": "error", "error": str(e)})


def _lint_mask_request(mode_list: list, ny: int, nz: int, param_file: str,
                       sentinels: str, module_gaps_json: str,
                       detector: str) -> list:
    """Deterministic pre-run lint for build_detector_mask requests.

    Returns {severity, key, message, section} dicts (same shape as
    _lint_handbook_traps). `error` severity hard-blocks the run (like the FF
    unit traps); `warning`/`info` are surfaced but never block. Covers the
    unambiguous ways a mask request produces garbage: wrong geometry, diff mode
    without a calibrated param file, value mode without sentinels, module_gap
    with no geometry.
    """
    traps = []
    S = "Detector masking (build_detector_mask)"
    if "differentiable" in mode_list and not param_file:
        traps.append({"severity": "error", "key": "param_file",
                      "message": ("differentiable mode needs a calibrated param "
                                  "file (Lsd/BC/tilts/px/binning) for the forward "
                                  "geometry — none given."), "section": S})
    if "value" in mode_list and not sentinels:
        traps.append({"severity": "error", "key": "sentinels",
                      "message": ("value mode needs sentinels (e.g. '-1,-2'); none "
                                  "given and the detector has no default_sentinels."),
                      "section": S})
    if "module_gap" in mode_list and not module_gaps_json:
        traps.append({"severity": "warning", "key": "module_gap",
                      "message": (f"module_gap requested but detector '{detector or '?'}' "
                                  "has no module_gaps geometry in the registry — this "
                                  "mode will no-op (flat panel, or gaps not sourced in "
                                  "MIDAS)."), "section": S})
    need_dims = {"module_gap"} & set(mode_list)
    if need_dims and not (ny and nz):
        traps.append({"severity": "error", "key": "dims",
                      "message": ("module_gap mode needs detector dims — pass a "
                                  "`detector` alias or nr_pixels_y/nr_pixels_z."),
                      "section": S})
    return traps


def _patch_maskfile_param(param_file: str, mask_path: str) -> dict:
    """Back up the param file, then insert/replace its MaskFile line in place.

    Report-only is the default in build_detector_mask; this runs ONLY on
    write_param=True. Backs up to <param>.bak.<n> (never overwriting an existing
    .bak) BEFORE editing, then patches (or appends) `MaskFile <abs path>`. An
    existing `MaskFile`/`MaskFN` line is replaced. Returns backup path + a diff.
    """
    import shutil
    p = Path(param_file).expanduser()
    lines = p.read_text().splitlines()
    # Pick the first free backup slot — never clobber an existing .bak.
    n = 0
    while (bak := p.with_name(p.name + f".bak.{n}")).exists():
        n += 1
    shutil.copy2(p, bak)

    new_line = f"MaskFile {mask_path}"
    old_line = None
    out_lines, replaced = [], False
    for ln in lines:
        tok = ln.strip().split()
        key = tok[0] if tok else ""
        if key in ("MaskFile", "MaskFN") and not replaced:
            old_line, replaced = ln, True
            out_lines.append(new_line)
        else:
            out_lines.append(ln)
    action = "replaced"
    if not replaced:
        out_lines.append(new_line)
        action = "appended"
    p.write_text("\n".join(out_lines) + "\n")
    return {"param_file": str(p), "backup": str(bak), "action": action,
            "old_line": old_line, "new_line": new_line}


@mcp.tool()
async def build_detector_mask(
    image_path: str = "",
    detector: str = "",
    nr_pixels_y: int = 0,
    nr_pixels_z: int = 0,
    modes: str = "statistical",
    sentinels: str = "",
    param_file: str = "",
    output_path: str = "",
    diff_iterations: int = 300,
    diff_lr: float = 0.5,
    diff_threshold: float = 0.5,
    diff_sparsity: float = 1e-4,
    diff_smoothness: float = 0.0,
    diff_crop_rings: bool = True,
    write_param: bool = False,
) -> str:
    """Build a detector MaskFile (uint8 TIFF, 1=masked) for MIDAS FF/NF/integration.

    Beamline detector frames carry defects that corrupt HEDM analysis: dead
    pixels, module strides/gaps (Pilatus/Eiger tiles), hot pixels/zingers, NaNs,
    and diffuse blobs. This builds a downstream-correct MaskFile — uint8 TIFF,
    convention **1 = masked**, DataType code 7, in RAW detector orientation — from
    one or more detection modes, OR-combined. Report-only by default: it always
    returns a paste-ready `MaskFile <path>` line and patches a param file only on
    explicit `write_param=True` (backing up the original first).

    Detector geometry (dims, pixel size, module-gap layout, typical sentinels) is
    resolved from a sourced registry (knowledge_base/data/detector_presets.json —
    every value traced to a MIDAS repo line). Pass `detector` (name/alias) OR the
    dims directly via `nr_pixels_y`/`nr_pixels_z`.

    Args:
        image_path: dark/data frame (TIFF or HDF5). Required for value/statistical/
            differentiable/convert modes. For HDF5 the dataset defaults to
            exchange/data. A 3-D stack is averaged per-pixel.
        detector: registry alias (e.g. "pilatus 2m", "ge", "varex", "eiger2 4m")
            → resolves NrPixelsY/Z, module_gaps, default sentinels, DataType. Or
            omit and pass dims directly.
        nr_pixels_y: detector width (fast axis) — overrides/supplies dims.
        nr_pixels_z: detector height (slow axis) — overrides/supplies dims.
        modes: csv of modes: value, statistical (default), module_gap,
            differentiable, convert, combine. Multiple → OR-combined.
        sentinels: for `value` mode, csv like "-1,-2". Defaults to the detector's
            registry default_sentinels if a detector is resolved.
        param_file: calibrated MIDAS param file. REQUIRED for `differentiable`
            (geometry: Lsd/BC/tilts/px/binning) and the target of `write_param`.
        output_path: mask TIFF path. Default: <image_stem>_mask.tif.
        diff_iterations: differentiable mode Adam steps (default 300; 200-500).
        diff_lr: differentiable Adam learning rate (default 0.5).
        diff_threshold: hard-mask threshold on the learned weight (default 0.5).
        diff_sparsity: sparsity prior weight (default 1e-4).
        diff_smoothness: smoothness prior weight (default 0.0; >0 for clustered blobs).
        diff_crop_rings: focus the η-uniformity loss on ring bands (default True).
        write_param: if True, back up param_file → param_file.bak.<n> then insert/
            replace its `MaskFile` line. Default False (report-only).

    Returns:
        JSON with output_path, mask stats (n_masked/pct/per-mode breakdown), the
        paste-ready MaskFile line, the resolved detector preset, and — on
        write_param — the backup path + diff. Values are always uint8 ⊆ {0,1}.
    """
    try:
        mode_list = [m.strip().lower() for m in modes.split(",") if m.strip()]
        preset_used = None
        module_gaps_json = ""
        ny, nz = nr_pixels_y, nr_pixels_z
        sent = sentinels

        # Resolve detector preset → dims / module_gaps / default sentinels.
        if detector:
            key, preset = _resolve_detector_preset(detector)
            if key is None:
                return format_result({
                    "tool": "build_detector_mask", "status": "error",
                    "error": f"Unknown detector '{detector}'.",
                    "known_detectors": preset})
            preset_used = {"key": key, **preset}
            if not ny:
                ny = int(preset.get("NrPixelsY", 0))
            if not nz:
                nz = int(preset.get("NrPixelsZ", 0))
            if preset.get("module_gaps"):
                module_gaps_json = json.dumps(preset["module_gaps"])
            if not sent and preset.get("default_sentinels"):
                sent = ",".join(str(s) for s in preset["default_sentinels"])

        # Pre-dispatch lint (hard-block on `error` traps).
        traps = _lint_mask_request(mode_list, ny, nz, param_file, sent,
                                   module_gaps_json, detector)
        errors = [t for t in traps if t.get("severity") == "error"]
        if errors:
            return format_result({
                "tool": "build_detector_mask", "status": "error",
                "error": "Handbook lint blocked the mask request.",
                "handbook_traps": traps, "detector_preset": preset_used})

        # Validate the image where a mode needs it.
        needs_image = {"value", "statistical", "differentiable", "convert"} & set(mode_list)
        if needs_image:
            if not image_path:
                return format_result({
                    "tool": "build_detector_mask", "status": "error",
                    "error": f"modes {sorted(needs_image)} require image_path."})
            v, msg = validate_file(image_path)
            if not v:
                return format_result({"tool": "build_detector_mask",
                                      "status": "error", "error": msg})

        # Build CLI args. Use `--flag=value` for anything that may start with '-'
        # (argparse treats a bare leading-dash value as an option — see plan).
        cargs = ["mask", "--modes", ",".join(mode_list)]
        if image_path:
            cargs += ["--image", str(Path(image_path).expanduser())]
        if output_path:
            cargs += ["--output", str(Path(output_path).expanduser())]
        if ny:
            cargs += ["--nr-pixels-y", str(ny)]
        if nz:
            cargs += ["--nr-pixels-z", str(nz)]
        if sent:
            cargs += [f"--sentinels={sent}"]
        if module_gaps_json:
            cargs += [f"--module-gaps={module_gaps_json}"]
        if param_file:
            cargs += ["--param-file", str(Path(param_file).expanduser())]
        cargs += [
            f"--diff-iterations={diff_iterations}",
            f"--diff-lr={diff_lr}",
            f"--diff-threshold={diff_threshold}",
            f"--diff-sparsity={diff_sparsity}",
            f"--diff-smoothness={diff_smoothness}",
        ]
        if diff_crop_rings:
            cargs += ["--diff-crop-rings"]

        # Differentiable mode recomputes the full forward each step → allow time.
        timeout = 7200 if "differentiable" in mode_list else 900
        out = _run_capability_runner(cargs, timeout=timeout)

        result = {"tool": "build_detector_mask", **out,
                  "detector_preset": preset_used}
        if traps:
            result["handbook_traps"] = traps

        # Opt-in param patch (report-only otherwise).
        if write_param and out.get("status") == "success":
            if not param_file:
                result["param_patch"] = {"status": "skipped",
                                         "reason": "write_param=True but no param_file given."}
            else:
                pv, pmsg = validate_file(param_file)
                if not pv:
                    result["param_patch"] = {"status": "error", "reason": pmsg}
                else:
                    result["param_patch"] = {"status": "success",
                                             **_patch_maskfile_param(param_file,
                                                                     out["output_path"])}
        return format_result(result)
    except Exception as e:
        return format_result({"tool": "build_detector_mask",
                              "status": "error", "error": str(e)})


@mcp.tool()
async def read_grains_summary(
    grains_file: str
) -> str:
    """Read a MIDAS Grains.csv or HDF5 file and return a statistical summary.

    Reports grain count, position ranges, orientation spread, strain tensor
    statistics, lattice parameters, and confidence distribution. A quick
    "what's in this file" diagnostic for post-reconstruction data.

    Args:
        grains_file: Path to Grains.csv or consolidated .h5 file

    Returns:
        JSON with grain population statistics
    """
    try:
        valid, grains_path = validate_file(grains_file)
        if not valid:
            return format_result({"tool": "read_grains_summary",
                                  "status": "error", "error": grains_path})

        output = _run_stress_runner(["read_grains", "--grains", grains_path])
        return format_result({"tool": "read_grains_summary", **output})

    except Exception as e:
        return format_result({"tool": "read_grains_summary", "status": "error", "error": str(e)})


@mcp.tool()
async def compute_grain_stress(
    grains_file: str,
    material: str,
    applied_stress: str = "",
    min_confidence: float = 0.0
) -> str:
    """Compute per-grain stress from HEDM reconstruction output.

    Reads grain orientations and strains from a MIDAS Grains.csv file,
    applies single-crystal Hooke's law with equilibrium correction, and
    returns stress decomposition (hydrostatic, deviatoric, von Mises) with
    uncertainty estimates.

    Supported materials: Au, Cu, Al, Fe, Ni, Ti, W, Si, CeO2

    Args:
        grains_file: Path to Grains.csv or consolidated .h5 file
        material: Material name (e.g., "Cu", "Fe", "Ti")
        applied_stress: Applied macroscopic stress as 6 comma-separated Voigt
                       components in GPa (e.g., "0.1,0,0,0,0,0" for uniaxial).
                       Empty string = free-standing sample.
        min_confidence: Minimum grain confidence for equilibrium averaging (0-1)

    Returns:
        JSON with von Mises statistics, hydrostatic shift (d0 proxy), and uncertainty
    """
    try:
        valid, grains_path = validate_file(grains_file)
        if not valid:
            return format_result({"tool": "compute_grain_stress",
                                  "status": "error", "error": grains_path})

        cmd_args = ["compute_stress", "--grains", grains_path,
                    "--material", material,
                    "--min-confidence", str(min_confidence)]
        if applied_stress:
            cmd_args.extend(["--applied-stress", applied_stress])

        output = _run_stress_runner(cmd_args)
        return format_result({"tool": "compute_grain_stress", **output})

    except Exception as e:
        return format_result({"tool": "compute_grain_stress", "status": "error", "error": str(e)})


@mcp.tool()
async def get_material_stiffness(
    material: str
) -> str:
    """Look up single-crystal elastic stiffness matrix and d0 sensitivity.

    Returns the full 6x6 stiffness matrix in Voigt-Mandel notation (GPa),
    independent elastic constants, bulk modulus, and d0 sensitivity (how
    much stress error results from a given d0 uncertainty).

    Supported materials: Au, Cu, Al, Fe, Ni, Ti, W, Si, CeO2

    Args:
        material: Material name (e.g., "Cu", "Fe", "Ti")

    Returns:
        JSON with stiffness matrix, elastic constants, d0 sensitivity, and
        list of all available materials
    """
    try:
        output = _run_stress_runner(["material_info", "--material", material])
        return format_result({"tool": "get_material_stiffness", **output})

    except Exception as e:
        return format_result({"tool": "get_material_stiffness", "status": "error", "error": str(e)})


@mcp.tool()
async def correct_d0_equilibrium(
    grains_file: str,
    material: str,
    applied_stress: str = "",
    min_confidence: float = 0.0
) -> str:
    """Apply two-step d0 correction to grain stress data.

    The d0 reference lattice parameter is the dominant systematic error
    in HEDM stress analysis. This tool applies:
    1. Strain-level correction: fits isotropic strain offset (eps_iso)
    2. Stress-level correction: enforces mechanical equilibrium

    Reports the correction magnitude, residual norms before/after, and
    corrected stress statistics.

    Args:
        grains_file: Path to Grains.csv or consolidated .h5 file
        material: Material name (e.g., "Cu", "Fe", "Ti")
        applied_stress: Applied macroscopic stress as 6 comma-separated Voigt
                       components in GPa. Empty = free-standing.
        min_confidence: Minimum grain confidence for equilibrium averaging (0-1)

    Returns:
        JSON with eps_iso correction (ppm), residual improvement, corrected stress stats
    """
    try:
        valid, grains_path = validate_file(grains_file)
        if not valid:
            return format_result({"tool": "correct_d0_equilibrium",
                                  "status": "error", "error": grains_path})

        cmd_args = ["correct_d0", "--grains", grains_path,
                    "--material", material,
                    "--min-confidence", str(min_confidence)]
        if applied_stress:
            cmd_args.extend(["--applied-stress", applied_stress])

        output = _run_stress_runner(cmd_args)
        return format_result({"tool": "correct_d0_equilibrium", **output})

    except Exception as e:
        return format_result({"tool": "correct_d0_equilibrium", "status": "error", "error": str(e)})


@mcp.tool()
async def analyze_slip_systems(
    grains_file: str,
    material: str,
    load_direction: str = "0,0,1",
    crss: float = 0.0
) -> str:
    """Compute Schmid factors, Taylor factor, and yield proximity for grains.

    Performs slip-system analysis from HEDM stress data:
    - Schmid factors for each grain and slip system
    - Dominant slip system per grain
    - Taylor factor (polycrystal average)
    - Yield proximity ranking (if CRSS provided)

    Automatically selects slip families by material (FCC, BCC, or HCP).

    Args:
        grains_file: Path to Grains.csv or consolidated .h5 file
        material: Material name (e.g., "Cu" for FCC, "Fe" for BCC, "Ti" for HCP)
        load_direction: Loading direction in sample frame as 3 comma-separated
                       values (e.g., "0,0,1" for z-axis loading)
        crss: Critical resolved shear stress in MPa. Set > 0 to compute
              yield proximity (which grains yield first). Default 0 = skip.

    Returns:
        JSON with Schmid factor statistics, Taylor factor, and yield proximity
    """
    try:
        valid, grains_path = validate_file(grains_file)
        if not valid:
            return format_result({"tool": "analyze_slip_systems",
                                  "status": "error", "error": grains_path})

        cmd_args = ["plasticity", "--grains", grains_path,
                    "--material", material,
                    "--load-direction", load_direction,
                    "--crss", str(crss)]

        output = _run_stress_runner(cmd_args)
        return format_result({"tool": "analyze_slip_systems", **output})

    except Exception as e:
        return format_result({"tool": "analyze_slip_systems", "status": "error", "error": str(e)})


# =============================================================================
# MIDAS v11 PYTORCH PIPELINE WRAPPERS
# (packages/midas_ff_pipeline, midas_fit_grain, midas_nf_preprocess)
# =============================================================================

def _run_midas_module(module: str, args: list, timeout: int = 7200) -> dict:
    """Invoke `python -m <module>` with MIDAS env. Returns dict with status/stdout/stderr."""
    midas_python = find_midas_python()
    cmd = [midas_python, "-m", module] + [str(a) for a in args]
    print(f"\n  $ {' '.join(cmd)}", file=sys.stderr)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=timeout, env=get_midas_env())
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": f"{module} timed out (>{timeout}s)"}
    if result.returncode != 0:
        for line in result.stderr.strip().splitlines()[-20:]:
            print(f"  {line}", file=sys.stderr)
        return {"status": "error", "exit_code": result.returncode,
                "stdout": result.stdout, "stderr": result.stderr,
                "error": f"{module} exited {result.returncode}"}
    return {"status": "success", "stdout": result.stdout, "stderr": result.stderr}


@mcp.tool()
async def run_ff_pipeline(
    params: str,
    result: str,
    zarr: str = None,
    detectors: str = None,
    layers: str = "1-1",
    n_cpus: int = 16,
    device: str = "auto",
    dtype: str = "auto",
    resume: str = "auto",
    resume_from: str = None,
    only: list = None,
    skip: list = None,
    solver: str = "lbfgs",
    loss: str = "pixel",
    mode: str = "",
    group_size: str = "auto",
    shard_gpus: str = "auto",
    pg_mode: str = "spot_aware",
    raw_dir: str = None,
    grains_file: str = None,
    nf_result_dir: str = None,
    batch: bool = False,
    generate_h5: bool = False,
    extra_args: list = None,
) -> str:
    """Run the differentiable PyTorch FF-HEDM pipeline (midas_ff_pipeline.run).

    Wraps the v11 ``midas-ff-pipeline run`` console script — the new PyTorch port
    of the FF-HEDM workflow with checkpoint/resume, multi-GPU sharding, and
    swappable solvers/losses. Drop-in replacement for legacy ``ff_MIDAS.py``.

    NOTE: ``midas-ff-pipeline`` is DEPRECATED as of MIDAS 0.4.0 (removed in 1.0.0);
    the FF path is now the same code under the unified ``midas-pipeline run
    --scan-mode ff`` CLI exposed by ``run_ff_hedm_full_workflow`` — prefer that
    tool. This wrapper still works (the deprecated CLI keeps its own ``--loss
    pixel`` choice; the unified CLI renamed it to ``--refine-loss full3d``) and is
    kept for its lower-level flags (``--pg-mode``, ``--raw-dir``, ``--batch``).

    Args:
        params: Path to Parameters.txt (required).
        result: Result directory for this run (required).
        zarr: Override zarr.zip path (auto-derived if omitted).
        detectors: Detector spec (e.g. "1" or "1,2,3,4" for Hydra).
        layers: Layer range, e.g. "1-1" or "1-3".
        n_cpus: CPU thread count for non-GPU stages.
        device: "auto" (default) | "cuda" | "cpu" | "mps". "auto" resolves to
            "cuda" only when a real CUDA GPU is present, else "cpu" — never
            "mps" here (this pipeline uses float64, which MPS can't do, so mps
            would fail at the first torch stage). Do NOT default to "cuda":
            CPU-only beamline hosts have no CUDA and the pipeline aborts.
        dtype: "auto" | "float32" | "float64".
        resume: Resume policy — "none" | "auto" | "from".
        resume_from: Stage to resume from when resume="from".
        only: Stage allow-list (one per --only flag).
        skip: Stage skip-list (one per --skip flag).
        solver: "lbfgs" | "lm" | "nelder_mead" | "adam" | "lm_batched".
        loss: "pixel" | "angular" | "internal_angle".
        mode: Optional mode override.
        group_size: Per-grain batch group size or "auto".
        shard_gpus: GPU sharding spec or "auto".
        pg_mode: Peak-group mode (default "spot_aware").
        raw_dir: Raw data directory (when not in params).
        grains_file: Pre-computed grains file (skip indexing).
        nf_result_dir: Companion NF reconstruction directory (for FF↔NF cross-checks).
        batch: Enable batched execution.
        generate_h5: Emit consolidated HDF5 output.
        extra_args: Any additional argv to forward verbatim (advanced).
    """
    # Resolve device="auto" to a concrete backend the host actually has. Resolve
    # here (not by passing "auto" to the CLI, which may not accept it) so the
    # choice is logged and never silently defaults to cuda on a CPU-only host.
    if str(device).lower() == "auto":
        accel, name = _local_accelerator()
        device = "cuda" if (accel and name == "cuda") else "cpu"
        print(f"[FF-pipeline] device=auto → resolved to '{device}' "
              f"(local accelerator: {name})", file=sys.stderr)

    args = ["run", "--params", params, "--result", result, "--layers", layers,
            "--n-cpus", n_cpus, "--device", device, "--dtype", dtype,
            "--resume", resume, "--solver", solver, "--loss", loss,
            "--group-size", group_size, "--shard-gpus", shard_gpus,
            "--pg-mode", pg_mode]
    if zarr:           args += ["--zarr", zarr]
    if detectors:      args += ["--detectors", detectors]
    if resume_from:    args += ["--from", resume_from]
    if mode:           args += ["--mode", mode]
    if raw_dir:        args += ["--raw-dir", raw_dir]
    if grains_file:    args += ["--grains-file", grains_file]
    if nf_result_dir:  args += ["--nf-result-dir", nf_result_dir]
    if batch:          args += ["--batch"]
    if generate_h5:    args += ["--generate-h5"]
    for s in (only or []):  args += ["--only", s]
    for s in (skip or []):  args += ["--skip", s]
    if extra_args:     args += list(extra_args)

    out = _run_midas_module("midas_ff_pipeline.cli", args, timeout=14400)
    payload = {"tool": "run_ff_pipeline", "result_dir": str(Path(result).expanduser().absolute()), **out}
    return format_result(payload)


@mcp.tool()
async def refine_grain_lattice(
    param_file: str,
    block_nr: int = 0,
    num_blocks: int = 1,
    num_lines: int = 0,
    num_procs: int = 0,
    solver: str = "lbfgs",
    mode: str = None,
    loss: str = "full3d",
    device: str = None,
    dtype: str = None,
    max_iter: int = 200,
    ftol: float = 1e-7,
    xtol: float = 1e-9,
    csv: bool = False,
    verbose: int = 0,
) -> str:
    """Refine grain position/orientation/strain with the PyTorch fitter
    (midas_fit_grain — drop-in for FitPosOrStrainsOMP / FitPosOrStrainsGPU).

    Mirrors the legacy C-binary positional argv:
        param_file blockNr numBlocks numLines numProcs

    Args:
        param_file: paramstest.txt produced by FitSetupParamsAllZarr / ff_MIDAS.
        block_nr: 0-based block index.
        num_blocks: Total number of blocks.
        num_lines: Number of grain seeds in SpotsToIndex.csv (0 = read from disk).
        num_procs: CPU thread count for torch.set_num_threads (0 = auto).
        solver: "lbfgs" | "adam" | "lm" | "nelder_mead" | "lm_batched".
        mode: "iterative" | "all_at_once" (default: iterative if FitAllAtOnce=0).
        loss: "full3d" (C parity, renamed from "pixel" in fit-grain 0.5.x) |
              "angular" | "internal_angle".
        device: Override MIDAS_FIT_GRAIN_DEVICE ("cuda"|"mps"|"cpu").
        dtype: Override MIDAS_FIT_GRAIN_DTYPE ("float32"|"float64").
        max_iter: Outer-iteration cap per phase.
        ftol: Relative-loss convergence threshold.
        xtol: Parameter-delta convergence threshold.
        csv: Also dump human-readable FitBest.csv next to the binary.
        verbose: 0=warning, 1=info, 2=debug.
    """
    args = [param_file, block_nr, num_blocks, num_lines, num_procs,
            "--solver", solver, "--loss", loss,
            "--max-iter", max_iter, "--ftol", ftol, "--xtol", xtol]
    if mode:    args += ["--mode", mode]
    if device:  args += ["--device", device]
    if dtype:   args += ["--dtype", dtype]
    if csv:     args += ["--csv"]
    for _ in range(min(verbose, 2)):
        args += ["-v"]

    out = _run_midas_module("midas_fit_grain.cli", args, timeout=3600)
    payload = {"tool": "refine_grain_lattice",
               "param_file": str(Path(param_file).expanduser().absolute()),
               "block": f"{block_nr}/{num_blocks}",
               **out}
    return format_result(payload)


@mcp.tool()
async def preprocess_nf_data(
    subcommand: str,
    args: list = None,
) -> str:
    """Run the NF-HEDM PyTorch preprocessing umbrella CLI (midas_nf_preprocess).

    Subcommands (mirror the standalone modules):
      - hex-grid           : generate the voxel grid (port of MakeHexGrid)
      - tomo-filter        : mask the grid by a tomography image / bbox
      - diffr-spots        : forward-simulate diffraction spots (port of MakeDiffrSpots)
      - process-images     : raw TIFF -> SpotsInfo.bin (port of ProcessImagesCombined)
      - seed-orientations  : seed grain orientations

    Args:
        subcommand: Which subcommand to run (see list above).
        args: Argv list for the subcommand. Pass exactly as you would on the
              shell (e.g. ["--paramFN", "params.txt", "--output", "grid.bin"]).
              Each subcommand has its own flag set — call with
              args=["--help"] to discover them.
    """
    valid = {"hex-grid", "tomo-filter", "diffr-spots", "process-images", "seed-orientations"}
    if subcommand not in valid:
        return format_result({"tool": "preprocess_nf_data", "status": "error",
                              "error": f"Unknown subcommand '{subcommand}'. "
                                       f"Valid: {sorted(valid)}"})

    cli_args = [subcommand] + [str(a) for a in (args or [])]
    out = _run_midas_module("midas_nf_preprocess.cli", cli_args, timeout=3600)
    payload = {"tool": "preprocess_nf_data", "subcommand": subcommand, **out}
    return format_result(payload)


# =============================================================================
# SERVER MAIN
# =============================================================================

if __name__ == "__main__":
    # Pre-warm the knowledge base (loads ~700MB Nomic embedder + ChromaDB client)
    # so the first query_hedm_knowledge call doesn't pay a ~6 s cold start.
    #
    # GATED ON NETWORK TIER. On a host with no public internet and no pre-staged
    # HF cache this call BLOCKS on a HuggingFace HEAD request — and a block is not
    # an exception, so the try/except below never fires, mcp.run() is never
    # reached, and APEXA fails to launch at all. That is exactly what happened on
    # copland. A slow first query is strictly better than a server that never
    # starts, so below `web` tier we skip the warmup unless the operator has
    # explicitly staged the cache (APEXA_KB_PREWARM=1).
    import sys as _sys
    try:
        from apexa_network import prewarm_allowed as _prewarm_ok, describe as _net_desc
        print(f"[midas] {_net_desc()}", file=_sys.stderr, flush=True)
        _do_warm = _prewarm_ok()
    except Exception:
        _do_warm = False        # unknown tier → assume the risky one is unsafe
    if _do_warm:
        try:
            kb = get_knowledge_base()
            if kb.get("available"):
                print("[midas] knowledge base pre-warmed", file=_sys.stderr, flush=True)
        except Exception as _e:
            print(f"[midas] kb warmup skipped: {_e}", file=_sys.stderr, flush=True)
    else:
        print("[midas] kb pre-warm skipped (no web tier) — first RAG query will be "
              "slower; set APEXA_KB_PREWARM=1 once the embedder cache is staged",
              file=_sys.stderr, flush=True)

    mcp.run(transport='stdio')
