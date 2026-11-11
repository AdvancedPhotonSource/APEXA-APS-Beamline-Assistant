"""Generic, handbook/notebook-sourced guardrails for MIDAS FF/NF parameter files.

This module replaces the old hand-typed per-failure ``if``-ladder in
``midas_comprehensive_server._lint_handbook_traps`` with a *generic* engine whose
facts come from the MIDAS **FF/NF Handbook** (the agreed source of truth) instead
of literals baked into Python.

Design (approved plan — "generic guardrails, not last-case hardcoded rules"):

  A1  ``load_param_facts(pipeline)`` — parse ``manuals/FF_Parameters_Reference.md``
      (every GFM pipe table, header-mapped) into per-parameter *facts*
      (units / default / arity / aliases / section). The clean "Defaults summary"
      table is authoritative and overlays the noisier per-section Default cells.

  A2  A *small closed set* of generic constraint primitives — ``um_unit_floor``
      (px-for-µm), ``positive_span`` (collapsed box), ``degenerate_bound_pair``
      (dead ω-window). Each takes facts + parsed values and yields a trap.

  A3  A curated, **cited** relational/outcome spec (data + tiny predicates) for the
      rules that are not a single table cell — dark ``exchange/data``, calibrant
      lattice-on-sample, template RingThresh, SkipFrame GE-vs-NF, MinPeakSNR,
      Rsample/Hbeam search-bound, and the always-on OmegaStart/Lsd reminders.
      Notebook *prose* carries RETRACTED landmines, so it is NOT auto-parsed —
      these rules are curated but each carries an explicit ``section`` citation.

  A4  ``evaluate_param_guardrails(...)`` — the engine. Returns the exact
      ``{severity, key, message, section}`` dict shape the callers already expect,
      so ``_lint_handbook_traps`` becomes a thin wrapper (A5) with no behaviour
      change for the FF gate or the two renderers.

  B   ``verify_ff_outputs(result_folder)`` — a post-run outcome verifier that reads
      the on-disk artifacts and flags the notebook's documented silent-failure
      chains (empty InputAll, 0 seeds, 4-byte IndexBest, ...). Deterministic,
      fail-open, no MIDAS invocation.

Pure-Python, importable by the server and by tests, no MCP spin-up — same pattern
as ``skill_registry.py``. Fail-open everywhere: a parse/IO error yields ``[]``
(guardrails) or an ``error``-status dict (verifier), never an exception that could
break a run.
"""
from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

# Trap dict shape: {"severity": "error"|"warning"|"info", "key", "message", "section"}

_MU = "µ"          # MICRO SIGN (as used in the handbook)
_MU_GREEK = "μ"    # GREEK SMALL LETTER MU (occasional alternative)


# ─────────────────────────────────────────────────────────────────────────────
# MIDAS checkout resolution (manuals + Example params are the fact sources)
# ─────────────────────────────────────────────────────────────────────────────

def _midas_root() -> Path | None:
    """Locate the MIDAS checkout the same way the server does (MIDAS_PATH env,
    then a couple of conventional locations). Returns None if none exists."""
    cand = []
    env = os.environ.get("MIDAS_PATH") or os.environ.get("MIDAS_ROOT")
    if env:
        cand.append(Path(env).expanduser())
    cand += [Path.home() / "Git" / "MIDAS", Path.home() / "MIDAS", Path("/opt/MIDAS")]
    for c in cand:
        try:
            if c and c.is_dir():
                return c
        except Exception:
            continue
    return None


def _manuals_dir() -> Path | None:
    root = _midas_root()
    if not root:
        return None
    d = root / "manuals"
    return d if d.is_dir() else None


def _reference_md() -> Path | None:
    d = _manuals_dir()
    if not d:
        return None
    p = d / "FF_Parameters_Reference.md"
    return p if p.is_file() else None


def _example_params() -> Path | None:
    root = _midas_root()
    if not root:
        return None
    p = root / "FF_HEDM" / "Example" / "Parameters.txt"
    return p if p.is_file() else None


def _capsule_param_text(pipeline: str) -> str:
    """The PARAMETERS doc text from the vendored technique capsule for this
    pipeline (ff/nf/pf/dfxm) — offline-safe and generic across techniques. "" if
    no capsule / no PARAMETERS doc. This is what lets the SAME table parser and
    trap engine enforce ANY technique's schema without a live MIDAS checkout: a
    new capsule with a PARAMETERS doc is picked up automatically."""
    try:
        import capsule_registry as _cr
    except Exception:
        return ""
    try:
        tech = _cr._canon(pipeline or "") or _cr._canon((pipeline or "") + "-hedm")
        return _cr.parameters_doc(tech) if tech else ""
    except Exception:
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# Parameter-file parsing (mirror of the server's _parse_param_multi, standalone)
# ─────────────────────────────────────────────────────────────────────────────

def parse_param_file(param_path) -> dict:
    """Parse a MIDAS parameter file into ``{lowercased_key: [token-lists]}``.

    Keys can repeat (RingThresh, Lsd, BoxSize, ...) so every occurrence is kept.
    ``_raw_keys`` maps the lowercased key → its original-case first spelling.
    Comments (``#`` to end of line) are stripped. Fail-open to an empty parse.
    """
    out: dict = {}
    raw_keys: dict = {}
    try:
        text = Path(param_path).expanduser().read_text(errors="ignore")
    except Exception:
        return {"_raw_keys": {}}
    for line in text.splitlines():
        s = line.split("#", 1)[0].strip()
        if not s:
            continue
        parts = s.split()
        k = parts[0].lower()
        out.setdefault(k, []).append(parts[1:])
        raw_keys.setdefault(k, parts[0])
    out["_raw_keys"] = raw_keys
    return out


# ─────────────────────────────────────────────────────────────────────────────
# A1. Handbook facts loader
# ─────────────────────────────────────────────────────────────────────────────

