"""Technique-capsule registry + parser.

MIDAS ships a self-describing, agent-first documentation "capsule" per technique
under ``manuals/<technique>/`` — vendored into APEXA at
``knowledge_base/capsules/<technique>/`` by ``scripts/sync_midas_capsules.py``.
Every capsule follows the same schema (authored to ``beamreport/DOCS_SPEC.md`` and
enforced upstream by ``beamreport-doc-lint`` + ``doc_citation_check.py``):

    README.md        the spine — the ONLY file meant to stay loaded. Carries a
                     "what to read when" load schedule, a halt-conditions gate,
                     numbered hard rules, a traps table, and THE ORDER (phases).
    ENVELOPE.md      what the measurement can / cannot determine (Fixed/Configured/Intrinsic)
    RUNBOOK.md       VOLATILE host + version state — re-verify live, never trust the copy
    phase-N-*.md     each ordered workflow step (opened just-in-time when reached)
    PARAMETERS.md    parameter schema as GFM tables (| Key | units | Read by |)
    DIAGNOSIS.md     symptom -> discriminating test -> cause -> lever
    LAB_NOTEBOOK.md  evidence + retracted claims (RAG depth, never always-loaded)

This module turns that invariant structure into typed accessors so APEXA can:
  * discover techniques from the filesystem (drop a new capsule dir -> it appears),
  * load only the README spine up front and page in phase docs on demand (JIT),
  * read the halt-conditions / scope for a fail-closed preconditions gate,
  * feed PARAMETERS/DIAGNOSIS into the guardrail engine and verifier.

WHY THIS IS GENERIC (the whole point): a new technique is adopted by vendoring a
new ``manuals/<technique>/`` directory that follows the pattern — **zero
per-technique Python here.** Nothing in this file names ff/nf/pf/dfxm.

Design notes:
  * stdlib only (mirrors ``skill_registry.py``) so importing it never spins up the
    MCP server or pulls heavyweight deps.
  * every accessor is FAIL-OPEN: a missing/malformed capsule yields ``[]``/``""``,
    never an exception into the agent loop.
  * parsers are tolerant — they locate sections by heading keywords and read GFM
    pipe tables by header name, so cosmetic doc edits (a renamed column, an extra
    section) do not break ingestion. What they must NOT do is invent content: an
    unparseable section returns empty and the caller falls back to the raw spine.
  * RUNBOOK/version content is deliberately NOT promoted to any "fact" — the docs
    insist it is checked live. This module exposes it as raw text only.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

# Anchor to THIS module's dir, not the process CWD (the agent loop runs from an
# arbitrary working dir). Matches skill_registry.py's anchoring choice.
CAPSULES_DIR = Path(__file__).resolve().parent / "knowledge_base" / "capsules"
PIN_FILE = CAPSULES_DIR / "_pin.json"

# The two files DOCS_SPEC guarantees for every technique doc set; their presence
# is what marks a directory as a capsule (structural, not a hardcoded list).
_MARKERS = ("README.md", "ENVELOPE.md")

_MD_LINK_OR_CODE = re.compile(r"`?([A-Za-z0-9_.-]+\.md)`?")


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def available_techniques() -> tuple:
    """Sorted technique names (capsule dir names) present on disk. Fail-open ()."""
    if not CAPSULES_DIR.is_dir():
        return tuple()
    out = []
    for sub in sorted(p for p in CAPSULES_DIR.iterdir() if p.is_dir()):
        if all((sub / m).is_file() for m in _MARKERS):
            out.append(sub.name)
    return tuple(out)


def has_technique(name: str) -> bool:
    return _canon(name) in available_techniques()


def _canon(name: str) -> str:
    """Match a user string to a capsule dir, case/punct-insensitively.

    'FF', 'ff-hedm', 'ff_hedm', 'FF HEDM' -> 'ff-hedm' when unambiguous.
    """
    if not name:
        return ""
    techs = available_techniques()
    raw = name.strip()
    if raw in techs:
        return raw
    norm = re.sub(r"[^a-z0-9]", "", raw.lower())
    # exact normalized match first
    for t in techs:
        if re.sub(r"[^a-z0-9]", "", t.lower()) == norm:
            return t
    # prefix/substring (e.g. "ff" -> "ff-hedm") only when unambiguous
    hits = [t for t in techs if re.sub(r"[^a-z0-9]", "", t.lower()).startswith(norm)] if norm else []
    if len(hits) == 1:
        return hits[0]
    hits2 = [t for t in techs if norm and norm in re.sub(r"[^a-z0-9]", "", t.lower())]
    if len(hits2) == 1:
        return hits2[0]
    return ""  # ambiguous or unknown -> caller decides (never guess)


# --------------------------------------------------------------------------- #
# Raw file access (cached)
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=256)
def _read(technique: str, filename: str) -> str:
    t = _canon(technique)
    if not t:
        return ""
    path = CAPSULES_DIR / t / filename
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def spine(technique: str) -> str:
    """The README body — the doc meant to stay loaded the whole session."""
    return _read(technique, "README.md")


def envelope_doc(technique: str) -> str:
    return _read(technique, "ENVELOPE.md")


def runbook_doc(technique: str) -> str:
    """VOLATILE host/version state. Raw text only — never treat as fact."""
    return _read(technique, "RUNBOOK.md")


def diagnosis_doc(technique: str) -> str:
    return _read(technique, "DIAGNOSIS.md")


def lab_notebook_doc(technique: str) -> str:
    return _read(technique, "LAB_NOTEBOOK.md")


def parameters_doc(technique: str) -> str:
    """The parameter-schema doc. Filename varies (PARAMETERS.md / PF_PARAMETERS.md);
    some capsules keep the param table inside a phase file instead."""
    t = _canon(technique)
    if not t:
        return ""
    d = CAPSULES_DIR / t
    for cand in ("PARAMETERS.md", "PF_PARAMETERS.md"):
        if (d / cand).is_file():
            return _read(t, cand)
    # fall back to any *PARAMETERS*.md
    try:
        for p in sorted(d.glob("*PARAMETERS*.md")):
            return _read(t, p.name)
    except OSError:
        pass
    return ""


# --------------------------------------------------------------------------- #
# Markdown helpers
# --------------------------------------------------------------------------- #
def _heading_level(line: str) -> int:
    m = re.match(r"^(#{1,6})\s", line)
    return len(m.group(1)) if m else 0


def _section(md: str, *keywords: str) -> str:
    """Return the body under the first heading containing ALL keywords (ci),
    up to the next heading of the same-or-higher level. "" if not found."""
    if not md or not keywords:
        return ""
    kws = [k.lower() for k in keywords]
    lines = md.splitlines()
    start = level = None
    for i, ln in enumerate(lines):
        lv = _heading_level(ln)
        if lv and all(k in ln.lower() for k in kws):
            start, level = i + 1, lv
            break
    if start is None:
        return ""
    end = len(lines)
    for j in range(start, len(lines)):
        lv = _heading_level(lines[j])
        if lv and lv <= level:
            end = j
            break
    return "\n".join(lines[start:end]).strip()


def _pipe_tables(md: str) -> List[Dict]:
    """Parse every GFM pipe table in ``md`` -> [{header:[...], rows:[[...]]}]."""
    tables: List[Dict] = []
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i].strip()
        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
        # header row followed by a |---|---| separator
        if ln.startswith("|") and re.match(r"^\|[\s:|-]+\|?\s*$", nxt) and "-" in nxt:
            header = _split_row(ln)
            rows = []
            j = i + 2
            while j < len(lines) and lines[j].strip().startswith("|"):
                rows.append(_split_row(lines[j]))
                j += 1
            tables.append({"header": header, "rows": rows})
            i = j
        else:
            i += 1
    return tables


def _split_row(row: str) -> List[str]:
    cells = row.strip().strip("|").split("|")
    return [c.strip() for c in cells]


def _first_table(section_md: str) -> Optional[Dict]:
    tabs = _pipe_tables(section_md)
    return tabs[0] if tabs else None


def _rows_as_dicts(table: Optional[Dict]) -> List[Dict]:
    if not table or not table.get("rows"):
        return []
    hdr = [h.lower() for h in table["header"]]
    out = []
    for r in table["rows"]:
        out.append({hdr[k] if k < len(hdr) else f"col{k}": v for k, v in enumerate(r)})
    return out


# --------------------------------------------------------------------------- #
# Structured accessors — parse the invariant README schema
# --------------------------------------------------------------------------- #
def title(technique: str) -> str:
    md = spine(technique)
    for ln in md.splitlines():
        if ln.startswith("# "):
            return ln[2:].strip()
    return _canon(technique)


def scope(technique: str) -> str:
    """The '**Scope.**' paragraph (beamline gate prose). "" if the capsule keeps
    scope only in the halt table (e.g. dfxm)."""
    md = spine(technique)
    m = re.search(r"\*\*Scope\.\*\*(.+?)(?:\n\n|\n#{1,6}\s)", md, re.S)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()
    # Fallback: a '## … Scope …' heading section (e.g. calibrate-integrate
    # '## §0. Scope gate'). Return the leading prose before any table.
    sec = _section(md, "scope")
    if not sec:
        return ""
    lead = []
    for ln in sec.splitlines():
        if ln.strip().startswith("|"):
            break
        lead.append(ln)
    return re.sub(r"\s+", " ", "\n".join(lead)).strip()


def load_schedule(technique: str) -> List[Dict]:
    """The 'what to read when' table -> [{file, holds, when}], in doc order."""
    sec = _section(spine(technique), "read when") or _section(spine(technique), "doc set")
    rows = _rows_as_dicts(_first_table(sec))
    out = []
    for r in rows:
        vals = list(r.values())
        file_cell = vals[0] if vals else ""
        m = _MD_LINK_OR_CODE.search(file_cell)
        out.append({
            "file": m.group(1) if m else file_cell,
            "holds": vals[1] if len(vals) > 1 else "",
            "when": vals[2] if len(vals) > 2 else "",
        })
    return out


def halt_conditions(technique: str) -> List[Dict]:
    """The 'Halt on these named conditions' table -> [{condition, why}].

    This is the fail-closed gate: if any of these fire, the agent must stop and
    ask rather than proceed. Includes the beamline-scope rows."""
    md = spine(technique)
    sec = _section(md, "stop") or _section(md, "halt")
    rows = _rows_as_dicts(_first_table(sec))
    out = []
    for r in rows:
        vals = list(r.values())
        if len(vals) >= 2 and vals[0]:
            out.append({"condition": vals[0], "why": vals[1]})
    return out


def _numbered_items(section_md: str) -> List[Dict]:
    """Parse a top-level numbered list ('N. ...') -> [{n, text}] (full item text,
    multi-line items joined). Sub-list numbers indented under an item are ignored
    because the split only fires on markers at column 0."""
    if not section_md:
        return []
    out = []
    parts = re.split(r"(?m)^(\d+)\.\s+", section_md)  # [pre, n1, body1, n2, body2, ...]
    for k in range(1, len(parts) - 1, 2):
        try:
            n = int(parts[k])
        except ValueError:
            continue
        text = re.sub(r"\s+", " ", parts[k + 1]).strip()
        out.append({"n": n, "text": text})
    return out


def _fenced_phase_steps(section_md: str) -> List[Dict]:
    """Parse a fenced 'phase N  name  description' block (dct-tt, xrd-ct THE
    ORDER) -> [{num, step, where, note}]. Continuation lines (``---- … ----``)
    and branch labels ('DCT branch') don't start with 'phase' and are skipped."""
    if not section_md:
        return []
    out = []
    for ln in section_md.splitlines():
        m = re.match(r"^\s*phase\s+([\w.]+)\s+(\S+)\s+(.+)$", ln, re.I)
        if m:
            out.append({"num": m.group(1), "step": m.group(2),
                        "where": "", "note": m.group(3).strip()})
    return out


