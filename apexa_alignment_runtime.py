"""Durable, bounded diode scan recommendations; deliberately no hardware I/O.

Simulation, immutable replay and caller-supplied shadow observations share the
same contracts and Gaussian quality check. This is not a commissioned motion
broker. SQLite revisions/leases protect checkpoints across processes; no database
transaction is held while generating or fitting observations.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

TASK = "diode_max_1d_scan"
RECIPE = "bounded_grid_gaussian_v1"
TASK_PATH = Path(__file__).parent / "knowledge_base/alignment_tasks/diode_max_1d_scan.yaml"
MODES = ("simulation", "replay", "shadow")
TERMINAL = frozenset(("completed", "partial", "failed", "stopped"))
LEASE_SECONDS = 30.0


class AlignmentError(ValueError):
    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(detail)


def _require(condition: bool, code: str, detail: str) -> None:
    if not condition:
        raise AlignmentError(code, detail)


def _json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError, RecursionError) as exc:
        raise AlignmentError("invalid_json", "Inputs must be finite JSON values.") from exc


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _number(value: Any, name: str, *, positive: bool = False) -> float:
    _require(isinstance(value, (int, float)) and not isinstance(value, bool),
             "invalid_number", f"{name} must be a finite number.")
    try:
        result = float(value)
    except OverflowError as exc:
        raise AlignmentError("invalid_number", f"{name} must be finite.") from exc
    _require(math.isfinite(result) and (not positive or result > 0),
             "invalid_number", f"{name} must be finite" + (" and positive." if positive else "."))
    return result


def _integer(value: Any, name: str, minimum: int, maximum: int) -> int:
    _require(type(value) is int and minimum <= value <= maximum,
             "invalid_integer", f"{name} must be an integer in [{minimum}, {maximum}].")
    return value


def _text(value: Any, name: str) -> str:
    _require(isinstance(value, str) and bool(value.strip()) and len(value) <= 512,
             "invalid_identity", f"{name} must be a nonempty string of at most 512 characters.")
    return value


def _bounds(value: Any, name: str) -> list[float]:
    _require(isinstance(value, list) and len(value) == 2,
             "invalid_bounds", f"{name} must contain two finite increasing values.")
    lo, hi = [_number(v, name) for v in value]
    _require(lo < hi and math.isfinite(hi - lo), "invalid_bounds", f"{name} must increase finitely.")
    return [lo, hi]


def _keys(value: dict, allowed: set[str], name: str) -> None:
    _require(not set(value) - allowed, "invalid_plan", f"Unknown {name} fields are not accepted.")


def recipe_identity() -> dict:
    return {"recipe": RECIPE,
            "runtime_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "task_sha256": hashlib.sha256(TASK_PATH.read_bytes()).hexdigest()}


def validate_plan(plan: dict) -> dict:
    """Return an isolated canonical plan; no filesystem writes or device access."""
    _require(isinstance(plan, dict), "invalid_plan", "plan must be an object.")
    _require(plan.get("mode") in MODES, "broker_unavailable",
             "Only simulation, replay and read-only shadow are available. Real motion requires a separately qualified broker.")
    p = json.loads(_json(plan))
    allowed = {"task", "mode", "source_id", "instrument", "scan", "budget", "observation", "fit", "simulation", "replay"}
    _require(not set(p) - allowed, "invalid_plan", "Unknown top-level plan fields are not accepted.")
    _require(p.get("task") == TASK, "unsupported_task", f"Only {TASK} has a runtime.")
    p["source_id"] = _text(p.get("source_id"), "source_id")
    instrument = p.get("instrument")
    _require(isinstance(instrument, dict), "invalid_plan", "instrument must be an object.")
    for key in ("beamline", "motor", "monitor", "position_units", "signal_units", "configuration_revision"):
        _text(instrument.get(key), f"instrument.{key}")
    _require(isinstance(instrument.get("configuration"), dict) and bool(instrument["configuration"]),
             "invalid_configuration", "A nonempty instrument.configuration mapping is required.")
    scan = p.get("scan")
    _require(isinstance(scan, dict), "invalid_plan", "scan must be an object.")
    _keys(scan, {"bounds", "device_limits", "initial_position", "grid_points", "max_step", "max_total_travel"}, "scan")
    for key in ("bounds", "device_limits"):
        scan[key] = _bounds(scan.get(key), f"scan.{key}")
    lo, hi = scan["bounds"]
    d_lo, d_hi = scan["device_limits"]
    _require(d_lo <= lo < hi <= d_hi, "outside_device_limits", "Scan bounds exceed declared device limits.")
    scan["initial_position"] = _number(scan.get("initial_position"), "scan.initial_position")
    _require(d_lo <= scan["initial_position"] <= d_hi, "outside_device_limits", "Initial position exceeds device limits.")
    n = scan["grid_points"] = _integer(scan.get("grid_points", 21), "scan.grid_points", 5, 201)
    step = (hi - lo) / (n - 1)
    scan["max_step"] = _number(scan.get("max_step"), "scan.max_step", positive=True)
    scan["max_total_travel"] = _number(scan.get("max_total_travel"), "scan.max_total_travel", positive=True)
    initial_travel = abs(scan["initial_position"] - lo)
    _require(max(initial_travel, step) <= scan["max_step"], "step_limit", "Initial approach or grid spacing exceeds max_step.")
    _require(initial_travel + hi - lo <= scan["max_total_travel"], "travel_limit", "Full planned scan exceeds max_total_travel.")
    budget = p.get("budget")
    _require(isinstance(budget, dict), "invalid_plan", "budget must be an object.")
    _keys(budget, {"max_evaluations", "max_seconds"}, "budget")
    budget["max_evaluations"] = _integer(budget.get("max_evaluations"), "budget.max_evaluations", 1, 201)
    budget["max_seconds"] = _number(budget.get("max_seconds"), "budget.max_seconds", positive=True)
    observation = p.get("observation")
    _require(isinstance(observation, dict), "invalid_plan", "observation must be an object.")
    _keys(observation, {"signal_bounds", "settle_seconds", "max_age_seconds", "position_tolerance"}, "observation")
    observation["signal_bounds"] = _bounds(observation.get("signal_bounds"), "observation.signal_bounds")
    observation["settle_seconds"] = _number(observation.get("settle_seconds"), "observation.settle_seconds")
    _require(observation["settle_seconds"] >= 0, "invalid_number", "settle_seconds cannot be negative.")
    observation["max_age_seconds"] = _number(observation.get("max_age_seconds"), "observation.max_age_seconds", positive=True)
    observation["position_tolerance"] = _number(observation.get("position_tolerance", step * 1e-4), "observation.position_tolerance", positive=True)
    _require(observation["position_tolerance"] <= step / 4, "invalid_tolerance", "Position tolerance cannot exceed one quarter of grid spacing.")
    fit = p.setdefault("fit", {})
    _require(isinstance(fit, dict), "invalid_plan", "fit must be an object.")
    _keys(fit, {"min_r2", "max_fwhm_fraction", "max_center_std_fraction"}, "fit")
    for key, default in (("min_r2", .90), ("max_fwhm_fraction", .25), ("max_center_std_fraction", .02)):
        fit[key] = _number(fit.get(key, default), f"fit.{key}", positive=True)
        _require(fit[key] <= 1, "invalid_fit_criteria", f"fit.{key} must be in (0, 1].")
    if p["mode"] == "simulation":
        _require("replay" not in p, "invalid_plan", "Replay input cannot be mixed with simulation.")
        sim = p.get("simulation")
        _require(isinstance(sim, dict), "invalid_plan", "simulation scenario is required.")
        _keys(sim, {"peak", "sigma", "amplitude", "baseline", "noise_std", "seed", "faults"}, "simulation")
        for key in ("peak", "sigma", "amplitude", "baseline", "noise_std"):
            sim[key] = _number(sim.get(key), f"simulation.{key}", positive=key == "sigma")
        _require(sim["amplitude"] >= 0 and sim["noise_std"] >= 0, "invalid_scenario", "Simulation amplitude and noise cannot be negative.")
        sim["seed"] = _integer(sim.get("seed", 0), "simulation.seed", 0, 2**32 - 1)
        faults = sim.setdefault("faults", {})
        _require(isinstance(faults, dict), "invalid_scenario", "simulation.faults must be a mapping.")
        for index, fault in faults.items():
            _require(index in {str(i) for i in range(n)} and fault in ("missing", "stale", "nonfinite", "configuration", "position"),
                     "invalid_scenario", "Faults must map valid zero-based grid indices to supported fault names.")
    elif p["mode"] == "replay":
        _require("simulation" not in p, "invalid_plan", "Simulation input cannot be mixed with replay.")
        records = p.get("replay")
        _require(isinstance(records, list) and len(records) <= 201 and all(isinstance(r, dict) for r in records),
                 "invalid_replay", "replay must be a list of at most 201 observation objects.")
    else:
        _require("simulation" not in p and "replay" not in p, "invalid_plan", "Shadow plans cannot contain synthetic input.")
    _require(len(_json(p)) <= 1_000_000, "plan_too_large", "Plan exceeds the 1 MB bound.")
    return p


def preflight(plan: dict) -> dict:
    p = validate_plan(plan)
    return {"status": "ready", "task": TASK, "mode": p["mode"], "plan": p,
            "plan_hash": _digest(p), "configuration_hash": _digest(p["instrument"]),
            "recipe_identity": recipe_identity(), "motion_executed": False,
            "hardware_preflight_verified": False,
            "preflight_scope": "Software constraints and caller-declared configuration only; no live interlocks or device limits read.",
            "planned_evaluations": min(p["scan"]["grid_points"], p["budget"]["max_evaluations"]),
            "planned_travel": abs(p["scan"]["initial_position"] - p["scan"]["bounds"][0]) + p["scan"]["bounds"][1] - p["scan"]["bounds"][0],
            "requires_operator_review": True}


def _fit(state: dict, reason: str) -> dict:
    """Quality-gated scientific result; a recommendation never commands a move."""
    rows = state["observations"]
    p = state["plan"]
    result = {"fit_accepted": False, "reason": reason, "motion_executed": False,
              "hardware_alignment_verified": False, "requires_operator_review": True,
              "evidence_scope": {"simulation": "simulation_only", "replay": "replay_only", "shadow": "advisory"}[p["mode"]],
              "observation_trust": "synthetic" if p["mode"] == "simulation" else "caller_supplied_unattested",
              "recommendation": None, "quality_failures": []}
    if rows:
        best = max(rows, key=lambda row: row["signal"])
        result["recommendation"] = {"position": best["position"], "units": p["instrument"]["position_units"],
                                    "basis": "sample_maximum", "provisional": True}
    if len(rows) < 5:
        result["quality_failures"].append("insufficient_samples")
        return result
    import warnings
    import numpy as np
    from scipy.optimize import OptimizeWarning, curve_fit
    x = np.asarray([r["position"] for r in rows])
    y = np.asarray([r["signal"] for r in rows])
    lo, hi = p["scan"]["bounds"]
    span = hi - lo
    # Work in dimensionless coordinates so amperes, nanoamperes and broad
    # coordinate ranges do not change numerical conditioning or quality gates.
    signal_scale = max(float(np.max(np.abs(y))), 1e-300)
    x_scaled = (x - lo) / span
    y_scaled = y / signal_scale
    if float(np.ptp(y_scaled)) <= 1e-10:
        result["quality_failures"].append("flat_trace")
        return result

    def gaussian(x, amplitude, center, sigma, baseline):
        return baseline + amplitude * np.exp(-.5 * ((x - center) / sigma) ** 2)

    center = state["prior"]["position"] if state.get("prior") else float(x[int(np.argmax(y))])
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", OptimizeWarning)
            warnings.simplefilter("error", RuntimeWarning)
            parameters, covariance = curve_fit(
                gaussian, x_scaled, y_scaled,
                p0=[float(np.ptp(y_scaled)), (center - lo) / span, 1 / 12, float(np.min(y_scaled))],
                bounds=([0, 0, .0001, -np.inf], [np.inf, 1, 1, np.inf]), maxfev=4000)
        _require(bool(np.all(np.isfinite(parameters))) and bool(np.all(np.isfinite(covariance))),
                 "fit_nonfinite", "Fit or covariance is nonfinite.")
        residual = float(np.sum((y_scaled - gaussian(x_scaled, *parameters)) ** 2))
        total = float(np.sum((y_scaled - np.mean(y_scaled)) ** 2))
        r2 = 1 - residual / total
        amplitude, center, sigma, baseline = [float(v) for v in parameters]
        amplitude, baseline = amplitude * signal_scale, baseline * signal_scale
        center, sigma = lo + center * span, sigma * span
        center_std = math.sqrt(max(0, float(covariance[1, 1]))) * span
        fwhm_fraction = 2 * math.sqrt(2 * math.log(2)) * sigma / span
        result["fit"] = {"amplitude": amplitude, "center": center, "sigma": sigma,
                         "baseline": baseline, "r2": r2, "center_std": center_std,
                         "fwhm_fraction_of_scan": fwhm_fraction}
        failures = result["quality_failures"]
        if len(rows) != p["scan"]["grid_points"] or reason != "scan_complete":
            failures.append("incomplete_scan")
        if r2 < p["fit"]["min_r2"]:
            failures.append("low_r2")
        if fwhm_fraction > p["fit"]["max_fwhm_fraction"]:
            failures.append("broad_peak")
        if fwhm_fraction * (p["scan"]["grid_points"] - 1) < 2:
            failures.append("underresolved_peak")
        margin = span / (p["scan"]["grid_points"] - 1) / 2
        if not lo + margin < center < hi - margin or int(np.argmax(y)) in (0, len(y) - 1):
            failures.append("boundary_peak")
        if center_std / span > p["fit"]["max_center_std_fraction"]:
            failures.append("uncertain_center")
        if not failures:
            result["fit_accepted"] = True
            result["recommendation"] = {"position": center, "units": p["instrument"]["position_units"],
                                        "basis": "gaussian_fit", "provisional": False}
        _json(result)
    except Exception as exc:
        result.pop("fit", None)
        result["fit_accepted"] = False
        result["recommendation"] = {"position": best["position"], "units": p["instrument"]["position_units"],
                                    "basis": "sample_maximum", "provisional": True}
        result["quality_failures"].append("fit_failed:" + type(exc).__name__)
    return result


class AlignmentRuntime:
    """One process-safe SQLite runtime, with injected clock for offline testing."""

    def __init__(self, database: str | Path, *, clock: Callable[[], float] = time.time):
        self.database = Path(database)
        self.clock = clock

    @contextmanager
    def _db(self):
        self.database.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(self.database), timeout=5)
        con.row_factory = sqlite3.Row
        try:
            con.execute("PRAGMA foreign_keys=ON")
            con.executescript("""
                CREATE TABLE IF NOT EXISTS runtime_runs (
                    run_id TEXT PRIMARY KEY, revision INTEGER NOT NULL,
                    state_json TEXT NOT NULL, lease_token TEXT, lease_until REAL);
                CREATE TABLE IF NOT EXISTS runtime_events (
                    run_id TEXT NOT NULL REFERENCES runtime_runs(run_id), revision INTEGER NOT NULL,
                    kind TEXT NOT NULL, at REAL NOT NULL, details_json TEXT NOT NULL,
                    PRIMARY KEY(run_id, revision));
            """)
            yield con
        finally:
            con.close()

    def _load(self, con, run_id: str) -> dict:
        row = con.execute("SELECT * FROM runtime_runs WHERE run_id=?", (run_id,)).fetchone()
        _require(row is not None, "run_not_found", "Alignment run does not exist.")
        state = json.loads(row["state_json"])
        state["revision"] = row["revision"]
        state["_lease_token"] = row["lease_token"]
        state["_lease_until"] = row["lease_until"]
        return state

    def _save(self, con, state: dict, kind: str, details: dict | None = None):
        previous = state["revision"]
        state["revision"] += 1
        clean = {k: v for k, v in state.items() if not k.startswith("_")}
        changed = con.execute("UPDATE runtime_runs SET revision=?,state_json=? WHERE run_id=? AND revision=?",
                              (state["revision"], _json(clean), state["run_id"], previous)).rowcount
        _require(changed == 1, "revision_conflict", "Run changed concurrently; refresh its status.")
        con.execute("INSERT INTO runtime_events VALUES (?,?,?,?,?)",
                    (state["run_id"], state["revision"], kind, self.clock(), _json(details or {})))

    def create(self, plan: dict) -> dict:
        checked = preflight(plan)
        p = checked["plan"]
        now = self.clock()
        state = {"run_id": uuid.uuid4().hex, "revision": 0, "status": "ready", "plan": p,
                 "plan_hash": checked["plan_hash"], "configuration_hash": checked["configuration_hash"],
                 "recipe_identity": checked["recipe_identity"], "started_at": now,
                 "deadline": now + p["budget"]["max_seconds"], "logical_elapsed": 0.0,
                 "observations": [], "pending": None, "result": None, "prior": None,
                 "replay_sha256": _digest(p["replay"]) if p["mode"] == "replay" else None}
        _require(math.isfinite(state["deadline"]), "invalid_budget", "Deadline must be finite.")
        with self._db() as con:
            con.execute("BEGIN IMMEDIATE")
            # Legacy experiment history is deliberately excluded. Only this exact
            # configuration, recipe, mode and accepted new-runtime fit can seed math.
            for row in con.execute("SELECT state_json FROM runtime_runs ORDER BY rowid DESC LIMIT 500"):
                old = json.loads(row[0])
                if (old["status"] == "completed" and old["configuration_hash"] == state["configuration_hash"]
                        and old["recipe_identity"] == state["recipe_identity"] and old["plan"]["mode"] == p["mode"]
                        and old.get("result", {}).get("fit_accepted")):
                    recommendation = old["result"]["recommendation"]
                    if p["scan"]["bounds"][0] < recommendation["position"] < p["scan"]["bounds"][1]:
                        state["prior"] = {"run_id": old["run_id"], "position": recommendation["position"],
                                          "use": "fit_initial_guess_only", "safety_evidence": False}
                        break
            con.execute("INSERT INTO runtime_runs VALUES (?,?,?,NULL,NULL)",
                        (state["run_id"], 0, _json(state)))
            con.execute("INSERT INTO runtime_events VALUES (?,?,?,?,?)", (state["run_id"], 0, "preflight_passed", now,
                        _json({"plan_hash": state["plan_hash"], "hardware_verified": False})))
            con.commit()
        return self.status(state["run_id"])

    def status(self, run_id: str, *, include_trajectory: bool = False) -> dict:
        with self._db() as con:
            state = self._load(con, run_id)
            events = [dict(r) for r in con.execute("SELECT revision,kind,at,details_json FROM runtime_events WHERE run_id=? ORDER BY revision", (run_id,))] if include_trajectory else []
        p = state["plan"]
        result = {key: state[key] for key in ("run_id", "revision", "status", "plan_hash", "configuration_hash", "recipe_identity", "started_at", "deadline", "pending", "result", "prior", "replay_sha256")}
        execution = {"ready": "pending", "waiting_observation": "pending", "running": "running",
                     "completed": "completed", "partial": "completed", "failed": "failed", "stopped": "cancelled"}[state["status"]]
        result["outcome"] = {"schema_version": 1, "execution": execution,
                             "scientific_verdict": "provisional" if state["status"] in ("completed", "partial") else "unassessed"}
        result.update({"task": TASK, "mode": p["mode"], "n_observations": len(state["observations"]),
                       "motion_executed": False, "hardware_alignment_verified": False,
                       "requires_operator_review": True,
                       "lease_active": bool(state["_lease_token"] and state["_lease_until"] > self.clock()),
                       "deadline_expired": self.clock() >= state["deadline"],
                       "recovery": "Resume with current revision and configuration hash after any lease expires. Pending simulation/replay steps are deterministic; shadow waits for a matching observation."})
        if include_trajectory:
            result["plan"] = p
            result["observations"] = state["observations"]
            result["events"] = [{**{k: v for k, v in row.items() if k != "details_json"}, "details": json.loads(row["details_json"])} for row in events]
        return result

    def _claim(self, run_id: str, configuration_hash: str, expected_revision: int | None) -> str | None:
        with self._db() as con:
            con.execute("BEGIN IMMEDIATE")
            state = self._load(con, run_id)
            _require(state["configuration_hash"] == configuration_hash, "configuration_changed", "Configuration hash does not match the immutable plan.")
            _require(state["recipe_identity"] == recipe_identity(), "recipe_changed", "Runtime or task specification changed; start a new run after review.")
            if expected_revision is not None:
                _require(type(expected_revision) is int and state["revision"] == expected_revision, "revision_conflict", "Run revision changed; refresh its status.")
            if state["status"] in TERMINAL:
                return None
            now = self.clock()
            _require(not state["_lease_token"] or state["_lease_until"] <= now, "run_busy", "Another worker owns this run until its bounded lease expires.")
            token = uuid.uuid4().hex
            if state["_lease_token"]:
                self._save(con, state, "lease_recovered", {"pending_request": (state["pending"] or {}).get("request_id")})
            con.execute("UPDATE runtime_runs SET lease_token=?,lease_until=? WHERE run_id=?", (token, now + LEASE_SECONDS, run_id))
            con.commit()
            return token

    def _transition(self, run_id: str, token: str, kind: str, change: Callable[[dict], Any]) -> dict:
        with self._db() as con:
            con.execute("BEGIN IMMEDIATE")
            state = self._load(con, run_id)
            _require(state["_lease_token"] == token and state["_lease_until"] > self.clock(), "lease_lost", "Worker lease expired or was revoked; refresh status before resuming.")
            _require(state["status"] not in TERMINAL, "run_terminal", "No more observations can be committed to a terminal run.")
            details = change(state)
            self._save(con, state, kind, details)
            con.execute("UPDATE runtime_runs SET lease_until=? WHERE run_id=? AND lease_token=?", (self.clock() + LEASE_SECONDS, run_id, token))
            con.commit()
            return state

    def _release(self, run_id: str, token: str):
        with self._db() as con:
            con.execute("UPDATE runtime_runs SET lease_token=NULL,lease_until=NULL WHERE run_id=? AND lease_token=?", (run_id, token))
            con.commit()

    def _snapshot(self, run_id: str) -> dict:
        with self._db() as con:
            return self._load(con, run_id)

    def _finish(self, run_id: str, token: str, reason: str, *, failed: bool = False):
        before = self._snapshot(run_id)
        result = _fit(before, reason)
        def commit(state):
            _require(state["revision"] == before["revision"], "revision_conflict", "Run changed while fitting.")
            if result["fit_accepted"] and self.clock() >= state["deadline"]:
                result["fit_accepted"] = False
                result["reason"] = "wall_budget_exhausted"
                result["quality_failures"].append("deadline_during_fit")
                result["recommendation"]["provisional"] = True
            state["status"] = "failed" if failed else "completed" if result["fit_accepted"] else "partial"
            state["result"] = result
            state["pending"] = None
            state["finished_at"] = self.clock()
            return {"reason": reason, "fit_accepted": result["fit_accepted"]}
        return self._transition(run_id, token, "finished", commit)

    def _budget_reason(self, state: dict) -> str | None:
        p = state["plan"]
        if self.clock() >= state["deadline"] or state["logical_elapsed"] >= p["budget"]["max_seconds"]:
            return "wall_budget_exhausted"
        if len(state["observations"]) >= p["scan"]["grid_points"]:
            return "scan_complete"
        if len(state["observations"]) >= p["budget"]["max_evaluations"]:
            return "evaluation_budget_exhausted"
        return None

    def _propose(self, run_id: str, token: str) -> dict:
        state = self._snapshot(run_id)
        if state["pending"]:
            return state
        def commit(current):
            p = current["plan"]
            i = len(current["observations"])
            lo, hi = p["scan"]["bounds"]
            requested = current["started_at"] + current["logical_elapsed"] if p["mode"] == "simulation" else self.clock()
            current["pending"] = {"request_id": f"{run_id}:{i}", "step_index": i,
                                  "position": lo + (hi - lo) * (i / (p["scan"]["grid_points"] - 1)),
                                  "position_units": p["instrument"]["position_units"],
                                  "signal_units": p["instrument"]["signal_units"],
                                  "source_id": p["source_id"], "configuration_hash": current["configuration_hash"],
                                  "configuration_revision": p["instrument"]["configuration_revision"],
                                  "requested_at": requested,
                                  "settle_seconds": p["observation"]["settle_seconds"],
                                  "advisory_only": True, "motion_executed": False}
            current["status"] = "waiting_observation" if p["mode"] == "shadow" else "running"
            return {"request_id": current["pending"]["request_id"], "position": current["pending"]["position"]}
        return self._transition(run_id, token, "proposal_persisted", commit)

    def _generate(self, state: dict) -> dict | None:
        p, request = state["plan"], state["pending"]
        i = request["step_index"]
        if p["mode"] == "replay":
            if i >= len(p["replay"]):
                return None
            record = dict(p["replay"][i])
            # Preserve original timestamps/request ID as provenance; the runtime
            # checks them against source time, not today's clock.
            record["replay_original_request_id"] = record.get("request_id")
            record["request_id"] = request["request_id"]
            return record
        sim = p["simulation"]
        fault = sim["faults"].get(str(i))
        if fault == "missing":
            return None
        distance = (request["position"] - sim["peak"]) / sim["sigma"]
        envelope = math.exp(-.5 * distance * distance) if abs(distance) < 40 else 0.0
        signal = sim["baseline"] + sim["amplitude"] * envelope
        signal += random.Random(f"{sim['seed']}:{i}").gauss(0, sim["noise_std"])
        record = {k: request[k] for k in ("request_id", "step_index", "position", "position_units", "signal_units", "source_id", "configuration_hash", "configuration_revision", "requested_at")}
        record.update({"sequence": i, "signal": signal, "acquired_at": request["requested_at"] + request["settle_seconds"] + .001,
                       "received_at": request["requested_at"] + request["settle_seconds"] + .001})
        if fault == "stale":
            record["acquired_at"] = request["requested_at"] - p["observation"]["max_age_seconds"] - 1
        elif fault == "nonfinite":
            record["signal"] = float("nan")
        elif fault == "configuration":
            record["configuration_hash"] = "changed"
        elif fault == "position":
            record["position"] = p["scan"]["device_limits"][1] + 1
        return record

    def _validate_observation(self, state: dict, record: dict | None) -> dict:
        _require(isinstance(record, dict), "missing_observation", "No observation is available for the pending request.")
        p, request = state["plan"], state["pending"]
        _require(request is not None, "no_pending_request", "No observation is currently requested.")
        _integer(record.get("step_index"), "observation.step_index", 0, p["scan"]["grid_points"] - 1)
        for key in ("request_id", "step_index", "source_id", "configuration_hash", "configuration_revision", "position_units", "signal_units"):
            _require(record.get(key) == request[key], "observation_identity", f"Observation {key} does not match the pending request.")
        row = {key: record[key] for key in ("request_id", "step_index", "source_id", "configuration_hash", "configuration_revision", "position_units", "signal_units")}
        row["sequence"] = _integer(record.get("sequence"), "observation.sequence", 0, 2**63 - 1)
        _require(not state["observations"] or row["sequence"] > state["observations"][-1]["sequence"],
                 "observation_duplicate", "Source sequence must increase strictly.")
        for key in ("position", "signal", "requested_at", "acquired_at"):
            row[key] = _number(record.get(key), f"observation.{key}")
        _require(abs(row["position"] - request["position"]) <= p["observation"]["position_tolerance"],
                 "observation_position", "Observed position does not match the requested candidate.")
        lo, hi = p["scan"]["bounds"]
        _require(lo <= row["position"] <= hi, "observation_position", "Observed position is outside scan bounds.")
        lo, hi = p["observation"]["signal_bounds"]
        _require(lo <= row["signal"] <= hi, "observation_signal", "Signal is outside the declared finite range.")
        if p["mode"] == "replay":
            row["received_at"] = _number(record.get("received_at"), "observation.received_at")
            row["replay_original_request_id"] = _text(record.get("replay_original_request_id"), "replay_original_request_id")
            _require(not state["observations"] or row["requested_at"] >= state["observations"][-1]["received_at"],
                     "replay_time_order", "Replay request times must follow previous source receipt.")
        else:
            _require(row["requested_at"] == request["requested_at"], "observation_identity", "Observation request time does not match.")
            row["received_at"] = self.clock() if p["mode"] == "shadow" else request["requested_at"] + request["settle_seconds"] + .001
        _require(row["requested_at"] + p["observation"]["settle_seconds"] <= row["acquired_at"] <= row["received_at"],
                 "observation_not_fresh", "Observation precedes settling or is from the future.")
        _require(row["received_at"] - row["acquired_at"] <= p["observation"]["max_age_seconds"],
                 "observation_stale", "Observation is older than max_age_seconds.")
        return row

    def _observe(self, run_id: str, token: str, record: dict | None):
        state = self._snapshot(run_id)
        try:
            row = self._validate_observation(state, record)
        except AlignmentError as exc:
            self._transition(run_id, token, "observation_rejected", lambda _: {"error_code": exc.code, "detail": exc.detail})
            return self._finish(run_id, token, exc.code, failed=True)
        if self._budget_reason(state):
            return self._finish(run_id, token, self._budget_reason(state))
        elapsed = row["received_at"] - row["requested_at"]
        if state["plan"]["mode"] == "replay" and state["observations"]:
            elapsed = row["received_at"] - state["observations"][-1]["received_at"]
        if state["plan"]["mode"] != "shadow" and state["logical_elapsed"] + elapsed >= state["plan"]["budget"]["max_seconds"]:
            return self._finish(run_id, token, "wall_budget_exhausted")
        def commit(current):
            _require(current["revision"] == state["revision"], "revision_conflict", "Run changed while observing.")
            # Enforce deadline again under the committing transaction.
            _require(self.clock() < current["deadline"], "deadline_reached", "Deadline expired before observation commit; resume to finalize.")
            current["observations"].append(row)
            current["logical_elapsed"] += elapsed
            current["pending"] = None
            current["status"] = "running"
            return {"request_id": row["request_id"], "sequence": row["sequence"]}
        return self._transition(run_id, token, "observation_committed", commit)

    def advance(self, run_id: str, configuration_hash: str, *, max_steps: int = 21,
                expected_revision: int | None = None) -> dict:
        _integer(max_steps, "max_steps", 1, 201)
        token = self._claim(run_id, configuration_hash, expected_revision)
        if token is None:
            return self.status(run_id)
        try:
            for _ in range(max_steps):
                state = self._snapshot(run_id)
                reason = self._budget_reason(state)
                if reason:
                    self._finish(run_id, token, reason)
                    break
                state = self._propose(run_id, token)
                if state["plan"]["mode"] == "shadow":
                    break
                state = self._observe(run_id, token, self._generate(state))
                if state["status"] in TERMINAL:
                    break
            state = self._snapshot(run_id)
            if state["status"] not in TERMINAL:
                reason = self._budget_reason(state)
                if reason:
                    self._finish(run_id, token, reason)
        finally:
            self._release(run_id, token)
        return self.status(run_id)

    def submit(self, run_id: str, observation: dict, *, expected_revision: int) -> dict:
        state = self._snapshot(run_id)
        _require(state["plan"]["mode"] == "shadow", "not_shadow", "External observations are accepted only in shadow mode.")
        _require(state["status"] not in TERMINAL, "run_terminal", "Run is terminal; no further observations are accepted.")
        _require(state["pending"] is not None, "no_pending_request", "Advance the run to persist a proposal first.")
        token = self._claim(run_id, state["configuration_hash"], expected_revision)
        _require(token is not None, "run_terminal", "Run is terminal.")
        try:
            state = self._observe(run_id, token, observation)
            if state["status"] not in TERMINAL:
                reason = self._budget_reason(state)
                if reason:
                    self._finish(run_id, token, reason)
        finally:
            self._release(run_id, token)
        return self.status(run_id)

    def stop(self, run_id: str, *, expected_revision: int | None = None) -> dict:
        with self._db() as con:
            con.execute("BEGIN IMMEDIATE")
            state = self._load(con, run_id)
            if expected_revision is not None:
                _require(type(expected_revision) is int and state["revision"] == expected_revision, "revision_conflict", "Run revision changed; refresh its status.")
            if state["status"] not in TERMINAL:
                state["status"] = "stopped"
                state["pending"] = None
                state["finished_at"] = self.clock()
                state["result"] = {"reason": "operator_stop", "fit_accepted": False,
                                   "motion_executed": False, "hardware_alignment_verified": False,
                                   "requires_operator_review": True,
                                   "recovery": "No automatic return move. This stops recommendations only; use the beamline's independent stop for any externally controlled hardware."}
                self._save(con, state, "operator_stopped")
                con.execute("UPDATE runtime_runs SET lease_token=NULL,lease_until=NULL WHERE run_id=?", (run_id,))
                con.commit()
        return self.status(run_id)