def _norm_units(cell: str) -> str:
    """Normalize a Units cell to a small category vocabulary."""
    u = (cell or "").strip().strip("`").lower().replace(_MU_GREEK, _MU)
    if not u or u == "—":
        return ""
    if f"{_MU}m" in u or re.search(r"\bum\b", u):
        return "µm"
    if "deg" in u:
        return "deg"
    if "pixel" in u or u == "px":
        return "pixels"
    if "Å" in cell or "å" in (cell or "").lower():  # Å
        return "Å"
    return u


def _arity_from_type(cell: str) -> int:
    """Best-effort arity from a Type cell (``4×double``→4, ``int int``→2, else 1)."""
    t = (cell or "").strip().strip("`").lower()
    if not t:
        return 1
    # N×type  /  Nxtype  (× = U+00D7)
    m = re.match(r"\s*(\d+)\s*[×x]\s*", t)
    if m:
        return int(m.group(1))
    # "up to N×..." → variable; treat as 1 for scalar checks
    if t.startswith("up to"):
        return 1
    # space-separated homogeneous/heterogeneous type words (int int, double int)
    typ_words = {"int", "int64", "double", "float", "str", "bool"}
    words = [w for w in re.split(r"[\s,]+", t) if w]
    n = sum(1 for w in words if w in typ_words)
    return n if n >= 2 else 1


def _default_from_cell(cell: str):
    """Parse a Default cell → float | str | None (``—``/blank → None)."""
    c = (cell or "").strip().strip("`").strip()
    if not c or c in ("—", "-"):
        return None
    # e.g. "0 (→8192 if ...)" or "0 0" → take a leading numeric token if present
    m = re.match(r"[-+]?\d*\.?\d+", c)
    if m:
        try:
            v = float(m.group(0))
            return int(v) if v.is_integer() and "." not in m.group(0) else v
        except Exception:
            return c
    return c


def _aliases_from_notes(notes: str) -> list:
    return [a.strip("`") for a in re.findall(r"Alias(?:es)?:\s*`?(\w+)`?", notes or "")]


def _iter_pipe_tables(text: str):
    """Yield (section_label, header_cells, [row_cells...]) for every GFM pipe
    table, tracking the nearest ``## N. Title`` heading as the section label."""
    lines = text.splitlines()
    section = ""
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        hm = re.match(r"^##+\s+(\d+[a-z]?)\.", line.strip())
        if hm:
            section = f"FF §{hm.group(1)}"
        # A table starts at a header row followed by a separator row of ---/:.
        if line.lstrip().startswith("|") and i + 1 < n and re.match(
                r"^\s*\|?[\s:|-]+\|?\s*$", lines[i + 1]) and "-" in lines[i + 1]:
            header = [c.strip() for c in line.strip().strip("|").split("|")]
            rows = []
            j = i + 2
            while j < n and lines[j].lstrip().startswith("|"):
                cells = [c.strip() for c in lines[j].strip().strip("|").split("|")]
                rows.append(cells)
                j += 1
            yield section, header, rows
            i = j
            continue
        i += 1


def _keys_in_cell(cell: str) -> list:
    """Extract simple backticked keys from a Key cell. Ranges (``p0``–``p14``) and
    comma lists are skipped — they are never guardrail targets here."""
    toks = re.findall(r"`([^`]+)`", cell or "")
    toks = [t for t in toks if re.fullmatch(r"\w+", t)]
    if "–" in (cell or "") or "-" in (cell or "").replace("–", "") and len(toks) > 1:
        # a range spelled with an en-dash/hyphen between two backticked keys
        if len(toks) > 1:
            return []
    return toks


def _parse_reference_tables(text: str) -> dict:
    """Parse FF_Parameters_Reference.md → {lkey: Fact}. Fact is a dict with
    units / default / arity / required / repeatable / aliases / section."""
    facts: dict = {}
    for section, header, rows in _iter_pipe_tables(text):
        hlow = [h.strip().strip("`").lower() for h in header]

        def col(*names):
            for nm in names:
                if nm in hlow:
                    return hlow.index(nm)
            return None

        ci_key = col("key", "flag", "coeff", "text-file key")
        if ci_key is None:
            continue
        ci_type = col("type")
        ci_units = col("units")
        ci_def = col("default")
        ci_req = col("required")
        ci_notes = col("notes", "pairs with")
        for cells in rows:
            if len(cells) <= ci_key:
                continue
            keys = _keys_in_cell(cells[ci_key])
            if not keys:
                continue
            typ = cells[ci_type] if ci_type is not None and ci_type < len(cells) else ""
            units = cells[ci_units] if ci_units is not None and ci_units < len(cells) else ""
            defc = cells[ci_def] if ci_def is not None and ci_def < len(cells) else ""
            reqc = cells[ci_req] if ci_req is not None and ci_req < len(cells) else ""
            notes = cells[ci_notes] if ci_notes is not None and ci_notes < len(cells) else ""
            repeatable = ("multi" in (reqc + " " + defc + " " + typ).lower()
                          or "up to" in typ.lower())
            fact = {
                "units": _norm_units(units),
                "default": _default_from_cell(defc),
                "arity": _arity_from_type(typ),
                "required": "yes" in reqc.lower(),
                "repeatable": repeatable,
                "aliases": _aliases_from_notes(notes),
                "section": section,
            }
            for k in keys:
                lk = k.lower()
                # First definition wins (per-section tables precede the summary).
                facts.setdefault(lk, fact)
    return facts