def _arrow_steps(section_md: str) -> List[Dict]:
    """Parse an arrow-joined prose order ('§0 scope gate → §1 install gate → …',
    calibrate-integrate) -> [{num, step, where, note}]. Handles a leading
    'Run the phases in this order:' preamble, inline markdown links on a step
    (→ where), and trailing prose after the final step."""
    if not section_md or "→" not in section_md:
        return []
    flat = re.sub(r"\s+", " ", section_md)
    segs = flat.split("→")
    out = []
    for idx, seg in enumerate(segs):
        s = seg.strip()
        if idx == 0 and ":" in s:          # drop "Run the phases in this order:"
            s = s.rsplit(":", 1)[-1].strip()
        where = ""
        mlink = re.match(r"\[([^\]]+)\]\(([^)]+)\)", s)   # [§4 calibrate](phase-4-calibrate.md)
        if mlink:
            where, s = mlink.group(2), mlink.group(1).strip()
        m = re.match(r"§?\s*([\w.]+)\s+(.+)", s)
        if not m:
            continue
        num, step = m.group(1), m.group(2)
        step = re.split(r"\.\s", step)[0].strip()   # trim trailing sentence on the last step
        out.append({"num": num, "step": step, "where": where, "note": ""})
    return out


def signatures(technique: str) -> List[Dict]:
    """Data-signature discriminators a capsule declares for auto-recognition,
    read from a '## Data signatures' table IF the capsule ships one -> [{signature,
    note}]. Empty today: no capsule ships this table yet. This is the future
    upstream schema — when a capsule adds it, it supersedes APEXA's curated
    ``_TECHNIQUE_SIGNATURES`` by precedence (the caller prefers this accessor).
    Fail-open []."""
    sec = _section(spine(technique), "data signatures")
    out = []
    for r in _rows_as_dicts(_first_table(sec)):
        vals = list(r.values())
        if vals and vals[0]:
            out.append({"signature": vals[0],
                        "note": vals[1] if len(vals) > 1 else ""})
    return out


