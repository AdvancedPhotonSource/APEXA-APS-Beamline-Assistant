"""Scientific correspondence, schema and output-safety regression tests."""
import asyncio
import json
from pathlib import Path
import sys

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _ff_nf_compare import compare, output_paths, match_orientations


def inputs(tmp_path):
    """Two symmetry-equivalent FF rows and one real mismatch; IDs deliberately shuffled."""
    source = tmp_path / "input"
    source.mkdir()
    ff = source / "Grains.csv"
    names = [f"O{i}{j}" for i in (1, 2, 3) for j in (1, 2, 3)]
    matrices = [np.eye(3), Rotation.from_euler("z", 90, degrees=True).as_matrix()]
    lines = ["%NumGrains 2", "%PhaseInfo", "% SpaceGroup:225", "%GrainID " + " ".join(names) + " X Y PhaseNr"]
    for grain_id, mat in zip([501, 7], matrices):
        lines.append(" ".join(map(str, [grain_id, *mat.reshape(-1), 0, 0, 1])))
    ff.write_text("\n".join(lines) + "\n")
    nf = source / "Microstructure"
    nf.write_text("%TriEdgeSize 1\n%NumPhases 1\n%OrientationRowNr OrientationID RunTime X Y TriEdgeSize UpDown Eul1 Eul2 Eul3 Confidence PhaseNr\n"
                  "812 10 0 1 2 1 1 0 0 0 0.7 1\n"
                  "0 11 0 3 4 1 -1 0.4 0 0 0.9 1\n"
                  "501 12 0 5 6 1 1 0 0 0 0.6 1\n")
    return ff, nf


@pytest.fixture
def orientation_backend():
    # Run these using the configured MIDAS environment, or PYTHONPATH pointing
    # to the upstream package. Never substitute a mocked crystallography routine.
    return pytest.importorskip("midas_stress.orientation")


def test_matches_symmetry_not_ids_and_counts_exact_threshold(tmp_path, orientation_backend):
    ff, nf = inputs(tmp_path)
    before = (ff.read_bytes(), nf.read_bytes())
    out = tmp_path / "analysis" / "compare.png"
    result = compare(ff, nf, out)
    assert result["counts"] == {
        "ff_total_rows": 2, "ff_selected_rows": 2, "nf_total_rows": 3,
        "nf_selected_rows": 2, "nf_matched_rows": 1, "nf_unmatched_rows": 1,
        "nf_ambiguous_rows": 1, "ff_rows_selected_as_nearest_for_matched_nf": 1,
    }
    assert result["matched_fraction"] == 0.5
    assert "not_established" in result["spatial_registration"]
    assert "resolution_improvement" not in json.dumps(result)
    assert out.read_bytes().startswith(b"\x89PNG")
    assert out.with_suffix(".matches.tsv").read_text().splitlines()[1].startswith("1\t0.7\t")
    assert json.loads(out.with_suffix(".json").read_text()) == result
    assert (ff.read_bytes(), nf.read_bytes()) == before
    with pytest.raises(ValueError, match="already exists"):
        compare(ff, nf, out)


def test_space_group_changes_correspondence(tmp_path, orientation_backend):
    ff, nf = inputs(tmp_path)
    # A sole 90-degree cubic symmetry mate must match cubic, but not triclinic.
    lines = ff.read_text().splitlines()
    ff.write_text("\n".join(lines[:4] + lines[-1:]) + "\n")
    assert compare(ff, nf, tmp_path / "cubic.png")["counts"]["nf_matched_rows"] == 1
    assert compare(ff, nf, tmp_path / "triclinic.png", space_group=1)["counts"]["nf_matched_rows"] == 0


@pytest.mark.parametrize("sg", [1, 194, 225])
def test_tree_matches_exhaustive_midas_reference(orientation_backend, sg):
    ff = Rotation.random(35, random_state=12).as_matrix().reshape(-1, 9)
    nf = np.vstack([ff[:5], Rotation.random(45, random_state=41).as_matrix().reshape(-1, 9)])
    reference = np.degrees(orientation_backend.misorientation_om_batch(
        np.repeat(nf, len(ff), axis=0), np.tile(ff, (len(nf), 1)), sg)).reshape(len(nf), len(ff))
    nearest, angles, counts = match_orientations(nf, ff, sg, 10)
    assert np.array_equal(nearest, reference.argmin(axis=1))
    assert np.allclose(angles, reference.min(axis=1), atol=1e-10)
    assert np.array_equal(counts, (reference <= 10).sum(axis=1))