def _overlay_defaults_summary(text: str, facts: dict) -> None:
    """The clean ``| Key | Default |`` summary is verified against
    ``midas_config_defaults()`` and is authoritative for defaults."""
    for _section, header, rows in _iter_pipe_tables(text):
        hlow = [h.strip().lower() for h in header]
        if hlow[:2] != ["key", "default"]:
            continue
        for cells in rows:
            if len(cells) < 2:
                continue
            ks = _keys_in_cell(cells[0])
            if not ks:
                continue
            dv = _default_from_cell(cells[1])
            for k in ks:
                lk = k.lower()
                if lk in facts:
                    facts[lk] = {**facts[lk], "default": dv}
                else:
                    facts[lk] = {"units": "", "default": dv, "arity": 1,
                                 "required": False, "repeatable": False,
                                 "aliases": [], "section": "FF §defaults"}


# Small embedded snapshot of the load-bearing facts — used ONLY when the MIDAS
# checkout (and thus the handbook) is absent, so the engine still enforces the
# unambiguous unit/degenerate traps offline. Mirrors FF_Parameters_Reference.md.
_FALLBACK_FACTS: dict = {
    "width":                {"units": "µm", "default": 1500, "arity": 1, "section": "FF §8"},
    "marginradius":         {"units": "µm", "default": None, "arity": 1, "section": "FF §9"},
    "marginradial":         {"units": "µm", "default": None, "arity": 1, "section": "FF §9"},
    "margineta":            {"units": "deg", "default": None, "arity": 1, "section": "FF §9"},
    "boxsize":              {"units": "µm", "default": None, "arity": 4, "repeatable": True, "section": "FF §5"},
    "minomespotidstoindex": {"units": "deg", "default": None, "arity": 1, "section": "FF §5"},
    "maxomespotidstoindex": {"units": "deg", "default": None, "arity": 1, "section": "FF §5"},
    "darkdataset":          {"units": "", "default": "exchange/dark", "arity": 1, "aliases": ["darkLoc"], "section": "FF §2"},
    "datadataset":          {"units": "", "default": "exchange/data", "arity": 1, "aliases": ["dataLoc"], "section": "FF §2"},
    "rsample":              {"units": "µm", "default": 0, "arity": 1, "section": "FF §10"},
    "hbeam":                {"units": "µm", "default": 0, "arity": 1, "section": "FF §10"},
    "stepsizepos":          {"units": "µm", "default": None, "arity": 1, "section": "FF §9"},
}


def _normalized_facts(raw: dict) -> dict:
    """Fill missing fact keys with safe defaults so callers can index freely."""
    out = {}
    for k, f in raw.items():
        out[k] = {
            "units": f.get("units", ""),
            "default": f.get("default", None),
            "arity": f.get("arity", 1),
            "required": f.get("required", False),
            "repeatable": f.get("repeatable", False),
            "aliases": f.get("aliases", []),
            "section": f.get("section", ""),
        }
    return out


@lru_cache(maxsize=8)
def load_param_facts(pipeline: str = "ff") -> dict:
    """Return ``{lowercased_key: Fact}`` for a technique's parameter schema, with
    the verified Defaults-summary overlaid. Fail-open to a small embedded snapshot.

    Sources, in precedence order (first definition of a key wins):
      1. FF only: the authoritative top-level ``manuals/FF_Parameters_Reference.md``
         from a live MIDAS checkout — richest schema (Type/Units/Default/Required).
      2. Any technique: the vendored capsule PARAMETERS doc
         (``knowledge_base/capsules/<technique>/PARAMETERS.md`` etc.) — offline-safe
         and generic, so nf/pf/dfxm (and future capsules) are enforced from the
         same parser without a MIDAS checkout.
      3. Embedded snapshot, when nothing else parsed."""
    pipe = (pipeline or "ff").lower()
    facts: dict = {}

    def _merge(text: str) -> None:
        f = _parse_reference_tables(text)
        _overlay_defaults_summary(text, f)
        for k, v in f.items():
            facts.setdefault(k, v)   # earlier (richer) source wins

    # 1. FF authoritative reference (live checkout) — richest columns.
    if pipe == "ff":
        ref = _reference_md()
        if ref:
            try:
                _merge(ref.read_text(encoding="utf-8", errors="ignore"))
            except Exception:
                pass

    # 2. Vendored capsule PARAMETERS doc for this technique (offline-safe, generic).
    cap = _capsule_param_text(pipe)
    if cap:
        try:
            _merge(cap)
        except Exception:
            pass

    if facts:
        return _normalized_facts(facts)
    return _normalized_facts(_FALLBACK_FACTS)   # 3. offline FF fallback


@lru_cache(maxsize=2)
def load_recommended_values() -> dict:
    """Parse FF_HEDM/Example/Parameters.txt → ``{lkey: float}`` of the recommended
    *working* magnitudes (Width 1500, MarginRadius/Radial/Eta 500, StepSizePos
    100, Rsample/Hbeam 2000, ...). These are the handbook-blessed "typical" values
    used to source the µm-floor magnitude WITHOUT hardcoding numbers in code.
    Cited: FF_HEDM/Example/Parameters.txt + FF_HEDM_Lab_Notebook.md (§µm-vs-px)."""
    fallback = {
        "width": 1500.0, "marginradius": 500.0, "marginradial": 500.0,
        "margineta": 500.0, "stepsizepos": 100.0, "rsample": 2000.0,
        "hbeam": 2000.0,
    }
    p = _example_params()
    if not p:
        return dict(fallback)
    try:
        vals = {}
        for line in p.read_text(errors="ignore").splitlines():
            s = line.split("#", 1)[0].strip()
            if not s:
                continue
            parts = s.split()
            if len(parts) < 2:
                continue
            try:
                vals[parts[0].lower()] = float(parts[1])
            except Exception:
                continue
        # keep the fallback as a floor so load-bearing keys always resolve
        for k, v in fallback.items():
            vals.setdefault(k, v)
        return vals
    except Exception:
        return dict(fallback)


# ─────────────────────────────────────────────────────────────────────────────
# A2. Generic constraint primitives
# ─────────────────────────────────────────────────────────────────────────────

