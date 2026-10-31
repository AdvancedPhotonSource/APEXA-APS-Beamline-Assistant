"""Versioned scientific workflow recipes over the discovered canonical tools.

No scientific parameters are inferred here. A graph schedules existing tools;
their numerical engines, admission controls and scientific verdicts remain the
authority. Read-only primitives do not need to enter a workflow graph.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class WorkflowPlanError(ValueError):
    pass


# (stage ID, canonical tool, required). Order is part of each versioned recipe.
_RECIPES = {
    "calibration_integration": {
        "description": "Calibrate, review the geometry, then integrate an explicit image set.",
        "stages": [("calibrate", "midas_auto_calibrate", True), ("integrate", "midas_integrate_series", True)],
    },
    "ff_reconstruction": {
        "description": "Validate FF parameters, reconstruct, optionally generate a selected layer report.",
        "stages": [("validate", "validate_parameter_file", True), ("reconstruct", "run_ff_hedm_full_workflow", True),
                   ("report", "generate_ff_reconstruction_report", False)],
    },
    "nf_reconstruction": {
        "description": "Validate NF parameters and run the canonical NF reconstruction pipeline.",
        "stages": [("validate", "validate_parameter_file", True), ("reconstruct", "run_nf_hedm_reconstruction", True)],
    },
    "pf_reconstruction": {
        "description": "Validate PF parameters and run the canonical PF reconstruction pipeline.",
        "stages": [("validate", "validate_parameter_file", True), ("reconstruct", "run_pf_hedm_workflow", True)],
    },
    "powder_refinement": {
        "description": "Submit a reviewed powder job. External submission holds for reconciliation, not completion.",
        "stages": [("plan", "plan_refinement", False), ("submit", "powder_refine_submit", True),
                   ("status", "refinement_status", False), ("report", "powder_report", False)],
    },
    "tomography": {
        "description": "Survey a stack and reconstruct with explicitly supplied geometry and selected center.",
        "stages": [("survey", "survey_tomo_stack", True), ("reconstruct", "run_tomo_reconstruction", True)],
    },
    "synthesis": {
        "description": "Inspect, optionally map PCA, fit, optionally analyze kinetics/advisory steps, and report.",
        "stages": [("inspect", "synth_inspect_series", True), ("pca", "synth_pca_map", False),
                   ("fit", "synth_fit_series", True), ("kinetics", "synth_fit_kinetics", False),
                   ("recommend", "synth_recommend_step", False), ("report", "synth_report", True)],
    },
    "alignment": {
        "description": "Delegate software preflight and execution to the persisted simulation/replay/shadow alignment runtime.",
        "stages": [("preflight", "preflight_alignment", True), ("run", "run_alignment", True)],
    },
}
VERSION = "1"
# Only these output paths may cross a tool boundary automatically. No JSONPath,
# array selection, dirname inference, newest-file search or transcript extraction.
_BINDINGS = {
    ("midas_integrate_series", "parameter_file"): ("midas_auto_calibrate", ("refined_param_file",)),
    ("run_ff_hedm_full_workflow", "param_file"): ("validate_parameter_file", ("param_file",)),
    ("run_nf_hedm_reconstruction", "param_file"): ("validate_parameter_file", ("param_file",)),
    ("run_pf_hedm_workflow", "param_file"): ("validate_parameter_file", ("param_file",)),
    ("synth_fit_kinetics", "table_csv"): ("synth_fit_series", ("outputs", "table_csv")),
    ("synth_recommend_step", "table_csv"): ("synth_fit_series", ("outputs", "table_csv")),
    ("refinement_status", "job_id"): ("powder_refine_submit", ("job_id",)),
    ("powder_report", "job_id"): ("powder_refine_submit", ("job_id",)),
}
_FORBIDDEN_OVERRIDES = {"force", "ignore_handbook_traps", "ignore_calibration_gate", "skip_validation", "trust_seed_lsd"}


def _require(value: bool, message: str) -> None:
    if not value:
        raise WorkflowPlanError(message)


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise WorkflowPlanError("Workflow plans must contain finite JSON values") from exc


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _source_hash() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def catalog() -> list[dict]:
    return [{"recipe": name, "recipe_version": VERSION, "description": definition["description"],
             "stages": [{"id": stage, "tool": tool, "required": required, "review_after": True}
                        for stage, tool, required in definition["stages"]],
             "initial_review_required": True, "parameters_inferred": False,
             "hardware_execution": False if name == "alignment" else "not_a_hardware_recipe"}
            for name, definition in _RECIPES.items()]


def _schema(tool: str, schemas: dict) -> dict:
    _require(isinstance(schemas, dict) and tool in schemas, f"Canonical tool is not connected: {tool}")
    value = schemas[tool]
    _require(isinstance(value, dict), f"Invalid discovered schema for {tool}")
    value = value.get("inputSchema", value.get("input_schema", value.get("parameters", value)))
    _require(isinstance(value, dict) and value.get("type", "object") == "object", f"Expected an object input schema for {tool}")
    return json.loads(_canonical(value))


def _check_arguments(tool: str, arguments: dict, schema: dict, bound: set[str] = frozenset()) -> None:
    from jsonschema import Draft202012Validator, SchemaError
    _require(isinstance(arguments, dict), f"{tool} arguments must be an object")
    props = schema.get("properties", {})
    _require(not set(arguments) - set(props), f"Unknown arguments for {tool}: {sorted(set(arguments) - set(props))}")
    _require(not bound - set(props), f"Unknown binding arguments for {tool}")
    _require(not bound & set(arguments), f"{tool} argument cannot be both explicit and bound")
    # Bound fields are absent until a reviewed predecessor result exists. All
    # explicit fields retain the actual server's nested type/value constraints.
    partial = dict(schema)
    partial["required"] = [key for key in schema.get("required", []) if key not in bound]
    try:
        Draft202012Validator.check_schema(partial)
        errors = sorted(Draft202012Validator(partial).iter_errors(arguments), key=lambda e: str(e.path))
    except SchemaError as exc:
        raise WorkflowPlanError(f"Invalid discovered input schema for {tool}") from exc
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.path)
        raise WorkflowPlanError(f"Invalid {tool} argument {location or '<root>'}: {error.message}")
    for key in _FORBIDDEN_OVERRIDES:
        _require(arguments.get(key) in (None, False, "", 0), f"Workflow cannot override {key}; use a separately reviewed direct operation")
    for key in ("skip_stages", "resume_from"):
        _require(not arguments.get(key), f"Workflow cannot infer or skip existing engine state via {key}")


def compile_plan(recipe: str, parameters: dict, *, tool_schemas: dict, dataset_id: str = "") -> dict:
    """Compile user-specified stage dictionaries against connected MCP schemas.

    parameters = {"stages": {stage_id: tool_arguments},
                  "bindings": {target_stage: {argument: {"step": predecessor,
                                                       "path": ["exact", "keys"]}}}}
    Optional stages run only when supplied. Nothing selects geometry/calibrants,
    output directories, phase candidates or compute settings on the user's behalf.
    """
    _require(recipe in _RECIPES, "Unknown scientific workflow recipe")
    _require(isinstance(parameters, dict) and not set(parameters) - {"stages", "bindings"}, "Expected stages and optional bindings")
    params = json.loads(_canonical(parameters))
    stages, bindings = params.get("stages"), params.get("bindings", {})
    _require(isinstance(stages, dict) and isinstance(bindings, dict), "stages/bindings must be objects")
    known = {stage for stage, _, _ in _RECIPES[recipe]["stages"]}
    _require(not set(stages) - known and not set(bindings) - set(stages), "Unknown stage or binding target")
    steps = []
    for stage, tool, required in _RECIPES[recipe]["stages"]:
        _require(not required or stage in stages, f"Recipe requires stage {stage}")
        if stage in stages:
            steps.append({"id": stage, "tool": tool, "arguments": stages[stage],
                          "bindings": bindings.get(stage, {}), "review_after": True})
    plan = {"schema_version": 1, "recipe": recipe, "recipe_version": VERSION, "dataset_id": dataset_id,
            "recipe_source_hash": _source_hash(), "steps": steps,
            "tool_schema_hash": _hash({s["tool"]: _schema(s["tool"], tool_schemas) for s in steps})}
    plan["plan_hash"] = _hash(plan)
    return validate_plan(plan, tool_schemas)


def validate_plan(plan: dict, tool_schemas: dict) -> dict:
    """Reject plan/recipe/schema drift and return an independent canonical copy."""
    _require(isinstance(plan, dict), "Workflow plan must be an object")
    p = json.loads(_canonical(plan))
    expected = {"schema_version", "recipe", "recipe_version", "dataset_id", "recipe_source_hash", "steps", "tool_schema_hash", "plan_hash"}
    _require(set(p) == expected, "Unexpected or missing workflow plan fields")
    _require(type(p["schema_version"]) is int and p["schema_version"] == 1, "Unsupported workflow schema version")
    _require(p["recipe"] in _RECIPES and p["recipe_version"] == VERSION, "Unknown recipe or recipe version")
    _require(p["recipe_source_hash"] == _source_hash(), "Workflow recipe implementation changed; compile a reviewed new plan")
    _require(isinstance(p["dataset_id"], str) and bool(p["dataset_id"].strip()) and len(p["dataset_id"]) <= 512,
             "dataset_id must be a nonempty bounded dataset/revision identifier")
    _require(p["plan_hash"] == _hash({k: v for k, v in p.items() if k != "plan_hash"}), "Workflow plan hash does not match its contents")
    steps = p["steps"]
    _require(isinstance(steps, list) and 1 <= len(steps) <= 12, "Workflow must have 1..12 steps")
    allowed = {stage: (tool, required) for stage, tool, required in _RECIPES[p["recipe"]]["stages"]}
    selected, schemas = {}, {}
    for step in steps:
        _require(isinstance(step, dict) and set(step) == {"id", "tool", "arguments", "bindings", "review_after"}, "Invalid workflow step fields")
        name, tool = step["id"], step["tool"]
        _require(isinstance(name, str) and name in allowed and name not in selected, "Unknown or duplicate recipe stage")
        _require(tool == allowed[name][0] and step["review_after"] is True, "Recipe tool/review gate cannot be overridden")
        bindings = step["bindings"]
        _require(isinstance(bindings, dict), "bindings must be an object")
        for argument, binding in bindings.items():
            _require(isinstance(binding, dict) and set(binding) == {"step", "path"}, "Binding requires exact predecessor step and key path")
            source = selected.get(binding["step"])
            _require(source is not None and isinstance(binding["path"], list) and all(isinstance(k, str) for k in binding["path"]), "Binding must reference an earlier stage with a literal key path")
            _require(_BINDINGS.get((tool, argument)) == (source["tool"], tuple(binding["path"])), "This artifact handoff has no verified recipe binding")
        schemas[tool] = schema = _schema(tool, tool_schemas)
        _check_arguments(tool, step["arguments"], schema, set(bindings))
        selected[name] = step
    wanted = [stage for stage in allowed if stage in selected]
    _require(list(selected) == wanted, "Stages must follow recipe order")
    _require(all(not required or stage in selected for stage, (_, required) in allowed.items()), "Required recipe stage is missing")
    _require(p["tool_schema_hash"] == _hash(schemas), "Connected tool schemas changed; compile a reviewed new plan")
    _validate_semantics(p["recipe"], selected)
    return p


def _validate_semantics(recipe: str, steps: dict) -> None:
    args = {name: step["arguments"] for name, step in steps.items()}
    if recipe.endswith("_reconstruction"):
        pipeline = recipe.split("_")[0]
        _require(args["validate"].get("pipeline") == pipeline, "Declare the matching FF/NF/PF validation pipeline explicitly")
        if "param_file" in args["reconstruct"]:
            _require(args["validate"].get("param_file") == args["reconstruct"]["param_file"], "Validate the same parameter file that will be reconstructed")
    if recipe == "calibration_integration":
        _require(bool(args["integrate"].get("images")), "Workflow integration requires an explicit image snapshot, not a changing glob")
    if recipe == "powder_refinement":
        observations = args["submit"].get("observations")
        _require(isinstance(observations, list) and bool(observations), "Powder submission requires explicit observations")
        for item in observations:
            _require(isinstance(item, dict) and bool(item.get("data_path")) and bool(item.get("instrument_path"))
                     and isinstance(item.get("cif_paths"), list) and bool(item["cif_paths"]),
                     "Powder observations require data_path, instrument_path and explicit cif_paths")
    if recipe == "tomography":
        a, b = args["survey"], args["reconstruct"]
        _require(a.get("data_folder") == b.get("data_folder"), "Survey and reconstruction must use the same stack")
        _require(a.get("pixel_size_um") == b.get("pixel_size_um") and a.get("pixel_size_um") is not None, "Declare matching measured pixel sizes")
        _require(b.get("rotation_center") is not None and not b.get("center_sweep"), "Select a rotation center explicitly; exploratory sweeps remain direct tools followed by a reviewed new plan")
    if recipe == "synthesis":
        source = args["inspect"].get("h5_path")
        for name in ("pca", "fit"):
            if name in args:
                _require(args[name].get("h5_path") == source, "Synthesis stages must use the same processed series")
        _require(bool(args["fit"].get("phases")), "Supply explicit synthesis phase candidates")
        _require(bool(args["fit"].get("output_dir")) and args["report"].get("fit_dir") == args["fit"].get("output_dir"), "Declare the exact fit output directory as the report's fit_dir")
        if "recommend" in steps and "table_csv" in steps["recommend"]["bindings"]:
            _require(args["recommend"].get("signal") == "fractions", "A fit-table recommendation must use signal='fractions'")
    if recipe == "alignment":
        from apexa_alignment_runtime import validate_plan as validate_alignment
        checked = validate_alignment(args["preflight"].get("plan"))
        _require(checked == validate_alignment(args["run"].get("plan")), "Alignment preflight/run plans must match exactly")


def _valid_receipt(source: dict, *, calibration: bool = False, parameters: bool = False) -> None:
    from apexa_outcomes import classify_result
    _require(isinstance(source, dict), "Predecessor receipt must be a structured tool result")
    outcome = classify_result(source)
    _require(outcome.execution == "completed" and outcome.scientific_verdict != "rejected",
             "Artifact binding requires a completed, non-rejected predecessor")
    _require(str(source.get("status", "")).lower() not in {"halt", "invalid", "submitted", "unknown", "partial", "provisional", "warning"},
             "Unresolved or provisional results cannot supply an automatic artifact handoff")
    if parameters:
        _require(source.get("status") == "valid" and not source.get("errors"), "Parameter validation did not pass")
    if calibration:
        metrics, gate = source.get("convergence_metrics") or {}, source.get("gate") or {}
        _require(isinstance(metrics, dict) and isinstance(gate, dict), "Malformed calibration gate evidence")
        candidates = [source.get("gate_passed"), metrics.get("gate_passed"), metrics.get("converged"), gate.get("passed")]
        _require(False not in [v for v in candidates if type(v) is bool], "Calibration reported a failed scientific gate")
        _require(outcome.scientific_verdict == "accepted" or any(v is True for v in candidates),
                 "Automatic calibration reuse requires an explicit passed scientific gate")


def resolve_arguments(step: dict, results: dict) -> dict:
    """Resolve only catalogued literal artifact fields; absence never guesses a path.

    Call validate_plan first. A runtime must revalidate the resolved argument
    dictionary against the connected tool schema immediately before dispatch.
    """
    arguments = json.loads(_canonical(step["arguments"]))
    for argument, binding in step.get("bindings", {}).items():
        rule = _BINDINGS.get((step["tool"], argument))
        _require(rule is not None and tuple(binding["path"]) == rule[1], "Unknown workflow artifact binding")
        source = results.get(binding["step"])
        _valid_receipt(source, calibration=rule[0] == "midas_auto_calibrate", parameters=rule[0] == "validate_parameter_file")
        if source.get("tool") is not None:
            _require(source["tool"] == rule[0], "Predecessor tool receipt identity differs from the binding")
        value = source
        for key in binding["path"]:
            _require(isinstance(value, dict) and key in value, "Reviewed predecessor did not return the required artifact field")
            value = value[key]
        _require(isinstance(value, str) and bool(value.strip()) and "\x00" not in value, "Artifact field must be a nonempty path or job ID")
        arguments[argument] = value
    return arguments
