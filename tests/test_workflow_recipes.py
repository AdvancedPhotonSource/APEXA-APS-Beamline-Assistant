"""Recipe contracts against actual MCP schemas; no engine/provider/device calls."""
import ast
import asyncio
import copy
import importlib
import json
from pathlib import Path

import pytest

import apexa_workflow_recipes as recipes

REPO = Path(__file__).resolve().parents[1]
MODULES = ("midas_comprehensive_server", "gsas2_server", "tomo_server", "synthesis_server", "apexa_alignment_server")


@pytest.fixture(scope="module")
def schemas():
    result = {}
    for module_name in MODULES:
        module = importlib.import_module(module_name)
        for tool in asyncio.run(module.mcp.list_tools()):
            result[tool.name] = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema")
    return result


def alignment_plan():
    return {"task": "diode_max_1d_scan", "mode": "simulation", "source_id": "synthetic-series",
            "instrument": {"beamline": "SIM", "motor": "SIM:m1", "monitor": "SIM:diode",
                "position_units": "mm", "signal_units": "A", "configuration_revision": "1",
                "configuration": {"energy_kev": 65}},
            "scan": {"bounds": [-5, 5], "device_limits": [-5, 5], "initial_position": -5,
                "max_step": 1, "max_total_travel": 10, "grid_points": 21},
            "budget": {"max_evaluations": 21, "max_seconds": 60},
            "observation": {"signal_bounds": [0, 200], "settle_seconds": .1, "max_age_seconds": 1},
            "simulation": {"peak": .5, "sigma": .6, "amplitude": 100, "baseline": 2, "noise_std": 0}}


def parameters(recipe):
    if recipe == "calibration_integration":
        return {"stages": {"calibrate": {"image_file": "/data/cal.h5", "output_dir": "/out/cal"},
                           "integrate": {"parameter_file": "/out/cal/refined.txt", "images": ["/data/sample.h5"], "result_folder": "/out/int"}}}
    if recipe.endswith("_reconstruction"):
        mode = recipe.split("_")[0]
        return {"stages": {"validate": {"param_file": f"/data/{mode}.txt", "pipeline": mode},
                           "reconstruct": {"param_file": f"/data/{mode}.txt", "result_folder": f"/out/{mode}"}}}
    if recipe == "powder_refinement":
        return {"stages": {"submit": {"observations": [{"data_path": "/data/pattern.xye", "instrument_path": "/data/instrument.instprm", "cif_paths": ["/data/phase.cif"]}], "output_dir": "/out/powder"}}}
    if recipe == "tomography":
        return {"stages": {"survey": {"data_folder": "/data/tomo", "pixel_size_um": 1.5},
                           "reconstruct": {"data_folder": "/data/tomo", "pixel_size_um": 1.5,
                               "handedness": "left", "omega_step_deg": .1, "rotation_center": 1024.2, "output_dir": "/out/tomo"}}}
    if recipe == "synthesis":
        return {"stages": {"inspect": {"h5_path": "/data/processed.h5"},
                           "fit": {"h5_path": "/data/processed.h5", "phases": ["phase"], "output_dir": "/out/fit"},
                           "report": {"fit_dir": "/out/fit"}}}
    return {"stages": {"preflight": {"plan": alignment_plan()}, "run": {"plan": alignment_plan()}}}


def compile(recipe, schemas, params=None):
    return recipes.compile_plan(recipe, params or parameters(recipe), tool_schemas=schemas, dataset_id="synthetic-dataset-v1")


def test_catalog_tool_names_and_arguments_match_registered_source(schemas):
    functions = {}
    for module in MODULES:
        tree = ast.parse((REPO / f"{module}.py").read_text())
        functions.update({n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))})
    for recipe in recipes.catalog():
        for stage in recipe["stages"]:
            name = stage["tool"]
            assert name in schemas and name in functions
            signature = functions[name].args
            source_args = {a.arg for a in [*signature.posonlyargs, *signature.args, *signature.kwonlyargs]}
            assert set(schemas[name]["properties"]) == source_args
            required = [a.arg for a in signature.args[:len(signature.args) - len(signature.defaults)]]
            assert set(schemas[name].get("required", [])) == set(required)


