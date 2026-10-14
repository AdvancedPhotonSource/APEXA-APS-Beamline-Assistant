"""Network tiers — let APEXA run on a beamline machine with no public internet.

The failure this exists to prevent
----------------------------------
APEXA failed to launch on ``copland`` (a beamline analysis host with data access
and ANL-internal networking, but no route to the public internet). It hung at
startup trying to reach HuggingFace for the RAG embedding model.

The mechanism matters, because the obvious guard did not help: the knowledge-base
pre-warm in ``midas_comprehensive_server`` was already wrapped in ``try/except``,
but **a network hang is not an exception**. The HF HEAD request blocks on long
retries, the handler never fires, ``mcp.run()`` is never reached, and the client
waits forever for a server that never finishes starting. Fail-open only works if
the failure is *raised*; here it had to be *avoided*.

The model
---------
Three tiers, ordered by what the host can reach:

``data``
    No off-host services. Validation and analysis work locally; agentic use
    requires a separately installed, qualified model on a loopback endpoint.
``internal``  **← the beamline default**
    ANL-internal network: Argo Gateway, argo-proxy, ALCF Inference Service, SSH
    to analysis hosts. Everything that matters for data reduction works. The RAG
    embedder loads from a pre-staged cache and never touches the network.
``web``
    Also the public internet: HuggingFace downloads, Materials Project, DOI
    metadata.

``internal`` is the default **on purpose**. Defaulting to web-optimistic is what
caused the copland hang: a host that cannot reach the internet then discovers it
by blocking. Assuming the smaller network and letting a web-capable host opt up
fails safe on the machines that matter most.

Set ``APEXA_NETWORK=web|internal|data``; ``docs/setup_user.sh`` probes and writes
it. ``APEXA_OFFLINE=1`` remains supported and implies ``internal``.
"""
from __future__ import annotations

import os
import socket
from typing import Dict, Iterable, Optional, Tuple

DATA, INTERNAL, WEB = "data", "internal", "web"
_ORDER = {DATA: 0, INTERNAL: 1, WEB: 2}
DEFAULT_TIER = INTERNAL

_TRUTHY = ("1", "true", "yes", "on")

# Tools that reach the PUBLIC internet. Everything else in APEXA needs at most the
# ANL-internal network. Keep this list small and explicit — a tool added here is
# disabled on beamline hosts, so it must genuinely require the open web.
WEB_ONLY_TOOLS: Dict[str, str] = {
    "fetch_cif_from_mp": "Materials Project API (next-gen.materialsproject.org)",
    "propose_structures": "Crystallography Open Database retrieval",
    # NOTE: get_bibtex is NOT here — despite the name it makes no network call. It
    # reads local .bib sidecars from knowledge_base/papers/ (no DOI/CrossRef
    # fallback), so it works at every tier and must not be disabled offline.
}

# Capabilities that degrade rather than disappear without the public internet.
WEB_DEGRADED: Dict[str, str] = {
    "query_hedm_knowledge":
        "RAG works offline from the pre-staged embedder cache; only the FIRST "
        "download of the model needs the web (see docs/OFFLINE_DEPLOYMENT.md).",
}


def tier() -> str:
    """Resolve the active network tier.

    ``APEXA_OFFLINE=1`` is honoured as a legacy alias for "no public internet"
    and caps the tier at ``internal``.
    """
    raw = (os.environ.get("APEXA_NETWORK") or "").strip().lower()
    if raw in _ORDER:
        resolved = raw
    else:
        resolved = DEFAULT_TIER
    if (os.environ.get("APEXA_OFFLINE", "").lower() in _TRUTHY
            or os.environ.get("HF_HUB_OFFLINE", "").lower() in _TRUTHY):
        if _ORDER[resolved] > _ORDER[INTERNAL]:
            resolved = INTERNAL
    return resolved


def has_web() -> bool:
    return _ORDER[tier()] >= _ORDER[WEB]


