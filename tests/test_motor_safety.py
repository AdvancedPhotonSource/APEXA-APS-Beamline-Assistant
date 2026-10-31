"""Offline safety and cancellation regressions. All Channel Access I/O is stubbed."""
import asyncio
import json
import threading

import pytest

import epics_motor_server as motor


@pytest.fixture
def device(monkeypatch):
    state = {"RBV": 0., "HLM": 100., "LLM": -100., "HLS": 0., "LLS": 0.,
             "DMOV": 1., "LVIO": 0., "SEVR": 0., "RDBD": 0.01,
             "VMAX": 10., "JVEL": 1., "JAR": 1., "EGU": "mm", "DESC": "test"}
    writes = []
    def get_float(prefix, name, field, **kwargs):
        value = state.get(field)
        return (False, 0.) if value is None else (True, value)
    def get(pv, **kwargs):
        value = state.get(pv.rsplit(".", 1)[-1], 0.)
        return (False, "missing") if value is None else (True, str(value))
    def put(pv, value, **kwargs):
        writes.append((pv, value))
        if pv == "AUDIT:m1":
            state["RBV"] = value
        return True, "stub acknowledged"
    monkeypatch.setenv("APEXA_EPICS_WRITE_PVS", "AUDIT:m1")
    monkeypatch.setenv("APEXA_EPICS_STOP_PVS", "AUDIT:m1")
    monkeypatch.delenv("APEXA_EPICS_ALLOW_CONFIG_WRITES", raising=False)
    monkeypatch.delenv("APEXA_EPICS_ALLOW_HOMING", raising=False)
    monkeypatch.delenv("APEXA_EPICS_MAX_MOVE_FRACTION", raising=False)
    monkeypatch.setattr(motor, "_get_float", get_float)
    monkeypatch.setattr(motor, "_caget", get)
    monkeypatch.setattr(motor, "_caput", put)
    monkeypatch.setattr(motor, "_resolve_motor", lambda prefix, name: name)
    motor._MOTION_LOCKS.clear()
    motor._STOP_GENERATION.clear()
    return state, writes


def call(fn, *args, **kwargs):
    return json.loads(asyncio.run(fn(*args, prefix="AUDIT", **kwargs)))


@pytest.mark.parametrize("field", ["RBV", "HLM", "LLM", "HLS", "LLS", "DMOV", "LVIO", "SEVR", "RDBD"])
def test_every_safety_read_is_required(device, field):
    state, writes = device
    state[field] = None
    result = call(motor.move_motor_absolute, "m1", 10., wait=False)
    assert result["error_code"] == "unavailable_state"
    assert not writes


@pytest.mark.parametrize("field,value", [("RBV", float("nan")), ("HLM", float("inf")),
    ("DMOV", 0), ("SEVR", 2), ("LVIO", 1), ("HLS", 1), ("LLS", 1),
    ("HLS", 2), ("LLM", 100), ("RDBD", -1)])
def test_invalid_or_unsafe_state_issues_no_writes(device, field, value):
    state, writes = device
    state[field] = value
    assert not call(motor.move_motor_absolute, "m1", 10., wait=False)["success"]
    assert not writes


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_arguments_never_reach_caput(device, value):
    _, writes = device
    requests = [(motor.move_motor_absolute, ("m1", value), {}),
                (motor.move_motor_relative, ("m1", value), {}),
                (motor.set_motor_velocity, ("m1", value), {}),
                (motor.jog_motor, ("m1", "forward"), {"duration_s": value}),
                (motor.tweak_motor, ("m1", "forward", value), {}),
                (motor.set_motor_limits, ("m1",), {"high_limit": value})]
    for fn, args, kw in requests:
        assert call(fn, *args, **kw)["error_code"] == "invalid_argument"
    assert not writes


