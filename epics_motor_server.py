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
  - Writes require an operator-configured exact-PV registry (read-only by default).
  - Motion fails closed on missing/nonfinite limits, readback, alarm or motion state.
  - Model-supplied confirmation never authorizes a large move.
  - stop_motor uses a separate I/O lane and bypasses motion/configuration locks.
  - Never set .STOP=0 (arming — not our job)

Author: Beamline Assistant Team / APEXA
Organization: Argonne National Laboratory
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import math
import re
import subprocess
import sys
import time
import logging
from typing import Optional

from apexa_mcp import TracedMCPServer as MCPServer

logging.getLogger("mcp").setLevel(logging.WARNING)
logging.getLogger("fastmcp").setLevel(logging.WARNING)

mcp = MCPServer("epics-motor")

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
            ["caget", "-t", "-n", pv],
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
    """Run `caput <pv> <value>`.  Returns (ok, output_or_error)."""
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
        number = float(val)
        return (True, number) if math.isfinite(number) else (False, 0.0)
    except (ValueError, TypeError, OverflowError):
        return False, 0.0



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


# Reads and motion writes must not consume the independent STOP lane.
_READ_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="epics-read")
_WRITE_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="epics-write")
_STOP_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="epics-stop")
_MOTION_LOCKS: dict[str, asyncio.Lock] = {}
_STOP_GENERATION: dict[str, int] = {}
_PV_PART = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_:-]*$")
_MOTOR_PART = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]*$")


class MotorPolicyError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _error(exc, **details) -> str:
    return _fmt({"success": False, "status": "error", "error": str(exc),
                 "error_code": getattr(exc, "code", "motor_error"), **details})


def _base(prefix: str, motor: str) -> str:
    if not _PV_PART.fullmatch(prefix) or not _MOTOR_PART.fullmatch(motor):
        raise MotorPolicyError("invalid_pv", "Use a motor base name without field suffixes or shell characters")
    return _pv(prefix, motor)


def _finite(name, value, *, positive=False):
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise MotorPolicyError("invalid_argument", f"{name} must be finite")
    if positive and value <= 0:
        raise MotorPolicyError("invalid_argument", f"{name} must be greater than zero")
    return float(value)


def _authorize(prefix, motor, operation="motion"):
    base = _base(prefix, motor)
    allowed = {p.strip() for p in os.environ.get("APEXA_EPICS_WRITE_PVS", "").split(",") if p.strip()}
    if base not in allowed:
        raise MotorPolicyError("motor_not_commissioned", f"Writes to {base} are disabled. An operator must configure the exact base PV in APEXA_EPICS_WRITE_PVS after commissioning; STOP remains available.")
    flag = {"configuration": "APEXA_EPICS_ALLOW_CONFIG_WRITES", "homing": "APEXA_EPICS_ALLOW_HOMING"}.get(operation)
    if flag and os.environ.get(flag, "") != "1":
        raise MotorPolicyError("operation_disabled", f"{operation} is disabled. Only an operator may enable {flag}=1 after commissioning.")
    return base


async def _io(fn, *args, write=False, stop=False, **kwargs):
    from functools import partial
    pool = _STOP_POOL if stop else _WRITE_POOL if write else _READ_POOL
    task = asyncio.get_running_loop().run_in_executor(pool, partial(fn, *args, **kwargs))
    if not (write or stop):
        return await task
    # Do not let cancellation race a late caput against a cleanup STOP. Wait for
    # the bounded write call to settle before the caller runs its cleanup.
    return await _settle(task)


async def _settle(task):
    """Drain bounded writes/cleanup even if cancellation is requested repeatedly."""
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            cancelled = True
    if cancelled:
        raise asyncio.CancelledError
    return result


async def _cleanup(prefix, motor):
    return await _settle(asyncio.create_task(_stop(prefix, motor)))


async def _resolve(prefix, motor):
    # Alias discovery itself must not block the MCP event loop.
    if not _PV_PART.fullmatch(prefix):
        raise MotorPolicyError("invalid_pv", "Invalid IOC prefix")
    resolved = await _io(_resolve_motor, prefix, motor)
    _base(prefix, resolved)
    return resolved


