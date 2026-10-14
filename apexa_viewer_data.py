"""Read-only, frame-at-a-time science data for the web canvas.

No model calls, network retrieval, raw-data writes, or reconstruction are involved.
"""
from __future__ import annotations

import base64
import io
import itertools
import math
import re
from pathlib import Path

import h5py
import numpy as np
import tifffile
from PIL import Image

IMAGE_EXTS = {".h5", ".hdf5", ".nxs", ".hdf", ".tif", ".tiff",
              ".ge", ".ge1", ".ge2", ".ge3", ".ge4", ".ge5", ".cbf", ".edf"}


def inspect_image(path: str, dataset: str = "") -> dict:
    p = Path(path).expanduser().resolve(strict=True)
    if h5py.is_hdf5(p):
        with h5py.File(p, "r") as h:
            candidates = []
            def visit(name, obj):
                if len(candidates) >= 256:
                    raise ValueError("Too many image datasets; select an explicit dataset path")
                if isinstance(obj, h5py.Dataset) and obj.ndim in (2, 3) and obj.dtype.kind in "uif" and all(obj.shape):
                    candidates.append(name)
            if not dataset:
                h.visititems(visit)
                preferred = [n for n in candidates if n in ("exchange/data", "entry/data/data", "entry/instrument/detector/data")]
                choices = preferred or [n for n in candidates if "dark" not in n.lower() and "mask" not in n.lower()]
                if len(choices) != 1:
                    raise ValueError(f"Select a detector dataset explicitly. Candidates: {candidates}")
                dataset = choices[0]
            ds = h[dataset]
            if not isinstance(ds, h5py.Dataset) or ds.ndim not in (2, 3) or ds.dtype.kind not in "uif" or not all(ds.shape):
                raise ValueError("Detector data must be a nonempty numeric 2D image or 3D frame stack")
            shape, frames, kind = ds.shape[-2:], ds.shape[0] if ds.ndim == 3 else 1, "hdf5"
    elif p.suffix.lower() in (".tif", ".tiff"):
        with tifffile.TiffFile(p) as tf:
            s = tf.series[0]
            if not s.axes.endswith("YX") or len(s.shape) not in (2, 3):
                raise ValueError(f"Expected grayscale YX or frame/YX TIFF, got {s.axes} {s.shape}")
            shape, frames, kind = s.shape[-2:], int(np.prod(s.shape[:-2])) or 1, "tiff"
            if frames > 1 and len(s.pages) != frames:
                raise ValueError("TIFF stack is not independently addressable by page")
    else:
        import fabio
        with fabio.open(str(p)) as f:
            shape, frames, kind = f.data.shape, f.nframes, "fabio"
    if len(shape) != 2 or math.prod(shape) > 64_000_000:
        raise ValueError("Viewer requires a 2D detector with at most 64 million pixels")
    st = p.stat()
    return {"path": str(p), "dataset": dataset, "kind": kind, "shape": list(shape),
            "frames": int(frames), "size": st.st_size, "mtime_ns": st.st_mtime_ns}


