"""Profile resolution — the switch a user actually flips.

The bug class these guard against is a *silently wrong gateway*: a profile that
resolves to the facility when the user meant their own key (or worse, the other
way round, quietly sending beamline paths to a third party). So the tests pin
precedence, the credential gate, and the wire each preset speaks.
"""
import json

import pytest

import apexa_llm_endpoints as E
import apexa_profiles as P


@pytest.fixture(autouse=True)
def isolated_apexa_dir(tmp_path, monkeypatch):
    """Never touch the developer's real ~/.apexa while testing."""
    monkeypatch.setattr(P, "APEXA_DIR", tmp_path)
    monkeypatch.setattr(P, "PROFILES_TOML", tmp_path / "profiles.toml")
    monkeypatch.setattr(P, "STATE_JSON", tmp_path / "state.json")
    monkeypatch.setattr(P, "NETCACHE_JSON", tmp_path / "netcache.json")
    for var in ("APEXA_PROFILE", "APEXA_LLM_PRESET", "APEXA_LLM_BASE_URL",
                "APEXA_LLM_MODE", "APEXA_LLM_API_KEY", "ARGO_MODEL"):
        monkeypatch.delenv(var, raising=False)
    yield


def _all_reachable(monkeypatch):
    monkeypatch.setattr(P, "probe_cached",
                        lambda **kw: {P.ANL: True, P.ALCF: True, P.WEB: True})


# ── precedence ───────────────────────────────────────────────────────────────

def test_env_beats_saved_choice(monkeypatch):
    P.set_choice("alcf")
    monkeypatch.setenv("APEXA_PROFILE", "anl-service")
    prof, how = P.resolve_active()
    assert (prof.name, how) == ("anl-service", "env")


def test_saved_choice_beats_auto(monkeypatch):
    _all_reachable(monkeypatch)
    P.set_choice("gemini-personal")
    prof, how = P.resolve_active()
    assert (prof.name, how) == ("gemini-personal", "saved")


def test_auto_is_the_fallback(monkeypatch):
    """Auto-selection lands on `anl` — Argo's native OpenAI endpoint — not on
    `anl-service`, which needs an argo-proxy sidecar running. A host with no
    sidecar used to auto-select a profile that could not connect."""
    _all_reachable(monkeypatch)
    monkeypatch.setenv("ANL_USERNAME", "someone")
    prof, how = P.resolve_active()
    assert how == "auto" and prof.name == "anl"
    assert prof.preset == "argo"


def test_unknown_env_profile_is_refused_not_guessed(monkeypatch):
    with pytest.raises(E.EndpointRejected) as e:
        monkeypatch.setenv("APEXA_PROFILE", "nope")
        P.resolve_active()
    assert "nope" in str(e.value)


def test_cleared_choice_falls_back_to_auto(monkeypatch):
    _all_reachable(monkeypatch)
    monkeypatch.setenv("ANL_USERNAME", "someone")
    P.set_choice("alcf")
    P.clear_choice()
    _, how = P.resolve_active()
    assert how == "auto"


# ── the credential gate ──────────────────────────────────────────────────────

def test_auto_skips_a_profile_with_no_credential(monkeypatch):
    """Reachable is not enough.

    Without this, a user who never stored a key would be auto-selected onto
    api.anthropic.com and only discover it at the first query.
    """
    _all_reachable(monkeypatch)
    monkeypatch.delenv("ANL_USERNAME", raising=False)
    monkeypatch.setattr(E, "keyring_get", lambda account: "")
    monkeypatch.setattr(P, "has_credential",
                        lambda p: p.name == "gemini-personal")
    assert P.auto_select().name == "gemini-personal"


def test_keyring_supplies_the_credential(monkeypatch):
    monkeypatch.setattr(E, "keyring_get", lambda account: "sk-stored"
                        if account == "anthropic" else "")
    assert P.has_credential(P.load_profiles()["anthropic-personal"])


def test_env_key_supplies_the_credential(monkeypatch):
    """MAG documents AMSC_TOKEN; a user who set it must not be asked again."""
    monkeypatch.setattr(E, "keyring_get", lambda account: "")
    monkeypatch.setenv("AMSC_TOKEN", "pat-value")
    mag = E.PRESETS["mag"]()
    assert mag.has_credential() and mag.resolve_key() == "pat-value"


