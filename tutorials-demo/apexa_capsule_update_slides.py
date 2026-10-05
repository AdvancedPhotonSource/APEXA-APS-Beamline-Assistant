#!/usr/bin/env python3
"""Generate a focused 2-3 slide update deck for the APEXA capsule engine +
latest commits, in the APEXA_APS_Presentation house style.

Audience: APS beamline users + advisors (meeting update).
Output:   APEXA_Capsule_Update.pptx  (drop-in / merge with APEXA_APS_Presentation.pptx)

The deck covers the newly-committed work (c14130b):
  1. The technique-capsule engine  (teach APEXA any technique from docs)
  2. The four wiring points        (learn -> scope -> enforce -> verify)
  3. What shipped this cycle        (latest commits + impact)
"""

from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE

# ── Brand colors (match APEXA_APS_Presentation.py) ─────────────────────
DARK_BLUE  = RGBColor(0x00, 0x2B, 0x5C)
MED_BLUE   = RGBColor(0x00, 0x5E, 0xA2)
ACCENT     = RGBColor(0xE8, 0x7C, 0x00)
WHITE      = RGBColor(0xFF, 0xFF, 0xFF)
LIGHT_GRAY = RGBColor(0xF2, 0xF2, 0xF2)
DARK_GRAY  = RGBColor(0x33, 0x33, 0x33)
GREEN      = RGBColor(0x2E, 0x7D, 0x32)
RED        = RGBColor(0xC6, 0x28, 0x28)
LIGHT_BLUE = RGBColor(0xBB, 0xDE, 0xFB)
PALE_BLUE  = RGBColor(0x90, 0xCA, 0xF9)
SKY_BLUE   = RGBColor(0x64, 0xB5, 0xF6)
TEAL       = RGBColor(0x00, 0x79, 0x6B)

prs = Presentation()
prs.slide_width  = Inches(13.333)
prs.slide_height = Inches(7.5)


# ── Helpers ────────────────────────────────────────────────────────────
def add_bg(slide, color=DARK_BLUE):
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = color

def add_shape_bg(slide, left, top, width, height, color, shape=MSO_SHAPE.RECTANGLE):
    sp = slide.shapes.add_shape(shape, left, top, width, height)
    sp.fill.solid()
    sp.fill.fore_color.rgb = color
    sp.line.fill.background()
    sp.shadow.inherit = False
    return sp

def add_text_box(slide, left, top, width, height, text, font_size=18, bold=False,
                 color=DARK_GRAY, alignment=PP_ALIGN.LEFT, font_name="Calibri"):
    tb = slide.shapes.add_textbox(left, top, width, height)
    tf = tb.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = text
    p.font.size = Pt(font_size)
    p.font.bold = bold
    p.font.color.rgb = color
    p.font.name = font_name
    p.alignment = alignment
    return tf

def slide_title_bar(slide, title, kicker=""):
    add_shape_bg(slide, Inches(0), Inches(0), Inches(13.333), Inches(1.1), MED_BLUE)
    add_text_box(slide, Inches(0.55), Inches(0.14), Inches(10.5), Inches(0.55),
                 title, font_size=28, bold=True, color=WHITE)
    if kicker:
        add_text_box(slide, Inches(0.58), Inches(0.70), Inches(11.5), Inches(0.35),
                     kicker, font_size=13, color=LIGHT_BLUE)

def blank():
    return prs.slides.add_slide(prs.slide_layouts[6])

def box(slide, left, top, width, height, title, sub="", fill=WHITE,
        title_color=DARK_BLUE, sub_color=DARK_GRAY, title_size=14, sub_size=10.5,
        shape=MSO_SHAPE.ROUNDED_RECTANGLE, border=None):
    sp = add_shape_bg(slide, left, top, width, height, fill, shape=shape)
    if border:
        sp.line.color.rgb = border
        sp.line.width = Pt(1.5)
    tf = sp.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    tf.margin_top = Pt(3); tf.margin_bottom = Pt(3)
    tf.margin_left = Pt(6); tf.margin_right = Pt(6)
    p = tf.paragraphs[0]
    p.text = title
    p.alignment = PP_ALIGN.CENTER
    p.font.size = Pt(title_size); p.font.bold = True; p.font.color.rgb = title_color
    p.font.name = "Calibri"
    if sub:
        p2 = tf.add_paragraph()
        p2.text = sub
        p2.alignment = PP_ALIGN.CENTER
        p2.font.size = Pt(sub_size); p2.font.color.rgb = sub_color
        p2.font.name = "Calibri"
    return sp

