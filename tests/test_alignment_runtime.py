"""Offline alignment contracts: no EPICS, provider, remote or production data I/O."""
import asyncio
import copy
import json
import math
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

import apexa_alignment_runtime as alignment


class Clock:
    def __init__(self):
        self.now = 1_700_000_000.0

    def __call__(self):
        return self.now


@pytest.fixture
def plan():
    return {
        "task": "diode_max_1d_scan", "mode": "simulation", "source_id": "offline-diode-series-1",
        "instrument": {"beamline": "SIM", "motor": "SIM:m1", "monitor": "SIM:diode",
                       "position_units": "mm", "signal_units": "A", "configuration_revision": "optics-1",
                       "configuration": {"energy_kev": 65, "optics": "synthetic"}},
        "scan": {"bounds": [-5, 5], "device_limits": [-10, 10], "initial_position": -5,
                 "max_step": .5, "max_total_travel": 10, "grid_points": 21},
        "budget": {"max_evaluations": 21, "max_seconds": 300},
        "observation": {"signal_bounds": [-1, 200], "settle_seconds": .5,
                        "max_age_seconds": 2, "position_tolerance": .001},
        "simulation": {"peak": .7, "sigma": .6, "amplitude": 100, "baseline": 2,
                       "noise_std": 0, "seed": 42},
    }


@pytest.fixture
def runtime(tmp_path):
    return alignment.AlignmentRuntime(tmp_path / "alignment.db", clock=Clock())


def run(runtime, plan, max_steps=21):
    created = runtime.create(plan)
    return runtime.advance(created["run_id"], created["configuration_hash"], max_steps=max_steps,
                           expected_revision=created["revision"])


def shadow_plan(plan):
    plan = copy.deepcopy(plan)
    plan["mode"] = "shadow"
    plan.pop("simulation")
    return plan


def observation(runtime, status):
    pending = status["pending"]
    runtime.clock.now = pending["requested_at"] + pending["settle_seconds"] + .001
    result = {k: pending[k] for k in ("request_id", "step_index", "position", "position_units", "signal_units",
                                     "source_id", "configuration_hash", "configuration_revision", "requested_at")}
    result.update({"sequence": pending["step_index"], "acquired_at": runtime.clock(),
                   "signal": 2 + 100 * math.exp(-.5 * ((pending["position"] - .7) / .6) ** 2)})
    return result


def test_deterministic_scan_recovers_peak_and_persists_every_transition(runtime, plan):
    first = run(runtime, plan)
    second = run(runtime, plan)
    assert first["status"] == second["status"] == "completed"
    assert first["result"]["fit_accepted"]
    assert first["result"]["fit"]["center"] == pytest.approx(.7, abs=1e-6)
    assert first["result"]["fit"]["fwhm_fraction_of_scan"] == pytest.approx(.141289, abs=1e-5)
    assert not first["motion_executed"] and not first["hardware_alignment_verified"]
    assert first["result"]["evidence_scope"] == "simulation_only"
    from apexa_outcomes import classify_result
    assert classify_result(first).scientific_verdict == "provisional"
    assert not classify_result(first).successful
    a = runtime.status(first["run_id"], include_trajectory=True)
    b = runtime.status(second["run_id"], include_trajectory=True)
    assert [r["signal"] for r in a["observations"]] == [r["signal"] for r in b["observations"]]
    assert [e["kind"] for e in a["events"]] == ["preflight_passed"] + ["proposal_persisted", "observation_committed"] * 21 + ["finished"]
    assert "observations" not in first and "plan" not in first
    assert first["prior"] is None
    assert second["prior"]["run_id"] == first["run_id"]
    assert second["prior"]["safety_evidence"] is False
    json.dumps(a, allow_nan=False)


@pytest.mark.parametrize("scenario,expected", [
    ({"amplitude": 0}, "flat_trace"),
    ({"peak": 5}, "boundary_peak"),
    ({"sigma": 2}, "broad_peak"),
    ({"sigma": .3}, "underresolved_peak"),
    ({"noise_std": 50}, "low_r2"),
])
def test_quality_failures_never_claim_alignment(runtime, plan, scenario, expected):
    plan["simulation"].update(scenario)
    plan["observation"]["signal_bounds"] = [-1000, 1000]
    result = run(runtime, plan)
    assert result["status"] == "partial"
    assert not result["result"]["fit_accepted"]
    failures = result["result"]["quality_failures"]
    assert expected in failures or ("noise_std" in scenario and any(f.startswith("fit_failed:") for f in failures))
    assert result["result"]["recommendation"]["provisional"]
    assert result["result"]["recommendation"]["basis"] == "sample_maximum"