def hard_rules(technique: str) -> List[Dict]:
    """The numbered 'Hard rules' list -> [{n, text}] (full item text)."""
    return _numbered_items(_section(spine(technique), "hard rules"))


def traps(technique: str) -> List[Dict]:
    """The 'Traps that silently corrupt results' table -> [{trap, symptom, where}]."""
    sec = _section(spine(technique), "traps")
    rows = _rows_as_dicts(_first_table(sec))
    out = []
    for r in rows:
        vals = list(r.values())
        if not vals or not vals[0]:
            continue
        out.append({
            "trap": vals[0],
            "symptom": vals[1] if len(vals) > 1 else "",
            "where": vals[2] if len(vals) > 2 else "",
        })
    return out


def order(technique: str) -> List[Dict]:
    """THE ORDER -> [{num, step, where, note}] (the prescribed phase sequence).

    Handled, in precedence, as a GFM table (ff/nf/pf), a fenced 'phase N …'
    block (dct-tt, xrd-ct), an arrow-joined prose line (calibrate-integrate),
    or a numbered list (dfxm) — capsules use any of these; all mean the same
    thing. The heading is 'THE ORDER' on most capsules and 'Order of
    operations' on calibrate-integrate; both are accepted."""
    sec = (_section(spine(technique), "the order")
           or _section(spine(technique), "order of operations"))
    if not sec:
        return []
    rows = _rows_as_dicts(_first_table(sec))
    if rows:
        out = []
        for r in rows:
            vals = list(r.values())
            # tables are (#, Step, Where, Notes) or (#, Step, Where, Why)
            if len(vals) >= 2:
                out.append({
                    "num": vals[0],
                    "step": vals[1],
                    "where": vals[2] if len(vals) > 2 else "",
                    "note": vals[3] if len(vals) > 3 else "",
                })
        return out
    fenced = _fenced_phase_steps(sec)   # dct-tt / xrd-ct fenced 'phase N …' block
    if fenced:
        return fenced
    arrows = _arrow_steps(sec)          # calibrate-integrate '§0 … → §1 …' prose
    if arrows:
        return arrows
    # Fallback: numbered list (dfxm formats THE ORDER as prose steps).
    return [{"num": str(it["n"]), "step": it["text"], "where": "", "note": ""}
            for it in _numbered_items(sec)]


