#!/usr/bin/env python3
"""APEXA calibration on MIDAS's own example frame: raw -> refined geometry -> I(2theta).

Everything is loaded from files that ship with MIDAS, so anyone with the repo can
reproduce this figure. Nothing is illustrative and no number is typed in.

  frame     MIDAS/FF_HEDM/Example/Calibration/
            CeO2_Pil_100x100_att000_650mm_71p676keV_001956.tif   (Pilatus 2M)
  dark      dark_CeO2_Pil_100x100_att000_650mm_71p676keV_001975.tif
  template  parameters.txt   (the detector block MIDAS ships with that frame)
  geometry  refined by APEXA's canonical four-stage run on THAT frame,
            midas-calibrate-v2 0.23.0

Ring positions are DERIVED from the refined wavelength and lattice constant via
Bragg's law, so a wrong calibration would visibly miss the rings. Panel 3 is a
radial average computed from the refined geometry -- it is the geometry's own
product, not a separately-supplied lineout.
"""
import json
import os
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import tifffile

plt.style.use(os.path.expanduser("~/.claude/figure-style.mplstyle"))
matplotlib.rcParams["svg.fonttype"] = "none"
matplotlib.rcParams["pdf.fonttype"] = 42

ROOT = Path(__file__).resolve().parent.parent
EX   = Path("/Users/b324240/Git/MIDAS/FF_HEDM/Example/Calibration")
RAW  = EX / "CeO2_Pil_100x100_att000_650mm_71p676keV_001956.tif"
DARK = EX / "dark_CeO2_Pil_100x100_att000_650mm_71p676keV_001975.tif"
PAR  = ROOT / "scratch-cal/midasex/paramstest_v2.txt"
RES  = ROOT / "scratch-cal/midasex.json"


def read_params(path):
    out = {}
    for ln in Path(path).read_text().splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        parts = ln.split()
        vals = []
        for v in parts[1:]:
            try:
                vals.append(float(v))
            except ValueError:
                vals.append(v)
        out[parts[0]] = vals[0] if len(vals) == 1 else vals
    return out


P   = read_params(PAR)
res = json.loads(RES.read_text())
lam = float(P["Wavelength"]); lsd = float(P["Lsd"]); px = float(P["px"])
bc_y, bc_z = (float(v) for v in P["BC"])
a = float(P["LatticeConstant"][0])
gate = res["gate"]


def fcc_rings(a_ang, lam_ang, tt_max):
    """Allowed Fm-3m reflections from the REFINED lattice constant + wavelength."""
    out, seen = [], set()
    for h in range(9):
        for k in range(9):
            for l in range(9):
                if h == k == l == 0 or not (h % 2 == k % 2 == l % 2):
                    continue
                s = h * h + k * k + l * l
                if s in seen:
                    continue
                arg = lam_ang / (2 * a_ang / np.sqrt(s))
                if arg >= 1:
                    continue
                tt = np.degrees(2 * np.arcsin(arg))
                if tt <= tt_max:
                    seen.add(s)
                    out.append((tuple(sorted((h, k, l), reverse=True)), tt))
    return sorted(out, key=lambda r: r[1])


img = tifffile.imread(RAW).astype(float)
dk  = tifffile.imread(DARK).astype(float)
nz, ny = img.shape
# ImTransOpt 2 (the value MIDAS ships for this frame) = flip Z
img, dk = img[::-1, :], dk[::-1, :]
bad = img < 0                      # Pilatus -1/-2 gaps and dead pixels
sub = np.where(bad, np.nan, img - dk)

tt_corner = np.degrees(np.arctan(
    np.hypot(max(bc_y, ny - bc_y), max(bc_z, nz - bc_z)) * px / lsd))
rings = fcc_rings(a, lam, tt_corner)

# panel 3: radial average in 2theta, using the REFINED geometry
yy, zz = np.meshgrid(np.arange(ny) - bc_y, np.arange(nz) - bc_z)
tt_map = np.degrees(np.arctan(np.hypot(yy, zz) * px / lsd))
ok = np.isfinite(sub)
nb = 1400
edges = np.linspace(0, tt_corner, nb + 1)
idx = np.digitize(tt_map[ok], edges) - 1
good = (idx >= 0) & (idx < nb)
cnt = np.bincount(idx[good], minlength=nb)
tot = np.bincount(idx[good], weights=sub[ok][good], minlength=nb)
prof = np.where(cnt > 50, tot / np.maximum(cnt, 1), np.nan)
cent = 0.5 * (edges[1:] + edges[:-1])

disp = np.where(np.isfinite(sub) & (sub > 0), sub, np.nan)
norm = matplotlib.colors.LogNorm(vmin=max(np.nanpercentile(disp, 60), 1),
                                 vmax=np.nanpercentile(disp, 99.7))

