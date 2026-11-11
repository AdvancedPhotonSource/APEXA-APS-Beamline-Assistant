"""Read retained fit artifacts to freeze evaluator-only sweep references.

No numerical fit or model is run. Each prediction excludes the same acquisition
from its own sweep/calibrant. Source bytes and both route references are pinned.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import re

import rotation_benchmark as rb
import study


def write_csv(path, rows):
    with path.open("x", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def predict(rows):
    result = []
    for row in rows:
        others = [r for r in rows if r["calibrant"] == row["calibrant"] and r["scan"] != row["scan"]]
        a, b = rb.robust_line([r["scan"] for r in others], [r["tz_deg"] for r in others])
        result.append({**row, "predicted_tz_deg": a + b * row["scan"], "n_fit": len(others)})
    return result


def prepare(root, first, rs2):
    setup = root / "setup"
    if (setup / "frozen.json").exists():
        raise RuntimeError("Cannot change evaluator references after study freezing")
    sources = [{"path": str(first), "sha256": study.sha(first)}]
    coarse = []
    for row in csv.DictReader(first.open()):
        coarse.append({"scan": int(row["scan"]), "calibrant": row["calibrant"],
                       "sweep_calibrant": "first_" + row["calibrant"], "tz_deg": float(row["tz_deg"])})
    if {r["scan"] for r in coarse} != set(range(261, 304)):
        raise ValueError("First-sweep reference must retain all 43 original acquisitions")
    primary, sensitivity = [], []
    for scan in range(304, 386):
        calibrant = "CeO2" if scan < 345 else "LaB6"
        case = ("Ceria" if scan < 345 else "Lab6") + f"_{scan}"
        r1 = rs2 / "tilted" / case / "geometry.json"
        r2 = rs2 / "fourstage" / case / "paramstest_v2.txt"
        value = float(json.loads(r1.read_text())["geometry"]["tz"])
        found = re.findall(r"^\s*tz\s+([-+]?\d+(?:\.\d*)?(?:[eE][-+]?\d+)?)\s*$", r2.read_text(), re.M)
        if len(found) != 1:
            raise ValueError(f"Expected one finite tz in {r2}")
        primary.append({"scan": scan, "calibrant": calibrant, "tz_deg": value})
        sensitivity.append({"scan": scan, "calibrant": calibrant, "tz_deg": float(found[0])})
        sources.extend({"path": str(p), "sha256": study.sha(p)} for p in (r1, r2))
    import math
    if not all(math.isfinite(r["tz_deg"]) for r in coarse + primary + sensitivity):
        raise ValueError("Nonfinite retained fit; reference requires explicit review")
    write_csv(setup / "reference_rs2.csv", predict(primary))
    write_csv(setup / "reference_rs2_r2.csv", predict(sensitivity))
    combined = coarse + [{"scan": r["scan"], "calibrant": r["calibrant"],
        "sweep_calibrant": "second_" + r["calibrant"], "tz_deg": r["tz_deg"]} for r in primary]
    write_csv(setup / "reference.csv", combined)
    study.write_new(setup / "reference-provenance.json", {
        "kind": "within-sweep/calibrant leave-one-out relative consistency",
        "estimator": "S2 exhaustive LMedS with finite-sample scale and inlier least-squares refit",
        "estimator_source_sha256": study.sha(Path(rb.__file__)), "sources": sources,
        "outputs": {name: study.sha(setup / name) for name in
                    ("reference.csv", "reference_rs2.csv", "reference_rs2_r2.csv")},
        "prior_use": "Both sweeps have retained solver evaluations; these are not previously unused acquisitions",
        "model_calls": 0, "new_scientific_fits": 0})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--first-reference", type=Path, required=True)
    parser.add_argument("--rs2", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.root.resolve(), args.first_reference.resolve(), args.rs2.resolve())


if __name__ == "__main__":
    main()
