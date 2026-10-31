"""Network-tier tests — the copland launch failure must not recur.

APEXA hung at startup on a beamline host with data + ANL-internal access but no
public internet: the knowledge-base pre-warm blocked on a HuggingFace HEAD
request. The existing ``try/except`` did not help, because a network hang is not
an exception — so these tests assert the *avoidance*, not the recovery.

No network is touched: everything here is env-var driven policy.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import apexa_network as N  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("APEXA_NETWORK", "APEXA_OFFLINE", "HF_HUB_OFFLINE",
              "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE", "APEXA_KB_PREWARM"):
        monkeypatch.delenv(k, raising=False)
    importlib.reload(N)
    yield


def test_default_tier_is_internal_not_web():
    """The default must assume the SMALLER network.

    Defaulting to web-optimistic is what caused the copland hang: a host that
    cannot reach the internet discovers it by blocking. Beamline hosts are the
    primary deployment, so they must be the safe default and web-capable hosts
    opt up.
    """
    assert N.tier() == N.INTERNAL
    assert not N.has_web()
    assert N.has_internal()


@pytest.mark.parametrize("value,expected", [
    ("web", N.WEB), ("internal", N.INTERNAL), ("data", N.DATA),
    ("WEB", N.WEB), ("  internal  ", N.INTERNAL), ("nonsense", N.INTERNAL),
])
def test_tier_parsing(monkeypatch, value, expected):
    monkeypatch.setenv("APEXA_NETWORK", value)
    assert N.tier() == expected


def test_legacy_offline_flag_caps_the_tier(monkeypatch):
    """APEXA_OFFLINE=1 predates this module and must keep working."""
    monkeypatch.setenv("APEXA_NETWORK", "web")
    monkeypatch.setenv("APEXA_OFFLINE", "1")
    assert N.tier() == N.INTERNAL
    assert not N.has_web()


def test_hf_hub_offline_also_caps_the_tier(monkeypatch):
    monkeypatch.setenv("APEXA_NETWORK", "web")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    assert N.tier() == N.INTERNAL


# ── the actual bug: startup must never block ─────────────────────────────────

def test_prewarm_blocked_below_web_tier(monkeypatch):
    """THE regression test. Pre-warm loads a ~700MB HF model; without the web and
    without a staged cache that call blocks and the MCP server never starts."""
    monkeypatch.setenv("APEXA_NETWORK", "internal")
    assert N.prewarm_allowed() is False
    monkeypatch.setenv("APEXA_NETWORK", "data")
    assert N.prewarm_allowed() is False


def test_prewarm_allowed_on_web_tier(monkeypatch):
    monkeypatch.setenv("APEXA_NETWORK", "web")
    assert N.prewarm_allowed() is True


def test_operator_can_opt_into_prewarm_with_staged_cache(monkeypatch):
    """Correct once the embedder cache really is staged on an offline host."""
    monkeypatch.setenv("APEXA_NETWORK", "internal")
    monkeypatch.setenv("APEXA_KB_PREWARM", "1")
    assert N.prewarm_allowed() is True


def test_operator_can_force_prewarm_off_even_on_web(monkeypatch):
    monkeypatch.setenv("APEXA_NETWORK", "web")
    monkeypatch.setenv("APEXA_KB_PREWARM", "0")
    assert N.prewarm_allowed() is False


def test_offline_env_is_applied_below_web_and_not_above(monkeypatch):
    monkeypatch.setenv("APEXA_NETWORK", "internal")
    assert N.apply_offline_env() is True
    import os
    assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert os.environ["TRANSFORMERS_OFFLINE"] == "1"

    monkeypatch.setenv("APEXA_NETWORK", "web")
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    assert N.apply_offline_env() is False
    assert "HF_HUB_OFFLINE" not in os.environ


# ── web-only tools ───────────────────────────────────────────────────────────

def test_web_only_tools_refused_below_web_tier(monkeypatch):
    monkeypatch.setenv("APEXA_NETWORK", "internal")
    for name in N.WEB_ONLY_TOOLS:
        reason = N.tool_unavailable_reason(name)
        assert reason and "public internet" in reason
        # Must tell the model not to fabricate — a blocked tool is a common
        # trigger for inventing the result it would have returned.
        assert "invent" in reason.lower()
    assert set(N.disabled_tools()) == set(N.WEB_ONLY_TOOLS)


def test_web_only_tools_allowed_on_web_tier(monkeypatch):
    monkeypatch.setenv("APEXA_NETWORK", "web")
    for name in N.WEB_ONLY_TOOLS:
        assert N.tool_unavailable_reason(name) is None
    assert tuple(N.disabled_tools()) == ()


def test_data_reduction_tools_are_never_network_gated(monkeypatch):
    """Everything that actually reduces data must work at `internal`."""
    monkeypatch.setenv("APEXA_NETWORK", "internal")
    for name in ("run_ff_hedm_full_workflow", "midas_integrate_series",
                 "midas_auto_calibrate", "list_directory", "run_command",
                 "run_remote_command", "read_grains_summary",
                 "build_detector_mask", "query_hedm_knowledge"):
        assert N.tool_unavailable_reason(name) is None, f"{name} must not be gated"


def test_rag_is_degraded_not_disabled(monkeypatch):
    """RAG needs the web only for the FIRST model download; with a staged cache
    it works offline, so it must never be hard-disabled."""
    monkeypatch.setenv("APEXA_NETWORK", "internal")
    assert "query_hedm_knowledge" not in N.disabled_tools()
    assert "query_hedm_knowledge" in N.WEB_DEGRADED


def test_describe_is_operator_readable(monkeypatch):
    monkeypatch.setenv("APEXA_NETWORK", "internal")
    d = N.describe()
    assert "internal" in d and "disabled" in d
    monkeypatch.setenv("APEXA_NETWORK", "data")
    assert "will not work" in N.describe()


# ── tool surface integration ─────────────────────────────────────────────────

def test_tool_surface_hides_web_tools_below_web_tier(monkeypatch):
    """Defence in depth: the chokepoint refuses them, the surface hides them so
    the model never tries in the first place."""
    import apexa_toolsurface as TS
    importlib.reload(TS)

    def schema(n):
        return {"type": "function",
                "function": {"name": n, "description": f"{n} tool",
                             "parameters": {"type": "object", "properties": {}}}}

    cat = [schema(n) for n in ("list_directory", "read_file", "write_file",
                               "get_file_info", "run_command", "run_remote_command",
                               "recommend_workflow", "query_hedm_knowledge",
                               "inspect_dataset_file", "fetch_cif_from_mp",
                               "get_bibtex")]

    monkeypatch.setenv("APEXA_NETWORK", "internal")
    names = {TS.tool_name(t) for t in TS.initial_surface(cat)}
    assert "fetch_cif_from_mp" not in names and "get_bibtex" not in names
    assert "list_directory" in names
    assert not any(h["name"] in N.WEB_ONLY_TOOLS
                   for h in TS.search(cat, "fetch a cif structure file"))

    monkeypatch.setenv("APEXA_NETWORK", "web")
    hits = {h["name"] for h in TS.search(cat, "fetch cif from materials project")}
    assert "fetch_cif_from_mp" in hits


def test_probe_returns_a_valid_tier_and_checks():
    """Bounded TCP probe; used only by setup_user.sh, never in the hot path."""
    detected, checks = N.probe(timeout=0.001)     # near-instant, likely all False
    assert detected in (N.WEB, N.INTERNAL, N.DATA)
    assert set(checks) == {"anl_internal", "alcf", "web"}


# ── self-host detection (the copland self-SSH bug) ───────────────────────────

def test_registry_match_on_own_host_runs_local(monkeypatch, tmp_path):
    """APEXA deployed ON the analysis host must NOT SSH to itself.

    Regression for a live failure: on copland, /gdata resolved via the registry to
    host 'copland', so the FF workflow dispatched over SSH into a loopback login
    shell that had no midas_env, and midas-pipeline was 'not found' despite being
    installed and on PATH in the dispatching process.
    """
    import json, socket, importlib
    import apexa_remote_exec as R

    me = socket.gethostname()
    reg = tmp_path / "hosts.json"
    # Registry shape matters: records live under a top-level "hosts" key.
    reg.write_text(json.dumps({
        "default_host": me,
        "hosts": {me: {"host": me, "data_roots": ["/gdata"],
                       "activate": "conda activate midas_env"}}}))
    monkeypatch.setenv("APEXA_REMOTE_HOSTS_FILE", str(reg))
    for fn in (R.load_remote_registry, R.resolve_host_for_path):
        if hasattr(fn, "cache_clear"):
            fn.cache_clear()

    d = R.decide_exec_host("/gdata/dm/1ID/2026/x/data.h5")
    assert d["is_remote"] is False, "must not SSH to the machine we are already on"
    assert "this machine" in d["reason"].lower()
    # The record must survive so the caller can still apply `activate` locally --
    # otherwise the local run fails with 'midas-pipeline: command not found'.
    assert (d.get("record") or {}).get("activate") == "conda activate midas_env"


def test_explicit_host_pointing_at_self_runs_local(monkeypatch):
    import socket
    import apexa_remote_exec as R
    d = R.decide_exec_host("/tmp/x.h5", host=socket.gethostname())
    assert d["is_remote"] is False and "this machine" in d["reason"].lower()


def test_genuinely_remote_host_still_routes_remote(monkeypatch):
    """The fix must not collapse real remote routing."""
    import apexa_remote_exec as R
    d = R.decide_exec_host("/tmp/x.h5", host="some-other-beamline-host")
    assert d["is_remote"] is True


def test_hostname_aliases_are_honoured(monkeypatch):
    import apexa_remote_exec as R
    monkeypatch.setenv("APEXA_LOCAL_HOSTNAMES", "copland,copland.aps.anl.gov")
    assert R.is_local_host("copland")
    assert R.is_local_host("COPLAND.aps.anl.gov")
    assert not R.is_local_host("chiltepin")


# ── run_remote_command: the self-target collapse ─────────────────────────────
# APEXA deployed ON the analysis host resolved $APEXA_ANALYSIS_HOST to itself and
# SSHed loopback, into a login shell without the MIDAS activation -- so a binary
# plainly on PATH came back `command not found`. is_local_host() already covered
# the typed MIDAS tools; run_remote_command was the path left uncovered.

def _rrc(monkeypatch, host_env, **kw):
    """Call run_remote_command with SSH stubbed, and report which path ran."""
    import asyncio, json, socket
    import beamline_core_server as S

    calls = {}
    import subprocess
    original = S.run_subprocess
    async def stub_ssh(command, **kwargs):
        if isinstance(command, list) and command[0] == "ssh":
            calls["ssh"] = True
            return subprocess.CompletedProcess(command, 0, "via-ssh", "")
        return await original(command, **kwargs)
    monkeypatch.setattr(S, "run_subprocess", stub_ssh)
    monkeypatch.setenv("APEXA_ALLOW_UNSANDBOXED_COMMANDS", "1")
    monkeypatch.setenv("APEXA_ANALYSIS_HOST", host_env)
    fn = getattr(S.run_remote_command, "fn", S.run_remote_command)
    out = json.loads(asyncio.run(fn(command="echo hi", **kw)))
    return out, ("ssh" in calls)


def test_self_target_runs_locally_instead_of_loopback_ssh(monkeypatch):
    import socket
    out, used_ssh = _rrc(monkeypatch, socket.gethostname())
    assert used_ssh is False, "SSHed to the machine it is already running on"
    assert out["executed_locally"] is True
    assert out["stdout"].strip() == "hi"
    assert "run_command" in out["note"]          # points at the right tool


def test_a_real_remote_host_still_goes_over_ssh(monkeypatch):
    """The collapse must be narrow -- remote execution is the tool's whole job."""
    out, used_ssh = _rrc(monkeypatch, "some-other-host")
    assert used_ssh is True
    assert out.get("executed_locally") is None
    assert out["stdout"] == "via-ssh"