async def _state(prefix, motor):
    fields = ("RBV", "HLM", "LLM", "HLS", "LLS", "DMOV", "LVIO", "SEVR", "RDBD")
    start = time.monotonic()
    values = await asyncio.gather(*(_io(_get_float, prefix, motor, field) for field in fields))
    state = {}
    for field, (ok, value) in zip(fields, values):
        if not ok or not math.isfinite(value):
            raise MotorPolicyError("unavailable_state", f"Cannot read a finite {field} for {_pv(prefix, motor)}; no motion issued")
        state[field] = value
    if time.monotonic() - start > 5:
        raise MotorPolicyError("stale_state", "Motor safety reads exceeded the five-second observation window; no motion issued")
    if state["HLM"] <= state["LLM"] or not state["LLM"] <= state["RBV"] <= state["HLM"]:
        raise MotorPolicyError("invalid_limits", "Soft limits must be ordered and contain the current readback")
    if state["RDBD"] < 0:
        raise MotorPolicyError("invalid_state", "Readback deadband RDBD must be nonnegative")
    if state["DMOV"] != 1 or state["LVIO"] != 0 or state["SEVR"] != 0:
        raise MotorPolicyError("unsafe_state", "Motor must be stationary, without limit violation or alarm")
    if state["HLS"] != 0 or state["LLS"] != 0:
        raise MotorPolicyError("unsafe_state", "A limit switch is active or invalid; operator recovery is required")
    return state


def _target(state, position):
    _finite("position", position)
    if not state["LLM"] <= position <= state["HLM"]:
        raise MotorPolicyError("target_outside_limits", "Target is outside the current soft limits")
    try:
        fraction = float(os.environ.get("APEXA_EPICS_MAX_MOVE_FRACTION", "0.5"))
    except ValueError:
        fraction = float("nan")
    if not math.isfinite(fraction) or not 0 < fraction <= 1:
        raise MotorPolicyError("invalid_policy", "APEXA_EPICS_MAX_MOVE_FRACTION must be finite in (0, 1]")
    if abs(position - state["RBV"]) > fraction * (state["HLM"] - state["LLM"]):
        raise MotorPolicyError("large_move", "Move exceeds the operator-configured travel fraction. confirm_large_move cannot authorize motion; use a commissioned broker/operator plan.")


async def _stop(prefix, motor):
    base = _base(prefix, motor)
    _STOP_GENERATION[base] = _STOP_GENERATION.get(base, 0) + 1
    results = {}
    for field, value in (("STOP", 1), ("JOGF", 0), ("JOGR", 0)):
        ok, out = await _io(_caput, _pv(prefix, motor, field), value, stop=True)
        results[field] = {"success": ok, "output": out}
        if not ok:
            logging.getLogger(__name__).error("Motor stop/clear failed for %s.%s: %s", base, field, out)
    return results


async def _start(prefix, motor, field, value, generation):
    base = _base(prefix, motor)
    if _STOP_GENERATION.get(base, 0) != generation:
        raise MotorPolicyError("stop_requested", "STOP was requested during preflight; no motion issued")
    ok, out = await _io(_caput, _pv(prefix, motor, field), value, write=True)
    if not ok:
        raise MotorPolicyError("write_failed", f"Motor command failed: {out}")
    if _STOP_GENERATION.get(base, 0) != generation:
        # An in-flight write may have reached the IOC after the first STOP.
        await _stop(prefix, motor)
        raise MotorPolicyError("stop_requested", "STOP overlapped an in-flight command; STOP was reissued")


async def _wait_for_dmov(prefix, motor, poll_interval=0.2, timeout=60, target=None, deadband=0.0, generation=None):
    deadline = time.monotonic() + timeout
    base = _base(prefix, motor)
    while time.monotonic() < deadline:
        if generation is not None and _STOP_GENERATION.get(base, 0) != generation:
            raise MotorPolicyError("stop_requested", "Motion interrupted by a STOP request")
        readings = await asyncio.gather(*(_io(_get_float, prefix, motor, field) for field in ("DMOV", "SEVR", "LVIO")))
        ok, value = readings[0]
        if any(not available or not math.isfinite(number) for available, number in readings):
            raise MotorPolicyError("unavailable_state", "Motion/alarm state became unavailable")
        if readings[1][1] != 0 or readings[2][1] != 0:
            raise MotorPolicyError("unsafe_state", "Motor alarm or limit violation occurred during motion")
        if not ok or value not in (0, 1):
            raise MotorPolicyError("unavailable_state", "Motion state became unavailable")
        if value == 1:
            ok, rbv = await _io(_get_float, prefix, motor, "RBV")
            if not ok or not math.isfinite(rbv):
                raise MotorPolicyError("unavailable_state", "Final readback is unavailable")
            if generation is not None and _STOP_GENERATION.get(base, 0) != generation:
                raise MotorPolicyError("stop_requested", "Motion interrupted by a STOP request")
            if target is None or abs(rbv - target) <= max(deadband, 1e-9):
                await _state(prefix, motor)
                return rbv
        await asyncio.sleep(min(poll_interval, max(0, deadline - time.monotonic())))
    raise MotorPolicyError("motion_timeout", "Motion did not reach a verified final state before its timeout")


