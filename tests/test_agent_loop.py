"""Agent-loop tests: structured transport, execution ledger, progressive disclosure.

No network, no argo-proxy, no MCP servers — a stub provider drives ``AgentRunner``
and a stub executor stands in for the tool chokepoint. These are the first tests to
cover the loop itself, which previously had none despite being where every
correctness-critical behaviour lives.

Run:  uv run pytest tests/test_agent_loop.py -q
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apexa_agents import (  # noqa: E402
    AgentResponse,
    AgentRunner,
    APEXAAgent,
    ToolCall,
    provider_is_structured,
)
from apexa_ledger import ToolLedger, extract_paths  # noqa: E402
from apexa_toolsurface import (  # noqa: E402
    CORE_TOOLS,
    META_TOOLS,
    handle_meta_tool,
    initial_surface,
    search,
    tool_name,
)


# ── stubs ────────────────────────────────────────────────────────────────────

def _schema(name: str, desc: str = "") -> dict:
    return {"type": "function",
            "function": {"name": name, "description": desc or f"{name} tool",
                         "parameters": {"type": "object", "properties": {}}}}


CATALOGUE = [
    _schema("list_directory", "List files in a directory"),
    _schema("read_file", "Read a file"),
    _schema("write_file", "Write a file"),
    _schema("get_file_info", "Stat a file"),
    _schema("run_command", "Run a local shell command"),
    _schema("run_remote_command", "Run a command over SSH"),
    _schema("recommend_workflow", "Recommend a MIDAS workflow"),
    _schema("query_hedm_knowledge", "Search the HEDM knowledge base"),
    _schema("inspect_dataset_file", "Inspect an HDF5/zarr dataset"),
    _schema("midas_auto_calibrate", "Auto-calibrate detector geometry from CeO2 rings"),
    _schema("midas_integrate_series", "Integrate a series of 2D detector files to 1D"),
    _schema("read_grains_summary", "Read Grains.csv and summarize grain statistics"),
    _schema("move_motor_absolute", "Move an EPICS motor to an absolute position"),
]


class StubProvider:
    """Replays a scripted list of AgentResponses and records what it was sent."""

    structured_tools = True   # drives the structured loop

    def __init__(self, script, model="stubmodel"):
        self.model = model
        self._script = list(script)
        self.seen_messages = []
        self.seen_tools = []

    async def chat(self, messages, temperature=0.7, tools=None):
        self.seen_messages.append([dict(m) for m in messages])
        self.seen_tools.append([tool_name(t) for t in (tools or [])])
        if not self._script:
            return AgentResponse(content="done")
        return self._script.pop(0)


class TextProvider(StubProvider):
    structured_tools = False


def _runner(results=None, record=None):
    """AgentRunner with a stub executor. ``results`` maps tool name → result text."""
    results = results or {}

    async def _execute(name, args):
        if record is not None:
            record.append((name, args))
        return results.get(name, f"[{name}] ok")

    return AgentRunner(_execute)


def _agent(tool_names=None):
    return APEXAAgent(name="unified", instructions="Be useful.",
                      tool_names=tool_names if tool_names is not None else [],
                      temperature=0.3)


def _run(runner, agent, provider, query="do the thing", **kw):
    return asyncio.run(runner.run(agent, query, provider, CATALOGUE,
                                  max_iterations=kw.pop("max_iterations", 6), **kw))


# ── provider seam ────────────────────────────────────────────────────────────

def test_provider_is_structured_ducktypes():
    assert provider_is_structured(StubProvider([]))
    assert not provider_is_structured(TextProvider([]))
    assert not provider_is_structured(object())


# ── structured multi-turn round trip ─────────────────────────────────────────

def test_structured_multiturn_roundtrip():
    """assistant(tool_calls) → role:"tool" result → final answer, all structured."""
    calls = []
    runner = _runner({"read_file": "contents of /data/scan5/params.txt"}, record=calls)
    provider = StubProvider([
        AgentResponse(content="Reading it.",
                      tool_calls=[ToolCall(id="c1", name="read_file",
                                           arguments={"path": "/data/scan5/params.txt"})]),
        AgentResponse(content="The file /data/scan5/params.txt was read."),
    ])
    out = _run(runner, _agent(), provider)

    assert calls == [("read_file", {"path": "/data/scan5/params.txt"})]
    assert "/data/scan5/params.txt" in out

    # Second request must carry a real assistant tool_calls turn AND a role:"tool"
    # result — the thing Argo /chat/ cannot express, and the whole point of this work.
    second = provider.seen_messages[1]
    assistant = [m for m in second if m["role"] == "assistant" and m.get("tool_calls")]
    tool_turns = [m for m in second if m["role"] == "tool"]
    assert assistant, "no structured assistant tool_calls turn was replayed"
    assert assistant[0]["tool_calls"][0]["id"] == "c1"
    assert tool_turns and tool_turns[0]["tool_call_id"] == "c1"
    assert "contents of" in tool_turns[0]["content"]
    # And no text-protocol residue anywhere.
    assert not any("TOOL_CALL:" in (m.get("content") or "") for m in second)


def test_structured_path_skips_text_preamble():
    """_TOOL_PREAMBLE (~11K chars of text-protocol coaching) must not be sent."""
    runner = _runner()
    provider = StubProvider([AgentResponse(content="hello")])
    _run(runner, _agent(), provider)
    system = provider.seen_messages[0][0]
    assert system["role"] == "system"
    assert "TOOL_CALL:" not in system["content"]
    assert "ARGUMENTS:" not in system["content"]


# ── execution ledger ─────────────────────────────────────────────────────────

def test_ledger_records_only_what_executed():
    L = ToolLedger()
    L.dispatch("a", "read_file", {"p": 1})
    L.complete("a", "ok text")
    L.dispatch("b", "run_command", {})
    L.complete("b", "Error: boom", status="error")
    assert [e.name for e in L.entries] == ["read_file", "run_command"]
    assert len(L.succeeded) == 1
    assert L.unexecuted_call_ids() == []


def test_ledger_detects_silent_drop():
    L = ToolLedger()
    L.note_emitted(["a", "b"])
    L.dispatch("a", "read_file")
    L.complete("a", "ok")
    v = L.check_final_answer("all done")
    assert [x["code"] for x in v] == ["unexecuted_tool_calls"]
    assert "b" in v[0]["message"]


def test_ledger_zero_execution_claim():
    L = ToolLedger()
    L.add_grounding("what is in scan5?")
    v = L.check_final_answer("The file /data/scan5/Grains.csv contains 7 grains.")
    assert [x["code"] for x in v] == ["zero_execution_claim"]


def test_ledger_allows_conversational_answer_with_no_tools():
    """A tool-free answer that makes no concrete claim must pass untouched."""
    L = ToolLedger()
    assert L.check_final_answer(
        "HEDM stands for high-energy diffraction microscopy. It maps grains in 3D.") == []


def test_ledger_flags_ungrounded_path_but_allows_grounded_one():
    L = ToolLedger()
    L.dispatch("c1", "list_directory", {})
    L.complete("c1", "found /data/scan5/Grains.csv")
    assert L.check_final_answer("See /data/scan5/Grains.csv") == []
    v = L.check_final_answer("See /data/scan5/Invented.csv")
    assert [x["code"] for x in v] == ["ungrounded_paths"]


def test_ledger_does_not_penalize_long_investigations():
    """The regression the old thrash floor caused: many legitimate repeated calls
    must never trigger a violation."""
    L = ToolLedger()
    for i in range(12):
        L.dispatch(f"c{i}", "run_command", {"cmd": f"ls d{i}"})
        L.complete(f"c{i}", f"/data/d{i}/out.txt")
    assert L.check_final_answer("Checked /data/d7/out.txt across 12 directories.") == []


def test_extract_paths_ignores_urls_and_bare_words():
    assert extract_paths("see https://example.com/a/b.html") == set()
    assert extract_paths("the calibration step ran") == set()
    assert "/data/x/y.csv" in extract_paths("wrote /data/x/y.csv")


def test_integrity_gate_blocks_then_accepts_after_correction():
    """A fabricated path is rejected, the model is told, and the retry is accepted."""
    runner = _runner({"list_directory": "found /data/real.csv"})
    provider = StubProvider([
        AgentResponse(content="", tool_calls=[ToolCall(id="c1", name="list_directory",
                                                       arguments={})]),
        AgentResponse(content="I found /data/fabricated.csv"),   # ungrounded → rejected
        AgentResponse(content="I found /data/real.csv"),          # grounded → accepted
    ])
    out = _run(runner, _agent(), provider)
    assert "/data/real.csv" in out
    assert "fabricated" not in out
    correction = provider.seen_messages[2][-1]
    assert correction["role"] == "user"
    assert "EXECUTION-INTEGRITY" in correction["content"]


def test_integrity_gate_surfaces_unresolved_violation_after_budget():
    """If the model never grounds the claim, the answer ships flagged, not silently."""
    runner = _runner({"list_directory": "nothing here"})
    provider = StubProvider(
        [AgentResponse(content="", tool_calls=[ToolCall(id="c1", name="list_directory",
                                                        arguments={})])]
        + [AgentResponse(content="Still /data/ghost.csv") for _ in range(4)]
    )
    out = _run(runner, _agent(), provider)
    assert "Unverified" in out


# ── progressive tool disclosure ──────────────────────────────────────────────

def test_initial_surface_is_small_and_has_meta_tools():
    surf = initial_surface(CATALOGUE)
    names = {tool_name(t) for t in surf}
    assert set(META_TOOLS) <= names
    assert set(CORE_TOOLS) & names == set(CORE_TOOLS) & {tool_name(t) for t in CATALOGUE}
    assert len(surf) < len(CATALOGUE) + len(META_TOOLS)
    # High-consequence motor control is not on the table by default.
    assert "move_motor_absolute" not in names


def test_loop_starts_with_core_surface_not_everything():
    runner = _runner()
    provider = StubProvider([AgentResponse(content="hi")])
    _run(runner, _agent(), provider)
    sent = set(provider.seen_tools[0])
    assert "search_tools" in sent
    assert "midas_auto_calibrate" not in sent, "specialist tool leaked into initial surface"


def test_search_ranks_relevant_tools_first():
    assert search(CATALOGUE, "calibrate the detector with ceria")[0]["name"] == "midas_auto_calibrate"
    assert search(CATALOGUE, "integrate my scan series")[0]["name"] == "midas_integrate_series"
    assert search(CATALOGUE, "how many grains")[0]["name"] == "read_grains_summary"
    assert search(CATALOGUE, "move the sample stage")[0]["name"] == "move_motor_absolute"


def test_load_tools_expands_surface_within_turn():
    active = initial_surface(CATALOGUE)
    before = len(active)
    msg = handle_meta_tool("load_tools", {"names": ["midas_auto_calibrate"]},
                           CATALOGUE, active)
    assert "Loaded 1" in msg
    assert len(active) == before + 1
    assert "midas_auto_calibrate" in {tool_name(t) for t in active}


def test_load_tools_reports_unknown_with_suggestion():
    active = initial_surface(CATALOGUE)
    msg = handle_meta_tool("load_tools", {"names": ["calibrate_everything"]},
                           CATALOGUE, active)
    assert "Unknown tool name(s)" in msg


def test_meta_tool_handler_passes_through_real_tools():
    assert handle_meta_tool("read_file", {}, CATALOGUE, []) is None


def test_model_can_discover_and_call_a_hidden_tool():
    """End-to-end: search → load → call a tool absent from the initial surface."""
    calls = []
    runner = _runner({"midas_auto_calibrate": "Lsd=900.1mm BC=(1024,1023)"}, record=calls)
    provider = StubProvider([
        AgentResponse(content="", tool_calls=[ToolCall(id="s1", name="search_tools",
                                                       arguments={"query": "calibrate detector"})]),
        AgentResponse(content="", tool_calls=[ToolCall(id="l1", name="load_tools",
                                                       arguments={"names": ["midas_auto_calibrate"]})]),
        AgentResponse(content="", tool_calls=[ToolCall(id="c1", name="midas_auto_calibrate",
                                                       arguments={})]),
        AgentResponse(content="Calibrated: Lsd=900.1mm"),
    ])
    out = _run(runner, _agent(), provider)
    # Only the real tool reached the executor; the meta-tools were handled client-side.
    assert calls == [("midas_auto_calibrate", {})]
    assert "900.1" in out
    assert "midas_auto_calibrate" in provider.seen_tools[2]


def test_disclosure_off_sends_full_catalogue(monkeypatch):
    monkeypatch.setenv("APEXA_TOOL_DISCLOSURE", "0")
    runner = _runner()
    provider = StubProvider([AgentResponse(content="hi")])
    _run(runner, _agent(), provider)
    assert "midas_auto_calibrate" in provider.seen_tools[0]


def test_specialist_agent_keeps_its_explicit_scope():
    """An agent with an explicit tool list is unaffected by disclosure."""
    runner = _runner()
    provider = StubProvider([AgentResponse(content="hi")])
    _run(runner, _agent(tool_names=["read_file", "run_command"]), provider)
    assert set(provider.seen_tools[0]) == {"read_file", "run_command"}


# ── model-id resolution against argo-proxy naming ────────────────────────────
#
# Argo's own ids are compact (``claudeopus5``); argo-proxy serves dashed,
# ``argo:``-prefixed ids and exposes MORE THAN ONE alias per model
# (both ``argo:claude-opus-5`` and ``argo:claude-5-opus`` were observed live).
# Verified against a real proxy serving 51 models on 2026-08-14.

_SERVED = [
    "argo:claude-opus-5", "argo:claude-5-opus",          # dual alias, live
    "argo:claude-sonnet-5", "argo:claude-opus-4.8", "argo:claude-haiku-4.5",
    "argo:gpt-5.6-sol", "argo:gpt-5.6-terra", "argo:gpt-5.5", "argo:gpt-5.4",
    "argo:gemini-3.5-flash", "argo:gemini-3.1-flash-lite",
]


def _provider_with_served(model, served=_SERVED):
    from apexa_provider_openai import OpenAICompatProvider

    p = OpenAICompatProvider.__new__(OpenAICompatProvider)
    p.model, p.username, p.url = model, "u", "http://stub/v1"
    p._dropped, p._resolved_model = set(), None

    class _M:
        def __init__(self, i): self.id = i

    class _List:
        data = [_M(i) for i in served]

    class _Models:
        async def list(self): return _List()

    class _C:
        models = _Models()

    p._client = _C()
    return p


@pytest.mark.parametrize("apexa_id,expected", [
    ("claudeopus5",       "argo:claude-opus-5"),
    ("claudesonnet5",     "argo:claude-sonnet-5"),
    ("claudeopus48",      "argo:claude-opus-4.8"),
    ("claudehaiku45",     "argo:claude-haiku-4.5"),
    ("gpt56sol",          "argo:gpt-5.6-sol"),
    ("gpt55",             "argo:gpt-5.5"),
    ("gemini35flash",     "argo:gemini-3.5-flash"),
    ("gemini31flashlite", "argo:gemini-3.1-flash-lite"),
])
def test_model_ids_resolve_to_proxy_names(apexa_id, expected):
    assert asyncio.run(_provider_with_served(apexa_id)._resolve_model()) == expected


def test_unknown_model_raises_with_suggestions():
    from apexa_provider_openai import ProviderUnavailable
    with pytest.raises(ProviderUnavailable) as e:
        asyncio.run(_provider_with_served("claudeopus99")._resolve_model())
    assert "not served" in str(e.value)


def test_permutation_fold_resolves_when_only_reordered_alias_exists():
    """If a proxy ever served ONLY the reordered form, tier 2 must still match."""
    p = _provider_with_served("claudeopus5", served=["argo:claude-5-opus"])
    assert asyncio.run(p._resolve_model()) == "argo:claude-5-opus"


def _provider_whose_listing_raises(exc):
    from apexa_provider_openai import OpenAICompatProvider

    p = OpenAICompatProvider.__new__(OpenAICompatProvider)
    p.model, p.username, p.url = "claudeopus5", "u", "http://stub/v1"
    p._dropped, p._resolved_model = set(), None

    class _Models:
        async def list(self): raise exc

    class _C:
        models = _Models()

    p._client = _C()
    return p


def test_unreachable_proxy_is_fatal_not_swallowed():
    """Regression: an unreachable proxy must NOT resolve "successfully".

    The first implementation caught every listing failure and fell through to
    "use the model id verbatim", so preflight reported OK against a dead endpoint
    and APEXA_LLM_STRICT silently guaranteed nothing — a wrong port only failed at
    first query, mid-experiment.
    """
    from apexa_provider_openai import ProviderUnavailable

    class APIConnectionError(Exception):
        pass

    p = _provider_whose_listing_raises(APIConnectionError("Connection error."))
    with pytest.raises(ProviderUnavailable) as e:
        asyncio.run(p._resolve_model())
    # Assert the BEHAVIOUR (fatal, names the endpoint and the remedy), not the
    # exact wording: the message is now derived from which endpoint failed and
    # whether it was reachability or the credential, so a literal "argo-proxy"
    # would break every time that advice improves.
    msg = str(e.value)
    assert "cannot reach" in msg
    assert "http://stub/v1" in msg          # names the endpoint that failed
    assert "argo-proxy serve" in msg        # non-ANL host → sidecar advice


def test_proxy_without_models_endpoint_still_works():
    """A proxy that just doesn't implement /models is tolerable — use the id as-is."""
    p = _provider_whose_listing_raises(ValueError("404 no such route"))
    assert asyncio.run(p._resolve_model()) == "claudeopus5"