def test_improper_orientation_matrix_is_rejected(tmp_path):
    ff, nf = inputs(tmp_path)
    lines = ff.read_text().splitlines()
    fields = lines[-2].split()
    fields[1] = "2.0"
    lines[-2] = " ".join(fields)
    ff.write_text("\n".join(lines))
    with pytest.raises(ValueError, match="proper rotation"):
        compare(ff, nf, tmp_path / "out.png")


@pytest.mark.parametrize("alter,error", [
    (lambda s: s.replace("Eul3", "not_an_angle"), "missing columns"),
    (lambda s: s.replace("0.7 1", "nan 1"), "nonfinite"),
    (lambda s: s.replace("0.7 1", "1.7 1"), "Confidence outside"),
    (lambda s: s.replace("0.7 1", "0.7"), "expected 12 columns"),
])
def test_malformed_input_fails_before_output(tmp_path, alter, error):
    ff, nf = inputs(tmp_path)
    nf.write_text(alter(nf.read_text()))
    out = tmp_path / "analysis" / "compare.png"
    with pytest.raises(ValueError, match=error):
        compare(ff, nf, out)
    assert not out.parent.exists()


def test_no_selected_rows_is_not_success(tmp_path):
    ff, nf = inputs(tmp_path)
    with pytest.raises(ValueError, match="No FF or NF rows"):
        compare(ff, nf, tmp_path / "out.png", min_confidence=1)


def test_output_cannot_write_in_or_symlink_to_input(tmp_path):
    ff, nf = inputs(tmp_path)
    with pytest.raises(ValueError, match="outside both input"):
        output_paths(ff.parent / "out.png", [ff, nf])
    link = tmp_path / "analysis"
    link.symlink_to(ff.parent, target_is_directory=True)
    with pytest.raises(ValueError, match="outside both input"):
        output_paths(link / "out.png", [ff, nf])
    # Agents stage read-only inputs in cwd; an explicit analysis subdirectory
    # in that workspace is valid. Protected raw-data trees remain disallowed.
    assert output_paths(ff.parent / "analysis" / "out.png", [ff, nf])[0].parent.name == "analysis"


def test_protected_data_root_and_stale_sidecar(tmp_path, monkeypatch):
    ff, nf = inputs(tmp_path)
    monkeypatch.setenv("APEXA_PROTECTED_DATA_ROOTS", str(tmp_path))
    monkeypatch.delenv("APEXA_ALLOW_DATA_WRITE", raising=False)
    with pytest.raises(ValueError, match="detector data store"):
        output_paths(tmp_path / "data" / "out.png", [ff, nf])
    out = tmp_path / "out.png"
    out.with_suffix(".json").write_text('{"status":"completed"}')
    with pytest.raises(ValueError, match="already exists"):
        output_paths(out, [ff, nf])


def test_tool_rejects_unsupported_slice_and_missing_artifacts(tmp_path, monkeypatch):
    import midas_comprehensive_server as server
    ff, nf = inputs(tmp_path)
    result = json.loads(asyncio.run(server.overlay_ff_nf_results(str(ff), str(nf), slice_position="top")))
    assert result["status"] == "error"
    monkeypatch.setattr(server, "find_midas_python", lambda: sys.executable)
    class EmptySuccess:
        returncode = 0
        stderr = ""
    monkeypatch.setattr(server.subprocess, "run", lambda *a, **kw: EmptySuccess())
    result = json.loads(asyncio.run(server.overlay_ff_nf_results(str(ff), str(nf), str(tmp_path / "out.png"))))
    assert result["status"] == "error"
    assert "without all three" in result["error"]


def test_tool_runs_real_subprocess(tmp_path, monkeypatch, orientation_backend):
    import midas_comprehensive_server as server
    ff, nf = inputs(tmp_path)
    monkeypatch.setattr(server, "find_midas_python", lambda: sys.executable)
    result = json.loads(asyncio.run(server.overlay_ff_nf_results(str(ff), str(nf), str(tmp_path / "out.png"))))
    assert result["status"] == "completed", result
    assert result["counts"]["nf_matched_rows"] == 1
    assert result["tool"] == "overlay_ff_nf_results"


def test_tool_does_not_fall_back_from_missing_pinned_solver(tmp_path, monkeypatch):
    import midas_comprehensive_server as server
    ff, nf = inputs(tmp_path)
    monkeypatch.setenv("APEXA_MIDAS_BIN", str(tmp_path / "missing_solver"))
    monkeypatch.setattr(server, "find_midas_python", lambda: pytest.fail("must honor pinned solver"))
    result = json.loads(asyncio.run(server.overlay_ff_nf_results(str(ff), str(nf), str(tmp_path / "out.png"))))
    assert result["status"] == "error"
    assert "Configured MIDAS Python does not exist" in result["error"]
