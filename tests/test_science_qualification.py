import json
from pathlib import Path
import sys

import pytest

from scripts.qualify_science import validate, execute, evaluate
from apexa_slurm import digest_file


@pytest.fixture
def manifest(tmp_path):
    geometry = tmp_path / "reference.json"
    curve = tmp_path / "reference.xy"
    sample = tmp_path / "input.txt"
    geometry.write_text('{"distance_mm": 900.0}')
    curve.write_text("0 10\n1 20\n2 30\n")
    sample.write_text("frozen synthetic input")
    ref = lambda p: {"path": str(p), "sha256": digest_file(p)}
    code = "from pathlib import Path; import sys; p=Path(sys.argv[1]); (p/'geometry.json').write_text('{\"distance_mm\":900.0}'); (p/'curve.xy').write_text('0 10\\n1 20\\n2 30\\n')"
    return {"schema_version": 1, "workflow": "calibration_integration",
            "reference_provenance": {"independent": True, "reviewed_by": "synthetic test", "source": "fixed fixture"},
            "inputs": [ref(sample)], "engine": ref(Path(sys.executable)),
            "command": [sys.executable, "-c", code, "{output_dir}"], "timeout_seconds": 10,
            "checks": [{"kind": "json_scalar", "artifact": "geometry.json", "reference": ref(geometry), "field": ["distance_mm"], "atol": .01},
                       {"kind": "curve", "artifact": "curve.xy", "reference": ref(curve), "rtol": .001}]}


def test_validation_does_not_execute_and_requires_independent_reference(manifest, tmp_path):
    assert validate(manifest)["execution"] == "not_run"
    manifest["reference_provenance"]["independent"] = False
    with pytest.raises(ValueError, match="independently"):
        validate(manifest)
    assert not (tmp_path / "geometry.json").exists()


@pytest.mark.asyncio
async def test_verified_outputs_and_profiles_are_recorded_without_reusing_a_run(manifest, tmp_path):
    folder = tmp_path / "run"
    result = await execute(manifest, folder)
    assert result["execution"] == "completed"
    assert result["scientific_verdict"] == "accepted"
    assert result["performance"]["wall_seconds"] > 0
    assert json.loads((folder / "run.json").read_text())["checks"] == result["checks"]
    with pytest.raises(FileExistsError):
        await execute(manifest, folder)


@pytest.mark.asyncio
async def test_wrong_geometry_or_curve_is_rejected_even_with_successful_exit(manifest, tmp_path):
    folder = tmp_path / "run"
    await execute(manifest, folder)
    (folder / "geometry.json").write_text('{"distance_mm": 500.0}')
    assert evaluate(manifest, folder)["scientific_verdict"] == "rejected"
    (folder / "curve.xy").write_text("0 nan\n1 20\n2 30\n")
    assert evaluate(manifest, folder)["checks"][1]["passed"] is False


def test_changed_input_or_reference_is_not_qualified(manifest):
    Path(manifest["inputs"][0]["path"]).write_text("changed")
    with pytest.raises(ValueError, match="SHA-256"):
        validate(manifest)