def phases(technique: str) -> List[Dict]:
    """Ordered phase docs -> [{key, file, holds, when}].

    Order comes from the load schedule (intended reading order); enriched with the
    phase-*.md files actually on disk. 'key' is the short handle: 'phase-0', etc."""
    t = _canon(technique)
    if not t:
        return []
    on_disk = {}
    try:
        for p in sorted((CAPSULES_DIR / t).glob("phase-*.md")):
            m = re.match(r"(phase-\d+)", p.name)
            on_disk[p.name] = m.group(1) if m else p.stem
    except OSError:
        return []
    out, seen = [], set()
    for row in load_schedule(t):
        f = row["file"]
        if f in on_disk:
            out.append({"key": on_disk[f], "file": f, "holds": row["holds"], "when": row["when"]})
            seen.add(f)
    # append any phase files not referenced in the schedule, sorted
    for f in sorted(on_disk):
        if f not in seen:
            out.append({"key": on_disk[f], "file": f, "holds": "", "when": ""})
    return out


def phase_doc(technique: str, phase: str) -> str:
    """Body of a phase file, resolved loosely: 'phase-3', '3',
    'phase-3-geometry', or the exact filename all work. "" if not found."""
    t = _canon(technique)
    if not t or not phase:
        return ""
    want = str(phase).strip().lower()
    num = None
    m = re.search(r"(\d+)", want)
    if m:
        num = m.group(1)
    for ph in phases(t):
        fname = ph["file"].lower()
        key = ph["key"].lower()
        if want == fname or want == key or want == ph["file"].lower().removesuffix(".md"):
            return _read(t, ph["file"])
        if num is not None and key == f"phase-{num}":
            return _read(t, ph["file"])
    return ""