def test_explicit_remote_host_argument_still_goes_over_ssh(monkeypatch):
    import socket
    out, used_ssh = _rrc(monkeypatch, socket.gethostname(), host="chiltepin")
    assert used_ssh is True, "an explicitly named remote host must not run locally"


def test_force_ssh_takes_the_loopback_path_on_purpose(monkeypatch):
    """Escape hatch: verifying ssh-copy-id actually worked needs the real path."""
    import socket
    out, used_ssh = _rrc(monkeypatch, socket.gethostname(), force_ssh=True)
    assert used_ssh is True
    assert out.get("executed_locally") is None


def test_the_allowlist_is_still_applied_before_running_locally(monkeypatch):
    """The guard sits after the allowlist -- a blocked command must not run."""
    import asyncio, json, socket
    import beamline_core_server as S
    monkeypatch.setenv("APEXA_ANALYSIS_HOST", socket.gethostname())
    fn = getattr(S.run_remote_command, "fn", S.run_remote_command)
    out = json.loads(asyncio.run(fn(command="definitely_not_a_real_binary --x")))
    assert "error" in out and "not allowed" in out["error"].lower()


# ── Argo's native OpenAI-compatible endpoint (Oct-2026) ──────────────────────
def test_argo_native_is_the_default_preset():
    """The sidecar is no longer required: Argo serves /v1/chat/completions
    itself, so the default should not depend on a separate process."""
    import apexa_llm_endpoints as E
    assert E.DEFAULT_PRESET == "argo"
    ep = E.PRESETS["argo"]()
    assert ep.base_url == "https://apps.inside.anl.gov/argoapi/v1"
    assert ep.wire == "openai" and ep.tool_calling is True


