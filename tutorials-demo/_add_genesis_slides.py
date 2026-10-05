"""Append APEXA capability slides to 2610-Genesis-Demo.pptx, matching its template.

Template conventions read off the existing deck (slide 10):
  10 x 5.63 in, Arial throughout
  card header bar: rectangle fill 005EA2, white 12.75pt text
  body text: 9.75pt, 333333
  footer note strip: fill E8F5E9, 12pt
Numbers here are measured, not illustrative: technique counts come from
capsule_registry over the vendored MIDAS manuals; the calibration figures are from
the 20-ID beamtime of 2026-09-29/30.
"""
import copy
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor

BLUE  = RGBColor(0x00, 0x5E, 0xA2)
GREEN = RGBColor(0xE8, 0xF5, 0xE9)
AMBER = RGBColor(0xFF, 0xF4, 0xE5)
BODY  = RGBColor(0x33, 0x33, 0x33)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
RED   = RGBColor(0xB3, 0x26, 0x1E)

prs = Presentation("2610-Genesis-Demo.BACKUP.pptx")
LAYOUT = {l.name: l for l in prs.slide_layouts}["Title + Content 1"]


def slide(title):
    s = prs.slides.add_slide(LAYOUT)
    for ph in list(s.placeholders):
        if ph.placeholder_format.idx == 1:      # drop the body placeholder
            ph._element.getparent().remove(ph._element)
    s.shapes.title.text = title
    for p in s.shapes.title.text_frame.paragraphs:
        for r in p.runs:
            r.font.name, r.font.size, r.font.bold = "Arial", Pt(16), True
    return s


def bar(s, x, y, w, text, h=0.38, fill=BLUE, fg=WHITE, size=12.75):
    r = s.shapes.add_shape(1, Inches(x), Inches(y), Inches(w), Inches(h))
    r.fill.solid(); r.fill.fore_color.rgb = fill; r.line.fill.background()
    tf = r.text_frame; tf.word_wrap = True
    tf.margin_left = tf.margin_right = Inches(0.08)
    tf.text = text
    for p in tf.paragraphs:
        for run in p.runs:
            run.font.name, run.font.size, run.font.bold, run.font.color.rgb = "Arial", Pt(size), True, fg
    return r


def body(s, x, y, w, h, lines, size=9.75, color=BODY):
    tb = s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame; tf.word_wrap = True
    for i, ln in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        bold = ln.startswith("**")
        run = p.add_run(); run.text = ln.replace("**", "")
        run.font.name, run.font.size, run.font.color.rgb = "Arial", Pt(size), color
        run.font.bold = bold
        p.space_after = Pt(2)
    return tb


def note(s, text, fill=GREEN, y=4.92, size=11, color=BODY):
    r = s.shapes.add_shape(1, Inches(0.23), Inches(y), Inches(9.53), Inches(0.45))
    r.fill.solid(); r.fill.fore_color.rgb = fill; r.line.fill.background()
    tf = r.text_frame; tf.word_wrap = True
    tf.margin_left = Inches(0.1)
    tf.text = text
    for p in tf.paragraphs:
        for run in p.runs:
            run.font.name, run.font.size, run.font.bold, run.font.color.rgb = "Arial", Pt(size), True, color
    return r


def mono(s, x, y, w, h, text, size=9):
    tb = s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame; tf.word_wrap = False
    for i, ln in enumerate(text.split("\n")):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        run = p.add_run(); run.text = ln
        run.font.name, run.font.size, run.font.color.rgb = "Consolas", Pt(size), BODY
        p.space_after = Pt(0)
    return tb


# ───────────────────────────────────────────────────────────────── S1: the cost
s = slide("What a MIDAS Reconstruction Actually Costs")
rows = [("ff-hedm",14,17,59),("pf-hedm",13,12,18),("tomo",10,5,5),("xrd-ct",9,8,7),
        ("defect",8,14,0),("pdf",8,19,17),("solve-cell",8,0,0),
        ("calibrate-integrate",7,15,28),("nf-hedm",7,25,44),("dct-tt",6,8,9),
        ("dfxm",6,33,42)]
bar(s, 0.23, 0.72, 9.53, "11 techniques · 96 ordered steps · 156 hard rules · 229 documented traps")
hdr = ["technique", "steps", "hard rules", "traps"]
tbl = s.shapes.add_table(len(rows)+1, 4, Inches(0.23), Inches(1.22),
                         Inches(5.1), Inches(3.5)).table