def arrow(slide, left, top, width, height=Inches(0.32), color=ACCENT,
          shape=MSO_SHAPE.RIGHT_ARROW):
    return add_shape_bg(slide, left, top, width, height, color, shape=shape)

def chip(slide, left, top, width, text, fill, tcolor=WHITE, size=11.5, height=Inches(0.42)):
    sp = add_shape_bg(slide, left, top, width, height, fill, shape=MSO_SHAPE.ROUNDED_RECTANGLE)
    tf = sp.text_frame; tf.word_wrap = True
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    tf.margin_top = Pt(1); tf.margin_bottom = Pt(1)
    p = tf.paragraphs[0]; p.text = text; p.alignment = PP_ALIGN.CENTER
    p.font.size = Pt(size); p.font.bold = True; p.font.color.rgb = tcolor; p.font.name = "Calibri"
    return sp

def bullets(slide, left, top, width, height, items, size=13, gap=4, color=DARK_GRAY):
    tb = slide.shapes.add_textbox(left, top, width, height)
    tf = tb.text_frame; tf.word_wrap = True
    for i, (head, rest) in enumerate(items):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.space_after = Pt(gap)
        r1 = p.add_run(); r1.text = f"▸ {head}"
        r1.font.size = Pt(size); r1.font.bold = True; r1.font.color.rgb = DARK_BLUE; r1.font.name = "Calibri"
        if rest:
            r2 = p.add_run(); r2.text = f"  {rest}"
            r2.font.size = Pt(size); r2.font.color.rgb = color; r2.font.name = "Calibri"
    return tf


# ═══════════════════════════════════════════════════════════════════════
# SLIDE 1 — The technique-capsule engine (big idea + pipeline flowchart)
# ═══════════════════════════════════════════════════════════════════════
s = blank(); add_bg(s, WHITE)
slide_title_bar(s, "Teaching APEXA any beamline technique",
                "Technique-capsule engine  ·  committed c14130b  ·  zero per-technique Python")

# One-line thesis banner
banner = add_shape_bg(s, Inches(0.55), Inches(1.28), Inches(12.25), Inches(0.62), DARK_BLUE)
tf = banner.text_frame; tf.vertical_anchor = MSO_ANCHOR.MIDDLE
p = tf.paragraphs[0]
p.text = "Adopt a new technique by vendoring its MIDAS docs — the engine auto-discovers it. No new Python."
p.alignment = PP_ALIGN.CENTER
p.font.size = Pt(15); p.font.bold = True; p.font.color.rgb = WHITE; p.font.name = "Calibri"

# Pipeline flowchart:  MIDAS manuals -> sync -> capsules/ -> registry -> agent turn
y = Inches(2.35); bh = Inches(1.15); bw = Inches(2.25)
xs = [Inches(0.55), Inches(3.15), Inches(5.75), Inches(8.35), Inches(10.95)]
box(s, xs[0], y, bw, bh, "MIDAS manuals",
    "source of truth\nff · nf · pf · dfxm", fill=LIGHT_GRAY, title_size=13)
box(s, xs[1], y, bw, bh, "sync_midas_capsules.py",
    "structural sync\n--check drift · _pin.json", fill=LIGHT_BLUE, title_size=12.5)
box(s, xs[2], y, bw, bh, "knowledge_base/capsules/",
    "49 vendored docs\npinned @4225c760", fill=LIGHT_BLUE, title_size=12)
box(s, xs[3], y, bw, bh, "capsule_registry.py",
    "stdlib · lru_cache\nfail-open accessor", fill=SKY_BLUE, title_size=12.5, title_color=WHITE, sub_color=WHITE)
