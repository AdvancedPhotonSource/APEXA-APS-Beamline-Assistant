#!/usr/bin/env python3
"""
APEXA Tomography MCP Server — TomoPy backend
Absorption / propagation-based parallel-beam tomography for APS 1-ID and 20-ID:
survey a projection stack, reconstruct it with TomoPy, optionally threshold it to a
sample mask — with the `tomo` capsule's silent-failure guards enforced and an honest
status per result.

RELATIONSHIP TO MIDAS
---------------------
MIDAS has its own tomo (`midas-tomo`, FBP + shift sweep). This server is the
APS-standard TomoPy path instead (tomopy / dxchange / astra), chosen for 1-ID/20-ID.
The methodology is shared: the vendored `knowledge_base/capsules/tomo/` capsule holds
THE ORDER, the five hard rules and the traps, all backend-agnostic — only the
reconstructor differs. Registration of the reconstructed shape into the MIDAS lab
frame (the HEDM-envelope use, `midas_stress.frames.tomo_grid_to_midas`) is a tracked
follow-up, not part of this v1.

ROUTE-TO-NATIVE
---------------
TomoPy/astra are a conda-forge stack, not reliable pip wheels, so — exactly like
GSAS-II (`APEXA_GSASII_PYTHON`) and MIDAS (`APEXA_MIDAS_BIN`) — this server routes to a
**native tomo env** via `APEXA_TOMO_PYTHON` and runs `_tomo_runner.py` there in a
subprocess. Nothing heavy enters APEXA's base `.venv`; the base install stays
offline-clean. With no tomo env the tools degrade to a structured error (the model is
told not to fabricate), never a crash.
"""
from typing import Any, Dict, List, Optional
import json
import asyncio
import os
import subprocess
import sys
from pathlib import Path

import logging

from mcp.server.mcpserver import MCPServer  # mcp>=2: FastMCP was renamed

logging.getLogger("mcp").setLevel(logging.WARNING)
logging.getLogger("fastmcp").setLevel(logging.WARNING)
logger = logging.getLogger("tomo_server")

mcp = MCPServer("APEXA Tomography (TomoPy)")

# ---------------------------------------------------------------------------
# Environment — route to the native TomoPy interpreter
# ---------------------------------------------------------------------------
DEFAULT_TOMO_PYTHON = os.environ.get(
    "APEXA_TOMO_PYTHON",
    str(Path.home() / "miniconda3" / "envs" / "tomopy" / "bin" / "python"))

_TOMO_RUNNER = str(Path(__file__).resolve().parent / "_tomo_runner.py")

_ENV_PROBE_CACHE: Optional[str] = None  # None=unprobed, ""=ok, else error string


def format_result(result: dict) -> str:
    return json.dumps(result, indent=2)


def _env_ok() -> Optional[str]:
    """Return an actionable error string if the TomoPy env cannot be reached.

    Checks the interpreter exists, then probes (once, cached) that it can import
    tomopy + dxchange — the two libraries the runner needs. Mirrors
    gsas2_server._env_ok; the probe is the tomo analogue of GSAS-II's repo check.
    """
    global _ENV_PROBE_CACHE
    py = Path(DEFAULT_TOMO_PYTHON)
    if not py.exists():
        return (f"TomoPy python not found at {py}. Set APEXA_TOMO_PYTHON to a conda env "
                f"that has tomopy + dxchange (the APS tomo stack). See docs/TOMO_SETUP.md.")
    if _ENV_PROBE_CACHE is None:
        try:
            p = subprocess.run(
                [str(py), "-c", "import tomopy, dxchange; print('ok')"],
                capture_output=True, text=True, timeout=60)
            _ENV_PROBE_CACHE = "" if p.returncode == 0 else (
                "APEXA_TOMO_PYTHON exists but cannot import tomopy/dxchange: "
                + (p.stderr or "").strip()[-400:])
        except Exception as e:  # noqa: BLE001
            _ENV_PROBE_CACHE = f"probe of APEXA_TOMO_PYTHON failed: {e}"
    return _ENV_PROBE_CACHE or None


