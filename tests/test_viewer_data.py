import math
from pathlib import Path

import h5py
import numpy as np
import pytest
import tifffile
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apexa_viewer_data import (inspect_sequence, inspect_image, read_frame, correct_frame,
                              frame_payload, structure_payload, simulated_rings)
from apexa_viewer_api import router


def test_hdf5_reads_selected_frame_not_entire_stack(tmp_path):
    p = tmp_path / "neutral.bin"
    with h5py.File(p, "w") as h:
        h.create_dataset("exchange/data", data=np.arange(60).reshape(3, 4, 5))
    manifest = inspect_sequence(str(p))
    assert manifest["frames"] == 3
    np.testing.assert_array_equal(read_frame(manifest["sources"][0], 2), np.arange(40, 60).reshape(4, 5))
    with pytest.raises(ValueError, match="outside"):
        read_frame(manifest["sources"][0], 3)


def test_ambiguous_hdf5_requires_explicit_dataset(tmp_path):
    p = tmp_path / "data.h5"
    with h5py.File(p, "w") as h:
        h.create_dataset("first/data", data=np.ones((4, 5)))
        h.create_dataset("second/data", data=np.ones((4, 5)))
    with pytest.raises(ValueError, match="explicitly"):
        inspect_image(str(p))
    assert inspect_image(str(p), "second/data")["dataset"] == "second/data"


def test_tiff_sequence_natural_order_and_changed_source(tmp_path):
    for i in [10, 2, 1]:
        tifffile.imwrite(tmp_path / f"scan_{i}.tif", np.full((8, 9), i, dtype=np.uint16))
    m = inspect_sequence(str(tmp_path), "scan_*.tif")
    assert [Path(s["path"]).name for s in m["sources"]] == ["scan_1.tif", "scan_2.tif", "scan_10.tif"]
    np.testing.assert_array_equal(read_frame(m["sources"][1], 0), np.full((8, 9), 2))
    Path(m["sources"][0]["path"]).write_bytes(b"replaced")
    with pytest.raises(ValueError, match="changed"):
        read_frame(m["sources"][0], 0)


def test_multipage_tiff(tmp_path):
    p = tmp_path / "stack.tif"
    data = np.arange(120, dtype=np.uint16).reshape(3, 5, 8)
    tifffile.imwrite(p, data, photometric="minisblack")
    source = inspect_image(str(p))
    assert source["frames"] == 3
    np.testing.assert_array_equal(read_frame(source, 1), data[1])


def test_dark_mask_never_mutate_raw_or_include_excluded_pixels():
    raw = np.array([[10, 20], [30, 40]], dtype=np.uint16)
    copy = raw.copy()
    corrected = correct_frame(raw, np.full((2, 2), 5), np.array([[0, 1], [0, 0]]))
    np.testing.assert_array_equal(raw, copy)
    assert corrected[0, 0] == 5 and np.isnan(corrected[0, 1])
    payload = frame_payload(corrected, center_x=0, center_y=0)
    assert payload["stats"]["excluded_pixels"] == 1
    assert payload["stats"]["mean"] == pytest.approx((5 + 25 + 35) / 3)
    assert payload["preview"].startswith("data:image/png;base64,")
    assert payload["profile"]["intensity"][0] == 5
    with pytest.raises(ValueError, match="Dark"):
        correct_frame(raw, np.ones((3, 3)))
    with pytest.raises(ValueError, match="Mask"):
        correct_frame(raw, mask=np.full((2, 2), np.nan))


def test_structure_symmetry_and_bragg_geometry():
    cif = str(Path(__file__).resolve().parent / "data/nickel_fcc.cif")
    structure = structure_payload(cif)
    assert len(structure["atoms"]) == 4  # FCC symmetry expands the single 4a site
    assert {a["element"] for a in structure["atoms"]} == {"Ni"}
    result = simulated_rings(cif, 65, 900, 150, 2000, index_limit=2)
    first = result["rings"][0]
    d = structure["cell"]["a"] / math.sqrt(3)
    angle = 2 * math.asin((12.39841984332 / 65) / (2 * d))
    assert first["d_angstrom"] == pytest.approx(d)
    assert first["radius_px"] == pytest.approx(900000 / 150 * math.tan(angle))
    assert not any(sorted(map(abs, r["hkl"])) == [0, 0, 1] for r in result["rings"])


def test_api_frames_include_provenance_and_corrections(tmp_path):
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    path = tmp_path / "scan.tif"
    dark = tmp_path / "dark.tif"
    tifffile.imwrite(path, np.full((8, 8), 20, dtype=np.uint16))
    tifffile.imwrite(dark, np.full((8, 8), 5, dtype=np.uint16))
    manifest = client.post("/api/science/sequence", json={"path": str(path)}).json()
    request = {"sequence_id": manifest["sequence_id"], "dark_path": str(dark)}
    corrected = client.post("/api/science/frame", json=request)
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["stats"]["mean"] == 15
    raw = client.post("/api/science/frame", json={**request, "corrected": False}).json()
    assert raw["stats"]["mean"] == 20
    assert raw["analysis_key"] != corrected.json()["analysis_key"]
    assert raw["context"]["corrections"] == {}
    assert client.post("/api/science/frame", json={**request, "index": 10}).status_code == 422


def test_preview_resolution_does_not_change_statistics_or_profile():
    data = np.arange(128 * 256, dtype=np.float32).reshape(128, 256)
    small = frame_payload(data, center_x=100, center_y=50, preview_max_side=64)
    full = frame_payload(data, center_x=100, center_y=50, preview_max_side=512)
    assert small["preview_shape"] == [32, 64]
    assert small["shape"] == full["shape"] == [128, 256]
    assert small["stats"] == full["stats"]
    assert small["profile"] == full["profile"]
    assert small["vmin"] == full["vmin"] and small["vmax"] == full["vmax"]