def test_preflight_reports_legacy_mode_ok(monkeypatch):
    from apexa_provider_openai import preflight
    monkeypatch.setenv("APEXA_LLM_MODE", "argo")
    ok, detail = asyncio.run(preflight("u", "claudeopus5"))
    assert ok and "legacy" in detail


def test_strict_mode_defaults_on_under_proxy(monkeypatch):
    from apexa_provider_openai import strict_mode
    monkeypatch.delenv("APEXA_LLM_STRICT", raising=False)
    monkeypatch.setenv("APEXA_LLM_MODE", "proxy")
    assert strict_mode() is True
    monkeypatch.setenv("APEXA_LLM_MODE", "argo")
    assert strict_mode() is False
    monkeypatch.setenv("APEXA_LLM_MODE", "proxy")
    monkeypatch.setenv("APEXA_LLM_STRICT", "0")
    assert strict_mode() is False


def test_permutation_fold_refuses_ambiguity_rather_than_guessing():
    from apexa_provider_openai import ProviderUnavailable
    p = _provider_with_served("claudeopus46",
                              served=["argo:claude-4.6-opus", "argo:claude-6.4-opus"])
    with pytest.raises(ProviderUnavailable) as e:
        asyncio.run(p._resolve_model())
    assert "ambiguous" in str(e.value)