def _first_float(params: dict, key: str):
    try:
        return float(params[key][0][0])
    except Exception:
        return None


def um_unit_floor(key, label, fact, value, magnitude):
    """px-for-µm detector: a length parameter carried in µm entered as a pixel
    count. Fires only when (a) the fact's units are µm, (b) a real "typical"
    magnitude is known and is a genuinely-large length (≥100 µm), and (c) the
    value is implausibly small (``0 < v < max(50, 0.05·magnitude)``) — i.e. it
    looks like the pixel equivalent (~magnitude/pixel_size). Blocks (``error``):
    Width 7.5 → a 0.0375 px ring band rejects nearly every peak → empty
    InputAll.csv → aborted run. Magnitude comes from the handbook default OR the
    Example recommended value, never a literal here."""
    if not fact or fact.get("units") != "µm" or fact.get("arity", 1) != 1:
        return None
    if magnitude is None or magnitude < 100:
        return None
    if value is None or not (0 < value < max(50.0, 0.05 * magnitude)):
        return None
    px = value / 200.0  # ~200 µm/px is the common APS far-field pixel scale
    return {
        "severity": "error", "key": label,
        "message": (
            f"{label}={value:g} is far below its µm working value (~{magnitude:g}). "
            f"{label} is in MICRONS, not pixels — at ~200 µm/px that is only "
            f"~{px:.3g} px, a band so narrow it rejects nearly all peaks (empty "
            f"InputAll.csv, aborted run). You likely entered pixels; use ~{magnitude:g} µm."),
        "section": "FF Parameters Reference (µm units)",
    }


def positive_span(key, label, fact, toks_list):
    """Arity-4 µm box (BoxSize = Ymin Ymax Zmin Zmax): each (min,max) pair must
    have max > min. A collapsed span makes the fit_setup keep-box reject EVERY
    spot → all-zero InputAll.csv → the indexer dies on an empty Data.bin two
    stages later. Absent BoxSize is safe (the box filter is skipped) and not
    flagged. Blocks (``error``)."""
    if not fact or fact.get("arity", 1) != 4:
        return None
    for toks in toks_list:
        try:
            bx = [float(t) for t in toks[:4]]
        except Exception:
            continue
        if len(bx) < 4:
            continue
        y_span, z_span = bx[1] - bx[0], bx[3] - bx[2]
        if y_span <= 0 or z_span <= 0:
            axis = "Z" if z_span <= 0 else "Y"
            return {
                "severity": "error", "key": label,
                "message": (
                    f"{label} {bx[0]:g} {bx[1]:g} {bx[2]:g} {bx[3]:g} has a collapsed "
                    f"{axis}-span (min ≥ max). {label} is Ymin Ymax Zmin Zmax in µm; a "
                    "zero/negative span makes the spot keep-box reject EVERY peak → "
                    "all-zero InputAll.csv → the indexer dies on an empty Data.bin "
                    "('mmap Data.bin failed') two stages later. Use a permissive box, "
                    "e.g. BoxSize -1000000 1000000 -1000000 1000000."),
                "section": "FF Parameters Reference (µm units)",
            }
    return None


def degenerate_bound_pair(lo_key, hi_key, lo_label, hi_label, params, section):
    """A declared (min,max) bound pair. Both absent OR both present with min≥max
    → degenerate window that keeps no spots (blocks). Exactly one present → the
    other defaults to 0.0, an asymmetric window (warning). Used for the seed
    ω-window (Min/MaxOmeSpotIDsToIndex): a dead window → empty SpotsToIndex.csv →
    0 indexing seeds → 0 grains (a 4-byte IndexBest_all.bin), ring-independent."""
    has_lo, has_hi = lo_key in params, hi_key in params
    lo, hi = _first_float(params, lo_key), _first_float(params, hi_key)
    if not has_lo and not has_hi:
        return {
            "severity": "error", "key": lo_label,
            "message": (
                f"{lo_label}/{hi_label} are not set — both default to 0.0, a "
                "degenerate single-point ω seed-window at 0° that keeps no spots "
                "(empty SpotsToIndex.csv → 0 indexing seeds → 0 grains, independent "
                f"of RingToIndex). Set them to the full ω sweep, e.g. {lo_label} "
                f"-180 / {hi_label} 180."),
            "section": section,
        }
    if lo is not None and hi is not None and lo >= hi:
        return {
            "severity": "error", "key": lo_label,
            "message": (
                f"Seed ω-window is degenerate: {lo_label}={lo:g} ≥ {hi_label}={hi:g}. "
                "A zero/negative-width window keeps no spots (empty SpotsToIndex.csv "
                "→ 0 seeds → 0 grains). Open it to the ω sweep, e.g. -180 / 180."),
            "section": section,
        }
    if has_lo != has_hi:
        present = lo_label if has_lo else hi_label
        missing = hi_label if has_lo else lo_label
        return {
            "severity": "warning", "key": lo_label,
            "message": (
                f"{present} is set but {missing} is not — {missing} defaults to 0.0, "
                "giving an asymmetric seed ω-window that likely drops most spots. Set "
                "both bounds explicitly (e.g. -180 / 180)."),
            "section": section,
        }
    return None


# ─────────────────────────────────────────────────────────────────────────────
# A3. Curated relational/outcome spec (data + tiny cited predicates)
# ─────────────────────────────────────────────────────────────────────────────

# Calibrant cell db for the "calibrant LatticeConstant left on a sample" rule.
# Kept in sync with midas_comprehensive_server._CALIBRANT_DB (name → (SG, a[Å])).
_CALIBRANT_DB = {
    "CeO2": (225, 5.411651), "LaB6": (221, 4.156890), "Si": (227, 5.431020),
    "Ni": (225, 3.523870), "Al": (225, 4.049500), "Au": (225, 4.078250),
    "Cu": (225, 3.615000), "W": (229, 3.165000),
}