for c, t in enumerate(hdr):
    cell = tbl.cell(0, c); cell.text = t
    for p in cell.text_frame.paragraphs:
        for r in p.runs:
            r.font.name, r.font.size, r.font.bold, r.font.color.rgb = "Arial", Pt(9), True, WHITE
    cell.fill.solid(); cell.fill.fore_color.rgb = BLUE
for i, (t, st, hr, tr) in enumerate(rows, start=1):
    for c, v in enumerate([t, st, hr, tr]):
        cell = tbl.cell(i, c); cell.text = str(v)
        for p in cell.text_frame.paragraphs:
            for r in p.runs:
                r.font.name, r.font.size, r.font.color.rgb = "Arial", Pt(8.5), BODY
                r.font.bold = (t in ("ff-hedm", "dfxm"))
body(s, 5.55, 1.22, 4.2, 3.5, [
 "**Every number is read from MIDAS's own manuals.",
 "Vendored as technique capsules, pinned to commit 227d87c2, 0 drift.",
 "",
 "**Order is load-bearing.",
 "FF-HEDM step 7 measures RingThresh on the zarr built at step 6. A value",
 "copied from a template gives 0 peaks — and the tell is that the output is",
 "threshold-invariant, not that anything errors.",
 "",
 "**229 traps.",
 "Each one is written down because it had already produced a confidently",
 "wrong answer in a real beamtime.",
 "",
 "**Traditionally the scientist holds all of this,",
 "or re-reads the manual every time.",
])
note(s, "APEXA recognises the technique from the raw data, loads that capsule, enforces its rules before dispatch, and verifies the artifacts after.")

# ─────────────────────────────────────────────────── S2: FF-HEDM order vs APEXA
s = slide("Example: FF-HEDM Reconstruction — 14 Ordered Steps")
bar(s, 0.23, 0.72, 4.6, "Traditional — the documented order")
mono(s, 0.3, 1.18, 4.6, 3.6,
 "0   verify install\n"
 "1   survey the folder\n"
 "2   ω sign (par field 9)\n"
 "3   scan definition + dark pairing\n"
 "3b  settle ImTransOpt\n"
 "4   energy, then distance\n"
 "5   calibrate + ring overlay\n"
 "6   zip the sweep only\n"
 "7   measure RingThresh on THAT zarr\n"
 "8   build the parameter file\n"
 "8b  check RhoD + ring count\n"
 "9   run the pipeline\n"
 "9b  refine tx, Wedge, re-run 9\n"
 "10  read the result, then report")
bar(s, 5.15, 0.72, 4.6, "APEXA — one call, three gates")
mono(s, 5.22, 1.18, 4.6, 0.9,
 "run_ff_hedm_full_workflow(\n"
 "   result_folder=…, param_file=…,\n"
 "   refine_backend=\"c-omp\")")
body(s, 5.22, 2.15, 4.5, 2.6, [
 "**1. Before dispatch — handbook lint gate",
 "Hard-blocks on any error trap: µm-vs-px on Width/Margin*, SkipFrame",
 "GE-vs-NF, dark exchange/data, template RingThresh. Nothing runs.",
 "",
 "**2. Locality",
 "Data on another host → the typed tool routes over SSH and lints the",
 "remote param file, instead of the agent hand-driving ff_MIDAS.py",
 "past every gate.",
 "",
 "**3. After — verify_ff_reconstruction",
 "Reads artifacts against the documented silent-failure chains.",
 "A present-but-degenerate file is a fail; a missing one is n/a.",
])
note(s, "returncode 0 is not success — a stale Grains.csv from yesterday satisfies “the command exited cleanly”.", fill=AMBER)

# ───────────────────────────────────────────── S3: forward simulation closed loop
s = slide("Example: Forward Simulation → Reconstruction → Ground Truth")
bar(s, 0.23, 0.72, 9.53, "run_forward_simulation — real, not a synthetic demo")
mono(s, 0.3, 1.2, 9.4, 0.8,
 "run_forward_simulation(input_grains_file=\"Grains.csv\",   # known orientations\n"
 "                       param_file=\"Parameters.txt\",      # experimental geometry\n"
 "                       output_prefix=\"sim_\", scanning_mode=False)   # True → PF/scanning")
mono(s, 0.3, 2.15, 9.4, 1.5,
 "   known Grains.csv  ── run_forward_simulation ──▶  simulated patterns\n"
 "          ▲                                                   │\n"
 "          │                                    run_ff_hedm_full_workflow\n"
 "          │                                                   ▼\n"
 "   ground truth  ◀── match_grains + calculate_misorientation ── recovered Grains.csv")