# --------------------------------------------------------------------------- #
# Tool -> technique mapping + beamline scope gate
# --------------------------------------------------------------------------- #
# Shared-phase capsules describe a phase (powder calibration / azimuthal
# integration) that also occurs *inside* a modality workflow (ff/nf/pf). When a
# tool name carries BOTH a modality token and a shared-phase token, the modality
# is the real owner and the shared-phase capsule is only a sub-step — drop it so
# the name resolves to the modality instead of going ambiguous. This is the ONE
# place a capsule id is named in this module: a deliberate, contained exception,
# gated on has_technique so it is inert until the capsule is vendored.
_SHARED_PHASE_TECHNIQUES = {"calibrate-integrate"}


def technique_for_tool(tool_name: str) -> str:
    """Which capsule a tool belongs to, derived from the tool name — NO per-tool
    table. Each capsule contributes its distinctive token (the first dir segment:
    ff-hedm->'ff', dfxm->'dfxm'); a tool whose name contains exactly one such
    token maps to that technique. Ambiguous ('overlay_ff_nf_results') or
    token-free ('match_grains') -> "" (never guess). New capsule -> new token,
    automatically.

    Disambiguation: when the hits are one modality capsule plus a shared-phase
    capsule (calibration/integration is a phase within ff/nf/pf), the modality
    wins — the shared-phase capsule is a sub-step, not the technique."""
    if not tool_name:
        return ""
    parts = set(re.split(r"[^a-z0-9]+", tool_name.lower()))
    hits = [t for t in available_techniques() if t.split("-")[0].lower() in parts]
    if len(hits) > 1:
        non_shared = [t for t in hits if t not in _SHARED_PHASE_TECHNIQUES]
        if len(non_shared) == 1:
            return non_shared[0]
    return hits[0] if len(hits) == 1 else ""