# Which single-value µm length params get the px-for-µm floor. Sourced from
# FF_HEDM_Lab_Notebook.md §"µm vs px" (Width + the Margin* band-width family are
# the documented offenders) — NOT an arbitrary code list. Each resolves its
# magnitude from the handbook default or the Example recommended value.
_UM_FLOOR_KEYS = {
    "width": "Width",
    "marginradius": "MarginRadius",
    "marginradial": "MarginRadial",
    "margineta": "MarginEta",
}

# Declared (min,max) bound pairs fed to degenerate_bound_pair.
_BOUND_PAIRS = [
    ("minomespotidstoindex", "maxomespotidstoindex",
     "MinOmeSpotIDsToIndex", "MaxOmeSpotIDsToIndex",
     "FF Parameters Reference (seed ω-window)"),
]


def _curated_relational(params: dict, pipe: str, source_h5: str) -> list:
    """The relational/contextual rules that are not a single table cell. One
    place to add a rule; each carries an explicit handbook ``section`` citation."""
    traps: list = []

    def add(sev, key, msg, section):
        traps.append({"severity": sev, "key": key, "message": msg, "section": section})

    have = lambda k: k in params  # noqa: E731

    if pipe in ("ff", "pf"):
        # §3e — GE/far-field frames carry a leading throwaway frame; SkipFrame 1.
        if not have("skipframe"):
            # GE-specific: only GE panels write a leading throwaway frame. On other
            # detectors an unset SkipFrame is correct, and a warning here made agents
            # treat a non-GE scan as unresolved (FF pilot, Nov 2026).
            add("info", "SkipFrame",
                "SkipFrame is not set. If the detector is a GE panel, its files carry a "
                "leading throwaway frame: set 'SkipFrame 1' (FF §3e), otherwise every "
                "omega shifts by one step. Other detectors, or files whose frame count "
                "matches the stated omega range, need no skip.", "FF §3e")
        elif str(params["skipframe"][0][0] if params["skipframe"][0] else "") != "1":
            add("info", "SkipFrame",
                f"SkipFrame={params['skipframe'][0]} (expected 1 for GE/far-field). "
                "Confirm this matches the detector's frame layout (FF §3e).", "FF §3e")

        # §3d — a separate Dark .h5 stores its frame at exchange/data, so BOTH
        # darkLoc and darkDataset must point at exchange/data (not exchange/dark).
        if have("dark") or have("darkfile"):
            dl = (params.get("darkloc", [[""]])[0] or [""])[0].lower()
            dd = (params.get("darkdataset", [[""]])[0] or [""])[0].lower()
            bad = []
            if have("darkloc") and dl and dl != "exchange/data":
                bad.append(f"darkLoc={dl}")
            if have("darkdataset") and dd and dd != "exchange/data":
                bad.append(f"darkDataset={dd}")
            if not have("darkloc") and not have("darkdataset"):
                add("warning", "darkLoc",
                    "A separate Dark file is set but darkLoc/darkDataset are not. A "
                    "standalone dark .h5 stores its frame at 'exchange/data' — set "
                    "BOTH darkLoc and darkDataset to exchange/data (FF §3d). The "
                    "integrator default 'exchange/dark' reads nothing → an all-zero "
                    "dark → threshold-invariant 0-peak output.", "FF §3d")
            elif bad:
                add("warning", "darkLoc",
                    "Separate Dark file with " + ", ".join(bad) + ". A standalone "
                    "dark .h5 stores its frame at 'exchange/data' — set BOTH darkLoc "
                    "and darkDataset to exchange/data (FF §3d).", "FF §3d")

        # midas-pipeline >= 0.22 locates raw files as RawFolder/FileStem_<Padding digits>Ext
        # and refuses to start without Padding, or with an Ext lacking its dot.
        if have("filestem"):
            if not have("padding"):
                add("warning", "Padding",
                    "FileStem is set but Padding is not. midas-pipeline 0.22+ builds the "
                    "raw file name as FileStem_<number zero-padded to Padding digits><Ext> "
                    "and refuses to start without Padding (e.g. 'Padding 6' for _000001).",
                    "midas-pipeline preflight")
            ext = (params.get("ext", [[""]])[0] or [""])[0]
            if have("ext") and ext and not ext.startswith("."):
                add("warning", "Ext",
                    f"Ext={ext} has no leading dot. midas-pipeline appends Ext directly, so "
                    f"the file is looked up as ...{ext} without a dot; write 'Ext .{ext}'.",
                    "midas-pipeline preflight")

        # §6c — MinPeakSNR is the spot-quality gate; FitRMSE/NImgs are not proxies.
        if not have("minpeaksnr"):
            add("info", "MinPeakSNR",
                "MinPeakSNR is not set. It is the peak/spot quality gate (FF §6c) — "
                "set it explicitly rather than relying on FitRMSE/NImgs proxies.",
                "FF §6c")

        # §6 — Rsample/Hbeam are the grain-SEARCH BOUND, never the sample size.
        for k, label in (("rsample", "Rsample"), ("hbeam", "Hbeam")):
            v = _first_float(params, k)
            if v is not None and 0 < v < 100:
                add("warning", label,
                    f"{label}={v} looks small. {label} is the grain-search bound "
                    "(half-width in µm the indexer searches), NOT the physical "
                    "sample size (FF §6). Setting it to the sample size drops grains "
                    "at the edges. Use a bound comfortably larger than the beam-lit "
                    "volume.", "FF §6")

        # §6 — a calibrant LatticeConstant left on a sample run.
        lc = params.get("latticeconstant") or params.get("latticeparameter")
        if lc:
            try:
                a0 = float(lc[0][0])
                for nm, (sg, a_cal) in _CALIBRANT_DB.items():
                    if abs(a0 - a_cal) < 0.01:
                        add("warning", "LatticeConstant",
                            f"LatticeConstant a={a0} ≈ {nm} ({a_cal} Å). If this is a "
                            "SAMPLE run, you are indexing with the calibrant's cell "
                            "(FF §6). Set the sample's LatticeConstant + SpaceGroup.",
                            "FF §6")
                        break
            except Exception:
                pass

        # §6b — template-looking RingThresh (all rings share one identical value).
        rt = params.get("ringthresh", [])
        if rt:
            vals = [toks[1] for toks in rt if len(toks) >= 2]
            if len(vals) >= 2 and len(set(vals)) == 1:
                add("warning", "RingThresh",
                    f"All {len(vals)} RingThresh lines share the same value "
                    f"({vals[0]}) — that is a template, not data-derived. Real "
                    "per-ring thresholds differ. Run calibrate_ring_thresholds on the "
                    ".MIDAS.zip and paste its RingThresh block (FF §6b); a wrong value "
                    "gives 0 peaks and an empty grain list.", "FF §6b")

        # §2 — omega sign / aero convention: unconfirmable without the _FF.par.
        add("info", "OmegaStart",
            "Verify the omega sign against the beamline's rotation convention "
            "(FF §2). On aero-convention data, OmegaStart AND OmegaStep must be "
            "negated; a sign flip mirrors the microstructure and is undetectable "
            "from the grain list alone." + (
                "" if source_h5 else " (No _FF.par provided to auto-check.)"), "FF §2")

        # §4b — Lsd is calibrated, not a stage readback; DetZ ≠ Lsd.
        add("info", "Lsd",
            "Lsd must come from the calibrant fit, not a DetZ stage readback "
            "(FF §4b). Confirm this Lsd is the calibrated sample-to-detector "
            "distance.", "FF §4b")

    if pipe == "pf":
        # PF phase-2-configure §RingNumbers — PF indexing/refinement needs an explicit
        # RingNumbers list (which rings to index/refine on). MIDAS's own `midas-params
        # validate` does not flag a missing RingNumbers, so a PF run started without it
        # indexes on the wrong/default rings → a wrong or empty grain list.
        if not have("ringnumbers"):
            add("error", "RingNumbers",
                "RingNumbers is not set. PF HEDM must be told which rings to "
                "index/refine on (pf-hedm phase-2-configure §RingNumbers). Without it "
                "the run indexes on default/wrong rings and produces a wrong or empty "
                "grain list. Set RingNumbers to the bright, on-detector rings (prefer "
                "those best for strain).", "PF phase-2 §RingNumbers")

    if pipe == "nf":
        # §3g — the GE SkipFrame-1 rule must NOT cross to NF.
        if have("skipframe") and str(
                params["skipframe"][0][0] if params["skipframe"][0] else "") == "1":
            add("warning", "SkipFrame",
                "SkipFrame 1 is set on an NF parameter file. The GE far-field "
                "SkipFrame rule does NOT apply to NF — NF's extra frame is a trailing "
                "omega-wrap frame, not a leading throwaway (NF §3g). Remove SkipFrame "
                "here or you will drop a real omega frame.", "NF §3g")

    return traps