def test_writes_default_to_read_only_and_registry_matches_exact_pv(device, monkeypatch):
    _, writes = device
    monkeypatch.delenv("APEXA_EPICS_WRITE_PVS")
    assert call(motor.move_motor_absolute, "m1", 1.)["error_code"] == "motor_not_commissioned"
    monkeypatch.setenv("APEXA_EPICS_WRITE_PVS", "OTHER:m1,AUDIT:m10")
    assert call(motor.move_motor_absolute, "m1", 1.)["error_code"] == "motor_not_commissioned"
    assert not writes
    assert call(motor.stop_motor, "m1")["status"] == "stop_requested"
    assert ("AUDIT:m1.STOP", 1) in writes


def test_model_confirmation_cannot_authorize_large_move(device):
    state, writes = device
    state["RBV"] = -90.
    result = call(motor.move_motor_absolute, "m1", 90., confirm_large_move=True)
    assert result["error_code"] == "large_move"
    assert not writes


def test_completed_move_verifies_target(device):
    result = call(motor.move_motor_absolute, "m1", 10.)
    assert result["completed"] is True
    assert result["final_rbv"] == 10.


def test_done_flag_alone_cannot_claim_success_and_timeout_requests_stop(device, monkeypatch):
    _, writes = device
    monkeypatch.setattr(motor, "_caput", lambda pv, value, **kw: (writes.append((pv, value)) is None, "stub"))
    result = call(motor.move_motor_absolute, "m1", 10., timeout=0.01)
    assert result["timed_out"] and not result["completed"]
    assert ("AUDIT:m1.STOP", 1) in writes
    assert ("AUDIT:m1.JOGF", 0) in writes


def test_tweak_uses_checked_relative_target(device):
    state, writes = device
    state["RBV"] = 99.
    assert call(motor.tweak_motor, "m1", "forward", 2.)["error_code"] == "target_outside_limits"
    assert not writes
    result = call(motor.tweak_motor, "m1", "reverse", 1.)
    assert result["final_rbv"] == 98.
    assert writes == [("AUDIT:m1", 98.)]


def test_configuration_requires_distinct_operator_enablement(device, monkeypatch):
    _, writes = device
    assert call(motor.set_motor_limits, "m1", high_limit=150.)["error_code"] == "operation_disabled"
    assert call(motor.set_motor_description, "m1", "label")["error_code"] == "operation_disabled"
    assert call(motor.home_motor, "m1")["error_code"] == "operation_disabled"
    assert not writes
    monkeypatch.setenv("APEXA_EPICS_ALLOW_CONFIG_WRITES", "1")
    result = call(motor.set_motor_limits, "m1", low_limit=101.)
    assert result["error_code"] == "invalid_limits" and not writes


def test_partial_limit_write_failure_is_not_success(device, monkeypatch):
    _, writes = device
    monkeypatch.setenv("APEXA_EPICS_ALLOW_CONFIG_WRITES", "1")
    def put(pv, value, **kw):
        writes.append((pv, value))
        return (not pv.endswith("LLM")), "stub"
    monkeypatch.setattr(motor, "_caput", put)
    result = call(motor.set_motor_limits, "m1", high_limit=90., low_limit=-90.)
    assert not result["success"]
    assert result["updated"]["HLM"]["success"] and not result["updated"]["LLM"]["success"]


def test_velocity_requires_vmax(device):
    state, writes = device
    state["VMAX"] = None
    assert call(motor.set_motor_velocity, "m1", 1.)["error_code"] == "invalid_velocity_limit"
    assert not writes


def test_jog_travel_envelope_is_checked(device):
    state, writes = device
    state["RBV"] = 99.
    assert call(motor.jog_motor, "m1", "forward", duration_s=1.)["error_code"] == "target_outside_limits"
    assert not writes


def test_cancelled_jog_stops_and_clears_both_fields(device):
    _, writes = device
    async def run():
        task = asyncio.create_task(motor.jog_motor("m1", "forward", prefix="AUDIT", duration_s=10))
        for _ in range(100):
            if ("AUDIT:m1.JOGF", 1) in writes:
                break
            await asyncio.sleep(0.002)
        assert ("AUDIT:m1.JOGF", 1) in writes
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(run())
    assert ("AUDIT:m1.STOP", 1) in writes
    assert ("AUDIT:m1.JOGF", 0) in writes
    assert ("AUDIT:m1.JOGR", 0) in writes