# ── LLM endpoint presets (multi-user / ALCF) ─────────────────────────────────

def test_all_presets_resolve_or_reject_explicitly(monkeypatch):
    from apexa_llm_endpoints import PRESETS, active_endpoint, EndpointRejected
    monkeypatch.delenv("APEXA_LLM_BASE_URL", raising=False)
    for name in PRESETS:
        monkeypatch.setenv("APEXA_LLM_PRESET", name)
        try:
            assert active_endpoint().base_url.startswith("http")
        except EndpointRejected as e:
            assert "tool calling" in str(e)          # only legitimate rejection reason


def test_minerva_usable_despite_missing_T_flag(monkeypatch):
    """Metis is DOCUMENTED as no-tool-calling; Minerva merely lacked a T flag.

    Those are different epistemic states, and treating them the same would have
    wrongly excluded Minerva. Measured 2026-08-18: both ``inkling-bf16`` and
    ``nemotron-3-ultra`` passed full multi-turn tool CHAINING in ~3.8 s — the
    fastest qualified endpoint. ALCF's flag table is incomplete for this cluster,
    so the preset must stay enabled even though the docs show no T.
    """
    from apexa_llm_endpoints import active_endpoint
    monkeypatch.delenv("APEXA_LLM_BASE_URL", raising=False)
    monkeypatch.setenv("APEXA_LLM_PRESET", "alcf-minerva")
    ep = active_endpoint()                     # must NOT raise
    assert "minerva" in ep.base_url
    assert ep.tool_calling is True
    assert "CHAIN" in ep.notes.upper()         # records the measurement, not a guess