@pytest.mark.parametrize("section,key,value,code", [
    ("scan", "bounds", [float("nan"), 5], "invalid_json"),
    ("scan", "bounds", [5, -5], "invalid_bounds"),
    ("scan", "bounds", [-15, 5], "outside_device_limits"),
    ("scan", "initial_position", 12, "outside_device_limits"),
    ("scan", "initial_position", 0, "step_limit"),
    ("scan", "max_step", .1, "step_limit"),
    ("scan", "max_total_travel", 9, "travel_limit"),
    ("scan", "grid_points", 202, "invalid_integer"),
    ("budget", "max_evaluations", True, "invalid_integer"),
    ("budget", "max_seconds", 0, "invalid_number"),
    ("observation", "settle_seconds", -.1, "invalid_number"),
    ("observation", "position_tolerance", 1, "invalid_tolerance"),
    ("instrument", "configuration_revision", "", "invalid_identity"),
    ("instrument", "configuration", {}, "invalid_configuration"),
])
def test_invalid_plans_fail_before_database_creation(runtime, plan, section, key, value, code):
    plan[section][key] = value
    with pytest.raises(alignment.AlignmentError) as caught:
        runtime.create(plan)
    assert caught.value.code == code
    assert not runtime.database.exists()


@pytest.mark.parametrize("mode", ["hardware", "execute", "live"])
def test_hardware_is_unconditionally_refused(runtime, plan, mode):
    plan["mode"] = mode
    with pytest.raises(alignment.AlignmentError, match="separately qualified broker") as caught:
        runtime.create(plan)
    assert caught.value.code == "broker_unavailable"
    assert not runtime.database.exists()


def test_plan_is_immutable_and_preflight_does_not_create_database(runtime, plan):
    checked = alignment.preflight(plan)
    assert checked["status"] == "ready"
    assert not checked["hardware_preflight_verified"]
    assert not runtime.database.exists()
    status = runtime.create(plan)
    plan["scan"]["bounds"][0] = -100
    stored = runtime.status(status["run_id"], include_trajectory=True)
    assert stored["plan"]["scan"]["bounds"] == [-5, 5]
    assert stored["plan_hash"] == checked["plan_hash"]


def test_misspelled_constraints_are_rejected(runtime, plan):
    plan["fit"] = {"min_r_squared": .99}
    with pytest.raises(alignment.AlignmentError) as caught:
        runtime.create(plan)
    assert caught.value.code == "invalid_plan"


def test_fit_uses_physical_scale_without_an_arbitrary_current_floor(runtime, plan):
    for key in ("amplitude", "baseline"):
        plan["simulation"][key] *= 1e-15
    status = run(runtime, plan)
    assert status["status"] == "completed"
    assert status["result"]["fit"]["center"] == pytest.approx(.7, abs=1e-6)


def test_finite_extreme_simulation_does_not_overflow_generator(runtime, plan):
    plan["simulation"]["sigma"] = 1e-300
    plan["simulation"]["peak"] = 1e308
    status = run(runtime, plan)
    assert status["status"] == "partial" and "flat_trace" in status["result"]["quality_failures"]


@pytest.mark.parametrize("fault,code", [("missing", "missing_observation"), ("stale", "observation_not_fresh"),
                                        ("nonfinite", "invalid_number"), ("configuration", "observation_identity"),
                                        ("position", "observation_position")])
def test_simulated_observation_faults_are_durable_and_finite(runtime, plan, fault, code):
    plan["simulation"]["faults"] = {"2": fault}
    status = run(runtime, plan)
    assert status["status"] == "failed" and status["n_observations"] == 2
    assert status["result"]["reason"] == code
    stored = runtime.status(status["run_id"], include_trajectory=True)
    assert stored["events"][-2]["kind"] == "observation_rejected"
    assert stored["events"][-2]["details"]["error_code"] == code
    json.dumps(stored, allow_nan=False)