def has_internal() -> bool:
    return _ORDER[tier()] >= _ORDER[INTERNAL]


def apply_offline_env() -> bool:
    """Force HuggingFace/transformers fully offline unless the tier allows the web.

    Must run BEFORE ``sentence_transformers`` is imported or a model constructed.
    Idempotent. Returns True when offline was enforced.
    """
    if has_web():
        return False
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    # Belt and braces: some stacks consult only this one.
    os.environ.setdefault("HF_DATASETS_OFFLINE", "1")
    # ChromaDB ships anonymised telemetry to PostHog. That is an outbound call to
    # a third party from a beamline host — undesirable on its own terms, and one
    # more thing that can block when there is no route out.
    os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
    os.environ.setdefault("CHROMA_TELEMETRY_ENABLED", "False")
    return True


def tool_unavailable_reason(tool_name: str) -> Optional[str]:
    """Why this tool cannot run at the current tier, or None if it can.

    Returned to the model as a normal tool result so it can adapt, rather than
    letting the call block on a network that isn't there.
    """
    tool_name = tool_name.rsplit(":", 1)[-1]
    if tool_name in WEB_ONLY_TOOLS and not has_web():
        return (f"{tool_name} needs the public internet ({WEB_ONLY_TOOLS[tool_name]}), "
                f"but APEXA is running at network tier '{tier()}'. It is unavailable "
                f"on this host. Do not retry, and do not invent its output — either "
                f"proceed without it or ask the user to supply the file directly. "
                f"(An operator with web access can set APEXA_NETWORK=web.)")
    return None


def prewarm_allowed() -> bool:
    """Whether the knowledge-base pre-warm may run at startup.

    Pre-warming loads a ~700 MB embedder. On a host without the public internet
    and without a pre-staged cache that call BLOCKS, and a blocking startup is
    strictly worse than a slow first query — the server never reaches
    ``mcp.run()`` and the whole assistant fails to launch.

    So: never pre-warm below ``web`` tier unless the operator explicitly opts in
    with ``APEXA_KB_PREWARM=1`` (correct once the cache really is staged).
    """
    raw = (os.environ.get("APEXA_KB_PREWARM") or "").strip().lower()
    if raw in _TRUTHY:
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return has_web()


# ── reachability probe (setup-time only, never in the hot path) ──────────────

def _can_connect(host: str, port: int = 443, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def probe(timeout: float = 3.0) -> Tuple[str, Dict[str, bool]]:
    """Detect the tier this host can actually reach.

    Short, bounded TCP connects only — no HTTP, no retries, no DNS-hang risk
    beyond the timeout. Intended for ``docs/setup_user.sh`` and diagnostics; the
    runtime never probes, it reads the configured value, because a probe at
    startup would reintroduce exactly the blocking behaviour this module exists
    to prevent.
    """
    checks = {
        "anl_internal": _can_connect("apps.inside.anl.gov", 443, timeout),
        "alcf":         _can_connect("inference-api.alcf.anl.gov", 443, timeout),
        "web":          _can_connect("huggingface.co", 443, timeout),
    }
    if checks["web"]:
        detected = WEB
    elif checks["anl_internal"] or checks["alcf"]:
        detected = INTERNAL
    else:
        detected = DATA
    return detected, checks


def describe() -> str:
    """One-line operator summary for startup diagnostics."""
    t = tier()
    if t == WEB:
        return "network: web (public internet available)"
    if t == INTERNAL:
        n = len(WEB_ONLY_TOOLS)
        return (f"network: internal (ANL only) — {n} web-only tool(s) disabled, "
                f"HuggingFace forced offline")
    return "network: data (no network) — LLM transport will not work"


def disabled_tools() -> Iterable[str]:
    """Tool names that should be withheld from the model at this tier."""
    if has_web():
        return ()
    return tuple(WEB_ONLY_TOOLS)