def test_no_credential_means_no_selection(monkeypatch):
    _all_reachable(monkeypatch)
    monkeypatch.delenv("ANL_USERNAME", raising=False)
    monkeypatch.setattr(P, "has_credential", lambda p: False)
    assert P.auto_select() is None
    assert "none usable" in P.describe(None)


# ── user-defined profiles ────────────────────────────────────────────────────

def test_toml_overlays_a_builtin_field_by_field(monkeypatch):
    P.PROFILES_TOML.write_text(
        '[profile.institutional]\n'
        'base_url = "https://gw.example.edu/v1"\n'
        'model = "local-llm"\n', encoding="utf-8")
    inst = P.load_profiles()["institutional"]
    assert inst.base_url == "https://gw.example.edu/v1"
    assert inst.model == "local-llm"
    assert inst.external is True          # untouched fields survive the overlay


def test_toml_can_define_a_new_profile():
    P.PROFILES_TOML.write_text(
        '[profile.mylab]\npreset = "custom"\nrequires = "web"\n', encoding="utf-8")
    assert P.load_profiles()["mylab"].preset == "custom"


def test_malformed_toml_does_not_strand_the_operator():
    P.PROFILES_TOML.write_text("this is not toml {{{", encoding="utf-8")
    assert "anl-service" in P.load_profiles()      # built-ins still available


# ── applying a profile ───────────────────────────────────────────────────────

def test_apply_sets_what_the_rest_of_apexa_reads(monkeypatch):
    P.apply(P.load_profiles()["gemini-personal"])
    import os
    assert os.environ["APEXA_LLM_PRESET"] == "gemini"
    assert os.environ["ARGO_MODEL"] == "gemini-2.5-flash"
    assert os.environ["APEXA_ACTIVE_PROFILE"] == "gemini-personal"


def test_facility_profile_does_not_touch_llm_mode(monkeypatch):
    """Regression guard: anl-service must not change an existing install.

    It deliberately carries llm_mode=None so a site that runs the legacy Argo
    transport keeps running it after profiles ship.
    """
    monkeypatch.setenv("APEXA_LLM_MODE", "argo")
    P.apply(P.load_profiles()["anl-service"])
    import os
    assert os.environ["APEXA_LLM_MODE"] == "argo"


# ── reachability cache ───────────────────────────────────────────────────────

def test_cached_probe_is_not_re_run(monkeypatch):
    P.NETCACHE_JSON.write_text(json.dumps(
        {"at": __import__("time").time(), "checks": {P.ANL: True}}), encoding="utf-8")

    def explode():                       # a probe here would mean a startup hang
        raise AssertionError("probe_cached must not probe while the cache is warm")
    monkeypatch.setattr("apexa_network.probe", explode)
    assert P.probe_cached()[P.ANL] is True


def test_stale_cache_triggers_a_fresh_probe(monkeypatch):
    P.NETCACHE_JSON.write_text(json.dumps(
        {"at": 0, "checks": {P.ANL: False}}), encoding="utf-8")
    monkeypatch.setattr("apexa_network.probe", lambda: ("web", {P.WEB: True}))
    assert P.probe_cached()[P.WEB] is True


# ── wires (regression guards for the Anthropic preset) ───────────────────────

def test_anthropic_is_native_wire_not_openai():
    """It used to be an OpenAIEndpoint pointed at api.anthropic.com/v1.

    That endpoint has no /v1/chat/completions, so the preset could never have
    worked. Pin both halves of the fix.
    """
    ep = E.PRESETS["anthropic"]()
    assert ep.wire == "anthropic"
    assert ep.base_url == "https://api.anthropic.com"   # host root, no /v1


def test_gemini_rides_the_openai_compat_layer():
    ep = E.PRESETS["gemini"]()
    assert ep.wire == "openai"
    assert ep.base_url.endswith("/v1beta/openai")


def test_facility_presets_never_read_the_keyring():
    """A stray stored secret must not shadow the ANL-username convention."""
    for name in ("argo-proxy", "alcf-sophia", "alcf-minerva", "ollama"):
        assert E.PRESETS[name]().uses_keyring is False


def test_every_builtin_profile_names_a_real_preset():
    for prof in P.BUILTIN_PROFILES:
        assert prof.preset in E.PRESETS, prof.name