def test_evaluation_and_elapsed_budgets_end_partial(runtime, plan):
    plan["budget"]["max_evaluations"] = 6
    status = run(runtime, plan)
    assert status["n_observations"] == 6 and status["status"] == "partial"
    assert status["result"]["reason"] == "evaluation_budget_exhausted"
    assert not status["result"]["fit_accepted"]
    plan["budget"] = {"max_evaluations": 21, "max_seconds": 1}
    status = run(runtime, plan)
    assert status["n_observations"] == 1 and status["status"] == "partial"
    assert status["result"]["reason"] == "wall_budget_exhausted"


def test_restarted_expired_deadline_does_not_observe(runtime, plan):
    status = run(runtime, plan, max_steps=1)
    runtime.clock.now += 301
    resumed = alignment.AlignmentRuntime(runtime.database, clock=runtime.clock)
    status = resumed.advance(status["run_id"], status["configuration_hash"], expected_revision=status["revision"])
    assert status["status"] == "partial" and status["n_observations"] == 1
    assert status["result"]["reason"] == "wall_budget_exhausted"


def test_frozen_replay_preserves_original_time_and_provenance(runtime, plan):
    source = run(runtime, plan)
    rows = runtime.status(source["run_id"], include_trajectory=True)["observations"]
    replay = copy.deepcopy(plan)
    replay["mode"] = "replay"
    replay.pop("simulation")
    replay["replay"] = rows
    runtime.clock.now += 86_400 * 365
    created = runtime.create(replay)
    rows[0]["signal"] = -999  # modifying caller memory cannot change the frozen source
    status = runtime.advance(created["run_id"], created["configuration_hash"])
    assert status["status"] == "completed" and status["prior"] is None
    assert status["result"]["evidence_scope"] == "replay_only"
    assert status["result"]["fit"]["center"] == pytest.approx(.7, abs=1e-6)
    stored = runtime.status(status["run_id"], include_trajectory=True)
    assert stored["observations"][0]["signal"] != -999
    assert stored["observations"][0]["replay_original_request_id"] == f"{source['run_id']}:0"
    assert stored["observations"][0]["received_at"] < stored["started_at"]
    assert stored["replay_sha256"]


def test_replay_missing_and_stale_records_fail_explicitly(runtime, plan):
    source = run(runtime, plan)
    plan["mode"] = "replay"
    plan.pop("simulation")
    plan["replay"] = []
    missing = run(runtime, plan)
    assert missing["status"] == "failed" and missing["result"]["reason"] == "missing_observation"
    plan["replay"] = runtime.status(source["run_id"], include_trajectory=True)["observations"]
    plan["replay"][0]["received_at"] += 3
    stale = run(runtime, plan)
    assert stale["status"] == "failed" and stale["result"]["reason"] == "observation_stale"


def test_complete_shadow_scan_is_advisory_and_requires_fresh_each_step(runtime, plan):
    status = run(runtime, shadow_plan(plan))
    from apexa_outcomes import classify_result
    assert classify_result(status).execution == "pending"
    for index in range(21):
        assert status["status"] == "waiting_observation"
        assert status["pending"]["advisory_only"]
        assert not status["pending"]["motion_executed"]
        assert status["n_observations"] == index
        record = observation(runtime, status)
        # A newly constructed runtime can accept a persisted request.
        resumed = alignment.AlignmentRuntime(runtime.database, clock=runtime.clock)
        status = resumed.submit(status["run_id"], record, expected_revision=status["revision"])
        if index < 20:
            status = resumed.advance(status["run_id"], status["configuration_hash"], expected_revision=status["revision"])
    assert status["status"] == "completed" and status["result"]["fit_accepted"]
    assert status["result"]["evidence_scope"] == "advisory"
    assert status["result"]["observation_trust"] == "caller_supplied_unattested"
    assert not status["hardware_alignment_verified"]