def test_the_sidecar_preset_is_gone_but_its_name_still_starts(monkeypatch, capsys):
    """argo-proxy was removed once Argo served the API itself. An old .env that
    still names it must keep starting, on the native endpoint."""
    import apexa_llm_endpoints as E
    assert "argo-proxy" not in E.PRESETS
    monkeypatch.delenv("APEXA_LLM_BASE_URL", raising=False)
    monkeypatch.setenv("APEXA_LLM_PRESET", "argo-proxy")
    ep = E.active_endpoint()
    assert ep.name == "argo" and "retired" in capsys.readouterr().err


@pytest.mark.parametrize("url", ["http://127.0.0.1:44497/v1", "http://localhost:44497/v1",
                                 "http://localhost:44497"])
def test_a_leftover_sidecar_url_is_refused_with_the_fix(monkeypatch, url):
    """Left in .env it would override the native URL and fail to connect, which
    reads as 'Argo is down' rather than 'delete one line'."""
    import apexa_llm_endpoints as E
    monkeypatch.setenv("APEXA_LLM_PRESET", "argo")
    monkeypatch.setenv("APEXA_LLM_BASE_URL", url)
    with pytest.raises(E.EndpointRejected, match="retired argo-proxy"):
        E.active_endpoint()


def test_an_unrelated_local_port_is_not_mistaken_for_the_sidecar(monkeypatch):
    import apexa_llm_endpoints as E
    monkeypatch.setenv("APEXA_LLM_PRESET", "custom")
    monkeypatch.setenv("APEXA_LLM_BASE_URL", "http://127.0.0.1:8000/v1")
    assert E.active_endpoint().base_url == "http://127.0.0.1:8000/v1"


