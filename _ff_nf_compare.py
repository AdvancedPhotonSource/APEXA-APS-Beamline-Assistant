"""Headless FF/NF orientation correspondence; never infer physical validation.

Uses the installed MIDAS crystallographic routines. Run with MIDAS_PYTHON when
the solver packages are separate from APEXA. Inputs are read-only. NF rows are
triangular map elements, not grains; correspondence is many-to-one and uses
orientation, not seed row numbers or an estimated spatial registration.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import re

import numpy as np

from apexa_data_guard import check_write_target


def _table(path: Path, marker: str, required: list[str]):
    """Parse named MIDAS whitespace/comma columns, including multiple % headers."""
    columns = None
    rows = []
    for lineno, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        tokens = re.split(r"[\s,]+", line.lstrip("%#").strip())
        if tokens[0] == marker:
            if columns is not None or rows:
                raise ValueError(f"{path}:{lineno}: repeated column header")
            columns = tokens
            if len(set(columns)) != len(columns):
                raise ValueError(f"{path}: duplicate column names")
            continue
        if line.startswith(("%", "#")):
            continue
        if columns is None:
            raise ValueError(f"{path}:{lineno}: missing {marker} column header")
        if len(tokens) != len(columns):
            raise ValueError(f"{path}:{lineno}: expected {len(columns)} columns, got {len(tokens)}")
        try:
            rows.append([float(v) for v in tokens])
        except ValueError as exc:
            raise ValueError(f"{path}:{lineno}: nonnumeric data") from exc
    missing = set(required) - set(columns or [])
    if missing or not rows:
        raise ValueError(f"{path}: missing columns {sorted(missing)} or no data rows")
    data = np.asarray(rows)
    selected = data[:, [columns.index(c) for c in required]]
    if not np.isfinite(selected).all():
        raise ValueError(f"{path}: nonfinite required values")
    return {c: data[:, columns.index(c)] for c in required}


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def output_paths(output_plot, inputs):
    """Resolve output before creating anything; forbid writes beside inputs."""
    plot = Path(output_plot).expanduser().resolve()
    if plot.suffix.lower() not in {".png", ".pdf"}:
        raise ValueError("output_plot must end in .png or .pdf")
    paths = (plot, plot.with_suffix(".json"), plot.with_suffix(".matches.tsv"))
    for out in paths:
        resolved = out.resolve()
        for source in inputs:
            parent = Path(source).expanduser().resolve().parent
            if resolved.parent == parent:
                raise ValueError("Choose a separate analysis directory outside both input directories")
        blocked = check_write_target(resolved, what="write FF/NF comparison")
        if blocked:
            raise ValueError(blocked)
        if out.exists() or out.is_symlink():
            raise ValueError(f"Output already exists; choose a fresh output name: {out}")
    return paths


def match_orientations(nf_om, ff_om, space_group, tolerance):
    """Search symmetry-equivalent unit quaternions, then verify with MIDAS.

    Older MIDAS batch APIs loop over scalar pairs. Avoid an N*M scalar sweep:
    the quaternion tree uses upstream symmetry operators, and MIDAS checks
    every candidate within a 0.001-degree search margin plus the nearest row.
    The margin accommodates six-decimal FF matrix serialization. It never
    relaxes the reported match threshold. Crystal symmetry acts on the right.
    """
    from scipy.spatial import cKDTree
    from scipy.spatial.transform import Rotation
    from midas_stress.orientation import make_symmetries, misorientation_om_batch
    _, sym = make_symmetries(space_group)
    sym = np.asarray(sym)
    rotations = Rotation.from_matrix(ff_om.reshape(-1, 3, 3))
    nf_quats = Rotation.from_matrix(nf_om.reshape(-1, 3, 3)).as_quat()
    equivalent = np.concatenate([
        (rotations * Rotation.from_quat([s[1], s[2], s[3], s[0]])).as_quat()
        for s in sym
    ])
    ff_index = np.tile(np.arange(len(ff_om)), len(sym))
    tree = cKDTree(np.concatenate([equivalent, -equivalent]))
    labels = np.tile(ff_index, 2)
    chord, nearest_sym = tree.query(nf_quats)
    radius = 2 * np.sin(np.radians(min(180, tolerance + 0.001)) / 4)
    neighbours = tree.query_ball_point(nf_quats, radius)
    owners, candidates = [], []
    for i, indices in enumerate(neighbours):
        rows = np.unique(np.append(labels[indices], labels[nearest_sym[i]]))
        owners.extend([i] * len(rows))
        candidates.extend(rows)
    owners, candidates = np.asarray(owners), np.asarray(candidates)
    exact = np.degrees(misorientation_om_batch(nf_om[owners], ff_om[candidates], space_group))
    if not np.isfinite(exact).all():
        raise ValueError("MIDAS returned nonfinite misorientations")
    nearest = np.empty(len(nf_om), dtype=int)
    angles = np.empty(len(nf_om))
    counts = np.empty(len(nf_om), dtype=int)
    boundaries = np.r_[0, np.flatnonzero(np.diff(owners)) + 1, len(owners)]
    for i, (start, end) in enumerate(zip(boundaries[:-1], boundaries[1:])):
        best = start + exact[start:end].argmin()
        nearest[i], angles[i] = candidates[best], exact[best]
        counts[i] = (exact[start:end] <= tolerance).sum()
    tree_angles = np.degrees(4 * np.arcsin(np.clip(chord / 2, 0, 1)))
    if np.max(np.abs(tree_angles - angles)) > 0.001:
        raise ValueError("Quaternion search disagrees with MIDAS by >0.001 degrees; check conventions")
    return nearest, angles, counts


def compare(ff_file, nf_file, output_plot, *, space_group=225,
            min_confidence=0.7, max_misorientation_deg=1.0, phase=1):
    """Compare a selected NF phase with one FF layer in the same MIDAS frame.

    The operator must establish acquisition/phase/frame correspondence. No
    transform is fitted; matching success does not establish that correspondence.
    The denominator is every NF row of `phase` with confidence >= threshold.
    """
    if not 1 <= space_group <= 230 or int(space_group) != space_group:
        raise ValueError("space_group must be an integer in 1..230")
    if not 0 <= min_confidence <= 1 or not 0 < max_misorientation_deg <= 180:
        raise ValueError("Confidence must be in [0,1], angular tolerance in (0,180]")
    if phase < 1 or int(phase) != phase:
        raise ValueError("phase must be a positive integer")
    ff_path, nf_path = [Path(p).expanduser().resolve(strict=True) for p in (ff_file, nf_file)]
    plot, receipt, matches = output_paths(output_plot, (ff_path, nf_path))
    # Hash before reading; check again before output to detect concurrent changes.
    input_hashes = {str(p): _sha(p) for p in (ff_path, nf_path)}
    om_cols = [f"O{i}{j}" for i in (1, 2, 3) for j in (1, 2, 3)]
    ff = _table(ff_path, "GrainID", ["GrainID", *om_cols, "X", "Y", "PhaseNr"])
    nf = _table(nf_path, "OrientationRowNr", ["X", "Y", "Eul1", "Eul2", "Eul3", "Confidence", "PhaseNr"])
    for table in (ff, nf):
        if np.any(table["PhaseNr"] != np.floor(table["PhaseNr"])):
            raise ValueError("PhaseNr must contain integers")
    if np.any((nf["Confidence"] < 0) | (nf["Confidence"] > 1)):
        raise ValueError("NF Confidence outside [0,1]")
    ff_sel = np.flatnonzero(ff["PhaseNr"] == phase)
    nf_sel = np.flatnonzero((nf["PhaseNr"] == phase) & (nf["Confidence"] >= min_confidence))
    if not len(ff_sel) or not len(nf_sel):
        raise ValueError("No FF or NF rows pass the specified phase/confidence selection")
    ids = ff["GrainID"][ff_sel]
    if np.any(ids != np.floor(ids)) or len(np.unique(ids)) != len(ids):
        raise ValueError("FF GrainID must be unique integers within the selected phase")
    ff_om = np.column_stack([ff[c][ff_sel] for c in om_cols])
    mats = ff_om.reshape(-1, 3, 3)
    if (np.max(np.abs(mats @ mats.transpose(0, 2, 1) - np.eye(3))) > 5e-5
            or np.max(np.abs(np.linalg.det(mats) - 1)) > 5e-5):
        raise ValueError("FF orientations are not proper rotation matrices (tolerance 5e-5)")
    try:
        import midas_stress
        from midas_stress.orientation import euler_to_orient_mat_batch
    except ImportError as exc:
        raise RuntimeError("This tool requires midas_stress with batch orientation routines; set MIDAS_PYTHON to the installed solver environment") from exc
    nf_om = euler_to_orient_mat_batch(np.column_stack([nf[c][nf_sel] for c in ("Eul1", "Eul2", "Eul3")]))
    nearest, angles, candidates = match_orientations(nf_om, ff_om, space_group, max_misorientation_deg)
    accepted = angles <= max_misorientation_deg
    for path in (ff_path, nf_path):
        if _sha(path) != input_hashes[str(path)]:
            raise ValueError(f"Input changed during comparison: {path}")

    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt
    plot.parent.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(1, 3, figsize=(13, 4), constrained_layout=True)
    axes[0].scatter(ff["X"][ff_sel], ff["Y"][ff_sel], s=6, color="#41658a")
    axes[0].set_title(f"FF layer: {len(ff_sel)} candidate rows")
    sc = axes[1].scatter(nf["X"][nf_sel], nf["Y"][nf_sel], c=angles,
                         s=4, cmap="viridis_r", vmin=0, vmax=max_misorientation_deg * 2)
    axes[1].set_title(f"NF: {len(nf_sel)} selected map rows")
    fig.colorbar(sc, ax=axes[1], label="Nearest FF misorientation (degrees)")
    for ax in axes[:2]:
        ax.set(xlabel="Reported X (µm)", ylabel="Reported Y (µm)", aspect="equal")
    axes[2].hist(angles, bins=40, color="#41658a")
    axes[2].axvline(max_misorientation_deg, color="#ba4d2a", label="Match tolerance")
    axes[2].set(xlabel="Nearest FF misorientation (degrees)", ylabel="NF map rows",
                title=f"{accepted.sum()}/{len(nf_sel)} within tolerance")
    axes[2].legend()
    fig.suptitle("Orientation correspondence; spatial registration not established")
    try:
        fig.savefig(plot, dpi=180)
    finally:
        plt.close(fig)
    with matches.open("x", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["nf_data_row_1based", "nf_confidence", "nearest_ff_data_row_1based", "nearest_ff_grain_id", "misorientation_deg", "within_tolerance", "ff_candidates_within_tolerance"])
        for i, nf_index in enumerate(nf_sel):
            ff_index = ff_sel[nearest[i]]
            writer.writerow([int(nf_index + 1), nf["Confidence"][nf_index], int(ff_index + 1),
                             int(ff["GrainID"][ff_index]), float(angles[i]), bool(accepted[i]), int(candidates[i])])
    result = {
        "status": "completed", "method": "MIDAS symmetry-aware nearest orientation, many-to-one",
        "inputs_sha256": input_hashes,
        "midas_stress_version": midas_stress.__version__,
        "orientation_module_sha256": _sha(Path(midas_stress.__file__).parent / "orientation.py"),
        "parameters": {"space_group": space_group, "phase": phase, "min_nf_confidence": min_confidence,
                       "max_misorientation_deg": max_misorientation_deg,
                       "orientation_frame_assumption": "same MIDAS sample frame; NF Euler angles in radians"},
        "counts": {"ff_total_rows": len(ff["GrainID"]), "ff_selected_rows": len(ff_sel),
                   "nf_total_rows": len(nf["X"]), "nf_selected_rows": len(nf_sel),
                   "nf_matched_rows": int(accepted.sum()), "nf_unmatched_rows": int((~accepted).sum()),
                   "nf_ambiguous_rows": int((candidates > 1).sum()),
                   "ff_rows_selected_as_nearest_for_matched_nf": len(np.unique(nearest[accepted]))},
        "matched_fraction": float(accepted.mean()),
        "misorientation_deg": {"median_all_selected": float(np.median(angles)), "p95_all_selected": float(np.percentile(angles, 95))},
        "spatial_registration": "not_established; no position error or fitted transform computed",
        "limitations": [
            "Agreement is cross-reconstruction consistency, not independent ground truth or accuracy.",
            "Acquisition identity, shared seeds/calibration and coordinate conventions require a separate provenance audit.",
            "NF map rows are not grains; voxel/grain count ratios do not measure spatial resolution.",
            "Nearest orientation matching is many-to-one, without spatial gating; ambiguous candidates are reported.",
            "Only the supplied FF layer and NF section are compared; no 3D slice selection or frame search is performed.",
        ],
        "artifacts": {"plot": str(plot), "matches": str(matches), "receipt": str(receipt)},
        "artifacts_sha256": {str(p): _sha(p) for p in (plot, matches)},
    }
    with receipt.open("x") as handle:
        json.dump(result, handle, indent=2, allow_nan=False)
        handle.write("\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ff_file")
    parser.add_argument("nf_file")
    parser.add_argument("output_plot")
    parser.add_argument("--space-group", type=int, default=225)
    parser.add_argument("--min-confidence", type=float, default=0.7)
    parser.add_argument("--max-misorientation-deg", type=float, default=1.0)
    parser.add_argument("--phase", type=int, default=1)
    args = parser.parse_args()
    print(json.dumps(compare(**vars(args)), indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