@pytest.mark.parametrize("change,code", [
    ({"source_id": "different-source"}, "observation_identity"),
    ({"configuration_revision": "optics-2"}, "observation_identity"),
    ({"configuration_hash": "other"}, "observation_identity"),
    ({"signal_units": "mA"}, "observation_identity"),
    ({"position": -4}, "observation_position"),
    ({"signal": float("nan")}, "invalid_number"),
    ({"signal": 201}, "observation_signal"),
    ({"sequence": -1}, "invalid_integer"),
    ({"step_index": False}, "invalid_integer"),
    ({"requested_at": 0}, "observation_identity"),
    ({"acquired_at": 0}, "observation_not_fresh"),
    ({"acquired_at": 2_000_000_000}, "observation_not_fresh"),
])
def test_shadow_rejects_invalid_observations(runtime, plan, change, code):
    status = run(runtime, shadow_plan(plan))
    record = observation(runtime, status)
    record.update(change)
    status = runtime.submit(status["run_id"], record, expected_revision=status["revision"])
    assert status["status"] == "failed" and status["n_observations"] == 0
    assert status["result"]["reason"] == code
    json.dumps(status, allow_nan=False)


def test_shadow_checks_age_against_runtime_clock_and_ignores_claimed_receipt(runtime, plan):
    status = run(runtime, shadow_plan(plan))
    record = observation(runtime, status)
    record["received_at"] = record["acquired_at"]
    runtime.clock.now += 3
    status = runtime.submit(status["run_id"], record, expected_revision=status["revision"])
    assert status["result"]["reason"] == "observation_stale"


def test_shadow_sequence_must_advance(runtime, plan):
    status = run(runtime, shadow_plan(plan))
    record = observation(runtime, status)
    status = runtime.submit(status["run_id"], record, expected_revision=status["revision"])
    status = runtime.advance(status["run_id"], status["configuration_hash"])
    record = observation(runtime, status)
    record["sequence"] = 0
    status = runtime.submit(status["run_id"], record, expected_revision=status["revision"])
    assert status["status"] == "failed" and status["n_observations"] == 1
    assert status["result"]["reason"] == "observation_duplicate"


def test_stop_revokes_worker_and_prevents_late_observation_commit(runtime, plan, monkeypatch):
    created = runtime.create(plan)
    original = runtime._generate
    def late_worker(state):
        record = original(state)
        runtime.stop(state["run_id"])
        return record
    monkeypatch.setattr(runtime, "_generate", late_worker)
    with pytest.raises(alignment.AlignmentError) as caught:
        runtime.advance(created["run_id"], created["configuration_hash"])
    assert caught.value.code == "lease_lost"
    stopped = runtime.status(created["run_id"])
    assert stopped["status"] == "stopped" and stopped["n_observations"] == 0
    assert stopped["pending"] is None
    again = runtime.advance(stopped["run_id"], stopped["configuration_hash"])
    assert again["revision"] == stopped["revision"]


def test_stop_shadow_run_rejects_submission(runtime, plan):
    status = run(runtime, shadow_plan(plan))
    record = observation(runtime, status)
    stopped = runtime.stop(status["run_id"])
    with pytest.raises(alignment.AlignmentError) as caught:
        runtime.submit(status["run_id"], record, expected_revision=stopped["revision"])
    assert caught.value.code == "run_terminal"


def test_recover_after_proposal_and_observation_without_duplicates(runtime, plan):
    created = runtime.create(plan)
    token = runtime._claim(created["run_id"], created["configuration_hash"], created["revision"])
    proposed = runtime._propose(created["run_id"], token)
    with pytest.raises(alignment.AlignmentError) as busy:
        runtime.advance(created["run_id"], created["configuration_hash"])
    assert busy.value.code == "run_busy"
    runtime.clock.now += alignment.LEASE_SECONDS + 1
    restarted = alignment.AlignmentRuntime(runtime.database, clock=runtime.clock)
    status = restarted.advance(created["run_id"], created["configuration_hash"], expected_revision=proposed["revision"], max_steps=1)
    assert status["n_observations"] == 1
    with pytest.raises(alignment.AlignmentError) as lost:
        runtime._observe(created["run_id"], token, runtime._generate(proposed))
    assert lost.value.code == "lease_lost"
    # Simulate death after a committed observation, before releasing ownership.
    token = restarted._claim(status["run_id"], status["configuration_hash"], status["revision"])
    state = restarted._propose(status["run_id"], token)
    restarted._observe(status["run_id"], token, restarted._generate(state))
    runtime.clock.now += alignment.LEASE_SECONDS + 1
    completed = runtime.advance(status["run_id"], status["configuration_hash"])
    assert completed["status"] == "completed" and completed["n_observations"] == 21
    stored = runtime.status(status["run_id"], include_trajectory=True)
    assert [r["step_index"] for r in stored["observations"]] == list(range(21))
    assert sum(e["kind"] == "lease_recovered" for e in stored["events"]) == 2