# MIDAS reconstruction-driver markers. A RAW shell command (run_command /
# run_remote_command) is treated as a MIDAS workflow only when one of these
# appears, so arbitrary filesystem paths never trigger a spurious technique
# inject. Driver-focused and deliberately small.
_MIDAS_CMD_MARKERS = ("midas", "_midas.py")


def technique_for_command(command: str) -> str:
    """Best-effort technique id for a RAW shell command that hand-drives MIDAS.

    The agent often falls back to ``run_command``/``run_remote_command`` to drive
    a MIDAS CLI by hand (e.g. when a typed workflow tool is blocked by a locality
    rule). On that raw path the tool NAME carries no technique, so the handbook
    methodology would never load — exactly when the operator most needs it. This
    recovers the technique from the command text so the capsule spine + traps
    still inject before the reconstruction proceeds.

    Generic (no per-command table): uses the same first-segment capsule tokens as
    ``technique_for_tool`` (ff/nf/pf/dfxm/tomo/dct/xrd/calibrate), plus explicit
    ``--scan-mode ff|nf|pf`` disambiguation. Returns "" unless the command clearly
    invokes MIDAS AND names exactly one technique (never guess)."""
    if not command:
        return ""
    low = command.lower()
    if not any(m in low for m in _MIDAS_CMD_MARKERS):
        return ""
    # Strip file extensions before tokenizing: an extension is not a technique.
    # Without this, `midas_ff_report.py --out report.pdf` yields BOTH 'ff' and
    # 'pdf', reads as ambiguous, and injects nothing — silently dropping the FF
    # methodology on exactly the commands that write a report. Only the dotted
    # suffix is removed, so '/data/scan_ff/param.txt' still resolves to ff-hedm.
    toks = set(re.split(r"[^a-z0-9]+", re.sub(r"\.[a-z0-9]{1,5}\b", " ", low)))

    # Explicit scan-mode wins (midas-pipeline run --scan-mode ff|pf|nf|auto).
    m = re.search(r"scan[-_ ]?mode[=\s]+([a-z]+)", low)
    if m:
        forced = [t for t in available_techniques()
                  if t.split("-")[0].lower() == m.group(1)]
        if len(forced) == 1:
            return forced[0]

    hits = [t for t in available_techniques() if t.split("-")[0].lower() in toks]
    if len(hits) > 1:
        non_shared = [t for t in hits if t not in _SHARED_PHASE_TECHNIQUES]
        if len(non_shared) == 1:
            return non_shared[0]
    return hits[0] if len(hits) == 1 else ""


# APS beamline identifiers as written in the scope/halt prose: 1-ID, 1-ID-E,
# 6-ID-C, 17-BM, 8-ID-E, 20-ID, 20-D-E, 25-ID, 11-ID … The station segment is
# ID or BM (optionally + a station letter), or a D that MUST carry one (20-D-E).
#
# The bare-D case requires its station letter to keep dimensionality prose out:
# "a 2-D voxel grid", "a 3-D map", "one 1-D pattern" all match \d-D otherwise,
# and a capsule then silently declares itself scoped to a nonexistent beamline.
# That was live: pf-hedm claimed {2-D, 3-D} and xrd-ct {2-D}, so scope_conflict
# blocked every pf-hedm tool at any configured APEXA_BEAMLINE.
_BEAMLINE_RE = re.compile(r"\b(\d{1,2}-(?:(?:ID|BM)(?:-[A-Za-z])?|D-[A-Za-z]))\b")