# ─────────────────────────────────────────────────────────────────────────────
# A4. Engine
# ─────────────────────────────────────────────────────────────────────────────

def evaluate_param_guardrails(param_path, pipeline: str = "ff",
                              source_h5: str = "") -> list:
    """Evaluate a MIDAS parameter file against the handbook-sourced guardrails.

    Returns a list of ``{severity, key, message, section}`` dicts — the exact
    shape the FF gate and the two renderers already consume. Generic primitives
    (fed by parsed handbook facts) run first for the unit/degenerate class; the
    curated cited relational rules run after. Fail-open to ``[]``.
    """
    try:
        pipe = (pipeline or "ff").lower()
        params = parse_param_file(param_path)
        facts = load_param_facts(pipe)
        recommended = load_recommended_values()
        traps: list = []

        if pipe in ("ff", "pf"):
            # (1) px-for-µm floor on the documented µm band-width family. Magnitude
            #     = handbook default if it is a real large length, else the Example
            #     recommended value — no numeric literals in this code path.
            for lkey, label in _UM_FLOOR_KEYS.items():
                fact = facts.get(lkey)
                if not fact:
                    continue
                v = _first_float(params, lkey)
                dflt = fact.get("default")
                mag = dflt if isinstance(dflt, (int, float)) and dflt >= 100 else None
                if mag is None:
                    mag = recommended.get(lkey)
                t = um_unit_floor(lkey, label, fact, v, mag)
                if t:
                    traps.append(t)

            # (2) collapsed-span box (BoxSize).
            bfact = facts.get("boxsize")
            btoks = params.get("boxsize", [])
            if btoks:
                t = positive_span("boxsize", "BoxSize", bfact, btoks)
                if t:
                    traps.append(t)

            # (3) degenerate seed ω-window (and any future declared bound pair).
            for lo, hi, lo_lab, hi_lab, section in _BOUND_PAIRS:
                t = degenerate_bound_pair(lo, hi, lo_lab, hi_lab, params, section)
                if t:
                    traps.append(t)

        # (4) curated relational / always-on reminders.
        traps.extend(_curated_relational(params, pipe, source_h5))
        return traps
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# B. Post-run FF outcome verifier
# ─────────────────────────────────────────────────────────────────────────────

def _iter_layer_dirs(result_folder: Path, layers=None):
    """Yield (layer_nr, dir) for LayerNr_<N> dirs (or the folder itself)."""
    if layers:
        for n in layers:
            d = result_folder / f"LayerNr_{n}"
            if d.is_dir():
                yield n, d
        return
    found = False
    for d in sorted(result_folder.glob("LayerNr_*")):
        if d.is_dir():
            found = True
            m = re.search(r"LayerNr_(\d+)", d.name)
            yield (int(m.group(1)) if m else d.name), d
    if not found:
        yield None, result_folder


