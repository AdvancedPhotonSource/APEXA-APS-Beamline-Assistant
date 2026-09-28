#!/usr/bin/env python3
"""
EPICS Motor Control MCP Server

Provides tools for reading and controlling EPICS motor records (motorRecord)
at APS beamlines via caget/caput command-line utilities.

All tools work with any IOC that follows the standard motorRecord convention:
  {prefix}:{motor}       → VAL (setpoint / desired position)
  {prefix}:{motor}.RBV   → readback (actual position)
  {prefix}:{motor}.DMOV  → done-moving flag (1 = done, 0 = moving)
  etc.

Reference: https://epics-modules.github.io/motor/motorRecord.html

Safety policy:
  - move_motor_absolute checks soft limits (HLM/LLM) before issuing caput
  - Large moves (>50% of travel range) are rejected unless confirm=True
  - stop_motor is always allowed regardless of other constraints
  - Never set .STOP=0 (arming — not our job)

Author: Beamline Assistant Team / APEXA
Organization: Argonne National Laboratory
"""

import json
import re
import subprocess
import sys
import time
import logging
from typing import Optional

from mcp.server.fastmcp import FastMCP

logging.getLogger("mcp").setLevel(logging.WARNING)
logging.getLogger("fastmcp").setLevel(logging.WARNING)

mcp = FastMCP("epics-motor")

# Default IOC prefix — used when the model omits prefix from tool calls.
# Override with EPICS_MOTOR_PREFIX env var for different beamlines.
import os
DEFAULT_PREFIX = os.environ.get("EPICS_MOTOR_PREFIX", "20idMotSim")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt(result: dict) -> str:
    return json.dumps(result, indent=2, default=str)


def _caget(pv: str, timeout: int = 5) -> tuple[bool, str]:
    """Run `caget -t <pv>` (terse: value only).  Returns (ok, value_or_error)."""
    try:
        r = subprocess.run(
            ["caget", "-t", pv],
            capture_output=True, text=True, timeout=timeout
        )
        if r.returncode != 0:
            return False, r.stderr.strip() or f"caget failed for {pv}"
        return True, r.stdout.strip()
    except FileNotFoundError:
        return False, "caget not found — EPICS Channel Access tools not installed"
    except subprocess.TimeoutExpired:
        return False, f"caget timed out after {timeout}s for {pv}"
    except Exception as e:
        return False, str(e)


def _caput(pv: str, value, timeout: int = 5) -> tuple[bool, str]:
    """Run `caget <pv> <value>`.  Returns (ok, output_or_error)."""
    try:
        r = subprocess.run(
            ["caput", pv, str(value)],
            capture_output=True, text=True, timeout=timeout
        )
        if r.returncode != 0:
            return False, r.stderr.strip() or f"caput failed for {pv}"
        return True, r.stdout.strip()
    except FileNotFoundError:
        return False, "caput not found — EPICS Channel Access tools not installed"
    except subprocess.TimeoutExpired:
        return False, f"caput timed out after {timeout}s"
    except Exception as e:
        return False, str(e)


def _pv(prefix: str, motor: str, field: str = "") -> str:
    """Build a PV string: prefix:motor[.field]"""
    base = f"{prefix}:{motor}"
    return f"{base}.{field}" if field else base


def _get_float(prefix: str, motor: str, field: str, timeout: int = 5) -> tuple[bool, float]:
    ok, val = _caget(_pv(prefix, motor, field), timeout=timeout)
    if not ok:
        return False, 0.0
    try:
        return True, float(val)
    except ValueError:
        return False, 0.0


