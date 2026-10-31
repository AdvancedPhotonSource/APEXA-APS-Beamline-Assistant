"""Plot the existing summary inspection; does not read raw data or run calibration."""
from pathlib import Path
import json

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

audit = Path(__file__).resolve().parents[1]
rows = json.loads((audit / "rotation-inspection.json").read_text())["rows"]
fig, axes = plt.subplots(1, 2, figsize=(10.8, 4.6), layout="constrained")
for passed, color, marker, label in [
    (True, "#15795b", "o", "Existing gate: pass (35)"),
    (False, "#bb3939", "x", "Existing gate: fail (8)"),
]:
    subset = [row for row in rows if row["passed"] == passed]
    axes[0].scatter([r["loo_reference"] for r in subset], [r["tz"] for r in subset],
                    color=color, marker=marker, s=32, label=label, zorder=3)
    axes[1].scatter([r["scan"] for r in subset], [r["abs_error_deg"] for r in subset],
                    color=color, marker=marker, s=32, zorder=3)
axes[0].plot([-18, 20], [-18, 20], color="#7c8792", linestyle="--", linewidth=1)
axes[0].set(xlabel="Leave-one-out trend estimate of tz (degrees)",
            ylabel="Existing fitted tz (degrees)", title="Series consistency", xlim=(-18, 20), ylim=(-18, 20))
axes[0].legend(loc="lower right", frameon=False, fontsize=9)
axes[1].set(yscale="log", xlabel="Scan ID", ylabel="Absolute deviation from trend (degrees)",
            title="Residual to the fitted series trend")
axes[1].axhline(0.1, color="#44515d", linestyle="--", linewidth=1)
axes[1].text(302, 0.13, "0.1° diagnostic threshold", ha="right", fontsize=8, color="#44515d")
for r in rows:
    if r["scan"] in (269, 276):
        axes[1].annotate(str(r["scan"]), (r["scan"], r["abs_error_deg"]),
                         xytext=(0, 9), textcoords="offset points", ha="center", fontsize=8)
for ax in axes:
    ax.grid(alpha=0.18)
    ax.spines[["top", "right"]].set_visible(False)
fig.suptitle("Rotation calibration: existing 43-exposure summary", fontsize=13)
fig.supxlabel("Reference is fitted from other APEXA results; this is not independent absolute ground truth.\n"
              "No new calibration was run. Source: rotation-inspection.json, audit 28 October 2026.", fontsize=8)
for extension in ("png", "pdf"):
    fig.savefig(audit / f"rotation-consistency.{extension}", dpi=190)
plt.close(fig)