def inspect_sequence(path: str, pattern: str = "*", dataset: str = "") -> dict:
    root = Path(path).expanduser().resolve(strict=True)
    if root.is_dir():
        if "/" in pattern or "\\" in pattern:
            raise ValueError("Sequence pattern must match files in this directory only")
        paths = []
        for p in root.glob(pattern):
            if p.is_file() and p.suffix.lower() in IMAGE_EXTS:
                paths.append(p)
                if len(paths) > 4096:
                    raise ValueError("More than 4096 files; narrow the sequence pattern")
        paths.sort(key=lambda p: [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", p.name)])
    else:
        paths = [root]
    if not paths:
        raise ValueError("No detector files match; choose a sample-only file pattern")
    sources = [inspect_image(str(p), dataset) for p in paths]
    if len({tuple(s["shape"]) for s in sources}) != 1:
        raise ValueError("Detector dimensions differ; select one detector/scan using the pattern")
    return {"path": str(root), "sources": sources, "frames": sum(s["frames"] for s in sources),
            "shape": sources[0]["shape"], "ordering": "natural filename order, then frame index; verify against scan metadata"}


def read_frame(source: dict, index: int) -> np.ndarray:
    if index < 0 or index >= source["frames"]:
        raise ValueError("Frame index outside the acquisition snapshot")
    p = Path(source["path"])
    st = p.stat()
    if (st.st_size, st.st_mtime_ns) != (source["size"], source["mtime_ns"]):
        raise ValueError("Source changed since inspection; reload the sequence snapshot")
    if source["kind"] == "hdf5":
        with h5py.File(p, "r") as h:
            ds = h[source["dataset"]]
            frame = ds[index] if ds.ndim == 3 else ds[()]
    elif source["kind"] == "tiff":
        with tifffile.TiffFile(p) as tf:
            frame = tf.series[0].pages[index].asarray()
    else:
        import fabio
        with fabio.open(str(p)) as f:
            frame = f.data.copy() if index == 0 else f.getframe(index).data.copy()
    return np.asarray(frame, dtype=np.float32)


def correct_frame(raw: np.ndarray, dark=None, mask=None) -> np.ndarray:
    out = np.array(raw, dtype=np.float32, copy=True)
    if dark is not None:
        if dark.shape != out.shape or not np.isfinite(dark).all():
            raise ValueError("Dark must match detector shape and contain only finite values")
        out -= dark
    if mask is not None:
        if mask.shape != out.shape or not np.isfinite(mask).all():
            raise ValueError("Mask must match detector shape and contain only finite values")
        out[mask != 0] = np.nan  # MIDAS convention: nonzero means excluded
    out[~np.isfinite(out)] = np.nan
    return out


def frame_payload(frame: np.ndarray, *, vmin=None, vmax=None, log=False, colormap="viridis",
                  center_x=None, center_y=None, preview_max_side=1024) -> dict:
    from matplotlib import colormaps
    valid = np.isfinite(frame)
    values = frame[valid]
    if not values.size:
        raise ValueError("No finite, unmasked pixels remain")
    limits = np.percentile(values, [1, 99.5]) if vmin is None or vmax is None else (vmin, vmax)
    lo = float(limits[0]) if vmin is None else float(vmin)
    hi = float(limits[1]) if vmax is None else float(vmax)
    if not math.isfinite(lo) or not math.isfinite(hi) or hi < lo:
        raise ValueError("Contrast limits must be finite with maximum >= minimum")
    # Only the display is reduced. Statistics and profiles use every valid pixel.
    # Nearest-neighbor sampling does not fabricate intensities between pixels.
    if not 64 <= preview_max_side <= 4096:
        raise ValueError("Preview size must be between 64 and 4096 pixels")
    scale = min(1, preview_max_side / max(frame.shape))
    preview_shape = [max(1, round(n * scale)) for n in frame.shape]
    yi = np.minimum(((np.arange(preview_shape[0]) + .5) * frame.shape[0] / preview_shape[0]).astype(int), frame.shape[0] - 1)
    xi = np.minimum(((np.arange(preview_shape[1]) + .5) * frame.shape[1] / preview_shape[1]).astype(int), frame.shape[1] - 1)
    display = frame[np.ix_(yi, xi)]
    scaled = np.clip((np.nan_to_num(display, nan=lo) - lo) / max(hi - lo, 1e-12), 0, 1)
    if log:
        scaled = np.log1p(1000 * scaled) / np.log1p(1000)
    rgba = colormaps[colormap](scaled, bytes=True)
    rgba[~np.isfinite(display)] = (80, 80, 80, 255)
    output = io.BytesIO()
    Image.fromarray(rgba).save(output, format="PNG", compress_level=1)
    result = {"preview": "data:image/png;base64," + base64.b64encode(output.getvalue()).decode(),
              "shape": list(frame.shape), "vmin": lo, "vmax": hi,
              "preview_shape": preview_shape, "preview_sampling": "nearest pixel; statistics/profile at full resolution",
              "stats": {"min": float(values.min()), "max": float(values.max()),
                        "mean": float(values.mean()), "excluded_pixels": int((~valid).sum())}}
    if center_x is not None and center_y is not None:
        if not all(math.isfinite(v) for v in (center_x, center_y)):
            raise ValueError("Profile center must be finite")
        y, x = np.ogrid[:frame.shape[0], :frame.shape[1]]
        radius = np.hypot(x - center_x, y - center_y)
        if radius.max() > 100000:
            raise ValueError("Profile center too far from detector")
        bins = radius.astype(np.int32)
        counts = np.bincount(bins[valid])
        sums = np.bincount(bins[valid], weights=frame[valid], minlength=len(counts))
        used = counts > 0
        result["profile"] = {"radius_px": np.flatnonzero(used).tolist(),
                             "intensity": (sums[used] / counts[used]).tolist()}
    return result


def read_structure(path: str):
    import xrayutilities as xu
    p = Path(path).expanduser().resolve(strict=True)
    if p.stat().st_size > 10_000_000:
        raise ValueError("CIF exceeds the 10 MB viewer limit")
    crystal = xu.materials.Crystal.fromCIF(str(p))
    return p, crystal


def structure_payload(path: str) -> dict:
    p, crystal = read_structure(path)
    lattice = crystal.lattice
    atoms = []
    for atom, fractional, occupancy, _ in lattice.base():
        if len(atoms) >= 20000:
            raise ValueError("Unit cell exceeds the 20000-site viewer limit")
        atoms.append({"element": atom.name, "fractional": np.asarray(fractional).tolist(),
                      "position": np.asarray(lattice.GetPoint(fractional)).tolist(), "occupancy": float(occupancy)})
    if not atoms:
        raise ValueError("CIF contains no resolved atomic sites")
    return {"path": str(p), "name": crystal.name, "space_group": str(lattice.space_group),
            "cell": {k: float(getattr(lattice, k)) for k in ("a", "b", "c", "alpha", "beta", "gamma")},
            "vectors": [np.asarray(lattice.GetPoint(v)).tolist() for v in np.eye(3)], "atoms": atoms,
            "parser": "xrayutilities", "units": "angstrom"}


def simulated_rings(path: str, energy_kev: float, distance_mm: float, pixel_um: float,
                    max_radius: float, index_limit: int = 8) -> dict:
    if not all(math.isfinite(x) and x > 0 for x in (energy_kev, distance_mm, pixel_um, max_radius)):
        raise ValueError("Energy, distance, pixel size and radius must be positive and finite")
    if not 1 <= index_limit <= 12:
        raise ValueError("Reflection index limit must be between 1 and 12")
    _, crystal = read_structure(path)
    wavelength = 12.39841984332 / energy_kev
    rings = {}
    for hkl in itertools.product(range(-index_limit, index_limit + 1), repeat=3):
        if hkl == (0, 0, 0):
            continue
        q = float(np.linalg.norm(crystal.lattice.GetQ(hkl)))
        sine = wavelength * q / (4 * np.pi)
        if not 0 < sine < math.sin(math.pi / 4):
            continue
        two_theta = 2 * math.asin(sine)
        radius = distance_mm * 1000 / pixel_um * math.tan(two_theta)
        if radius > max_radius or not crystal.lattice.hkl_allowed(hkl):
            continue
        key = round(q, 7)
        if key not in rings:
            rings[key] = {"hkl": list(hkl), "d_angstrom": 2 * np.pi / q,
                          "two_theta_deg": math.degrees(two_theta), "radius_px": radius}
    return {"rings": sorted(rings.values(), key=lambda r: r["radius_px"]),
            "wavelength_angstrom": wavelength, "index_limit": index_limit,
            "model": "Ideal powder ring positions; untilted detector, no distortion or intensity prediction"}