def scope_beamlines(technique: str) -> set:
    """Beamline IDs a capsule declares itself scoped to, read from the scope
    paragraph AND the halt-conditions table (dfxm keeps scope in the halt table).
    Empty set => the capsule names no specific beamline (do not assert a conflict)."""
    text = scope(technique) + "\n"
    for hc in halt_conditions(technique):
        text += f"{hc.get('condition', '')} {hc.get('why', '')}\n"
    return {m.group(1).upper() for m in _BEAMLINE_RE.finditer(text)}


def _bl_compatible(a: str, b: str) -> bool:
    """Segment-prefix compatible: '1-ID-E' ⊇ '1-ID' (a branch of the same line),
    but '6-ID-C' vs '1-ID-E' differ at segment 0. Prefix either direction."""
    sa = [s for s in a.upper().split("-") if s]
    sb = [s for s in b.upper().split("-") if s]
    n = min(len(sa), len(sb))
    return n > 0 and sa[:n] == sb[:n]


def scope_conflict(technique: str, beamline: str) -> tuple:
    """(blocked, reason). Deterministic teeth for the scope gate: fires only when
    the capsule names specific beamlines AND the configured one is compatible with
    none of them. Unconfigured beamline or a capsule that names none -> no block
    (fail-open: we cannot assert a conflict we cannot see)."""
    bl = (beamline or "").strip()
    if not bl:
        return (False, "")
    caps = scope_beamlines(technique)
    if not caps:
        return (False, "")
    if any(_bl_compatible(bl, c) for c in caps):
        return (False, "")
    return (True, (
        f"'{technique}' recipes are scoped to {', '.join(sorted(caps))}; the "
        f"configured beamline (APEXA_BEAMLINE={bl}) is not among them. The capsule's "
        f"geometry/conventions do not transfer across beamlines — stop and confirm "
        f"before running, or set APEXA_IGNORE_SCOPE_GATE=1 to override."
    ))


def halt_checklist(technique: str) -> str:
    """The halt conditions as a compact numbered checklist for injection."""
    hcs = halt_conditions(technique)
    if not hcs:
        return ""
    return "\n".join(
        f"  {i}. {h['condition']} — {h['why']}" for i, h in enumerate(hcs, 1)
    )


# --------------------------------------------------------------------------- #
# Manifest + provenance (for list_techniques / learn_technique meta-tools)
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def pin_info() -> Dict:
    """Provenance of the vendored snapshot (MIDAS commit, sync time). {} if absent."""
    try:
        return json.loads(PIN_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def manifest() -> List[Dict]:
    """One summary row per technique, for a `list_techniques` surface."""
    out = []
    for t in available_techniques():
        out.append({
            "technique": t,
            "title": title(t),
            "scope": scope(t),
            "n_phases": len(phases(t)),
            "n_hard_rules": len(hard_rules(t)),
            "n_halt_conditions": len(halt_conditions(t)),
            "has_parameters": bool(parameters_doc(t)),
            "has_diagnosis": bool(diagnosis_doc(t).strip()),
        })
    return out


# --------------------------------------------------------------------------- #
# Context assembly (for injection into an agent's working context)
# --------------------------------------------------------------------------- #
_SPINE_HEADER = (
    "=== TECHNIQUE CAPSULE (MIDAS handbook, source of truth) ===\n"
    "This is the {tech} workflow spine — vendored from MIDAS {commit}. Follow it in "
    "order. It is the authority for scope, the step order, hard rules, halt "
    "conditions, and traps. Open a phase doc (via open_phase) only when you reach "
    "that step; re-verify any version/host claim LIVE (the RUNBOOK is volatile).\n"
)


def spine_context(technique: str) -> str:
    """The README spine wrapped with a short header, for injecting into context.

    Returns "" for an unknown technique so the caller can fall back cleanly."""
    md = spine(technique)
    if not md:
        return ""
    commit = (pin_info().get("midas_commit") or "")[:12]
    hdr = _SPINE_HEADER.format(tech=_canon(technique), commit=f"@{commit}" if commit else "")
    return "\n\n" + hdr + "\n" + md + "\n"
