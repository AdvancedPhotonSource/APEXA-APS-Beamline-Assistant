"""Connection profiles — one name that carries a whole LLM configuration.

Why this exists
---------------
Taking APEXA outside ANL used to mean hand-editing three independent
environment variables that must agree: ``APEXA_LLM_MODE`` (which provider class
runs), ``APEXA_LLM_PRESET`` (which URL and credential) and ``ARGO_MODEL`` (a
model id whose names are Argo-specific and meaningless at api.anthropic.com).
Any one of them wrong fails confusingly — Argo 404s on an ALCF model, or
the loop silently drops to the fabrication-prone text protocol.

A **profile** bundles all of it behind a single name, so the combination can
never be set inconsistently:

    preset + model + network requirement + credential source

Resolution order, highest wins:

1. ``APEXA_PROFILE``                      — explicit, for scripts and CI
2. the profile last chosen in the CLI     — ``~/.apexa/state.json``
3. auto-detection                         — first profile that is both
                                            reachable and has a credential

Auto-detection and the no-blocking rule
---------------------------------------
``apexa_network`` exists because a startup network probe once hung APEXA on a
beamline host with no route out: a hang is not an exception, so the ``try/except``
around it never fired. That lesson holds here. Auto-detection therefore uses
``apexa_network.probe()`` — short, bounded TCP connects, no HTTP, no retries —
and then **caches the verdict** in ``~/.apexa/netcache.json``. Startup reads the
cache; only an explicit ``profile refresh`` (or an expired TTL) pays for a probe.

Data governance
---------------
A profile knows whether it is ``external`` — i.e. whether inference leaves ANL.
The CLI banners that, because the risk of seamless switching is a user who does
not notice that beamline paths are now going to a third party.
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

from apexa_llm_endpoints import PRESETS, EndpointRejected
# Deliberately reusing apexa_network's connect helper rather than copying it:
# one implementation means one timeout policy and one set of semantics for
# "reachable", which is the whole point of that module.
from apexa_network import _can_connect

try:                      # stdlib on 3.11+
    import tomllib
except ModuleNotFoundError:   # pragma: no cover - 3.10 and older
    tomllib = None          # type: ignore[assignment]


APEXA_DIR     = Path.home() / ".apexa"
PROFILES_TOML = APEXA_DIR / "profiles.toml"
STATE_JSON    = APEXA_DIR / "state.json"
NETCACHE_JSON = APEXA_DIR / "netcache.json"

#: Long enough that a normal session never re-probes, short enough that moving
#: between the beamline and a hotel is noticed without an explicit refresh.
NETCACHE_TTL_S = 900

#: Reachability keys. ``none`` always passes; ``local`` is resolved against the
#: preset's own host:port rather than a fixed hostname.
ANL, ALCF, WEB, LOCAL, NONE = "anl_internal", "alcf", "web", "local", "none"


@dataclass
class Profile:
    """One switchable LLM configuration."""

    name: str
    preset: str
    #: "" ⇒ fall back to the preset's ``default_model``.
    model: str = ""
    #: Reachability this profile needs before auto-selection will pick it.
    requires: str = ANL
    #: Override the preset's URL (the institutional case). "" ⇒ preset default.
    base_url: str = ""
    #: ``APEXA_LLM_MODE`` to force. **None means "do not touch"**, which is what
    #: keeps the facility profile byte-identical to today's behaviour for users
    #: who already set the mode in their .env.
    llm_mode: Optional[str] = None
    #: True ⇒ inference leaves ANL. Drives the startup banner.
    external: bool = False
    notes: str = ""


# Ordered by auto-selection preference: facility resources first (governed, and
# already paid for), then an institutional gateway, then personal keys, with the
# local model last — it always works, so anything above it should win.
BUILTIN_PROFILES: List[Profile] = [
    # llm_mode="proxy" is what makes this profile use the endpoint at all:
    # select_provider() only builds OpenAICompatProvider in proxy mode, and the
    # global default is still "argo". Without it, a host whose .env does not
    # already say proxy silently got the legacy /chat/ provider instead.
    Profile("anl", "argo", requires=ANL, llm_mode="proxy",
            notes="Beamline default. Argo's native OpenAI endpoint — no sidecar."),
    Profile("alcf", "alcf-minerva", model="inkling-bf16", requires=ALCF,
            llm_mode="proxy",
            notes="ALCF Inference Service, per-user Globus identity. On-prem DOE hardware."),
    Profile("mag", "mag", requires=WEB, external=True,
            notes="AmSC Model Access Gateway (DOE). Anthropic wire, AMSC_TOKEN."),
    Profile("institutional", "custom", requires=WEB, llm_mode="proxy", external=True,
            notes="Another institution's OpenAI-compatible gateway. Set base_url."),
    Profile("anthropic-personal", "anthropic", requires=WEB, external=True,
            notes="api.anthropic.com with a personal key. Native /v1/messages wire."),
    Profile("openai-personal", "openai", requires=WEB, llm_mode="proxy", external=True,
            notes="api.openai.com with a personal key."),
    Profile("gemini-personal", "gemini", requires=WEB, llm_mode="proxy", external=True,
            notes="Gemini via Google's OpenAI-compatibility layer."),
    Profile("local", "ollama", requires=LOCAL, llm_mode="proxy",
            notes="A model on this machine. Nothing leaves the host."),
]


# ── user-defined profiles ────────────────────────────────────────────────────

def load_profiles() -> Dict[str, Profile]:
    """Built-ins overlaid with ``~/.apexa/profiles.toml``.

    A user entry with a built-in's name overrides it field by field, so someone
    can point ``institutional`` at their own gateway without restating the rest.
    A malformed file is reported and skipped rather than taking APEXA down — a
    broken config should not strand an operator mid-experiment.
    """
    out: Dict[str, Profile] = {p.name: p for p in BUILTIN_PROFILES}
    if not PROFILES_TOML.exists() or tomllib is None:
        return out
    try:
        raw = tomllib.loads(PROFILES_TOML.read_text(encoding="utf-8"))
    except Exception as e:                       # noqa: BLE001 - report, continue
        print(f"  ⚠ {PROFILES_TOML} is not valid TOML ({e}); using built-ins only.")
        return out
    for name, body in (raw.get("profile") or {}).items():
        if not isinstance(body, dict):
            continue
        fields = {k: v for k, v in body.items() if k in Profile.__dataclass_fields__}
        fields.pop("name", None)
        base = out.get(name)
        try:
            out[name] = replace(base, **fields) if base else Profile(name=name, **fields)
        except TypeError as e:
            print(f"  ⚠ profile {name!r} in {PROFILES_TOML}: {e}; skipped.")
    return out


# ── remembered choice ────────────────────────────────────────────────────────

def _read_state() -> dict:
    try:
        return json.loads(STATE_JSON.read_text(encoding="utf-8"))
    except Exception:
        return {}


def saved_choice() -> str:
    return str(_read_state().get("profile") or "").strip()


def set_choice(name: str) -> None:
    APEXA_DIR.mkdir(parents=True, exist_ok=True)
    state = _read_state()
    state["profile"] = name
    STATE_JSON.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def clear_choice() -> None:
    state = _read_state()
    if state.pop("profile", None) is not None:
        STATE_JSON.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


# ── reachability, cached ─────────────────────────────────────────────────────

def probe_cached(force: bool = False, ttl: int = NETCACHE_TTL_S) -> Dict[str, bool]:
    """Reachability checks, from cache unless stale or forced.

    The cache is what keeps startup non-blocking; see the module docstring.
    """
    if not force:
        try:
            blob = json.loads(NETCACHE_JSON.read_text(encoding="utf-8"))
            if (time.time() - float(blob.get("at", 0))) < ttl:
                return {k: bool(v) for k, v in (blob.get("checks") or {}).items()}
        except Exception:
            pass
    from apexa_network import probe          # imported late: probe() opens sockets
    _, checks = probe()
    try:
        APEXA_DIR.mkdir(parents=True, exist_ok=True)
        NETCACHE_JSON.write_text(
            json.dumps({"at": time.time(), "checks": checks}, indent=2) + "\n",
            encoding="utf-8")
    except Exception:
        pass                                  # a read-only home must not be fatal
    return checks


def _local_reachable(profile: Profile) -> bool:
    url = profile.base_url or PRESETS[profile.preset]().base_url
    u = urlparse(url)
    return _can_connect(u.hostname or "127.0.0.1", u.port or 80, timeout=1.0)


def is_reachable(profile: Profile, checks: Dict[str, bool]) -> bool:
    if profile.requires == NONE:
        return True
    if profile.requires == LOCAL:
        return _local_reachable(profile)
    return bool(checks.get(profile.requires))


def has_credential(profile: Profile) -> bool:
    """Whether the profile's endpoint can produce a credential unprompted."""
    try:
        ep = PRESETS[profile.preset]()
    except KeyError:
        return False
    return ep.has_credential(os.environ.get("ANL_USERNAME", ""))