def test_concurrent_advance_has_one_owner(runtime, plan, monkeypatch):
    created = runtime.create(plan)
    entered, release = threading.Event(), threading.Event()
    original = runtime._generate
    def slow(state):
        entered.set()
        assert release.wait(5)
        return original(state)
    monkeypatch.setattr(runtime, "_generate", slow)
    with ThreadPoolExecutor(max_workers=1) as pool:
        worker = pool.submit(runtime.advance, created["run_id"], created["configuration_hash"], max_steps=1)
        assert entered.wait(5)
        try:
            with pytest.raises(alignment.AlignmentError) as caught:
                alignment.AlignmentRuntime(runtime.database, clock=runtime.clock).advance(created["run_id"], created["configuration_hash"])
            assert caught.value.code == "run_busy"
        finally:
            release.set()
        assert worker.result()["n_observations"] == 1


def test_revision_configuration_and_recipe_changes_refuse_resume(runtime, plan, monkeypatch):
    created = runtime.create(plan)
    status = runtime.advance(created["run_id"], created["configuration_hash"], max_steps=1)
    with pytest.raises(alignment.AlignmentError) as revision:
        runtime.advance(status["run_id"], status["configuration_hash"], expected_revision=created["revision"])
    assert revision.value.code == "revision_conflict"
    with pytest.raises(alignment.AlignmentError) as config:
        runtime.advance(status["run_id"], "wrong")
    assert config.value.code == "configuration_changed"
    monkeypatch.setattr(alignment, "recipe_identity", lambda: {"recipe": "changed"})
    with pytest.raises(alignment.AlignmentError) as recipe:
        runtime.advance(status["run_id"], status["configuration_hash"])
    assert recipe.value.code == "recipe_changed"
    assert runtime.status(status["run_id"])["n_observations"] == 1


@pytest.mark.parametrize("change", ["revision", "energy", "mode", "recipe"])
def test_priors_require_exact_configuration_recipe_and_mode(runtime, plan, monkeypatch, change):
    run(runtime, plan)
    if change == "revision":
        plan["instrument"]["configuration_revision"] = "optics-2"
    elif change == "energy":
        plan["instrument"]["configuration"]["energy_kev"] = 66
    elif change == "mode":
        plan = shadow_plan(plan)
    else:
        identity = alignment.recipe_identity()
        monkeypatch.setattr(alignment, "recipe_identity", lambda: {**identity, "recipe": "changed"})
    assert runtime.create(plan)["prior"] is None


def test_canonical_mcp_tools_registered_and_wrappers_exercise_runtime(tmp_path, plan, monkeypatch):
    import apexa_alignment_server as server
    monkeypatch.setattr(server, "RUNTIME_DB", tmp_path / "mcp-runtime.db")
    names = {tool.name for tool in asyncio.run(server.mcp.list_tools())}
    assert {"preflight_alignment", "run_alignment", "get_alignment_status", "resume_alignment",
            "submit_alignment_observation", "stop_alignment"} <= names
    assert json.loads(server.preflight_alignment(plan))["status"] == "ready"
    status = json.loads(server.run_alignment(plan, max_steps=1))
    assert status["n_observations"] == 1
    status = json.loads(server.resume_alignment(status["run_id"], status["configuration_hash"], status["revision"]))
    assert status["status"] == "completed"
    stored = json.loads(server.get_alignment_status(status["run_id"], include_trajectory=True))
    assert len(stored["observations"]) == 21
    plan["mode"] = "hardware"
    assert json.loads(server.run_alignment(plan))["error_code"] == "broker_unavailable"
    listed = json.loads(server.list_alignment_tasks())["tasks"]
    assert next(t for t in listed if t["name"] == "diode_max_1d_scan")["runtime_modes"] == list(alignment.MODES)
    assert all(not t["hardware_execution"] for t in listed)
    assert all(not t["runtime_modes"] for t in listed if t["name"] != "diode_max_1d_scan")