@pytest.mark.parametrize("recipe", [r["recipe"] for r in recipes.catalog()])
def test_all_recipes_compile_against_real_discovered_schemas(recipe, schemas):
    plan = compile(recipe, schemas)
    assert recipes.validate_plan(plan, schemas) == plan
    assert len(plan["plan_hash"]) == len(plan["tool_schema_hash"]) == 64
    assert all(step["review_after"] for step in plan["steps"])
    assert [step["arguments"] for step in plan["steps"]] == list(parameters(recipe)["stages"].values())


def test_no_inferred_defaults_or_caller_mutation(schemas):
    params = parameters("calibration_integration")
    plan = compile("calibration_integration", schemas, params)
    params["stages"]["calibrate"]["energy_kev"] = 999
    assert "energy_kev" not in plan["steps"][0]["arguments"]
    assert "n_cpus" not in plan["steps"][1]["arguments"]


def test_required_tool_and_argument_fail_before_execution(schemas):
    params = parameters("calibration_integration")
    params["stages"]["calibrate"].pop("image_file")
    with pytest.raises(recipes.WorkflowPlanError, match="required"):
        compile("calibration_integration", schemas, params)
    absent = dict(schemas)
    absent.pop("midas_auto_calibrate")
    with pytest.raises(recipes.WorkflowPlanError, match="not connected"):
        compile("calibration_integration", absent)


@pytest.mark.parametrize("args", [{"n_iterations": "forty"}, {"unknown_flag": True}, {"ignore_calibration_gate": True}, {"energy_kev": float("nan")}])
def test_schema_types_unknown_keys_and_bypasses_rejected(schemas, args):
    params = parameters("calibration_integration")
    params["stages"]["calibrate"].update(args)
    with pytest.raises(recipes.WorkflowPlanError):
        compile("calibration_integration", schemas, params)


def test_plan_and_live_schema_drift_refuse(schemas):
    plan = compile("calibration_integration", schemas)
    changed = copy.deepcopy(plan)
    changed["steps"][0]["arguments"]["image_file"] = "/other/data.h5"
    with pytest.raises(recipes.WorkflowPlanError, match="hash"):
        recipes.validate_plan(changed, schemas)
    changed_schemas = copy.deepcopy(schemas)
    changed_schemas["midas_auto_calibrate"]["description"] = "new tool contract"
    with pytest.raises(recipes.WorkflowPlanError, match="schemas changed"):
        recipes.validate_plan(plan, changed_schemas)


def test_recipe_change_and_edited_tool_or_review_gate_refuse(schemas, monkeypatch):
    plan = compile("calibration_integration", schemas)
    monkeypatch.setattr(recipes, "_source_hash", lambda: "changed")
    with pytest.raises(recipes.WorkflowPlanError, match="implementation changed"):
        recipes.validate_plan(plan, schemas)
    monkeypatch.undo()
    for field, value in (("tool", "run_command"), ("review_after", False)):
        changed = copy.deepcopy(plan)
        changed["steps"][0][field] = value
        changed["plan_hash"] = recipes._hash({k: v for k, v in changed.items() if k != "plan_hash"})
        with pytest.raises(recipes.WorkflowPlanError, match="cannot be overridden"):
            recipes.validate_plan(changed, schemas)


def calibration_binding(schemas):
    params = parameters("calibration_integration")
    del params["stages"]["integrate"]["parameter_file"]
    params["bindings"] = {"integrate": {"parameter_file": {"step": "calibrate", "path": ["refined_param_file"]}}}
    return compile("calibration_integration", schemas, params)["steps"][1]


def test_explicit_calibration_artifact_binding_preserves_exact_path(schemas):
    step = calibration_binding(schemas)
    result = {"tool": "midas_auto_calibrate", "status": "success", "refined_param_file": "/out/cal/selected.txt",
              "convergence_metrics": {"gate_passed": True}}
    assert recipes.resolve_arguments(step, {"calibrate": result})["parameter_file"] == "/out/cal/selected.txt"
    assert "parameter_file" not in step["arguments"]