box(s, xs[4], y, bw, bh, "Agent turn",
    "spine · scope · lint\ninjected in-context", fill=ACCENT, title_size=13, title_color=WHITE, sub_color=WHITE)
for x in xs[:-1]:
    arrow(s, x + bw + Inches(0.02), y + Inches(0.42), Inches(0.32))

# What a capsule contains
add_text_box(s, Inches(0.55), Inches(3.95), Inches(12), Inches(0.35),
             "Each capsule = a self-describing doc set (the same shape for every technique):",
             font_size=13, bold=True, color=DARK_BLUE)
cap_items = [
    ("README.md", "spine: scope · hard rules · halt · THE ORDER", PALE_BLUE),
    ("ENVELOPE.md", "Fixed / Configured / Intrinsic / Derived", LIGHT_GRAY),
    ("phase-N-*.md", "just-in-time step docs", LIGHT_GRAY),
    ("PARAMETERS.md", "GFM param tables → lint facts", PALE_BLUE),
    ("RUNBOOK.md", "volatile host/version — re-verify", LIGHT_GRAY),
    ("DIAGNOSIS.md", "symptom → test → cause → lever", LIGHT_GRAY),
]
cx = Inches(0.55); cw = Inches(3.95); gap = Inches(0.15); ch = Inches(0.55)
col = 0; row = 0
for head, rest, fill in cap_items:
    x = Inches(0.55 + col * 4.10); yy = Inches(4.35 + row * 0.68)
    sp = add_shape_bg(s, x, yy, cw, ch, fill, shape=MSO_SHAPE.ROUNDED_RECTANGLE)
    tf = sp.text_frame; tf.vertical_anchor = MSO_ANCHOR.MIDDLE; tf.word_wrap = True
    tf.margin_left = Pt(8)
    p = tf.paragraphs[0]
    r1 = p.add_run(); r1.text = head + "  "; r1.font.bold = True; r1.font.size = Pt(12)
    r1.font.color.rgb = DARK_BLUE; r1.font.name = "Consolas"
    r2 = p.add_run(); r2.text = rest; r2.font.size = Pt(11); r2.font.color.rgb = DARK_GRAY
    r2.font.name = "Calibri"
    col += 1
    if col == 3:
        col = 0; row += 1

# Why-it-matters strip
strip = add_shape_bg(s, Inches(0.55), Inches(6.35), Inches(12.25), Inches(0.78), DARK_BLUE)
tf = strip.text_frame; tf.vertical_anchor = MSO_ANCHOR.MIDDLE; tf.word_wrap = True
tf.margin_left = Pt(12); tf.margin_right = Pt(12)
p = tf.paragraphs[0]
r1 = p.add_run(); r1.text = "Why it matters:  "
r1.font.bold = True; r1.font.size = Pt(14); r1.font.color.rgb = ACCENT; r1.font.name = "Calibri"
r2 = p.add_run()
r2.text = ("4 techniques live today (ff / nf / pf / dfxm); extensible to all 9 techniques across "
           "11-ID, 1-ID-E, 20-D-E, 6-ID-C …  A new beamline technique is a docs directory, not a code change.")
r2.font.size = Pt(13.5); r2.font.color.rgb = WHITE; r2.font.name = "Calibri"


# ═══════════════════════════════════════════════════════════════════════
# SLIDE 2 — Four wiring points (agent-turn enforcement flow)
# ═══════════════════════════════════════════════════════════════════════
s = blank(); add_bg(s, WHITE)
slide_title_bar(s, "Four wiring points: learn → scope → enforce → verify",
                "How a capsule reaches the model — every tool call funnels through one chokepoint")

# Top row: the meta-tools (model-driven, on-demand)
add_text_box(s, Inches(0.55), Inches(1.25), Inches(12), Inches(0.3),
             "On-demand (model asks):  three client-side meta-tools on the core tool surface",
             font_size=13, bold=True, color=TEAL)