async def _motion(prefix, motor, *, position=None, delta=None, wait=True, timeout=60, tool="move_motor_absolute"):
    _finite("timeout", timeout, positive=True)
    if timeout > 600:
        raise MotorPolicyError("invalid_argument", "timeout must not exceed 600 seconds")
    base = _authorize(prefix, motor)
    generation = _STOP_GENERATION.get(base, 0)
    lock = _MOTION_LOCKS.setdefault(base, asyncio.Lock())
    async with lock:
        state = await _state(prefix, motor)
        if delta is not None:
            _finite("delta", delta)
            position = state["RBV"] + delta
        _target(state, position)
        started = False
        try:
            started = True  # A failed/ambiguous caput still needs a STOP attempt.
            await _start(prefix, motor, "", position, generation)
            result = {"tool": tool, "pv": base, "target": position, "start_rbv": state["RBV"], "success": True}
            if not wait:
                return _fmt({**result, "status": "move_issued", "completed": False,
                             "note": "Command acknowledged; final position is not yet verified. Poll get_motor_status."})
            final = await _wait_for_dmov(prefix, motor, timeout=timeout, target=position, deadband=state["RDBD"], generation=generation)
            return _fmt({**result, "status": "completed", "completed": True, "timed_out": False, "final_rbv": final})
        except asyncio.CancelledError:
            if started:
                await _cleanup(prefix, motor)
            raise
        except Exception as exc:
            cleanup = await _stop(prefix, motor) if started else {}
            return _error(exc, tool=tool, pv=base, completed=False,
                          timed_out=getattr(exc, "code", "") == "motion_timeout", stop_results=cleanup)


@mcp.tool()
async def get_motor_position(motor: str, prefix: str = DEFAULT_PREFIX) -> str:
    """Read motor RBV, VAL and engineering units without blocking the control service."""
    try:
        motor = await _resolve(prefix, motor)
        result = {}
        for field, label in (("RBV", "readback"), ("VAL", "setpoint"), ("EGU", "units")):
            ok, value = await _io(_caget, _pv(prefix, motor, field))
            result[label] = value if ok else f"ERROR: {value}"
        return _fmt({"tool": "get_motor_position", "pv": _pv(prefix, motor), **result})
    except Exception as exc:
        return _error(exc)


@mcp.tool()
async def get_motor_status(motor: str, prefix: str = DEFAULT_PREFIX) -> str:
    """Read position, motion, alarms, limits, velocity, description and units."""
    try:
        motor = await _resolve(prefix, motor)
        fields = {"RBV": "readback_position", "VAL": "setpoint_position", "DMOV": "done_moving",
                  "HLS": "at_high_limit", "LLS": "at_low_limit", "HLM": "high_soft_limit", "LLM": "low_soft_limit",
                  "LVIO": "limit_violation", "SEVR": "alarm_severity", "VELO": "velocity", "ACCL": "accel_time_s",
                  "EGU": "units", "DESC": "description"}
        values = await asyncio.gather(*(_io(_caget, _pv(prefix, motor, field)) for field in fields))
        status = {label: value if ok else f"ERROR: {value}" for label, (ok, value) in zip(fields.values(), values)}
        status["motion_state"] = {"1": "stationary", "0": "moving"}.get(status["done_moving"], "unknown")
        return _fmt({"tool": "get_motor_status", "pv": _pv(prefix, motor), "status": status})
    except Exception as exc:
        return _error(exc)


@mcp.tool()
async def move_motor_absolute(motor: str, position: float, prefix: str = DEFAULT_PREFIX,
                              wait: bool = True, timeout: int = 60, confirm_large_move: bool = False) -> str:
    """Move a registered motor after fresh fail-closed state checks.

    Writes require operator-configured APEXA_EPICS_WRITE_PVS. Model-supplied
    confirm_large_move is retained for compatibility but never grants approval.
    wait=True verifies final RBV within the motor's RDBD, not only DMOV.
    """
    try:
        _finite("position", position)
        motor = await _resolve(prefix, motor)
        return await _motion(prefix, motor, position=position, wait=wait, timeout=timeout)
    except Exception as exc:
        return _error(exc)