@pytest.mark.parametrize("changes", [
    {"refined_param_file": None}, {"convergence_metrics": {}},
    {"status": "warning"}, {"status": "error"}, {"convergence_metrics": {"gate_passed": False}},
    {"tool": "different_tool"}, {"refined_param_file": ["first", "second"]},
])
def test_missing_uncertain_or_rejected_calibration_never_guesses_handoff(schemas, changes):
    result = {"tool": "midas_auto_calibrate", "status": "success", "refined_param_file": "/out/cal/selected.txt",
              "convergence_metrics": {"gate_passed": True}}
    result.update(changes)
    with pytest.raises(recipes.WorkflowPlanError):
        recipes.resolve_arguments(calibration_binding(schemas), {"calibrate": result})


def test_synthesis_fit_table_binding_and_declared_report_directory(schemas):
    params = parameters("synthesis")
    params["stages"]["kinetics"] = {"phase_column": "weight1"}
    params["bindings"] = {"kinetics": {"table_csv": {"step": "fit", "path": ["outputs", "table_csv"]}}}
    plan = compile("synthesis", schemas, params)
    step = next(s for s in plan["steps"] if s["id"] == "kinetics")
    assert recipes.resolve_arguments(step, {"fit": {"status": "success", "outputs": {"table_csv": "/exact/fit.csv"}}})["table_csv"] == "/exact/fit.csv"
    params["stages"]["report"]["fit_dir"] = "/unrelated"
    with pytest.raises(recipes.WorkflowPlanError, match="fit_dir"):
        compile("synthesis", schemas, params)


def test_arbitrary_or_forward_bindings_and_unselected_stages_refused(schemas):
    params = parameters("calibration_integration")
    del params["stages"]["integrate"]["parameter_file"]
    params["bindings"] = {"integrate": {"parameter_file": {"step": "calibrate", "path": ["stdout"]}}}
    with pytest.raises(recipes.WorkflowPlanError, match="verified recipe binding"):
        compile("calibration_integration", schemas, params)
    params["bindings"]["integrate"]["parameter_file"] = {"step": "later", "path": ["refined_param_file"]}
    with pytest.raises(recipes.WorkflowPlanError, match="earlier stage"):
        compile("calibration_integration", schemas, params)


@pytest.mark.parametrize("recipe,stage,key,value", [
    ("ff_reconstruction", "validate", "pipeline", "nf"),
    ("nf_reconstruction", "reconstruct", "param_file", "/different.txt"),
    ("pf_reconstruction", "reconstruct", "skip_stages", "indexing"),
    ("tomography", "reconstruct", "rotation_center", None),
    ("tomography", "reconstruct", "data_folder", "/different"),
    ("calibration_integration", "integrate", "images", []),
    ("synthesis", "fit", "phases", []),
])
def test_scientific_handoff_invariants(recipe, stage, key, value, schemas):
    params = parameters(recipe)
    params["stages"][stage][key] = value
    with pytest.raises(recipes.WorkflowPlanError):
        compile(recipe, schemas, params)


def test_alignment_delegates_exact_existing_plan_and_rejects_hardware(schemas):
    params = parameters("alignment")
    params["stages"]["run"]["plan"]["instrument"]["configuration_revision"] = "changed"
    with pytest.raises(recipes.WorkflowPlanError, match="match exactly"):
        compile("alignment", schemas, params)
    params = parameters("alignment")
    for stage in params["stages"].values():
        stage["plan"]["mode"] = "hardware"
    with pytest.raises(ValueError, match="qualified broker"):
        compile("alignment", schemas, params)


def test_powder_submission_receipt_cannot_bind_as_completed_job(schemas):
    params = parameters("powder_refinement")
    params["stages"]["status"] = {}
    params["bindings"] = {"status": {"job_id": {"step": "submit", "path": ["job_id"]}}}
    plan = compile("powder_refinement", schemas, params)
    with pytest.raises(recipes.WorkflowPlanError):
        recipes.resolve_arguments(plan["steps"][1], {"submit": {"status": "submitted", "job_id": "gsas2-1234567890"}})