body(s, 0.3, 3.7, 9.4, 1.1, [
 "**Per-grain orientation error against ground truth — a reconstruction-fidelity test with no beamtime.",
 "match_grains (Hungarian assignment) and calculate_misorientation close the loop; both exist today.",
])
note(s, "DFXM hard rule 5:  Validate real data by injection-recovery, never round-trip. A forward-then-inverse round-trip returns ~1e-16 and proves nothing.", fill=AMBER, size=10.5, color=RED)

# ──────────────────────────────────────────────────────────────── S4: DFXM split
s = slide("DFXM in APEXA: Methodology Is Deep, the Forward Model Is Synthetic")
bar(s, 0.23, 0.72, 4.6, "The methodology — richest capsule in the set")
body(s, 0.3, 1.2, 4.6, 3.6, [
 "**33 hard rules · 42 traps · 12 halt conditions",
 "(next highest: nf-hedm, 25 rules)",
 "",
 "**Six phases, core is real-data reduction:",
 "0  survey the scan folder",
 "1  material, reflection, geometry, Λ, resolution",
 "2  raw frames → orientation / strain maps",
 "3  multi-reflection full-F tensor (≥2 reflections)",
 "4  analyse past orientation; validity boundary",
 "5  report with provenance",
 "",
 "**Measured, not stylistic:",
 "• subtract the pedestal before the first moment — on raw",
 "   ID03 frames it carries 98.5 % of the signal",
 "• refraction is a gauge, not a per-pixel strain",
 "• strain has a validity boundary at ~0.3 Λ",
])
bar(s, 5.15, 0.72, 4.6, "The tooling — what is real, what is not")
body(s, 5.22, 1.2, 4.5, 3.6, [
 "**real",
 "run_forward_simulation — diffraction from a known microstructure",
 "compute_pair_distribution — I(Q) → G(r)",
 "analyze_grain_defects — rods / asterism from FF diffuse scattering",
 "fit_grain_odf — per-grain ODF",
 "",
 "**synthetic (forward model only)",
 "simulate_dfxm_image — uniform-strain field on a grid, rendered",
 "through the resolution function + objective optics.",
 "Real strain/orientation-field ingestion deferred upstream.",
 "simulate_2d_diffraction · design_xaf_experiment",
 "",
 "**the gap, stated plainly",
 "No typed tool for DFXM phase-2 real-data reduction yet. The capsule",
 "guides it; it is not guard-railed. Closing that is the same shape of",
 "work just completed for calibration.",
])
note(s, "Every payload carries mode and real_data_supported — APEXA cannot report a synthetic result as a measurement.")

# ────────────────────────────────────────── S5: calibration, real numbers
s = slide("Why the Guards Matter: One Real Calibration, 20-ID, 29 Sep 2026")
bar(s, 0.23, 0.72, 4.6, "Pixel size entered as 100 µm", fill=RGBColor(0xB3,0x26,0x1E))
mono(s, 0.3, 1.2, 4.6, 1.5,
 "Lsd   913.2 mm      (nominal 900)\n"
 "ty     −3.16°   tz   −10.05°\n"
 "held-out strain    3057 µε\n"
 "\n"
 "p3 −180   p6 −180   p12 −180\n"
 "p14 +180  p5 0.01   ← all on bounds")
body(s, 0.3, 2.85, 4.6, 1.9, [
 "A number was produced. Nothing errored.",
 "",
 "**The filename reads _100x100_ — that is the BEAM size.",
 "The VarexD detector is 150 µm.",
])
bar(s, 5.15, 0.72, 4.6, "Re-run at 150 µm", fill=RGBColor(0x1B,0x5E,0x20))
mono(s, 5.22, 1.2, 4.5, 1.5,
 "seed  893.9 mm      (nominal 900)\n"
 "Lsd   895.4 mm\n"
 "ty     −0.36°   tz    −3.44°\n"
 "held-out strain     314 µε\n"
 "\n"
 "595939.5 ÷ (100/150) = 893909")
body(s, 5.22, 2.85, 4.5, 1.9, [
 "The seeder's two answers differ by exactly the pixel-size ratio.",
 "",
 "**That is what a wrong input looks like",
 "**when nothing validates it.",
])
note(s, "APEXA now returns px_um_source, at_bounds (which parameters ran out of room), the seed-vs-recorded distance, and the gate read against its geometry.", fill=AMBER)

prs.save("2610-Genesis-Demo.NEW.pptx")
print("wrote 2610-Genesis-Demo.NEW.pptx with", len(prs.slides.__iter__.__self__._sldIdLst), "slides")