def _wait_for_dmov(prefix: str, motor: str,
                   poll_interval: float = 0.2, timeout: int = 60) -> bool:
    """Poll DMOV until 1 (motion complete) or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        ok, val = _get_float(prefix, motor, "DMOV")
        if ok and val == 1.0:
            return True
        time.sleep(poll_interval)
    return False


def _discover_motors(prefix: str) -> list[str]:
    """Discover all motor PV names by scanning m1, m2, ... until 4 consecutive failures."""
    motors = []
    consecutive_fails = 0
    i = 1
    while consecutive_fails < 4:
        name = f"m{i}"
        ok, _ = _caget(_pv(prefix, name, "RBV"), timeout=1)
        if ok:
            motors.append(name)
            consecutive_fails = 0
        else:
            consecutive_fails += 1
        i += 1
    return motors


def _resolve_motor(prefix: str, motor: str) -> str:
    """Resolve motor name from DESC field if not a standard PV name.

    Allows users to reference motors by description (e.g. "SamX")
    instead of PV name (e.g. "m1"). Dynamically discovers motors
    from the IOC by scanning until consecutive failures.

    Returns the PV name (e.g. "m3") or the original string if no match.
    """
    # Already a PV name like m1, m2, ..., m99
    if re.match(r'^m\d+$', motor, re.IGNORECASE):
        return motor

    motor_lower = motor.lower().strip()

    # Discover all motors from IOC, then read their DESC fields
    motor_names = _discover_motors(prefix)
    descs = {}
    for name in motor_names:
        ok, desc = _caget(_pv(prefix, name, "DESC"), timeout=1)
        if ok:
            descs[name] = desc.strip()

    # Exact match (case-insensitive)
    for name, desc in descs.items():
        if desc.lower() == motor_lower:
            return name

    # Substring match
    for name, desc in descs.items():
        d = desc.lower()
        if d and (motor_lower in d or d in motor_lower):
            return name

    return motor  # No match — return as-is, let caget give a clear error


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
async def get_motor_position(motor: str, prefix: str = DEFAULT_PREFIX) -> str:
    """Read the actual (readback) position of a motor.

    Args:
        prefix: IOC prefix, e.g. "20idMotSim"
        motor:  Motor name, e.g. "m1"

    Returns:
        JSON with RBV (readback), VAL (setpoint), and EGU (engineering units)
    """
    motor = _resolve_motor(prefix, motor)
    results = {}
    for field, label in [("RBV", "readback"), ("VAL", "setpoint"), ("EGU", "units")]:
        ok, val = _caget(_pv(prefix, motor, field))
        results[label] = val if ok else f"ERROR: {val}"

    return _fmt({
        "tool": "get_motor_position",
        "pv": _pv(prefix, motor),
        **results,
    })


@mcp.tool()
async def get_motor_status(motor: str, prefix: str = DEFAULT_PREFIX) -> str:
    """Read comprehensive status of a motor record.

    Reads: position (RBV/VAL), motion state (DMOV), limit switches (HLS/LLS),
    soft limits (HLM/LLM), velocity (VELO), description (DESC), units (EGU).

    Args:
        prefix: IOC prefix, e.g. "20idMotSim"
        motor:  Motor name, e.g. "m1"

    Returns:
        JSON with all status fields
    """
    motor = _resolve_motor(prefix, motor)
    fields = {
        "RBV":  "readback_position",
        "VAL":  "setpoint_position",
        "DMOV": "done_moving",
        "HLS":  "at_high_limit",
        "LLS":  "at_low_limit",
        "HLM":  "high_soft_limit",
        "LLM":  "low_soft_limit",
        "VELO": "velocity",
        "ACCL": "accel_time_s",
        "EGU":  "units",
        "DESC": "description",
    }

    status = {}
    for field, label in fields.items():
        ok, val = _caget(_pv(prefix, motor, field))
        status[label] = val if ok else f"ERROR: {val}"

    # Derive human-readable motion state
    dmov = status.get("done_moving", "")
    status["motion_state"] = "stationary" if dmov == "1" else "moving" if dmov == "0" else "unknown"

    return _fmt({
        "tool":   "get_motor_status",
        "pv":     _pv(prefix, motor),
        "status": status,
    })


@mcp.tool()
async def move_motor_absolute(
    motor: str,
    position: float,
    prefix: str = DEFAULT_PREFIX,
    wait: bool = True,
    timeout: int = 60,
    confirm_large_move: bool = False,
) -> str:
    """Move a motor to an absolute position.

    Checks soft limits (HLM/LLM) before moving.
    Flags moves that span >50% of the travel range as "large" and requires
    confirm_large_move=True to proceed.

    Args:
        prefix:             IOC prefix, e.g. "20idMotSim"
        motor:              Motor name, e.g. "m1"
        position:           Target position in motor engineering units
        wait:               Wait for DMOV=1 before returning (default: True)
        timeout:            Max seconds to wait for motion complete (default: 60)
        confirm_large_move: Set True to allow moves >50% of travel range

    Returns:
        JSON with motion result and final readback position
    """
    motor = _resolve_motor(prefix, motor)
    # --- Read current state ---
    ok_rbv, rbv      = _get_float(prefix, motor, "RBV")
    ok_hlm, hlm      = _get_float(prefix, motor, "HLM")
    ok_llm, llm      = _get_float(prefix, motor, "LLM")
    ok_hls, hls_val  = _get_float(prefix, motor, "HLS")
    ok_lls, lls_val  = _get_float(prefix, motor, "LLS")
    _, egu           = _caget(_pv(prefix, motor, "EGU"))

    # --- Limit-switch guard ---
    if ok_hls and hls_val == 1.0:
        return _fmt({"error": "Motor is at HIGH limit switch — cannot move further positive"})
    if ok_lls and lls_val == 1.0:
        return _fmt({"error": "Motor is at LOW limit switch — cannot move further negative"})

    # --- Soft limit check ---
    if ok_hlm and position > hlm:
        return _fmt({
            "error": f"Target {position} {egu} exceeds high soft limit {hlm} {egu}",
            "hint":  "Check HLM with get_motor_limits or adjust the limit first"
        })
    if ok_llm and position < llm:
        return _fmt({
            "error": f"Target {position} {egu} below low soft limit {llm} {egu}",
            "hint":  "Check LLM with get_motor_limits or adjust the limit first"
        })

    # --- Large-move guard ---
    if ok_rbv and ok_hlm and ok_llm:
        travel_range = abs(hlm - llm)
        move_size    = abs(position - rbv)
        if travel_range > 0 and move_size > 0.5 * travel_range and not confirm_large_move:
            return _fmt({
                "error":  f"Large move detected: {move_size:.3f} {egu} "
                          f"({100*move_size/travel_range:.0f}% of travel range)",
                "hint":   "Set confirm_large_move=True to proceed",
                "current_rbv": rbv,
                "target":      position,
                "travel_range": travel_range,
            })

    # --- Issue move ---
    ok, out = _caput(_pv(prefix, motor), position)
    if not ok:
        return _fmt({"error": f"caput failed: {out}"})

    if not wait:
        return _fmt({
            "tool":    "move_motor_absolute",
            "pv":      _pv(prefix, motor),
            "target":  position,
            "units":   egu,
            "status":  "move_issued",
            "note":    "wait=False — poll get_motor_status for DMOV=1"
        })

    # --- Wait for completion ---
    done = _wait_for_dmov(prefix, motor, timeout=timeout)
    _, final_rbv = _caget(_pv(prefix, motor, "RBV"))

    return _fmt({
        "tool":      "move_motor_absolute",
        "pv":        _pv(prefix, motor),
        "target":    position,
        "final_rbv": final_rbv,
        "units":     egu,
        "completed": done,
        "timed_out": not done,
    })


@mcp.tool()
async def move_motor_relative(
    motor: str,
    delta: float,
    prefix: str = DEFAULT_PREFIX,
    wait: bool = True,
    timeout: int = 60,
    confirm_large_move: bool = False,
) -> str:
    """Move a motor by a relative amount from its current position.

    Reads the current RBV, computes target = RBV + delta, then calls
    move_motor_absolute with all the same safety checks.

    Args:
        prefix:             IOC prefix, e.g. "20idMotSim"
        motor:              Motor name, e.g. "m1"
        delta:              Relative move in motor engineering units (+/-)
        wait:               Wait for motion complete (default: True)
        timeout:            Max seconds to wait (default: 60)
        confirm_large_move: Required for moves >50% of travel range

    Returns:
        JSON with motion result
    """
    motor = _resolve_motor(prefix, motor)
    ok, rbv = _get_float(prefix, motor, "RBV")
    if not ok:
        return _fmt({"error": f"Cannot read current position (RBV) for {_pv(prefix, motor)}"})

    target = rbv + delta

    # Delegate to absolute move (all safety checks happen there)
    result_json = await move_motor_absolute(
        prefix=prefix, motor=motor, position=target,
        wait=wait, timeout=timeout,
        confirm_large_move=confirm_large_move,
    )
    result = json.loads(result_json)
    result["tool"]        = "move_motor_relative"
    result["delta"]       = delta
    result["start_rbv"]   = rbv
    return _fmt(result)


@mcp.tool()
async def stop_motor(motor: str, prefix: str = DEFAULT_PREFIX) -> str:
    """Stop a motor immediately by setting STOP=1.

    This is always permitted regardless of limits or move size.

    Args:
        prefix: IOC prefix, e.g. "20idMotSim"
        motor:  Motor name, e.g. "m1"

    Returns:
        JSON with stop command result
    """
    motor = _resolve_motor(prefix, motor)
    ok, out = _caput(_pv(prefix, motor, "STOP"), 1)
    _, rbv  = _caget(_pv(prefix, motor, "RBV"))
    _, egu  = _caget(_pv(prefix, motor, "EGU"))

    return _fmt({
        "tool":    "stop_motor",
        "pv":      _pv(prefix, motor),
        "success": ok,
        "rbv_at_stop": rbv,
        "units":   egu,
        "caput_output": out,
    })


@mcp.tool()
async def set_motor_velocity(motor: str, velocity: float, prefix: str = DEFAULT_PREFIX) -> str:
    """Set the motor velocity (VELO field).

    Args:
        prefix:   IOC prefix, e.g. "20idMotSim"
        motor:    Motor name, e.g. "m1"
        velocity: New velocity in EGU/s (must be > 0)

    Returns:
        JSON with result
    """
    motor = _resolve_motor(prefix, motor)
    if velocity <= 0:
        return _fmt({"error": "velocity must be > 0"})

    # Check VMAX if available
    ok_vmax, vmax = _get_float(prefix, motor, "VMAX")
    if ok_vmax and vmax > 0 and velocity > vmax:
        return _fmt({
            "error": f"Requested velocity {velocity} exceeds VMAX {vmax}",
            "hint":  "Reduce velocity or check motor configuration"
        })

    ok, out = _caput(_pv(prefix, motor, "VELO"), velocity)
    _, egu  = _caget(_pv(prefix, motor, "EGU"))

    return _fmt({
        "tool":     "set_motor_velocity",
        "pv":       _pv(prefix, motor, "VELO"),
        "velocity": velocity,
        "units":    f"{egu}/s",
        "success":  ok,
        "output":   out,
    })


@mcp.tool()
async def jog_motor(
    motor: str,
    direction: str,
    prefix: str = DEFAULT_PREFIX,
    duration_s: float = 1.0,
) -> str:
    """Jog a motor forward or reverse for a fixed duration.

    Sets JOGF=1 (or JOGR=1), waits duration_s, then clears the jog field.

    Args:
        prefix:     IOC prefix, e.g. "20idMotSim"
        motor:      Motor name, e.g. "m1"
        direction:  "forward" or "reverse"
        duration_s: How long to jog in seconds (default: 1.0, max: 30)

    Returns:
        JSON with start/end positions
    """
    motor = _resolve_motor(prefix, motor)
    if direction not in ("forward", "reverse"):
        return _fmt({"error": "direction must be 'forward' or 'reverse'"})
    if duration_s <= 0 or duration_s > 30:
        return _fmt({"error": "duration_s must be between 0 and 30 seconds"})

    jog_field = "JOGF" if direction == "forward" else "JOGR"

    _, rbv_before = _caget(_pv(prefix, motor, "RBV"))
    _, egu        = _caget(_pv(prefix, motor, "EGU"))

    ok_start, _ = _caput(_pv(prefix, motor, jog_field), 1)
    if not ok_start:
        return _fmt({"error": f"Failed to start jog on {_pv(prefix, motor, jog_field)}"})

    time.sleep(duration_s)

    _caput(_pv(prefix, motor, jog_field), 0)
    _wait_for_dmov(prefix, motor, timeout=10)

    _, rbv_after = _caget(_pv(prefix, motor, "RBV"))

    return _fmt({
        "tool":       "jog_motor",
        "pv":         _pv(prefix, motor),
        "direction":  direction,
        "duration_s": duration_s,
        "units":      egu,
        "rbv_before": rbv_before,
        "rbv_after":  rbv_after,
    })


@mcp.tool()
async def tweak_motor(motor: str, direction: str, step: float, prefix: str = DEFAULT_PREFIX) -> str:
    """Tweak a motor by a small step using TWV/TWF/TWR fields.

    Sets TWV to step size, then fires TWF (forward) or TWR (reverse).

    Args:
        prefix:    IOC prefix, e.g. "20idMotSim"
        motor:     Motor name, e.g. "m1"
        direction: "forward" or "reverse"
        step:      Step size in EGU (must be > 0)

    Returns:
        JSON with before/after positions
    """
    motor = _resolve_motor(prefix, motor)
    if direction not in ("forward", "reverse"):
        return _fmt({"error": "direction must be 'forward' or 'reverse'"})
    if step <= 0:
        return _fmt({"error": "step must be > 0"})

    tweak_field = "TWF" if direction == "forward" else "TWR"
    _, rbv_before = _caget(_pv(prefix, motor, "RBV"))
    _, egu        = _caget(_pv(prefix, motor, "EGU"))

    _caput(_pv(prefix, motor, "TWV"), step)
    _caput(_pv(prefix, motor, tweak_field), 1)
    _wait_for_dmov(prefix, motor, timeout=30)

    _, rbv_after = _caget(_pv(prefix, motor, "RBV"))

    return _fmt({
        "tool":       "tweak_motor",
        "pv":         _pv(prefix, motor),
        "direction":  direction,
        "step":       step,
        "units":      egu,
        "rbv_before": rbv_before,
        "rbv_after":  rbv_after,
    })


@mcp.tool()
async def get_motor_limits(motor: str, prefix: str = DEFAULT_PREFIX) -> str:
    """Read all limit-related fields for a motor.

    Returns soft limits (HLM/LLM), hard limit switch states (HLS/LLS),
    and DHLM/DLLM (dial limits).

    Args:
        prefix: IOC prefix, e.g. "20idMotSim"
        motor:  Motor name, e.g. "m1"

    Returns:
        JSON with all limit fields
    """
    motor = _resolve_motor(prefix, motor)
    fields = ["HLM", "LLM", "DHLM", "DLLM", "HLS", "LLS", "EGU"]
    result = {}
    for f in fields:
        ok, val = _caget(_pv(prefix, motor, f))
        result[f] = val if ok else f"ERROR: {val}"

    return _fmt({
        "tool":   "get_motor_limits",
        "pv":     _pv(prefix, motor),
        "limits": result,
        "note":   "HLM/LLM are user soft limits; HLS/LLS are hardware limit switch states (1=tripped)"
    })


@mcp.tool()
async def set_motor_limits(
    motor: str,
    prefix: str = DEFAULT_PREFIX,
    high_limit: Optional[float] = None,
    low_limit: Optional[float] = None,
) -> str:
    """Set user soft limits for a motor (HLM and/or LLM).

    Args:
        prefix:     IOC prefix, e.g. "20idMotSim"
        motor:      Motor name, e.g. "m1"
        high_limit: New high soft limit (HLM). Omit to leave unchanged.
        low_limit:  New low soft limit (LLM). Omit to leave unchanged.

    Returns:
        JSON with updated limit values
    """
    motor = _resolve_motor(prefix, motor)
    if high_limit is None and low_limit is None:
        return _fmt({"error": "Provide at least one of high_limit or low_limit"})

    if high_limit is not None and low_limit is not None and high_limit <= low_limit:
        return _fmt({"error": f"high_limit ({high_limit}) must be > low_limit ({low_limit})"})

    results = {}
    if high_limit is not None:
        ok, out = _caput(_pv(prefix, motor, "HLM"), high_limit)
        results["HLM"] = "set" if ok else f"ERROR: {out}"
    if low_limit is not None:
        ok, out = _caput(_pv(prefix, motor, "LLM"), low_limit)
        results["LLM"] = "set" if ok else f"ERROR: {out}"

    _, egu = _caget(_pv(prefix, motor, "EGU"))

    return _fmt({
        "tool":    "set_motor_limits",
        "pv":      _pv(prefix, motor),
        "units":   egu,
        "updated": results,
        "high_limit": high_limit,
        "low_limit":  low_limit,
    })


@mcp.tool()
async def set_motor_description(motor: str, description: str, prefix: str = DEFAULT_PREFIX) -> str:
    """Set the description (DESC field) for a motor.

    This allows referring to motors by name (e.g. "SamX") instead of PV name (e.g. "m1").
    All motor tools auto-resolve descriptions to PV names.

    Args:
        prefix:      IOC prefix, e.g. "20idMotSim"
        motor:       Motor PV name, e.g. "m1"
        description: Human-readable name, e.g. "SamX", "SamY", "DetZ"

    Returns:
        JSON with result
    """
    ok, out = _caput(_pv(prefix, motor, "DESC"), description)
    if not ok:
        return _fmt({"error": f"Failed to set DESC: {out}"})

    # Read back to confirm
    _, desc = _caget(_pv(prefix, motor, "DESC"))
    return _fmt({
        "tool": "set_motor_description",
        "pv": _pv(prefix, motor),
        "description": desc,
        "status": "ok",
        "note": f"Motor {motor} is now '{desc}'. You can use '{desc}' in all motor commands.",
    })


@mcp.tool()
async def list_motors(motor_list: list[str] = None, prefix: str = DEFAULT_PREFIX) -> str:
    """Read positions and status for motors under the same IOC prefix.

    If motor_list is empty or omitted, auto-discovers all motors from the IOC.

    Args:
        prefix:     IOC prefix, e.g. "20idMotSim"
        motor_list: List of motor names, e.g. ["m1", "m2", "m3"]. Omit to list all.

    Returns:
        JSON with position, units, and motion state for each motor
    """
    if not motor_list:
        motor_list = _discover_motors(prefix)
    motors = {}
    for m in motor_list:
        ok_rbv, rbv  = _get_float(prefix, m, "RBV")
        ok_dmov, dmov = _get_float(prefix, m, "DMOV")
        _, egu        = _caget(_pv(prefix, m, "EGU"))
        _, desc       = _caget(_pv(prefix, m, "DESC"))

        motors[m] = {
            "pv":          _pv(prefix, m),
            "rbv":         rbv if ok_rbv else "ERROR",
            "units":       egu,
            "description": desc,
            "moving":      dmov == 0.0 if ok_dmov else "unknown",
        }

    return _fmt({
        "tool":   "list_motors",
        "prefix": prefix,
        "motors": motors,
    })


@mcp.tool()
async def home_motor(motor: str, direction: str = "forward", prefix: str = DEFAULT_PREFIX) -> str:
    """Home a motor using HOMF (forward) or HOMR (reverse).

    Waits for homing to complete (DMOV=1).

    Args:
        prefix:    IOC prefix, e.g. "20idMotSim"
        motor:     Motor name, e.g. "m1"
        direction: "forward" (HOMF) or "reverse" (HOMR) — default: "forward"

    Returns:
        JSON with homing result and final position
    """
    motor = _resolve_motor(prefix, motor)
    if direction not in ("forward", "reverse"):
        return _fmt({"error": "direction must be 'forward' or 'reverse'"})

    hom_field = "HOMF" if direction == "forward" else "HOMR"
    ok, out = _caput(_pv(prefix, motor, hom_field), 1)
    if not ok:
        return _fmt({"error": f"Failed to start homing: {out}"})

    done = _wait_for_dmov(prefix, motor, timeout=120)
    _, rbv = _caget(_pv(prefix, motor, "RBV"))
    _, egu = _caget(_pv(prefix, motor, "EGU"))

    return _fmt({
        "tool":      "home_motor",
        "pv":        _pv(prefix, motor),
        "direction": direction,
        "completed": done,
        "final_rbv": rbv,
        "units":     egu,
    })


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Quick sanity check: is caget on PATH?
    import shutil
    if not shutil.which("caget"):
        print("⚠ caget not found on PATH — EPICS Channel Access tools required", file=sys.stderr)
        print("  Install: yum install epics-base  OR  set up EPICS_BASE and source envs", file=sys.stderr)
    else:
        print("✓ EPICS caget/caput found", file=sys.stderr)
    mcp.run()