def test_alcf_candidate_table_is_coherent():
    from apexa_llm_endpoints import ALCF_CANDIDATES, PRESETS
    assert ALCF_CANDIDATES, "candidate table must not be empty"
    for c in ALCF_CANDIDATES:
        assert c["preset"] in PRESETS, f"unknown preset {c['preset']}"
        assert c["model"] and c["flags"]
    models = [c["model"] for c in ALCF_CANDIDATES]
    assert len(models) == len(set(models)), "duplicate model in candidate table"
    # The models the flags say are the best fit must actually be present.
    assert "openai/gpt-oss-120b" in models          # only B,R,T,H model
    assert "inkling-bf16" in models                 # the unconfirmed one worth probing
    # Anything with no tool calling at all must NOT be offered as a candidate.
    assert not any("AuroraGPT" in m or "Devstral" in m for m in models)


def test_metis_rejected_because_no_tool_calling(monkeypatch):
    """APEXA has no non-tool mode; Metis must fail at config time, not first query."""
    from apexa_llm_endpoints import active_endpoint, EndpointRejected
    monkeypatch.setenv("APEXA_LLM_PRESET", "alcf-metis")
    with pytest.raises(EndpointRejected) as e:
        active_endpoint()
    assert "alcf-sophia" in str(e.value)