# ── resolution ───────────────────────────────────────────────────────────────

def auto_select(force_probe: bool = False) -> Optional[Profile]:
    """First profile that is reachable AND has a credential, in preference order.

    Requiring the credential matters: without it auto-detection would happily
    pick ``anthropic-personal`` for a user who never set a key, and the failure
    would surface at the first query instead of at selection.
    """
    checks   = probe_cached(force=force_probe)
    profiles = load_profiles()
    order    = [p.name for p in BUILTIN_PROFILES]
    order   += [n for n in profiles if n not in order]
    for name in order:
        p = profiles.get(name)
        if p and is_reachable(p, checks) and has_credential(p):
            return p
    return None


#: Profiles that used to exist, mapped to their replacement, so an old .env or a
#: saved choice keeps starting instead of failing on "unknown profile".
LEGACY_PROFILES = {"anl-service": "anl"}


def _modern(name: str) -> str:
    if name in LEGACY_PROFILES:
        print(f"[apexa] profile {name!r} was retired (it used the argo-proxy "
              f"sidecar); using {LEGACY_PROFILES[name]!r}.", file=sys.stderr)
        return LEGACY_PROFILES[name]
    return name


def resolve_active(force_probe: bool = False) -> Tuple[Optional[Profile], str]:
    """The active profile and a one-word reason, for the startup banner."""
    profiles = load_profiles()

    env = _modern((os.environ.get("APEXA_PROFILE") or "").strip())
    if env:
        p = profiles.get(env)
        if p is None:
            raise EndpointRejected(
                f"unknown APEXA_PROFILE {env!r}. Choose one of: "
                f"{', '.join(sorted(profiles))}, or define it in {PROFILES_TOML}.")
        return p, "env"

    saved = _modern(saved_choice())
    if saved and saved in profiles:
        return profiles[saved], "saved"

    auto = auto_select(force_probe=force_probe)
    return (auto, "auto") if auto else (None, "none")