mt = [("list_techniques", "manifest of capsules"),
      ("learn_technique(name)", "load spine + halt + phases"),
      ("open_phase(tech, N)", "one JIT phase doc")]
mx = Inches(0.55)
for head, sub in mt:
    box(s, mx, Inches(1.60), Inches(3.95), Inches(0.72), head, sub,
        fill=RGBColor(0xE0, 0xF2, 0xF1), title_color=TEAL, title_size=13, sub_size=11)
    mx += Inches(4.10)

# Divider label
add_text_box(s, Inches(0.55), Inches(2.55), Inches(12), Inches(0.3),
             "Automatic (every turn):  the tool call passes these gates in order",
             font_size=13, bold=True, color=DARK_BLUE)

# The 4 gates as a vertical flow with a tool call entering
gy = Inches(2.95); gh = Inches(0.92); gw = Inches(2.75)
gx = [Inches(0.55), Inches(3.55), Inches(6.55), Inches(9.55)]
gates = [
    ("1 · LEARN", "spine + halt injected once per\ntechnique on first tool fire", SKY_BLUE, "fail-open"),
    ("2 · SCOPE GATE", "APEXA_BEAMLINE incompatible?\ntool NOT run", RED, "fail-CLOSED"),
    ("3 · ENFORCE", "handbook lint: block on any\nµm-px / bound-pair error", ACCENT, "hard block"),
    ("4 · VERIFY", "post-stage on-disk check;\ndegenerate output → model", GREEN, "fail-open"),
]
for i, (head, sub, fill, tag) in enumerate(gates):
    box(s, gx[i], gy, gw, gh, head, sub, fill=fill, title_color=WHITE, sub_color=WHITE,
        title_size=14, sub_size=10.5)
    # tag chip under each
    chip(s, gx[i] + Inches(0.6), gy + gh + Inches(0.08), Inches(1.55), tag,
         fill=DARK_BLUE, size=10, height=Inches(0.34))
    if i < 3:
        arrow(s, gx[i] + gw + Inches(0.02), gy + Inches(0.30), Inches(0.24))

# Entry + exit labels
add_text_box(s, Inches(0.55), Inches(4.30), Inches(3), Inches(0.3),
             "⬇  tool call from model", font_size=11, bold=True, color=DARK_GRAY)
add_text_box(s, Inches(9.55), Inches(4.30), Inches(3.3), Inches(0.3),
             "⬇  executes, result grounded in ledger", font_size=11, bold=True, color=GREEN)

# Bottom: where each lives + the guardrail win
add_shape_bg(s, Inches(0.55), Inches(4.85), Inches(6.0), Inches(2.15), LIGHT_GRAY,
             shape=MSO_SHAPE.ROUNDED_RECTANGLE)
add_text_box(s, Inches(0.75), Inches(4.95), Inches(5.6), Inches(0.35),
             "Where it lives", font_size=14, bold=True, color=DARK_BLUE)
bullets(s, Inches(0.75), Inches(5.35), Inches(5.6), Inches(1.6), [
    ("Meta-tools", "— apexa_toolsurface.py"),
    ("Spine injection", "— apexa_agents.py (Mode-1 + Mode-2)"),
    ("Scope gate", "— argo_mcp_client.py chokepoint"),
    ("Lint + verify", "— handbook_guardrails.py"),
], size=12.5, gap=5)

add_shape_bg(s, Inches(6.85), Inches(4.85), Inches(5.95), Inches(2.15), DARK_BLUE,
             shape=MSO_SHAPE.ROUNDED_RECTANGLE)
add_text_box(s, Inches(7.05), Inches(4.95), Inches(5.6), Inches(0.35),
             "New this cycle: guardrails now cover nf + pf", font_size=14, bold=True, color=ACCENT)