def test_unknown_preset_lists_valid_choices(monkeypatch):
    from apexa_llm_endpoints import active_endpoint, EndpointRejected
    monkeypatch.setenv("APEXA_LLM_PRESET", "gemma-somewhere")
    with pytest.raises(EndpointRejected) as e:
        active_endpoint()
    assert "alcf-sophia" in str(e.value) and "argo-proxy" in str(e.value)


def test_base_url_env_overrides_preset(monkeypatch):
    from apexa_llm_endpoints import active_endpoint
    monkeypatch.setenv("APEXA_LLM_PRESET", "custom")
    monkeypatch.setenv("APEXA_LLM_BASE_URL", "http://gpu01:8000/v1")
    assert active_endpoint().base_url == "http://gpu01:8000/v1"


def test_credential_precedence(monkeypatch):
    """APEXA_LLM_API_KEY beats everything; ANL username is the argo-proxy default."""
    from apexa_llm_endpoints import ArgoProxyEndpoint
    monkeypatch.delenv("APEXA_LLM_TOKEN_CMD", raising=False)
    ep = ArgoProxyEndpoint()
    monkeypatch.delenv("APEXA_LLM_API_KEY", raising=False)
    assert ep.resolve_key("anluser") == "anluser"
    monkeypatch.setenv("APEXA_LLM_API_KEY", "sk-personal")
    assert ep.resolve_key("anluser") == "sk-personal"