def test_jog_clear_failure_is_visible(device, monkeypatch):
    _, writes = device
    def put(pv, value, **kw):
        writes.append((pv, value))
        return not (pv.endswith("JOGF") and value == 0), "stub failure"
    monkeypatch.setattr(motor, "_caput", put)
    result = call(motor.jog_motor, "m1", "forward", duration_s=.001)
    assert result["error_code"] == "stop_failed"
    assert not result["stop_results"]["JOGF"]["success"]


def test_stop_is_serviced_while_motion_read_blocks(device, monkeypatch):
    _, writes = device
    original_get = motor._get_float
    blocked = threading.Event()
    release = threading.Event()
    def get(prefix, name, field, **kw):
        if field == "DMOV" and ("AUDIT:m1", 10.) in writes:
            blocked.set()
            assert release.wait(3)
        return original_get(prefix, name, field, **kw)
    monkeypatch.setattr(motor, "_get_float", get)
    async def run():
        task = asyncio.create_task(motor.move_motor_absolute("m1", 10., prefix="AUDIT"))
        try:
            for _ in range(100):
                if blocked.is_set():
                    break
                await asyncio.sleep(.002)
            assert blocked.is_set()
            stopped = json.loads(await asyncio.wait_for(motor.stop_motor("m1", prefix="AUDIT"), timeout=.5))
            assert stopped["success"]
        finally:
            release.set()
        result = json.loads(await task)
        assert result["error_code"] == "stop_requested"
    asyncio.run(run())


def test_pv_field_injection_cannot_write(device):
    _, writes = device
    assert call(motor.stop_motor, "m1.VELO")["error_code"] == "invalid_pv"
    assert not writes


def test_unknown_stop_target_cannot_interrupt_unregistered_device(device):
    _, writes = device
    result = call(motor.stop_motor, "m2")
    assert result["error_code"] == "unknown_stop_target"
    assert not writes


def test_alarm_after_command_cannot_be_reported_completed(device, monkeypatch):
    state, writes = device
    original_put = motor._caput
    def put(pv, value, **kwargs):
        result = original_put(pv, value, **kwargs)
        if pv == "AUDIT:m1":
            state["SEVR"] = 2
        return result
    monkeypatch.setattr(motor, "_caput", put)
    result = call(motor.move_motor_absolute, "m1", 10.)
    assert result["error_code"] == "unsafe_state"
    assert not result["completed"]
    assert ("AUDIT:m1.STOP", 1) in writes


def test_repeated_cancellation_drains_late_write_before_cleanup_stop(device, monkeypatch):
    _, writes = device
    entered = threading.Event()
    release = threading.Event()
    def put(pv, value, **kwargs):
        if pv == "AUDIT:m1":
            entered.set()
            assert release.wait(3)
        writes.append((pv, value))
        return True, "stub"
    monkeypatch.setattr(motor, "_caput", put)
    async def run():
        task = asyncio.create_task(motor.move_motor_absolute("m1", 10., prefix="AUDIT"))
        try:
            for _ in range(100):
                if entered.is_set():
                    break
                await asyncio.sleep(.002)
            assert entered.is_set()
            task.cancel()
            await asyncio.sleep(.005)
            task.cancel()
            await asyncio.sleep(.005)
            assert not task.done(), "Cancellation returned while a write could still reach the IOC"
            stopped = json.loads(await asyncio.wait_for(motor.stop_motor("m1", prefix="AUDIT"), .5))
            assert stopped["success"]
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(run())
    write_index = writes.index(("AUDIT:m1", 10.))
    assert any(pv == "AUDIT:m1.STOP" and value == 1 for pv, value in writes[write_index + 1:])