def _run_runner(subcmd: str, args: List[str], timeout: int = 1800) -> Dict[str, Any]:
    """Run `_tomo_runner.py <subcmd> <args>` on the TomoPy interpreter, parse JSON.

    Same contract as gsas2_server._run_python: the runner prints one JSON object on
    stdout (last `{`-line wins); chatter goes to stderr. Thread counts are capped so a
    reconstruction does not oversubscribe a shared beamline host.
    """
    err = _env_ok()
    if err:
        return {"status": "error", "error": err}
    env = dict(os.environ)
    for v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
              "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
        env.setdefault(v, "4")
    try:
        p = subprocess.run([DEFAULT_TOMO_PYTHON, _TOMO_RUNNER, subcmd, *args],
                           capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return {"status": "error",
                "error": f"Timed out after {timeout}s. A large reconstruction can exceed "
                         "this on CPU — reduce the stack, use a center sweep first, or run "
                         "where a GPU/astra is available."}
    if p.returncode != 0 and not p.stdout:
        return {"status": "error", "error": (p.stderr or "")[-1500:]}
    for line in reversed(p.stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except Exception:  # noqa: BLE001
                continue
    return {"status": "error", "error": "no JSON payload on stdout",
            "stdout_tail": p.stdout[-800:], "stderr_tail": (p.stderr or "")[-800:]}


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
@mcp.tool()
async def survey_tomo_stack(
    data_folder: str,
    file_pattern: str = "*.tif*",
    pixel_size_um: Optional[float] = None,
) -> str:
    """Pre-flight a tomography projection stack BEFORE reconstructing (capsule THE ORDER 1-4).

    Cheap and read-only. Reports stack dimensions/dtype, resolves the flat/dark layout
    from measured frame means (never assumed — hard rule 1), measures transmission over
    the illuminated region only (rule 2), estimates mu*D (rule 5), and checks for
    truncation. Returns `status:"halt"` with the specific capsule rule when something
    would fail silently downstream (no flats, transmission < 0, missing pixel size,
    sample fills the field of view). Run this first; fix every halt before reconstructing.

    Args:
        data_folder: directory holding the projection TIFF stack (and flats/darks).
        file_pattern: glob for the frames (default "*.tif*").
        pixel_size_um: detector pixel size in microns. NOT defaulted (hard rule 3) —
            read it from the acquisition metadata; if omitted the survey flags it as a halt.
    """
    args = ["--data", data_folder, "--pattern", file_pattern]
    if pixel_size_um is not None:
        args += ["--pixel-size-um", str(pixel_size_um)]
    out = await asyncio.to_thread(_run_runner, "survey", args, 600)
    return format_result(out)


@mcp.tool()
async def run_tomo_reconstruction(
    data_folder: str,
    pixel_size_um: float,
    handedness: str,
    omega_step_deg: float,
    file_pattern: str = "*.tif*",
    output_dir: str = "",
    omega_start_deg: float = 0.0,
    algorithm: str = "gridrec",
    rotation_center: Optional[float] = None,
    center_sweep: Optional[List[float]] = None,
    phase_retrieval: bool = False,
    propagation_mm: float = 0.0,
    energy_kev: Optional[float] = None,
    paganin_alpha: float = 1e-3,
    threshold: Optional[float] = None,
) -> str:
    """Reconstruct a parallel-beam tomography stack with TomoPy (capsule THE ORDER 5-7).

    Normalizes against the measured flats/darks, takes -log, optionally retrieves phase
    (Paganin, required before thresholding a propagation scan), finds or sweeps the
    rotation center (the axis is an OUTPUT — the chosen shift is recorded), reconstructs
    (`gridrec`/`fbp`/`astra` per `algorithm`), and writes the volume (plus a binary mask
    if `threshold` is given). Honest status: `success` only when a fresh, non-degenerate
    volume was written.

    Refuses to default pixel size, rotation axis, or in-plane handedness (hard rule 3) —
    these are not recoverable after the fact. Run `survey_tomo_stack` first.

    Args:
        data_folder: directory with the projection TIFF stack (+ flats/darks).
        pixel_size_um: detector pixel size in microns (required; read from metadata).
        handedness: in-plane handedness, "left" or "right" (required; not guessable).
        omega_step_deg: rotation per projection in degrees (negative = CW).
        file_pattern: glob for frames (default "*.tif*").
        output_dir: where the reconstruction is written (default <data>/tomo_recon).
        omega_start_deg: omega of the first projection (degrees).
        algorithm: TomoPy recon algorithm — "gridrec" (default, fast CPU), "fbp", "sirt", "art".
        rotation_center: explicit rotation-axis column; omit to auto-find or sweep.
        center_sweep: [lo, hi, step] — write one slice per center to <out>/center_sweep
            for visual selection; the chosen center must then be passed as rotation_center.
        phase_retrieval: apply Paganin phase retrieval (needs propagation_mm + energy_kev).
        propagation_mm: sample-to-detector propagation distance (mm); >0 marks a phase scan.
        energy_kev: beam energy (keV), required when phase_retrieval is on.
        paganin_alpha: Paganin regularization (delta/beta scaling).
        threshold: if set, write a binary sample mask at this reconstructed-value cut.
    """
    if handedness not in ("left", "right"):
        return format_result({
            "tool": "run_tomo_reconstruction", "status": "error",
            "error": "handedness must be 'left' or 'right' — it is not guessable from "
                     "the data (capsule hard rule 3)."})
    args = ["--data", data_folder, "--pattern", file_pattern,
            "--pixel-size-um", str(pixel_size_um), "--handedness", handedness,
            "--omega-start", str(omega_start_deg), "--omega-step", str(omega_step_deg),
            "--algorithm", algorithm]
    if output_dir:
        args += ["--output-dir", output_dir]
    if rotation_center is not None:
        args += ["--rotation-center", str(rotation_center)]
    if center_sweep and len(center_sweep) == 3:
        args += ["--center-sweep", str(center_sweep[0]), str(center_sweep[1]),
                 str(center_sweep[2])]
    if phase_retrieval:
        args += ["--phase-retrieval", "--propagation-mm", str(propagation_mm),
                 "--paganin-alpha", str(paganin_alpha)]
        if energy_kev is not None:
            args += ["--energy-kev", str(energy_kev)]
    if threshold is not None:
        args += ["--threshold", str(threshold)]
    out = await asyncio.to_thread(_run_runner, "reconstruct", args, 7200)
    return format_result(out)


if __name__ == "__main__":
    mcp.run()