def test_token_command_supplies_and_caches_expiring_credential(monkeypatch, tmp_path):
    """ALCF Globus tokens expire (48h); the key must come from a refreshing helper."""
    import apexa_llm_endpoints as E
    monkeypatch.delenv("APEXA_LLM_API_KEY", raising=False)
    E._token_cache.clear()
    counter = tmp_path / "n"
    counter.write_text("0")
    script = tmp_path / "tok.sh"
    script.write_text(
        f'#!/bin/sh\nn=$(cat {counter}); n=$((n+1)); echo $n > {counter}; echo "tok-$n"\n')
    script.chmod(0o755)
    monkeypatch.setenv("APEXA_LLM_TOKEN_CMD", f"sh {script}")
    ep = E.ALCFSophiaEndpoint()
    assert ep.resolve_key("anluser") == "tok-1"
    assert ep.resolve_key("anluser") == "tok-1"      # cached, helper not re-run
    assert counter.read_text().strip() == "1"


def test_failing_token_command_is_actionable(monkeypatch):
    import apexa_llm_endpoints as E
    monkeypatch.delenv("APEXA_LLM_API_KEY", raising=False)
    E._token_cache.clear()
    monkeypatch.setenv("APEXA_LLM_TOKEN_CMD", "sh -c 'exit 3'")
    with pytest.raises(E.EndpointRejected) as e:
        E.ALCFSophiaEndpoint().resolve_key("u")
    assert "inference_auth_token" in str(e.value)