def _find_artifact(layer_dir: Path, name: str):
    """Locate an artifact by name in the layer dir or its Output/Temp subdirs."""
    for base in (layer_dir, layer_dir / "Output", layer_dir / "Temp"):
        p = base / name
        if p.exists():
            return p
    return None


def _csv_data_rows(path: Path, limit: int = 5000) -> int:
    """Count non-comment, non-blank data rows (cheap, capped)."""
    n = 0
    try:
        with path.open("r", errors="ignore") as fh:
            for i, line in enumerate(fh):
                if i > limit:
                    break
                s = line.strip()
                if s and not s.startswith("%") and not s.startswith("#"):
                    n += 1
    except Exception:
        return -1
    return n


def _csv_all_zero(path: Path, sample_rows: int = 200) -> bool:
    """True if every numeric token across a sample of rows is exactly zero — the
    all-zero InputAll.csv signature of a collapsed BoxSize / bad Width."""
    saw_number = False
    try:
        with path.open("r", errors="ignore") as fh:
            rows = 0
            for line in fh:
                s = line.split("#", 1)[0].split("%", 1)[0].strip()
                if not s:
                    continue
                for tok in s.replace(",", " ").split():
                    try:
                        val = float(tok)
                    except Exception:
                        continue
                    saw_number = True
                    if val != 0.0:
                        return False
                rows += 1
                if rows >= sample_rows:
                    break
    except Exception:
        return False
    return saw_number


def verify_ff_outputs(result_folder, layers=None) -> dict:
    """Independently verify an FF reconstruction's on-disk artifacts against the
    notebook's documented silent-failure chains. Deterministic, no MIDAS run.

    Signatures checked per LayerNr_<N> (each cites its source):
      • InputAll.csv present, non-empty, not all-zero  (bad Width / collapsed BoxSize)
      • SpotsToIndex.csv has > 0 seed rows            (dead ω seed-window)
      • Data.bin / nData.bin non-empty                (empty peak-search output)
      • IndexBest*.bin > 4 bytes with a solved grain  (0 seeds → 4-byte IndexBest)
      • Grains.csv has > 0 data rows                  (indexing produced no grains)

    Returns ``{status: ok|fail|incomplete|error, layers:[{layer, checks:[...]}],
    summary}``. ``fail`` = a present artifact is degenerate (the valuable signal);
    ``incomplete`` = nothing to check yet; a missing artifact is ``n/a``, not a
    fail (the stage may simply not have run).
    """
    try:
        import numpy as np
    except Exception:
        np = None

    rf = Path(result_folder).expanduser()
    if not rf.is_dir():
        return {"status": "error", "layers": [],
                "summary": f"result_folder not found: {rf}"}

    def chk(name, ok, detail, cite):
        return {"name": name, "ok": ok, "detail": detail, "cite": cite}

    layer_reports = []
    any_present = False
    for layer_nr, ld in _iter_layer_dirs(rf, layers):
        checks = []

        # InputAll.csv — present, non-empty, not all-zero
        ia = (_find_artifact(ld, "InputAllExtraInfoFittingAll.csv")
              or _find_artifact(ld, "InputAll.csv"))
        if ia is None:
            checks.append(chk("InputAll", None, "not found (stage may not have run)",
                              "FF Notebook §Width/BoxSize → empty InputAll.csv"))
        else:
            any_present = True
            sz = ia.stat().st_size
            if sz == 0:
                checks.append(chk("InputAll", False, f"{ia.name} is empty (0 bytes) — "
                                  "no peaks passed the ring/width filter",
                                  "FF Notebook §Width → empty InputAll.csv"))
            elif _csv_all_zero(ia):
                checks.append(chk("InputAll", False, f"{ia.name} is all-zero — the "
                                  "spot keep-box rejected every peak (collapsed BoxSize)",
                                  "FF Notebook §BoxSize → all-zero InputAll.csv"))
            else:
                checks.append(chk("InputAll", True, f"{ia.name} OK ({sz} bytes)",
                                  "FF Notebook §peak-search output"))

        # SpotsToIndex.csv — > 0 seed rows
        sti = _find_artifact(ld, "SpotsToIndex.csv")
        if sti is None:
            checks.append(chk("SpotsToIndex", None, "not found",
                              "FF Notebook §ω-window → empty SpotsToIndex.csv"))
        else:
            any_present = True
            rows = _csv_data_rows(sti)
            checks.append(chk("SpotsToIndex", rows > 0,
                              f"{rows} seed rows",
                              "FF Notebook §ω seed-window → 0 seeds"))

        # Data.bin / nData.bin — non-empty
        for bn in ("Data.bin", "nData.bin"):
            b = _find_artifact(ld, bn)
            if b is None:
                continue
            any_present = True
            sz = b.stat().st_size
            checks.append(chk(bn, sz > 0, f"{sz} bytes",
                              "FF Notebook §3b int64 Data.bin"))

        # IndexBest*.bin — > 4 bytes, at least one solved grain (col 14 > 0)
        ib = (_find_artifact(ld, "IndexBest_all.bin")
              or _find_artifact(ld, "IndexBest.bin"))
        if ib is None:
            checks.append(chk("IndexBest", None, "not found",
                              "FF Notebook §0 seeds → 4-byte IndexBest_all.bin"))
        else:
            any_present = True
            sz = ib.stat().st_size
            if sz <= 4:
                checks.append(chk("IndexBest", False,
                                  f"{ib.name} is {sz} bytes — indexer got 0 seeds → 0 grains",
                                  "FF Notebook §0 seeds → 4-byte IndexBest_all.bin"))
            elif np is not None:
                try:
                    arr = np.fromfile(ib, dtype=np.float64)
                    if arr.size % 15 == 0 and arr.size >= 15:
                        solved = int((arr.reshape(-1, 15)[:, 14] > 0).sum())
                        checks.append(chk("IndexBest", solved > 0,
                                          f"{solved} solved / {arr.size // 15} rows",
                                          "FF Notebook §indexing seeds"))
                    else:
                        checks.append(chk("IndexBest", True, f"{sz} bytes (non-empty)",
                                          "FF Notebook §indexing seeds"))
                except Exception:
                    checks.append(chk("IndexBest", True, f"{sz} bytes (non-empty)",
                                      "FF Notebook §indexing seeds"))
            else:
                checks.append(chk("IndexBest", True, f"{sz} bytes (non-empty)",
                                  "FF Notebook §indexing seeds"))

        # Grains.csv — > 0 data rows
        gr = _find_artifact(ld, "Grains.csv")
        if gr is None:
            checks.append(chk("Grains", None, "not found (run may not have consolidated)",
                              "FF §9 Grains.csv"))
        else:
            any_present = True
            rows = _csv_data_rows(gr)
            checks.append(chk("Grains", rows > 0, f"{rows} grains", "FF §9 Grains.csv"))

        layer_reports.append({"layer": layer_nr, "dir": str(ld), "checks": checks})

    failed = [(lr["layer"], c) for lr in layer_reports for c in lr["checks"]
              if c["ok"] is False]
    if failed:
        status = "fail"
        summary = "; ".join(f"L{lyr}:{c['name']} {c['detail']}" for lyr, c in failed[:6])
    elif not any_present:
        status = "incomplete"
        summary = "no FF artifacts found yet under " + str(rf)
    else:
        status = "ok"
        summary = "all present FF artifacts passed the notebook invariants"
    return {"status": status, "layers": layer_reports, "summary": summary}