@mcp.tool()
async def move_motor_relative(motor: str, delta: float, prefix: str = DEFAULT_PREFIX,
                              wait: bool = True, timeout: int = 60, confirm_large_move: bool = False) -> str:
    """Move relative to a fresh readback under the same lock and policy as absolute motion."""
    try:
        _finite("delta", delta)
        motor = await _resolve(prefix, motor)
        return await _motion(prefix, motor, delta=delta, wait=wait, timeout=timeout, tool="move_motor_relative")
    except Exception as exc:
        return _error(exc)


@mcp.tool()
async def stop_motor(motor: str, prefix: str = DEFAULT_PREFIX) -> str:
    """Request STOP and clear both jog fields through the independent stop I/O lane.

    Use the exact motor base name, not a description alias: discovery must not
    delay STOP. The device must be in APEXA_EPICS_WRITE_PVS or APEXA_EPICS_STOP_PVS.
    No motion-state checks or ordinary-write/configuration opt-ins apply.
    Command acknowledgement does not establish that the physical motor stopped.
    """
    try:
        base = _base(prefix, motor)
        known = {p.strip() for key in ("APEXA_EPICS_WRITE_PVS", "APEXA_EPICS_STOP_PVS")
                 for p in os.environ.get(key, "").split(",") if p.strip()}
        if base not in known:
            raise MotorPolicyError("unknown_stop_target", "STOP requires an exact known device in APEXA_EPICS_WRITE_PVS or APEXA_EPICS_STOP_PVS; no motion-state checks or write-mode opt-ins apply")
        results = await _stop(prefix, motor)
        success = all(r["success"] for r in results.values())
        return _fmt({"tool": "stop_motor", "pv": base, "success": success,
                     "status": "stop_requested" if success else "error", "writes": results,
                     "error": None if success else "One or more stop/clear writes failed; operator intervention required",
                     "note": "Verify DMOV and readback independently; a successful write is not a verified physical stop."})
    except Exception as exc:
        return _error(exc)


@mcp.tool()
async def set_motor_velocity(motor: str, velocity: float, prefix: str = DEFAULT_PREFIX) -> str:
    """Set positive finite VELO on a registered stationary motor, bounded by a readable VMAX."""
    try:
        _finite("velocity", velocity, positive=True)
        motor = await _resolve(prefix, motor)
        base = _authorize(prefix, motor)
        async with _MOTION_LOCKS.setdefault(base, asyncio.Lock()):
            await _state(prefix, motor)
            ok, vmax = await _io(_get_float, prefix, motor, "VMAX")
            if not ok or not math.isfinite(vmax) or vmax <= 0 or velocity > vmax:
                raise MotorPolicyError("invalid_velocity_limit", "A finite positive VMAX covering the requested velocity is required")
            ok, out = await _io(_caput, _pv(prefix, motor, "VELO"), velocity, write=True)
            return _fmt({"tool": "set_motor_velocity", "pv": base, "velocity": velocity,
                         "success": ok, "status": "completed" if ok else "error", "error": None if ok else out})
    except Exception as exc:
        return _error(exc)