def apply(profile: Profile) -> None:
    """Export the env vars the rest of APEXA already reads.

    Deliberately works through the existing variables rather than introducing a
    parallel configuration path: every consumer (``active_endpoint``,
    ``select_provider``, ``argo_mcp_client``) keeps reading exactly what it reads
    today, so profiles are a front end, not a fork.
    """
    os.environ["APEXA_LLM_PRESET"] = profile.preset
    if profile.base_url:
        os.environ["APEXA_LLM_BASE_URL"] = profile.base_url
    # An operator who explicitly chose the legacy Argo transport keeps it on the
    # Argo profile: that choice is only meaningful there (the legacy transport
    # reaches nothing but Argo), and silently switching an existing install's
    # transport is what this guard has always existed to prevent. Every other
    # profile needs the OpenAI-compatible path, so its mode is applied as-is.
    explicit_legacy = (os.environ.get("APEXA_LLM_MODE") or "").strip().lower() == "argo"
    if profile.llm_mode and not (explicit_legacy and profile.preset == "argo"):
        os.environ["APEXA_LLM_MODE"] = profile.llm_mode
    model = profile.model or PRESETS[profile.preset]().default_model
    if model:
        os.environ["ARGO_MODEL"] = model
    os.environ["APEXA_ACTIVE_PROFILE"] = profile.name


def describe(profile: Optional[Profile], how: str = "") -> str:
    if profile is None:
        return ("profile: none usable — no reachable endpoint has a credential. "
                "Run `profile list` to see why.")
    ep    = PRESETS[profile.preset]()
    model = profile.model or ep.default_model or os.environ.get("ARGO_MODEL", "?")
    url   = profile.base_url or ep.base_url
    tail  = f" (by {how})" if how else ""
    flag  = "  ⚠ EXTERNAL — inference leaves ANL" if profile.external else ""
    return f"profile: {profile.name}{tail} → {model} @ {url}{flag}"