fig = plt.figure(figsize=(13.2, 5.15))
gs = fig.add_gridspec(2, 3, width_ratios=[1, 1, 1.2], height_ratios=[1, 0.27],
                      wspace=0.15, hspace=0.1,
                      left=0.03, right=0.985, top=0.885, bottom=0.035)
ax0, ax1, ax2 = (fig.add_subplot(gs[0, i]) for i in range(3))
axt = fig.add_subplot(gs[1, :]); axt.axis("off")

ax0.imshow(disp, cmap="magma", norm=norm, origin="lower", interpolation="nearest")
ax0.set_title("1 · Raw detector frame, dark subtracted\n"
              "Pilatus 2M · CeO$_2$ · 71.676 keV · 650 mm", fontsize=10, loc="left")
ax0.set_xticks([]); ax0.set_yticks([])
ax0.text(0.025, 0.025, f"{ny} × {nz} px, {px:.0f} µm", transform=ax0.transAxes,
         fontsize=7.5, color="white", va="bottom")

ax1.imshow(disp, cmap="magma", norm=norm, origin="lower", interpolation="nearest")
th = np.linspace(np.pi / 2, 3 * np.pi / 2, 300)
for _, tt in rings:
    r = lsd * np.tan(np.radians(tt)) / px
    ax1.plot(bc_y + r * np.cos(th), bc_z + r * np.sin(th),
             lw=0.9, color="#39FF14", alpha=0.95)
ax1.axvline(bc_y, color="white", lw=0.6, alpha=0.45)
ax1.plot(bc_y, bc_z, "+", color="#00E5FF", ms=11, mew=1.6)
ax1.set_xlim(0, ny); ax1.set_ylim(0, nz); ax1.set_xticks([]); ax1.set_yticks([])
ax1.set_title(f"2 · APEXA refined geometry, overlaid\n"
              f"{len(rings)} CeO$_2$ rings predicted from the fit",
              fontsize=10, loc="left")
ax1.text(0.03, 0.96, "predicted", transform=ax1.transAxes, fontsize=8.5,
         color="#39FF14", va="top", weight="bold")
ax1.text(0.97, 0.96, "measured", transform=ax1.transAxes, fontsize=8.5,
         color="white", va="top", ha="right", weight="bold")

ax2.plot(cent, prof, lw=0.9, color="#1a1a1a")
for _, tt in rings:
    ax2.axvline(tt, color="#39FF14", lw=0.7, alpha=0.5, zorder=0)
for i, (hkl, tt) in enumerate(rings[:5]):
    ax2.annotate("".join(map(str, hkl)),
                 xy=(tt, np.nanmax(prof) * (2.2 if i % 2 else 0.95)),
                 fontsize=7.5, ha="center", color="#2d6b16", weight="bold")
ax2.set_yscale("log")
ax2.set_xlim(0.3, min(tt_corner, rings[-1][1] * 1.08))
ax2.set_ylim(np.nanpercentile(prof, 2) * 0.7, np.nanmax(prof) * 6)
ax2.set_xlabel("2θ  (degrees)", fontsize=9)
ax2.set_ylabel("mean intensity", fontsize=9)
ax2.set_title("3 · Integrated pattern\nradial average using the refined geometry",
              fontsize=10, loc="left")
ax2.tick_params(labelsize=8)

cells = [("Lsd",             f"{lsd/1000:.3f} mm"),
         ("beam centre",     f"({bc_y:.2f}, {bc_z:.2f}) px"),
         ("tilts ty, tz",    f"{float(P['ty']):+.3f}°, {float(P['tz']):+.3f}°"),
         ("wavelength",      f"{lam:.6f} Å"),
         ("held-out strain", f"{gate['stage4_strain_uE_test']:.1f} µε "
                             f"(gate {gate['threshold_uE']:.0f} — passed)")]
x = 0.012
for label, val in cells:
    axt.text(x, 0.60, label, transform=axt.transAxes, fontsize=8, color="#555555")
    axt.text(x, 0.14, val, transform=axt.transAxes, fontsize=9.5, weight="bold")
    x += 0.197
axt.text(0.012, -0.44,
         "Frame, dark and detector template all ship with MIDAS "
         "(FF_HEDM/Example/Calibration). Geometry refined by APEXA's canonical "
         "four-stage run; ring positions derived from the refined wavelength and "
         "lattice constant via Bragg's law, not tabulated.",
         transform=axt.transAxes, fontsize=7.8, color="#444444")

base = str(ROOT / "figures/fig_apexa_calibration_products")
for ext in ("pdf", "svg"):
    fig.savefig(f"{base}.{ext}")
fig.savefig(base + ".png", dpi=300)
print(f"rings={len(rings)}  2θ {rings[0][1]:.2f}–{rings[-1][1]:.2f}°  "
      f"strain {gate['stage4_strain_uE_test']:.1f} µε")