@mcp.tool()
async def jog_motor(motor: str, direction: str, prefix: str = DEFAULT_PREFIX, duration_s: float = 1.0) -> str:
    """Bounded jog on a registered motor with fresh state, travel estimate and guaranteed stop attempts.

    Uses JVEL and JAR to reserve estimated travel/deceleration distance within
    soft limits. Hardware interlocks remain necessary. Maximum duration: 30 s.
    """
    try:
        _finite("duration_s", duration_s, positive=True)
        if direction not in ("forward", "reverse") or duration_s > 30:
            raise MotorPolicyError("invalid_argument", "direction must be forward/reverse and duration_s <= 30")
        motor = await _resolve(prefix, motor)
        base = _authorize(prefix, motor)
        generation = _STOP_GENERATION.get(base, 0)
        async with _MOTION_LOCKS.setdefault(base, asyncio.Lock()):
            state = await _state(prefix, motor)
            ok_v, velocity = await _io(_get_float, prefix, motor, "JVEL")
            ok_a, acceleration = await _io(_get_float, prefix, motor, "JAR")
            if not ok_v or not ok_a or not math.isfinite(velocity) or not math.isfinite(acceleration) or velocity <= 0 or acceleration <= 0:
                raise MotorPolicyError("invalid_jog_limits", "A finite positive JVEL and JAR are required for bounded jog")
            # Conservative estimate: full speed for the interval plus braking.
            travel = velocity * duration_s + velocity * velocity / (2 * acceleration)
            _target(state, state["RBV"] + (travel if direction == "forward" else -travel))
            field = "JOGF" if direction == "forward" else "JOGR"
            failure = None
            cancelled = False
            try:
                await _start(prefix, motor, field, 1, generation)
                end = time.monotonic() + duration_s
                while time.monotonic() < end:
                    if _STOP_GENERATION.get(base, 0) != generation:
                        raise MotorPolicyError("stop_requested", "Jog interrupted by STOP")
                    await asyncio.sleep(min(0.1, max(0, end - time.monotonic())))
            except asyncio.CancelledError:
                cancelled = True
            except Exception as exc:
                failure = exc
            finally:
                cleanup = await _cleanup(prefix, motor)
            if cancelled:
                raise asyncio.CancelledError
            if not all(r["success"] for r in cleanup.values()):
                return _error(MotorPolicyError("stop_failed", "Jog stop/clear write failed"), stop_results=cleanup)
            if failure:
                return _error(failure, stop_results=cleanup)
            rbv = await _wait_for_dmov(prefix, motor, timeout=10)
            return _fmt({"tool": "jog_motor", "pv": base, "direction": direction, "duration_s": duration_s,
                         "success": True, "status": "completed", "rbv_before": state["RBV"], "rbv_after": rbv,
                         "stop_results": cleanup})
    except Exception as exc:
        return _error(exc)


@mcp.tool()
async def tweak_motor(motor: str, direction: str, step: float, prefix: str = DEFAULT_PREFIX) -> str:
    """Perform a checked small relative move; never bypass limits through TWV/TWF/TWR."""
    try:
        _finite("step", step, positive=True)
        if direction not in ("forward", "reverse"):
            raise MotorPolicyError("invalid_argument", "direction must be forward or reverse")
        motor = await _resolve(prefix, motor)
        return await _motion(prefix, motor, delta=step if direction == "forward" else -step,
                             tool="tweak_motor", timeout=30)
    except Exception as exc:
        return _error(exc)


@mcp.tool()
async def get_motor_limits(motor: str, prefix: str = DEFAULT_PREFIX) -> str:
    """Read user/dial soft limits, hardware switches and units."""
    try:
        motor = await _resolve(prefix, motor)
        fields = ("HLM", "LLM", "DHLM", "DLLM", "HLS", "LLS", "EGU")
        values = await asyncio.gather(*(_io(_caget, _pv(prefix, motor, field)) for field in fields))
        return _fmt({"tool": "get_motor_limits", "pv": _pv(prefix, motor),
                     "limits": {field: value if ok else f"ERROR: {value}" for field, (ok, value) in zip(fields, values)}})
    except Exception as exc:
        return _error(exc)


@mcp.tool()
async def set_motor_limits(motor: str, prefix: str = DEFAULT_PREFIX,
                           high_limit: Optional[float] = None, low_limit: Optional[float] = None) -> str:
    """Reconfigure ordered limits only with operator-enabled configuration access.

    Validates both effective limits against fresh state and the current position.
    Multi-field IOC writes are not atomic; partial failure is reported explicitly.
    """
    try:
        if high_limit is None and low_limit is None:
            raise MotorPolicyError("invalid_argument", "Provide high_limit and/or low_limit")
        for name, value in (("high_limit", high_limit), ("low_limit", low_limit)):
            if value is not None:
                _finite(name, value)
        motor = await _resolve(prefix, motor)
        base = _authorize(prefix, motor, "configuration")
        async with _MOTION_LOCKS.setdefault(base, asyncio.Lock()):
            state = await _state(prefix, motor)
            high = state["HLM"] if high_limit is None else high_limit
            low = state["LLM"] if low_limit is None else low_limit
            if high <= low or not low <= state["RBV"] <= high:
                raise MotorPolicyError("invalid_limits", "Effective limits must be ordered and contain the current readback")
            writes = {}
            for field, value in (("HLM", high_limit), ("LLM", low_limit)):
                if value is None:
                    continue
                ok, out = await _io(_caput, _pv(prefix, motor, field), value, write=True)
                writes[field] = {"success": ok, "output": out}
                if not ok:
                    return _error(MotorPolicyError("configuration_write_failed", "Limit update failed; inspect partial updates before further operation"), updated=writes)
            return _fmt({"tool": "set_motor_limits", "pv": base, "success": True, "status": "completed",
                         "updated": writes, "high_limit": high, "low_limit": low})
    except Exception as exc:
        return _error(exc)