# ── Stale stage outputs (FF phase-3 §"zip_convert reuses an existing archive") ──
#
# zip_convert REUSES any existing *.MIDAS.zip, and downstream stages read their
# parameters out of the zarr rather than out of Parameters.txt. So editing a key
# and re-running into the same result folder silently keeps the OLD value while
# the run reports success. Observed live on 1-ID (2026-08-26): RingThresh was
# measured and patched from 500 to 10, the run was resumed from peakfit, and the
# zarr still carried 500 -- peak search returned almost nothing and several cycles
# were spent before the cause was found.
#
# Data, not code: each row names the key, the artifact its change invalidates, and
# the handbook section. Adding a trap means adding a row.
_STALE_INVALIDATION = [
    # key(s)                       artifact glob (under result_folder)   stage        cite
    (("RingThresh",),              "**/Temp/AllPeaks_PS.bin",            "peak search", "FF phase-3 §zip_convert/param-refresh"),
    (("RingThresh", "MinPeakSNR"), "**/*.MIDAS.zip",                     "zip_convert", "FF phase-3 §zip_convert/param-refresh"),
    (("tx", "ty", "tz", "Lsd", "BC", "p0", "p1", "p2"),
                                   "**/*.MIDAS.zip",                     "zip_convert (geometry is read from the zarr, not the param file)",
                                                                                       "FF phase-3 §geometry bites hardest"),
    # These were consumed when the frames were written: patching the number leaves
    # data and metadata describing different things (a silently shifted omega for
    # SkipFrame). The handbook's remedy here is to delete the zip, not refresh it.
    (("SkipFrame", "Padding", "FileStem"),
                                   "**/*.MIDAS.zip",                     "frame writing", "FF phase-3 §stored-frame keys"),
]


def check_stale_stage_outputs(param_path: str, result_folder: str) -> List[Dict[str, str]]:
    """Detect parameters edited AFTER the stage outputs they feed were produced.

    Returns trap dicts in the same shape as the parameter lint. The test is
    deliberately cheap and conservative: a key is only flagged when the parameter
    file is strictly NEWER than an artifact that key feeds, so a first run (no
    artifacts) and an untouched re-run both stay silent.

    This cannot be fixed by refreshing the zarr alone -- doing so moves the
    staleness one stage downstream. The remedies the handbook gives are to delete
    the invalidated outputs, or to pass --force-param-refresh and accept they are
    stale.
    """
    traps: List[Dict[str, str]] = []
    try:
        if not (param_path and result_folder and os.path.isfile(param_path)):
            return traps
        rf = Path(result_folder)
        if not rf.is_dir():
            return traps
        p_mtime = os.path.getmtime(param_path)
        present = parse_param_file(param_path)   # {lowercased_key: [tokens]}

        for keys, pattern, stage, cite in _STALE_INVALIDATION:
            hit = [k for k in keys if k.lower() in present]
            if not hit:
                continue
            for art in rf.glob(pattern):
                try:
                    a_mtime = os.path.getmtime(art)
                except OSError:
                    continue
                if p_mtime > a_mtime:
                    traps.append({
                        "severity": "error",
                        "key": ", ".join(hit),
                        "message": (
                            f"{', '.join(hit)} was edited after {art.name} was written "
                            f"({stage}). Downstream stages read this value from the "
                            f"archive, not from the parameter file, so re-running into "
                            f"this folder would silently use the OLD value and report "
                            f"success. Refreshing the zarr alone only moves the "
                            f"staleness one stage downstream. Either delete the "
                            f"invalidated outputs (or the whole result folder and start "
                            f"clean), or pass --force-param-refresh and accept they are "
                            f"stale."),
                        "section": cite,
                    })
                    break       # one report per rule is enough
    except Exception as e:      # fail-open like the parameter lint -- but SAY SO.
        # A silent `return []` here disabled this check entirely when it referenced
        # a function that did not exist, and nothing indicated the guardrail was
        # dead. Fail open, loudly.
        import sys as _s
        print(f"  [handbook] stale-output check skipped: {type(e).__name__}: {e}",
              file=_s.stderr)
        return []
    return traps