bullets_tf = slide = None
tb = s.shapes.add_textbox(Inches(7.05), Inches(5.38), Inches(5.6), Inches(1.55))
tf = tb.text_frame; tf.word_wrap = True
lines = [
    ("load_param_facts() merges:", ""),
    ("  FF live reference", "234 keys"),
    ("  vendored capsule PARAMETERS", "nf 82 · pf 12  (first time)"),
    ("  embedded fallback", "offline-safe"),
]
for i, (h, r) in enumerate(lines):
    p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
    p.space_after = Pt(4)
    r1 = p.add_run(); r1.text = h
    r1.font.size = Pt(12.5); r1.font.bold = (i == 0); r1.font.name = "Calibri"
    r1.font.color.rgb = WHITE
    if r:
        r2 = p.add_run(); r2.text = "   " + r
        r2.font.size = Pt(12.5); r2.font.bold = True; r2.font.color.rgb = PALE_BLUE; r2.font.name = "Calibri"


# ═══════════════════════════════════════════════════════════════════════
# SLIDE 3 — What shipped this cycle (commit timeline + impact)
# ═══════════════════════════════════════════════════════════════════════
s = blank(); add_bg(s, WHITE)
slide_title_bar(s, "What shipped this cycle",
                "From regex prompt-guards → fact-based enforcement + generic technique adoption")

rows = [
    ("c14130b", "Technique-capsule engine + structured LLM transport",
     "generic per-technique docs; scope gate; nf/pf param facts; argo-proxy provider + ledger", ACCENT),
    ("5b52f36", "Generic handbook-sourced FF lint engine + post-stage verifier",
     "guardrails from the handbook, not per-failure if-statements; on-disk output verify", MED_BLUE),
    ("ac2c022", "Single-mode query-matched skills + BoxSize / ω-window lint traps",
     "the right skill per query; catch collapsed-span & degenerate bound pairs", MED_BLUE),
    ("2473d58", "Load Skills into agent context + block px-for-µm FF params",
     "verified procedure in-context before the model edits a param file", MED_BLUE),
    ("71f71cc", "Refresh Argo model catalog (Opus 5 default) + rm deletion gate",
     "current model list; every rm/rmdir/unlink needs live human approval", TEAL),
]
ry = Inches(1.35); rh = Inches(0.86)
for sha, title, sub, col in rows:
    # sha chip
    chip(s, Inches(0.55), ry + Inches(0.16), Inches(1.35), sha, fill=col, size=12, height=Inches(0.5))
    # title/sub block
    tb = s.shapes.add_textbox(Inches(2.10), ry, Inches(10.7), rh)
    tf = tb.text_frame; tf.word_wrap = True
    p = tf.paragraphs[0]; p.text = title
    p.font.size = Pt(15); p.font.bold = True; p.font.color.rgb = DARK_BLUE; p.font.name = "Calibri"
    p2 = tf.add_paragraph(); p2.text = sub
    p2.font.size = Pt(12); p2.font.color.rgb = DARK_GRAY; p2.font.name = "Calibri"
    # separator
    add_shape_bg(s, Inches(2.10), ry + rh - Inches(0.02), Inches(10.6), Pt(1), LIGHT_GRAY)
    ry += rh

# Impact footer band
band = add_shape_bg(s, Inches(0.55), Inches(5.95), Inches(12.25), Inches(1.15), DARK_BLUE)
tf = band.text_frame; tf.vertical_anchor = MSO_ANCHOR.MIDDLE; tf.word_wrap = True
tf.margin_left = Pt(14); tf.margin_right = Pt(14)
p = tf.paragraphs[0]
r1 = p.add_run(); r1.text = "Net effect:  "
r1.font.bold = True; r1.font.size = Pt(15); r1.font.color.rgb = ACCENT; r1.font.name = "Calibri"
r2 = p.add_run()
r2.text = ("must-never-violate rules are enforced from recorded execution state, not hoped-for in a prompt. "
           "Adding a guardrail = editing a handbook or one cited row. Adopting a technique = vendoring its docs.")
r2.font.size = Pt(13.5); r2.font.color.rgb = WHITE; r2.font.name = "Calibri"


# ── Save ────────────────────────────────────────────────────────────────
OUT = "APEXA_Capsule_Update.pptx"
prs.save(OUT)
print(f"Wrote {OUT}  ({len(prs.slides.__iter__.__self__._sldIdLst)} slides)")
