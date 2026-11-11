#!/usr/bin/env python3
"""Score a reconstructed FF-HEDM grain list against a known synthetic ground truth.

Both files use the MIDAS Grains.csv layout: '%' header lines, then one row per grain with
GrainID, the orientation matrix O11..O33 (row-major), X Y Z (micrometres), a b c alpha beta
gamma, ... A reconstructed grain matches a true grain when their misorientation (cubic
symmetry, m-3m proper rotations) is at most MAX_MIS_DEG and their centres are at most
MAX_POS_UM apart. Matching is one-to-one, greedy by misorientation.

Thresholds are fixed before any pilot trial (see the pilot PROTOCOL.md).

    python score_ff.py <reconstructed Grains.csv> <truth GrainsSim.csv> [--convention left|right]
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
from pathlib import Path

import numpy as np

MAX_MIS_DEG = 1.0
MAX_POS_UM = 200.0


def cubic_ops() -> np.ndarray:
    """The 24 proper rotations of the cubic group (signed permutation matrices, det +1)."""
    ops = []
    for perm in itertools.permutations(range(3)):
        for signs in itertools.product((1, -1), repeat=3):
            m = np.zeros((3, 3))
            for row, (col, s) in enumerate(zip(perm, signs)):
                m[row, col] = s
            if np.isclose(np.linalg.det(m), 1.0):
                ops.append(m)
    assert len(ops) == 24
    return np.array(ops)


OPS = cubic_ops()


def read_grains(path: Path) -> np.ndarray:
    rows = []
    for line in Path(path).read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("%"):
            continue
        parts = line.split()
        try:
            rows.append([float(x) for x in parts])
        except ValueError:
            continue
    if not rows:
        return np.zeros((0, 13))
    width = min(len(r) for r in rows)
    return np.array([r[:width] for r in rows])


def orientations(g: np.ndarray) -> np.ndarray:
    return g[:, 1:10].reshape(-1, 3, 3)


def misorientation_deg(a: np.ndarray, b: np.ndarray, convention: str) -> np.ndarray:
    """Pairwise misorientation (len(a) x len(b)), symmetry applied on the crystal side.

    'right': orientation matrices map crystal -> sample (g = O), so delta = O_a S O_b^T ... the
    symmetry multiplies the crystal frame: min_S angle(O_a S O_b^T).
    'left' : the transposed convention, min_S angle(S O_a O_b^T).
    """
    out = np.full((len(a), len(b)), 180.0)
    for i, ga in enumerate(a):
        for j, gb in enumerate(b):
            if convention == "right":
                d = np.einsum("ij,sjk,lk->sil", ga, OPS, gb)
            else:
                d = np.einsum("sij,jk,lk->sil", OPS, ga, gb)
            tr = np.clip((np.trace(d, axis1=1, axis2=2) - 1) / 2, -1, 1)
            out[i, j] = math.degrees(float(np.arccos(tr.max())))
    return out


def score(recon_path: Path, truth_path: Path, convention: str = "right") -> dict:
    rec, tru = read_grains(recon_path), read_grains(truth_path)
    result = {"n_truth": int(len(tru)), "n_reconstructed": int(len(rec)),
              "max_mis_deg": MAX_MIS_DEG, "max_pos_um": MAX_POS_UM, "convention": convention}
    if len(rec) == 0 or len(tru) == 0:
        result.update(n_matched=0, n_false=int(len(rec)), n_missed=int(len(tru)),
                      recall=0.0, precision=0.0)
        return result
    mis = misorientation_deg(orientations(rec), orientations(tru), convention)
    pos = np.linalg.norm(rec[:, None, 10:13] - tru[None, :, 10:13], axis=2)
    ok = (mis <= MAX_MIS_DEG) & (pos <= MAX_POS_UM)
    pairs, used_r, used_t = [], set(), set()
    for i, j in sorted(zip(*np.nonzero(ok)), key=lambda ij: mis[ij]):
        if i not in used_r and j not in used_t:
            pairs.append((int(i), int(j))); used_r.add(i); used_t.add(j)
    m = len(pairs)
    result.update(
        n_matched=m, n_false=int(len(rec) - m), n_missed=int(len(tru) - m),
        recall=round(m / len(tru), 4), precision=round(m / len(rec), 4),
        median_mis_deg=round(float(np.median([mis[p] for p in pairs])), 4) if m else None,
        max_mis_deg_matched=round(float(np.max([mis[p] for p in pairs])), 4) if m else None,
        median_pos_um=round(float(np.median([pos[p] for p in pairs])), 2) if m else None,
        max_pos_um_matched=round(float(np.max([pos[p] for p in pairs])), 2) if m else None)
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("reconstructed", type=Path)
    ap.add_argument("truth", type=Path)
    ap.add_argument("--convention", choices=("right", "left"), default="right")
    a = ap.parse_args()
    print(json.dumps(score(a.reconstructed, a.truth, a.convention), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