def test_provider_picks_up_refreshed_token_without_restart(monkeypatch, tmp_path):
    """A long beamline session must survive a token refresh mid-experiment."""
    import apexa_llm_endpoints as E
    from apexa_provider_openai import OpenAICompatProvider
    monkeypatch.delenv("APEXA_LLM_API_KEY", raising=False)
    E._token_cache.clear()
    monkeypatch.setenv("APEXA_LLM_PRESET", "alcf-sophia")
    monkeypatch.setenv("APEXA_LLM_TOKEN_CMD", "echo first-token")

    p = OpenAICompatProvider.__new__(OpenAICompatProvider)
    p.username, p.model, p.url = "u", "m", "http://stub/v1"
    p._key = "first-token"

    class _Client:
        def __init__(self, k): self.k = k
        def with_options(self, api_key): return _Client(api_key)

    p._client = _Client("first-token")
    assert p._client_for_request().k == "first-token"

    E._token_cache.clear()
    monkeypatch.setenv("APEXA_LLM_TOKEN_CMD", "echo second-token")
    assert p._client_for_request().k == "second-token"
    assert p._key == "second-token"


# ── deletion permission gate (must survive the refactor) ─────────────────────

def test_delete_command_detection_unchanged():
    from argo_mcp_client import _is_delete_command
    assert _is_delete_command("rm -rf /data/scratch")
    assert _is_delete_command('bash -c "rm -rf /tmp/x"')
    assert _is_delete_command("ls; rm foo")
    assert not _is_delete_command("grep rm file.txt")
    assert not _is_delete_command("ls /var/rm")


def _client_with_fake_session():
    """APEXAClient wired just far enough to reach the deletion gate.

    ``execute_tool_call`` checks server connectivity *before* the gate, so a bare
    stub returns "not connected" and never exercises it — the session has to exist
    for the test to mean anything.
    """
    from argo_mcp_client import APEXAClient

    class _Session:
        async def call_tool(self, name, args):        # pragma: no cover
            raise AssertionError("tool must NOT be dispatched when the gate denies")

    client = APEXAClient.__new__(APEXAClient)
    client._tool_registry = {"run_command": "core"}
    client.sessions = {"core": _Session()}
    client.permission_callback = None
    client._busy_server = None
    return client


def test_delete_without_callback_fails_safe_deny():
    """No permission callback ⇒ deletion must be denied and NOT dispatched."""
    client = _client_with_fake_session()
    out = asyncio.run(client.execute_tool_call("run_command", {"command": "rm -rf /data/x"}))
    assert "not run" in out.lower() or "⛔" in out


def test_delete_denied_when_callback_says_no():
    client = _client_with_fake_session()

    async def _deny(tool_name, arguments, command):
        return False

    client.permission_callback = _deny
    out = asyncio.run(client.execute_tool_call("run_command", {"command": "rm -rf /data/x"}))
    assert "not run" in out.lower() or "⛔" in out


def test_delete_denied_when_callback_raises():
    """A crashing permission channel must fail SAFE, not fail open."""
    client = _client_with_fake_session()

    async def _boom(tool_name, arguments, command):
        raise RuntimeError("UI channel dropped")

    client.permission_callback = _boom
    out = asyncio.run(client.execute_tool_call("run_command", {"command": "rm -rf /data/x"}))
    assert "not run" in out.lower() or "⛔" in out