@mcp.tool()
async def set_motor_description(motor: str, description: str, prefix: str = DEFAULT_PREFIX) -> str:
    """Set DESC only for a registered motor with explicit operator configuration access."""
    try:
        base = _authorize(prefix, motor, "configuration")
        if not description or len(description.encode()) > 39 or any(ord(c) < 32 for c in description):
            raise MotorPolicyError("invalid_argument", "description must be 1–39 bytes without control characters")
        async with _MOTION_LOCKS.setdefault(base, asyncio.Lock()):
            ok, out = await _io(_caput, _pv(prefix, motor, "DESC"), description, write=True)
            if not ok:
                return _error(MotorPolicyError("write_failed", out))
            ok, value = await _io(_caget, _pv(prefix, motor, "DESC"))
            if not ok or value != description:
                return _error(MotorPolicyError("verification_failed", "DESC readback did not match the requested description"))
            return _fmt({"tool": "set_motor_description", "pv": base, "description": value, "success": True, "status": "completed"})
    except Exception as exc:
        return _error(exc)


@mcp.tool()
async def list_motors(motor_list: list[str] = None, prefix: str = DEFAULT_PREFIX) -> str:
    """Read motor positions/status, optionally discovering records under the IOC prefix."""
    try:
        if not _PV_PART.fullmatch(prefix):
            raise MotorPolicyError("invalid_pv", "Invalid IOC prefix")
        names = motor_list or await _io(_discover_motors, prefix)
        motors = {}
        for name in names:
            _base(prefix, name)
            ok_r, rbv = await _io(_get_float, prefix, name, "RBV")
            ok_d, dmov = await _io(_get_float, prefix, name, "DMOV")
            _, egu = await _io(_caget, _pv(prefix, name, "EGU"))
            _, desc = await _io(_caget, _pv(prefix, name, "DESC"))
            motors[name] = {"pv": _pv(prefix, name), "rbv": rbv if ok_r else "ERROR", "units": egu,
                            "description": desc, "moving": dmov == 0 if ok_d else "unknown"}
        return _fmt({"tool": "list_motors", "prefix": prefix, "motors": motors})
    except Exception as exc:
        return _error(exc)


@mcp.tool()
async def home_motor(motor: str, direction: str = "forward", prefix: str = DEFAULT_PREFIX) -> str:
    """Home only a registered motor with separately commissioned homing enabled.

    Homing travel cannot be inferred from HLM/LLM. An operator must enable
    APEXA_EPICS_ALLOW_HOMING after validating the device's hardware home procedure.
    """
    try:
        if direction not in ("forward", "reverse"):
            raise MotorPolicyError("invalid_argument", "direction must be forward or reverse")
        motor = await _resolve(prefix, motor)
        base = _authorize(prefix, motor, "homing")
        generation = _STOP_GENERATION.get(base, 0)
        async with _MOTION_LOCKS.setdefault(base, asyncio.Lock()):
            await _state(prefix, motor)
            try:
                await _start(prefix, motor, "HOMF" if direction == "forward" else "HOMR", 1, generation)
                # HOMF/HOMR can be acknowledged before DMOV changes. Require a
                # moving observation, then completion, to avoid immediate success.
                begin_deadline = time.monotonic() + 5
                while True:
                    ok, dmov = await _io(_get_float, prefix, motor, "DMOV")
                    if not ok:
                        raise MotorPolicyError("unavailable_state", "Cannot observe homing start")
                    if _STOP_GENERATION.get(base, 0) != generation:
                        raise MotorPolicyError("stop_requested", "Homing interrupted by STOP")
                    if dmov == 0:
                        break
                    if time.monotonic() >= begin_deadline:
                        raise MotorPolicyError("homing_unverified", "Homing motion was not observed; completion is unverified")
                    await asyncio.sleep(0.1)
                final = await _wait_for_dmov(prefix, motor, timeout=120, generation=generation)
                return _fmt({"tool": "home_motor", "pv": base, "direction": direction, "completed": True,
                             "success": True, "status": "completed", "final_rbv": final})
            except asyncio.CancelledError:
                await _cleanup(prefix, motor)
                raise
            except Exception as exc:
                cleanup = await _stop(prefix, motor)
                return _error(exc, completed=False, stop_results=cleanup)
    except Exception as exc:
        return _error(exc)


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