def test_argo_native_takes_the_anl_username_not_a_secret():
    import apexa_llm_endpoints as E
    ep = E.PRESETS["argo"]()
    assert ep.uses_keyring is False and not ep.env_key
    assert ep.resolve_key("s20hedm") == "s20hedm"


def test_the_native_profile_is_the_only_anl_profile():
    import apexa_profiles as P
    names = [p.name for p in P.BUILTIN_PROFILES]
    assert "anl" in names and "anl-service" not in names
    assert {p.name: p.preset for p in P.BUILTIN_PROFILES}["anl"] == "argo"


def test_the_anl_profile_uses_the_native_endpoint_on_a_clean_host(monkeypatch):
    """Regression: without llm_mode on the profile, a host whose .env did not
    already set APEXA_LLM_MODE=proxy got ArgoProvider -- the legacy /chat/ path
    this profile exists to replace -- with nothing in the banner to say so."""
    for k in ("APEXA_LLM_MODE", "APEXA_LLM_BASE_URL", "APEXA_LLM_PRESET",
              "APEXA_PROFILE", "APEXA_PROVIDER"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ANL_USERNAME", "someone")
    import apexa_profiles as P, apexa_agents as A
    P.apply({p.name: p for p in P.BUILTIN_PROFILES}["anl"])
    prov = A.select_provider("someone", "gpt55")
    assert type(prov).__name__ == "OpenAICompatProvider"
