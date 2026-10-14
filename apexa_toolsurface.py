"""Progressive tool disclosure — a small always-on tool surface, the rest on demand.

The problem
-----------
APEXA registers 81 tools (11 core + 57 MIDAS + 13 motor). The default unified
agent has ``tool_names=[]``, so ``_filter_tools`` returns all of them and **every
single request carries all 81 JSON schemas**. That is a large fixed cost on every
turn, it pushes the genuinely relevant tools down among dozens of irrelevant ones,
and it makes the request prefix churn.

The approach
------------
Keep a small **core surface** always present — file/shell primitives plus the
discovery tools that lead everywhere else — and expose the remaining tools through
two meta-tools the model calls when it needs them:

    search_tools(query)   → ranked matches with one-line descriptions
    load_tools(names)     → attaches those schemas for the rest of the turn

Loaded tools stay loaded for the turn, so a multi-stage FF workflow pays the
disclosure cost once per stage rather than once per request. Schemas are
**appended** as they load rather than rebuilt, so the stable prefix of the request
keeps its shape (prompt-cache friendly).

Ranking is lexical (name/description/keyword overlap) rather than embedding-based
on purpose: it must be instant, deterministic, dependency-free, and available with
``APEXA_OFFLINE=1``. The heavyweight semantic path already exists as
``query_hedm_knowledge`` (RAG) and stays in the core surface for exactly the cases
where lexical search is not enough.
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

# Technique capsules (MIDAS handbooks vendored into knowledge_base/capsules/).
# stdlib-only, fail-open — import guarded so a missing/broken registry never
# takes down the tool surface; the capsule meta-tools simply report "unavailable".
try:
    import capsule_registry as _capsules
except Exception:  # pragma: no cover - defensive
    _capsules = None

# ── Core surface ─────────────────────────────────────────────────────────────
# Always present. File/shell primitives (needed to do anything at all) plus three
# discovery tools that can reach the rest of the catalogue.
#
# Motor tools are deliberately NOT core: EPICS motion is the highest-consequence
# surface APEXA exposes, and requiring an explicit load_tools() keeps it off the
# table for the many turns that have nothing to do with motion. They remain fully
# reachable via search_tools/load_tools.
CORE_TOOLS: tuple[str, ...] = (
    # core server — primitives
    "list_directory",
    "read_file",
    "write_file",
    "get_file_info",
    "run_command",
    "run_remote_command",
    "open_science_view",
    # midas server — discovery / orientation
    "recommend_workflow",
    "query_hedm_knowledge",
    "inspect_dataset_file",
)

META_TOOLS: tuple[str, ...] = (
    "search_tools", "load_tools",
    # Technique-capsule meta-tools (client-side; read the vendored handbooks).
    "list_techniques", "learn_technique", "open_phase",
)

# Domain hints that let a plain-language query reach tools whose names share no
# substring with it ("where are my grains" → read_grains_summary). Data, not code:
# extend this table rather than special-casing a query in the search function.
_DOMAIN_HINTS: Dict[str, tuple[str, ...]] = {
    "calibrat":  ("calibrate", "calibration", "ceo2", "lab6", "beam center", "lsd", "tilt", "detector distance"),
    "integrat":  ("integrate", "integration", "azimuthal", "caking", "lineout", "1d", "2d"),
    "hedm":      ("hedm", "grain", "grains", "indexing", "orientation", "ff", "nf", "pf", "reconstruction"),
    "strain":    ("strain", "stress", "lattice", "elastic", "stiffness", "slip"),
    "refine":    ("refine", "refinement", "rietveld", "gsas", "maud", "fit"),
    "motor":     ("motor", "move", "position", "epics", "caget", "caput", "jog", "tweak", "home", "limit"),
    "mask":      ("mask", "dead pixel", "hot pixel", "module gap", "sentinel"),
    "plot":      ("plot", "visualize", "view", "image", "heatmap", "contour", "render"),
    "pdf":       ("pdf", "pair distribution", "g(r)", "structure factor"),
    "report":    ("report", "summary", "verify", "diagnostics", "html"),
}

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def disclosure_enabled() -> bool:
    """Progressive disclosure on/off. Default ON; ``APEXA_TOOL_DISCLOSURE=0`` gives
    back the old all-schemas-every-request behaviour for A/B comparison."""
    return (os.environ.get("APEXA_TOOL_DISCLOSURE", "1").strip().lower()
            not in ("0", "false", "no", "off"))


def _tokens(text: str) -> Set[str]:
    return set(_TOKEN_RE.findall((text or "").lower()))


def tool_name(t: Dict[str, Any]) -> str:
    return (t.get("function") or t).get("name", "")


def tool_desc(t: Dict[str, Any]) -> str:
    return (t.get("function") or t).get("description", "") or ""


# ── Meta-tool schemas ────────────────────────────────────────────────────────

def _capsule_meta_schemas() -> List[Dict[str, Any]]:
    """Schemas for the technique-capsule meta-tools. Emitted only when at least
    one capsule is vendored, so the surface never advertises a dead tool."""
    if _capsules is None:
        return []
    try:
        techs = list(_capsules.available_techniques())
    except Exception:
        techs = []
    if not techs:
        return []
    tlist = ", ".join(techs)
    return [
        {
            "type": "function",
            "function": {
                "name": "list_techniques",
                "description": (
                    "List the MIDAS analysis techniques APEXA has a vendored handbook "
                    f"('capsule') for ({tlist}). Returns each technique's scope, phase "
                    "count, and rule count. Call this first when the user asks to run or "
                    "plan a full technique workflow (FF/NF/PF-HEDM, DFXM, …) so you know "
                    "which capsule to learn."
                ),
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "learn_technique",
                "description": (
                    "Load the handbook SPINE for a technique into your context — its "
                    "scope gate, the prescribed step ORDER, the numbered hard rules, the "
                    "halt conditions, and the traps that silently corrupt results. This "
                    "is the source of truth for HOW to run the workflow; read it BEFORE "
                    "editing a parameter file or firing a workflow tool. Then use "
                    "open_phase to page in each step as you reach it."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "technique": {"type": "string",
                                      "description": f"Technique name, one of: {tlist} "
                                                     "(ff/nf/pf/df accepted)."},
                    },
                    "required": ["technique"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "open_phase",
                "description": (
                    "Open one phase document of a technique's workflow — the detailed "
                    "procedure (exact flags, units, checks) for that single step. Call it "
                    "just-in-time when you reach that phase, not all at once. Phase keys "
                    "come from learn_technique's ORDER / phase index (e.g. 'phase-2', "
                    "or just the number)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "technique": {"type": "string", "description": f"One of: {tlist}."},
                        "phase": {"type": "string",
                                  "description": "Phase handle: 'phase-2', '2', or the file name."},
                    },
                    "required": ["technique", "phase"],
                },
            },
        },
    ]


def meta_tool_schemas(n_hidden: int) -> List[Dict[str, Any]]:
    """Schemas for search_tools / load_tools (+ capsule meta-tools), sized to the
    current catalogue."""
    return _capsule_meta_schemas() + [
        {
            "type": "function",
            "function": {
                "name": "search_tools",
                "description": (
                    f"Search APEXA's full catalogue of specialist tools ({n_hidden} not "
                    f"currently loaded) by task description. Use this whenever the loaded "
                    f"tools do not cover what you need — e.g. calibration, integration, "
                    f"FF/NF/PF-HEDM, GSAS-II refinement, stress/strain, detector masking, "
                    f"visualization, or EPICS motor control. Returns matching tool names "
                    f"with descriptions; follow up with load_tools to use them."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string",
                                  "description": "What you are trying to do, in plain language."},
                        "limit": {"type": "integer",
                                  "description": "Max results (default 10)."},
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "load_tools",
                "description": (
                    "Attach specialist tools by name so you can call them for the rest of "
                    "this turn. Names come from search_tools. Load everything you need for "
                    "the current stage in one call."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "names": {"type": "array", "items": {"type": "string"},
                                  "description": "Exact tool names to load."},
                    },
                    "required": ["names"],
                },
            },
        },
    ]


# ── Surface construction ─────────────────────────────────────────────────────

def _network_disabled() -> Set[str]:
    """Tools withheld because this host cannot reach the network they need.

    Filtering the SURFACE complements the hard gate in execute_tool_call: the gate
    guarantees such a tool can never run, this keeps it out of the model's context
    so it never tries and never has to be corrected.
    """
    try:
        from apexa_network import disabled_tools
        return set(disabled_tools())
    except Exception:
        return set()


def initial_surface(all_tools: Sequence[Dict[str, Any]],
                    extra: Iterable[str] = ()) -> List[Dict[str, Any]]:
    """The tool list a turn starts with: core + meta (+ any caller-pinned extras)."""
    wanted = (set(CORE_TOOLS) | set(extra)) - _network_disabled()
    present = [t for t in all_tools if tool_name(t) in wanted]
    n_hidden = max(0, len(all_tools) - len(present))
    return present + meta_tool_schemas(n_hidden)


def search(all_tools: Sequence[Dict[str, Any]], query: str,
           limit: int = 10,
           exclude: Iterable[str] = ()) -> List[Dict[str, str]]:
    """Rank tools against a plain-language query. Returns [{name, description}]."""
    q = (query or "").lower()
    qt = _tokens(q)
    if not qt:
        return []

    # Expand the query with domain hints whose trigger appears in it.
    for trigger, hints in _DOMAIN_HINTS.items():
        if trigger in q:
            for h in hints:
                qt |= _tokens(h)

    skip = set(exclude) | set(META_TOOLS) | _network_disabled()
    scored: List[tuple[float, str, str]] = []
    for t in all_tools:
        name = tool_name(t)
        if not name or name in skip:
            continue
        desc = tool_desc(t)
        nt = _tokens(name)
        dt = _tokens(desc)

        score = 0.0
        # Whole-query substring in the name is the strongest signal.
        if q.replace(" ", "_") in name.lower():
            score += 10.0
        # Token overlap with the name matters far more than with the description.
        score += 3.0 * len(qt & nt)
        score += 1.0 * len(qt & dt)
        # Partial-word name hits ("integrat" → "midas_integrate_series").
        for tok in qt:
            if len(tok) >= 5 and tok in name.lower():
                score += 2.0
        if score > 0:
            scored.append((score, name, desc))

    scored.sort(key=lambda r: (-r[0], r[1]))
    return [{"name": n, "description": d[:200]} for _, n, d in scored[:max(1, limit)]]


def resolve(all_tools: Sequence[Dict[str, Any]],
            names: Iterable[str]) -> tuple[List[Dict[str, Any]], List[str]]:
    """Map names → schemas. Returns (found_schemas, unknown_names)."""
    by_name = {tool_name(t): t for t in all_tools}
    found, unknown = [], []
    for n in names or []:
        n = (n or "").strip()
        if not n:
            continue
        if n in by_name:
            found.append(by_name[n])
        else:
            unknown.append(n)
    return found, unknown


# ── Meta-tool execution (client-side; never reaches an MCP server) ───────────

def handle_meta_tool(name: str, arguments: Dict[str, Any],
                     all_tools: Sequence[Dict[str, Any]],
                     active: List[Dict[str, Any]],
                     injected_techniques: Optional[Set[str]] = None) -> Optional[str]:
    """Execute a client-side meta-tool (search/load_tools + the capsule meta-tools)
    against ``active`` (mutated in place for load_tools).

    Returns the result string, or ``None`` if ``name`` is not a meta-tool — so the
    caller can fall through to normal MCP dispatch. ``injected_techniques`` (when
    supplied) is the loop's dedup set: learn_technique marks a technique there so
    the loop's automatic spine injection does not repeat it.
    """
    # ── Technique-capsule meta-tools ─────────────────────────────────────────
    if name in ("list_techniques", "learn_technique", "open_phase"):
        if _capsules is None:
            return "Technique capsules are unavailable (capsule_registry did not load)."
        try:
            return _handle_capsule_meta(name, arguments, injected_techniques)
        except Exception as e:  # pragma: no cover - fail-open
            return f"Capsule lookup failed: {e}"

    loaded_names = {tool_name(t) for t in active}

    if name == "search_tools":
        hits = search(all_tools, str(arguments.get("query", "")),
                      limit=int(arguments.get("limit") or 10))
        if not hits:
            return ("No tools matched. Try broader terms (e.g. 'calibration', "
                    "'integration', 'hedm', 'refinement', 'motor', 'mask', 'plot').")
        lines = [f"{len(hits)} matching tool(s) — call load_tools with the ones you need:"]
        for h in hits:
            mark = " [already loaded]" if h["name"] in loaded_names else ""
            lines.append(f"  - {h['name']}{mark}: {h['description']}")
        return "\n".join(lines)

    if name == "load_tools":
        raw = arguments.get("names") or []
        if isinstance(raw, str):
            raw = [s.strip() for s in raw.split(",")]
        found, unknown = resolve(all_tools, raw)
        added = []
        for t in found:
            n = tool_name(t)
            if n not in loaded_names:
                active.append(t)
                loaded_names.add(n)
                added.append(n)
        parts = []
        if added:
            parts.append(f"Loaded {len(added)} tool(s): {', '.join(added)}. "
                         f"You can call them now.")
        already = [tool_name(t) for t in found if tool_name(t) not in added]
        if already:
            parts.append(f"Already loaded: {', '.join(already)}.")
        if unknown:
            sugg = search(all_tools, " ".join(unknown), limit=5)
            hint = (" Did you mean: " + ", ".join(h["name"] for h in sugg)) if sugg else ""
            parts.append(f"Unknown tool name(s): {', '.join(unknown)}.{hint}")
        return " ".join(parts) or "Nothing to load."

    return None


def _handle_capsule_meta(name: str, arguments: Dict[str, Any],
                         injected_techniques: Optional[Set[str]]) -> str:
    """Execute list_techniques / learn_technique / open_phase (capsule reads)."""
    cr = _capsules

    if name == "list_techniques":
        rows = cr.manifest()
        if not rows:
            return "No technique capsules are vendored."
        pin = cr.pin_info()
        commit = (pin.get("midas_commit") or "")[:12]
        head = (f"{len(rows)} technique capsule(s) (MIDAS @{commit}). "
                "Call learn_technique(<name>) to load a workflow's spine:")
        lines = [head]
        for r in rows:
            scope = f" — scope: {r['scope'][:90]}" if r.get("scope") else ""
            lines.append(
                f"  - {r['technique']}: {r['title']} "
                f"[{r['n_phases']} phases, {r['n_hard_rules']} hard rules, "
                f"{r['n_halt_conditions']} halt conditions]{scope}"
            )
        return "\n".join(lines)

    if name == "learn_technique":
        raw = str(arguments.get("technique", "")).strip()
        tech = cr._canon(raw)
        if not tech:
            avail = ", ".join(cr.available_techniques()) or "(none)"
            return (f"Unknown or ambiguous technique '{raw}'. Available: {avail}. "
                    "Call list_techniques for details.")
        body = cr.spine_context(tech)
        if not body:
            return f"No spine found for '{tech}'."
        if injected_techniques is not None:
            injected_techniques.add(tech)   # loop won't re-inject the spine
        halt = cr.halt_checklist(tech)
        phases = cr.phases(tech)
        idx = ""
        if phases:
            idx = ("\n\nPHASES (open each with open_phase as you reach it):\n"
                   + "\n".join(f"  - {p['key']}: {p.get('holds', '') or p['file']}"
                               for p in phases))
        halt_block = (f"\n\nHALT CONDITIONS — stop and ask if any apply:\n{halt}"
                      if halt else "")
        return body + halt_block + idx

    if name == "open_phase":
        raw = str(arguments.get("technique", "")).strip()
        tech = cr._canon(raw)
        if not tech:
            return f"Unknown or ambiguous technique '{raw}'."
        phase = str(arguments.get("phase", "")).strip()
        doc = cr.phase_doc(tech, phase)
        if not doc:
            keys = ", ".join(p["key"] for p in cr.phases(tech)) or "(none)"
            return f"No phase '{phase}' in '{tech}'. Available phases: {keys}."
        return f"=== {tech} · {phase} (MIDAS handbook) ===\n\n{doc}"

    return "Unhandled capsule meta-tool."
