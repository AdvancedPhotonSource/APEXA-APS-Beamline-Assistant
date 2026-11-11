"""Artifact checks for the real-gold pilot, without an assumed grain-count truth.

These checks do not certify the physical reconstruction or prove that a declared
parameter file was consumed. Command/tool traces remain necessary for that audit.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path, PurePosixPath

import numpy as np


def artifact(workspace, entry, started_unix, max_bytes):
    name = entry.get("path")
    if not isinstance(name, str) or not name:
        raise ValueError("Missing artifact path")
    relative = PurePosixPath(name)
    path = workspace / name
    if (relative.is_absolute() or ".." in relative.parts or str(relative) != name
            or not path.resolve().is_relative_to(workspace.resolve())
            or any((workspace / parent).is_symlink() for parent in [relative, *relative.parents])):
        raise ValueError("Artifact must be a workspace-relative regular file")
    if not path.is_file() or not 0 < path.stat().st_size <= max_bytes:
        raise ValueError("Artifact is missing, empty or too large")
    if path.stat().st_mtime < started_unix - 1:
        raise ValueError("Artifact predates this trial")
    if hashlib.sha256(path.read_bytes()).hexdigest() != entry.get("sha256"):
        raise ValueError("Artifact hash mismatch")
    return path


def parameter_facts(path):
    lines = {}
    for line in path.read_text().splitlines():
        words = line.split("#", 1)[0].split()
        if words:
            lines.setdefault(words[0].lower(), []).append(words[1:])
    expected = {
        "wavelength": [12.398419843320026 / 63.314], "px": [150],
        "imtransopt": [2], "spacegroup": [225],
        "latticeconstant": [4.0782, 4.0782, 4.0782, 90, 90, 90],
        "omegastart": [180], "omegastep": [-.25], "skipframe": [1],
        "nrfilespersweep": [1], "startfilenrfirstlayer": [1181],
    }
    checks = {}
    for name, values in expected.items():
        rows = lines.get(name, [])
        try:
            actual = list(map(float, rows[0])) if len(rows) == 1 else []
            passed = len(actual) == len(values) and all(
                math.isfinite(a) and math.isclose(a, b, rel_tol=1e-7, abs_tol=1e-9)
                for a, b in zip(actual, values))
        except (ValueError, TypeError):
            actual, passed = rows, False
        checks[name] = {"passed": passed, "declared": actual, "expected": values}
    geometry = {}
    for name, count in [("lsd", 1), ("bc", 2), ("tx", 1), ("ty", 1), ("tz", 1)]:
        rows = lines.get(name, [])
        try:
            actual = list(map(float, rows[0])) if len(rows) == 1 else []
            passed = len(actual) == count and all(math.isfinite(v) for v in actual)
            if name == "lsd":
                passed = passed and actual[0] > 0
        except (ValueError, TypeError):
            actual, passed = rows, False
        geometry[name] = {"passed": passed, "declared": actual}
    return {"operator_facts_match": all(c["passed"] for c in checks.values()),
            "finite_geometry": all(c["passed"] for c in geometry.values()),
            "facts": checks, "geometry": geometry,
            "scope": "Declared parameter artifact only; effective execution requires trace audit"}


def grain_structure(path):
    rows = []
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith(("%", "#")):
            continue
        values = [float(v) for v in line.split()]
        if len(values) < 13 or not all(math.isfinite(v) for v in values[:13]):
            raise ValueError("Grain row has missing/nonfinite orientation or position")
        rows.append(values[:13])
    if not rows:
        return {"nonempty": False, "candidate_count": 0, "rotations_valid": False}
    array = np.asarray(rows)
    orientations = array[:, 1:10].reshape(-1, 3, 3)
    determinant_error = np.abs(np.linalg.det(orientations) - 1)
    orthogonality_error = np.max(np.abs(
        orientations @ np.transpose(orientations, (0, 2, 1)) - np.eye(3)), axis=(1, 2))
    ids = array[:, 0]
    return {"nonempty": True, "candidate_count": len(rows),
            "unique_integer_ids": bool(len(set(ids)) == len(ids) and np.all(ids == np.floor(ids))),
            "rotations_valid": bool(np.all(determinant_error < .005)
                                    and np.all(orthogonality_error < .005)),
            "maximum_determinant_error": float(determinant_error.max()),
            "maximum_orthogonality_error": float(orthogonality_error.max()),
            "scope": "Finite, near-rotation matrices; no physical identity/count truth"}


def evaluate(workspace, started_unix):
    workspace = Path(workspace)
    result = {"manifest_valid": False, "artifacts_valid": False, "artifact_hashes_valid": False,
              "physical_validity": "unestablished", "trace_audit": "required",
              "errors": []}
    try:
        manifest_path = workspace / "result_manifest.json"
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise ValueError("No regular result_manifest.json")
        if not 0 < manifest_path.stat().st_size <= 1024 * 1024:
            raise ValueError("Invalid manifest size")
        if manifest_path.stat().st_mtime < started_unix - 1:
            raise ValueError("Manifest predates this trial")
        manifest = json.loads(manifest_path.read_text())
        if manifest.get("schema_version") != 1 or manifest.get("task") != "real_gold_ff":
            raise ValueError("Unsupported manifest schema/task")
        status = manifest.get("scientific_status")
        if status not in ("accepted", "provisional", "rejected", "abstained"):
            raise ValueError("Missing scientific decision")
        if not isinstance(manifest.get("reason"), str) or not manifest["reason"].strip():
            raise ValueError("Missing decision reason")
        entries = manifest.get("artifacts", {})
        if not isinstance(entries, dict):
            raise ValueError("Artifacts must be a mapping")
        result.update(manifest_valid=True, scientific_status=status, reason=manifest["reason"],
                      manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest())
        if not entries and status != "accepted":
            return result
        for name in ("parameters", "grains", "calibration"):
            if name not in entries:
                raise ValueError(f"Missing adopted {name} artifact")
        parameters = artifact(workspace, entries["parameters"], started_unix, 2 * 1024 * 1024)
        grains = artifact(workspace, entries["grains"], started_unix, 50 * 1024 * 1024)
        calibration = artifact(workspace, entries["calibration"], started_unix, 10 * 1024 * 1024)
        calibration_value = json.loads(calibration.read_text())
        if not isinstance(calibration_value, dict) or not calibration_value:
            raise ValueError("Calibration receipt must be a nonempty JSON object")
        facts, structure = parameter_facts(parameters), grain_structure(grains)
        result.update(artifact_hashes_valid=True,
                      artifacts_valid=bool(facts["finite_geometry"] and structure["nonempty"]
                          and structure["rotations_valid"] and structure.get("unique_integer_ids")),
                      adopted_artifacts=entries, parameters=facts, grains=structure)
    except (ValueError, TypeError, KeyError, OSError, AttributeError) as exc:
        result["errors"].append(str(exc))
    return result
