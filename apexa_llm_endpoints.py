"""Typed LLM endpoint presets for APEXA.

Follows the pattern used by the sister APS framework
[EAA](https://github.com/AdvancedPhotonSource/EAA)
(`packages/eaa-core/src/eaa_core/api/llm_config.py`): a base OpenAI-compatible
config that vendor presets subclass and merely override defaults on. Typed
dataclasses beat env-var soup — the valid combinations are discoverable, and a
preset can carry the operational facts that matter (does it support tool calling?
how is it authenticated? does the token expire?).

APEXA extends the pattern in two ways EAA does not currently cover:

1. **An ALCF Inference Service preset.** ALCF is the answer to the multi-user
   question: it is OpenAI-compatible, on-prem DOE hardware, and authenticated with
   a **per-user Globus identity** — so each beamline user runs under their own
   credential rather than a shared beamline account.
2. **Lazy credential resolution.** ALCF access tokens expire (48 h, and a 30-day
   re-auth policy), so a key frozen at construction goes stale mid-experiment.
   Credentials resolve per request, optionally by shelling out to ALCF's own
   refreshing helper, with a short cache so it isn't invoked on every call.

A preset only sets *defaults*; any field can still be overridden by the matching
``APEXA_LLM_*`` environment variable, so an operator is never boxed in.

Selected with ``APEXA_LLM_PRESET``:

    argo-proxy    local argo-proxy sidecar (default)      ANL username as key
    alcf-sophia   ALCF Inference Service, Sophia cluster  Globus token, per-user
    alcf-metis    ALCF Metis cluster                      REJECTED: no tool calling
    openai        api.openai.com                          personal key (keyring)
    openai-responses  api.openai.com /v1/responses        REJECTED: no provider yet
    anthropic     api.anthropic.com (NATIVE wire)         personal key (keyring)
    gemini        Google OpenAI-compatibility layer       personal key (keyring)
    custom        anything OpenAI-compatible (vLLM, …)    personal key (keyring)
    ollama        a model on the user's own machine       none needed

Each preset also declares the ``wire`` it speaks, so ``select_provider()`` can
follow the endpoint rather than a separate env var that can disagree with it.
Only ``anthropic`` is not OpenAI-shaped; Gemini rides Google's compatibility
layer and so needs no provider of its own.

``apexa_profiles`` sits above this module and bundles a preset with a model, a
network tier, and a credential, so a user switches ONE name rather than three
environment variables that must agree.
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

# Cache shell-fetched tokens briefly: ALCF's helper does a Globus round trip, far
# too slow to run on every request, but tokens are short-lived so we must not
# cache them for long either.
_TOKEN_TTL_S = 600
_token_cache: dict[str, tuple[float, str]] = {}


def _default_token_cmd() -> str:
    """ALCF's Globus helper, invoked with the CURRENT interpreter.

    Hardcoding ``python`` breaks whenever the shell's ``python`` is not the
    interpreter APEXA is running under — a stale ``VIRTUAL_ENV`` from another repo
    is enough, and the failure looks like a missing ``globus_sdk`` rather than a
    path problem. ``sys.executable`` is unambiguous. Override with
    ``APEXA_LLM_TOKEN_CMD`` if the helper lives elsewhere.
    """
    script = os.environ.get("ALCF_AUTH_HELPER") or "inference_auth_token.py"
    return f"{shlex.quote(sys.executable)} {shlex.quote(script)} get_access_token"


# ── OS keyring (optional dependency) ─────────────────────────────────────────
# A personal API key must not go in ``.env``: on a beamline host that file lives
# on shared storage and lands in backups, and the same file is copied between
# users during setup. ``keyring`` routes to the macOS Keychain, Windows
# Credential Manager, or Secret Service instead.
#
# It is an OPTIONAL dependency and every call degrades to "no stored key", so an
# existing beamline install that never installed it keeps working unchanged on
# the ``APEXA_LLM_API_KEY`` path.
KEYRING_SERVICE = "apexa"


def keyring_available() -> bool:
    try:
        import keyring  # noqa: F401
    except Exception:
        return False
    return True


def keyring_get(account: str) -> str:
    """Stored secret for ``account``, or "" when absent/unavailable.

    Never raises: a locked, misconfigured, or missing backend must not take
    APEXA down mid-experiment — the caller falls through to the env var.
    """
    try:
        import keyring
    except Exception:
        return ""
    try:
        return (keyring.get_password(KEYRING_SERVICE, account) or "").strip()
    except Exception:
        return ""


def keyring_set(account: str, secret: str) -> bool:
    """Store a secret. Returns False when no backend is usable."""
    try:
        import keyring
    except Exception:
        return False
    try:
        keyring.set_password(KEYRING_SERVICE, account, secret)
        return True
    except Exception:
        return False


def keyring_delete(account: str) -> bool:
    try:
        import keyring
    except Exception:
        return False
    try:
        keyring.delete_password(KEYRING_SERVICE, account)
        return True
    except Exception:
        return False


class EndpointRejected(RuntimeError):
    """The endpoint cannot support APEXA (e.g. no tool calling)."""


@dataclass
class OpenAIEndpoint:
    """Base: any OpenAI-compatible ``/v1/chat/completions`` service."""

    name: str = "openai"
    base_url: str = "https://api.openai.com/v1"
    #: Static credential. When None, resolution falls back to token_cmd → ANL username.
    api_key: Optional[str] = None
    #: Shell command printing a fresh bearer token on stdout (for expiring creds).
    token_cmd: Optional[str] = None
    #: False ⇒ APEXA refuses the endpoint: structured tool calling is mandatory.
    tool_calling: bool = True
    #: False ⇒ no working GET /models. ALCF serves chat but 404s every listing
    #: route, so probing one just wastes a round trip and emits a scary warning
    #: for an entirely expected condition. Validate against ALCF_CANDIDATES instead.
    lists_models: bool = True
    #: Which HTTP protocol the endpoint speaks. ``openai`` is the OpenAI
    #: ``/v1/chat/completions`` shape (also what Google's compatibility layer and
    #: every self-hosted vLLM/Ollama serve); ``anthropic`` is the native
    #: ``/v1/messages`` shape, which is NOT OpenAI-compatible and needs its own
    #: provider. ``select_provider()`` dispatches on this, so the wire travels
    #: with the endpoint instead of living in a separate env var that can disagree.
    wire: str = "openai"
    #: True ⇒ the credential is a personal secret looked up in the OS keyring.
    #: Left False for facility endpoints so a stray stored key can never shadow
    #: the ANL-username or Globus-token conventions they rely on.
    uses_keyring: bool = False
    #: Endpoint-specific env var holding the credential (e.g. ``AMSC_TOKEN``).
    #: Checked before the keyring, so a gateway that already documents its own
    #: variable keeps working without the user re-entering the secret anywhere.
    env_key: str = ""
    #: How the ANTHROPIC wire presents the credential. ``x-api-key`` is what
    #: api.anthropic.com wants; gateways fronting the same wire generally want
    #: ``bearer``. Irrelevant on the openai wire, where the OpenAI client builds
    #: the Authorization header itself.
    auth_style: str = "x-api-key"
    #: Default model when the profile does not name one. Model ids are
    #: vendor-specific — an Argo id like ``claudeopus5`` means nothing to
    #: api.anthropic.com — so each preset carries its own.
    default_model: str = ""
    #: Glob proving a ``token_cmd`` credential has been obtained at least once.
    #: Lets ``has_credential()`` answer truthfully for expiring credentials
    #: without paying for the Globus round trip that ``token_cmd`` itself costs.
    token_file_glob: str = ""
    #: Operator-facing note surfaced at startup.
    notes: str = "Commercial OpenAI API — needs a personal API key and outbound egress."

    # ── credential resolution (lazy, so expiring tokens stay fresh) ─────────

    def resolve_key(self, username: str = "") -> str:
        """Credential for this request.

        Precedence: ``APEXA_LLM_API_KEY`` → OS keyring (personal endpoints only)
        → preset ``api_key`` → ``token_cmd`` output (cached) → ANL username (the
        argo-proxy convention).

        The env var stays first so an operator can always override without
        touching the keyring — the same "never boxed in" rule the presets follow.
        """
        explicit = (os.environ.get("APEXA_LLM_API_KEY") or "").strip()
        if explicit:
            return explicit
        if self.env_key:
            from_env = (os.environ.get(self.env_key) or "").strip()
            if from_env:
                return from_env
        if self.uses_keyring:
            stored = keyring_get(self.name)
            if stored:
                return stored
        if self.api_key:
            return self.api_key
        cmd = (os.environ.get("APEXA_LLM_TOKEN_CMD") or self.token_cmd or "").strip()
        if cmd:
            return self._token_from_cmd(cmd)
        return (username or "apexa").strip()

    def has_credential(self, username: str = "") -> bool:
        """Whether this endpoint can produce a credential without prompting.

        Used by profile auto-selection to skip an endpoint the user has not set
        up yet, rather than selecting it and failing at the first query. Never
        raises and never shells out — running ``token_cmd`` here would put a
        Globus round trip in the selection path. Instead, an endpoint that
        authenticates by token command is credentialled only if its token file
        already exists, which is a cheap stat and is the truthful answer: a user
        who has never run ``inference_auth_token.py authenticate`` has no ALCF
        credential, and auto-selection must not pick ALCF for them.
        """
        if (os.environ.get("APEXA_LLM_API_KEY") or "").strip():
            return True
        if self.env_key and (os.environ.get(self.env_key) or "").strip():
            return True
        if self.uses_keyring:
            return bool(keyring_get(self.name))
        if self.api_key:
            return True
        if (os.environ.get("APEXA_LLM_TOKEN_CMD") or self.token_cmd or "").strip():
            if self.token_file_glob:
                from glob import glob
                return bool(glob(os.path.expanduser(self.token_file_glob)))
            return True
        return bool((username or "").strip())

    @staticmethod
    def _token_from_cmd(cmd: str) -> str:
        now = time.monotonic()
        hit = _token_cache.get(cmd)
        if hit and (now - hit[0]) < _TOKEN_TTL_S:
            return hit[1]
        try:
            out = subprocess.run(shlex.split(cmd), capture_output=True, text=True,
                                 timeout=120, check=True).stdout.strip()
        except subprocess.CalledProcessError as e:
            raise EndpointRejected(
                f"token command failed ({cmd!r}): {(e.stderr or '').strip()[:200]}. "
                f"For ALCF, re-authenticate: `python inference_auth_token.py authenticate --force`."
            ) from e
        except (subprocess.TimeoutExpired, FileNotFoundError, ValueError) as e:
            raise EndpointRejected(f"token command unusable ({cmd!r}): {e}") from e
        if not out:
            raise EndpointRejected(f"token command {cmd!r} printed nothing.")
        # Tolerate helpers that print log lines before the token.
        token = out.splitlines()[-1].strip()
        _token_cache[cmd] = (now, token)
        return token

    def describe(self) -> str:
        return f"{self.name} → {self.base_url}"


@dataclass
class ArgoNativeEndpoint(OpenAIEndpoint):
    """Argo Gateway's own OpenAI-compatible surface. The preferred ANL path.

    Argo serves ``/v1/chat/completions`` directly as of the Oct-2026 update
    ("OpenAI-compatibility is now native to the Argo Gateway API"), so the
    argo-proxy sidecar is no longer required to get a structured tool-calling
    wire. That removes a whole deployment component from the beamline host and,
    with it, the sidecar's standing hazard: argo-proxy authenticates with a
    *username* and no secret, so a sidecar bound to anything but 127.0.0.1 is an
    unauthenticated LLM gateway under one person's identity.

    The credential is the ANL domain username in the api_key slot -- same
    convention as the proxy, so nothing about identity changes. Argo validates
    the username and caches that lookup for 24 h.

    Reachable from the ANL internal network or VPN only, which is why this is
    gated behind the ANL profile requirement rather than being a global default.
    """

    name: str = "argo"
    base_url: str = "https://apps.inside.anl.gov/argoapi/v1"
    tool_calling: bool = True
    #: Argo documents GET /v1/models as the live list of available LLMs.
    lists_models: bool = True
    notes: str = ("Argo Gateway, native OpenAI-compatible endpoint. No sidecar. "
                  "ANL internal network or VPN required; api_key is your ANL "
                  "domain username.")


@dataclass
class ArgoProxyEndpoint(OpenAIEndpoint):
    """Local argo-proxy sidecar fronting the Argo Gateway.

    Authenticates with the ANL **username** in the api_key slot — there is no
    secret, which is exactly why the sidecar must bind 127.0.0.1
    (see docs/ARGO_PROXY_DEPLOYMENT.md).
    """

    name: str = "argo-proxy"
    base_url: str = "http://127.0.0.1:44497/v1"
    tool_calling: bool = True
    notes: str = ("Argo via local sidecar. Shared ANL identity — inference is "
                  "attributed to whoever's username the proxy is configured with.")


@dataclass
class ALCFSophiaEndpoint(OpenAIEndpoint):
    """ALCF Inference Service, Sophia cluster (vLLM on A100).

    The multi-user answer: OpenAI-compatible, on-prem DOE hardware, and
    authenticated per user via Globus, so each beamline user runs under their own
    identity instead of a shared beamline account.

    Only models flagged **T** in the ALCF docs support tool calling — Llama 3.1/3.3/4,
    ``openai/gpt-oss-20b``/``-120b``, Gemma 3/4, Trinity, nemotron-3-super. APEXA is
    non-functional on a model without it.

    Cold starts run 10–15 min: only some nodes keep models hot and they unload after
    ~2 h idle. Prefer an always-hot (**H**) model for interactive beamline use.
    """

    name: str = "alcf-sophia"
    base_url: str = "https://inference-api.alcf.anl.gov/resource_server/sophia/vllm/v1"
    token_cmd: str = field(default_factory=_default_token_cmd)
    # Mirrors inference_auth_token.TOKENS_PATH (~/.globus/app/<client>/<app>/tokens.json).
    # Globbed rather than imported so this stays free of the optional globus-sdk.
    token_file_glob: str = "~/.globus/app/*/*/tokens.json"
    lists_models: bool = False
    tool_calling: bool = True
    notes: str = ("ALCF Inference Service (Sophia). Per-user Globus identity; tokens "
                  "expire after 48h and are refreshed by the token command. Use only "
                  "T-flagged (tool-calling) models. Cold start can be 10-15 min.")


@dataclass
class ALCFMetisEndpoint(OpenAIEndpoint):
    """ALCF Metis (SambaNova SN40L) — **unusable by APEXA**.

    ALCF documents that tool calling is not supported on Metis, with a known
    SambaNova sanitization bug producing ``Invalid function calling output.``
    Every APEXA capability is a tool call, so this is rejected up front rather
    than failing confusingly at the first query.
    """

    name: str = "alcf-metis"
    base_url: str = "https://inference-api.alcf.anl.gov/resource_server/metis/api/v1"
    token_cmd: str = field(default_factory=_default_token_cmd)
    # Mirrors inference_auth_token.TOKENS_PATH (~/.globus/app/<client>/<app>/tokens.json).
    # Globbed rather than imported so this stays free of the optional globus-sdk.
    token_file_glob: str = "~/.globus/app/*/*/tokens.json"
    lists_models: bool = False
    tool_calling: bool = False
    notes: str = "Metis does not support tool calling — use alcf-sophia instead."


@dataclass
class ALCFMinervaEndpoint(OpenAIEndpoint):
    """ALCF Minerva cluster (NVIDIA B200) — ``nemotron-3-ultra``, ``inkling-bf16``.

    Tool-calling status is **unconfirmed**, not denied: ALCF flags both models
    ``H`` (always hot) with no ``T``, but — unlike Metis, where the docs state
    outright that tool calling is unsupported — Minerva carries no such statement.
    So this preset is allowed through and left to be settled empirically by
    ``scripts/alcf_qualify_models.py``; if the probe fails, APEXA cannot use it.

    Both models being always-hot is attractive for interactive beamline use (no
    10-15 min cold start), which is exactly why it's worth actually measuring.
    """

    name: str = "alcf-minerva"
    base_url: str = "https://inference-api.alcf.anl.gov/resource_server/minerva/api/v1"
    token_cmd: str = field(default_factory=_default_token_cmd)
    # Mirrors inference_auth_token.TOKENS_PATH (~/.globus/app/<client>/<app>/tokens.json).
    # Globbed rather than imported so this stays free of the optional globus-sdk.
    token_file_glob: str = "~/.globus/app/*/*/tokens.json"
    lists_models: bool = False
    tool_calling: bool = True   # CONFIRMED by measurement (see notes)
    notes: str = ("ALCF Minerva (B200). Always-hot. ALCF publishes no T flag for "
                  "these models, but both inkling-bf16 and nemotron-3-ultra passed "
                  "full multi-turn tool CHAINING on 2026-08-18 (3.8-3.9 s) — the "
                  "docs' flags are incomplete here, not authoritative. Fastest "
                  "qualified endpoint measured so far.")


@dataclass
class OpenAIAPIEndpoint(OpenAIEndpoint):
    """Commercial OpenAI API with a personal key.

    Split from the ``OpenAIEndpoint`` base so that "the shape every
    OpenAI-compatible server speaks" and "the specific commercial endpoint at
    api.openai.com" are different things. Only the latter should reach for a
    personal key in the keyring; a facility preset that inherited that would let
    a stray stored secret shadow the ANL-username convention it depends on.
    """

    name: str = "openai"
    base_url: str = "https://api.openai.com/v1"
    uses_keyring: bool = True
    default_model: str = "gpt-5.1"
    notes: str = ("Commercial OpenAI API — personal pay-per-token key (a ChatGPT "
                  "subscription is NOT API access) and outbound egress. Review data "
                  "governance before sending beamline paths to a third party.")


@dataclass
class OpenAIResponsesEndpoint(OpenAIAPIEndpoint):
    """OpenAI's ``/v1/responses`` wire — the one Codex speaks.

    Opt-in, not the default: chat-completions serves the same models, so this
    exists for parity with Codex-style clients and for gateways that expose only
    the Responses surface. Needs a provider that speaks it; until then
    ``active_endpoint()`` rejects it rather than letting the OpenAI
    chat-completions client POST to the wrong route and fail obscurely.
    """

    name: str = "openai-responses"
    wire: str = "responses"
    notes: str = ("OpenAI /v1/responses (Codex wire). Verified live; requires a "
                  "Responses-aware provider, which APEXA does not ship yet.")


@dataclass
class AnthropicEndpoint(OpenAIEndpoint):
    """Commercial Anthropic API — **native wire, not OpenAI-compatible**.

    ``api.anthropic.com`` serves ``/v1/messages`` with ``x-api-key`` and an
    ``anthropic-version`` header; it has no ``/v1/chat/completions``. Before this
    preset carried ``wire="anthropic"`` it was consumed by the OpenAI client and
    could not have worked. ``base_url`` is therefore the host ROOT — the native
    provider appends the path itself.
    """

    name: str = "anthropic"
    base_url: str = "https://api.anthropic.com"
    wire: str = "anthropic"
    uses_keyring: bool = True
    lists_models: bool = False
    default_model: str = "claude-sonnet-5"
    notes: str = ("Commercial Anthropic API — needs a personal API key (a Claude Pro "
                  "subscription is NOT API access) and outbound egress. Review data "
                  "governance before sending beamline paths to a third party.")


@dataclass
class GeminiEndpoint(OpenAIEndpoint):
    """Google Gemini through its OpenAI-**compatibility** layer.

    Google publishes an OpenAI-shaped surface at ``/v1beta/openai``, so Gemini
    needs no new provider — only a base URL and key through the existing client.
    Confirmed live (the route answers 400 on a malformed body rather than 404).

    Tool calling crosses a translation shim here, which is exactly the kind of
    claim this file insists on measuring rather than trusting — see the Minerva
    note above. Qualify with ``scripts/profile_smoke.py`` before beamline use.
    """

    name: str = "gemini"
    base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    uses_keyring: bool = True
    default_model: str = "gemini-2.5-flash"
    notes: str = ("Google Gemini via its OpenAI-compatibility layer. Personal API "
                  "key (aistudio.google.com) and outbound egress. Tool calling goes "
                  "through a translation shim — qualify it before relying on it.")


@dataclass
class MAGEndpoint(OpenAIEndpoint):
    """DOE American Science Cloud — Model Access Gateway.

    An institutional gateway that fronts the **Anthropic wire** (as well as an
    OpenAI one), so it reuses the native provider rather than needing its own.
    It differs from api.anthropic.com in exactly one respect that matters here:
    the credential is presented as a bearer token, not ``x-api-key`` — which is
    why ``auth_style`` exists.

    Credential is the AmSC Personal Access Token, read from ``AMSC_TOKEN`` (the
    variable AmSC's own documentation tells users to set), falling back to the
    keyring. PATs are short-lived; an expired one returns 401.

    Model names are MAG's own aliases (``claude-opus``, ``claude-sonnet``,
    ``claude-haiku``), NOT vendor version strings — a pinned version that MAG
    does not serve comes back as "model not found".
    """

    name: str = "mag"
    base_url: str = "https://mag.genesis.americansciencecloud.org"
    wire: str = "anthropic"
    auth_style: str = "bearer"
    env_key: str = "AMSC_TOKEN"
    uses_keyring: bool = True
    lists_models: bool = True
    default_model: str = "claude-opus"
    notes: str = ("AmSC Model Access Gateway. Billed to your AmSC project "
                  "allocation. Inference leaves ANL — review data governance.")


@dataclass
class CustomEndpoint(OpenAIEndpoint):
    """Any other OpenAI-compatible server (institutional gateway, vLLM, …).

    This is the institutional path: another lab's or university's gateway, Azure
    OpenAI, LBNL CBorg. Point ``APEXA_LLM_BASE_URL`` at it and store the key in
    the keyring.
    """

    name: str = "custom"
    base_url: str = "http://127.0.0.1:8000/v1"
    uses_keyring: bool = True
    notes: str = "Custom OpenAI-compatible endpoint from APEXA_LLM_BASE_URL."


@dataclass
class OllamaEndpoint(OpenAIEndpoint):
    """A model running on the user's own machine, via Ollama's OpenAI surface.

    The only profile that works at the ``data`` tier, and the only one that sends
    nothing off the host — which makes it the right answer when beamline data
    governance forbids a third party, regardless of what the network allows.
    Ollama ignores the key, so no credential is needed.
    """

    name: str = "ollama"
    base_url: str = "http://127.0.0.1:11434/v1"
    api_key: Optional[str] = "ollama"   # ignored by the server; satisfies clients
    default_model: str = "llama3.1"
    notes: str = ("Local Ollama. No data leaves the machine and no credential is "
                  "needed. Small local models often tool-call poorly — qualify first.")


PRESETS: dict[str, type[OpenAIEndpoint]] = {
    "argo":             ArgoNativeEndpoint,
    "argo-proxy":       ArgoProxyEndpoint,
    "alcf-sophia":      ALCFSophiaEndpoint,
    "alcf-minerva":     ALCFMinervaEndpoint,
    "alcf-metis":       ALCFMetisEndpoint,
    "openai":           OpenAIAPIEndpoint,
    "openai-responses": OpenAIResponsesEndpoint,
    "anthropic":        AnthropicEndpoint,
    "gemini":           GeminiEndpoint,
    "mag":              MAGEndpoint,
    "custom":           CustomEndpoint,
    "ollama":           OllamaEndpoint,
}

#: Wires APEXA can actually drive today. ``responses`` is declared (so the
#: preset can exist and be named) but has no provider, so it is refused at
#: configuration time rather than at the first query — the same bargain
#: ``tool_calling`` strikes for alcf-metis.
SUPPORTED_WIRES = ("openai", "anthropic")

#: Argo's native OpenAI-compatible endpoint, not the sidecar: one less moving
#: part on the beamline host, and no 127.0.0.1-binding hazard. argo-proxy stays
#: registered and selectable for hosts already running it.
DEFAULT_PRESET = "argo"


# ── ALCF candidate models for APEXA ──────────────────────────────────────────
# Flags from docs.alcf.anl.gov/services/inference-endpoints (read 2026-08-18):
#   B = batch   R = reasoning   T = tool calling   H = always hot
#
# APEXA needs T (every capability is a tool call). R matters for multi-step FF
# workflows. H matters a lot operationally: without it, a cold model costs 10-15
# minutes before the first query — painful mid-experiment.
#
# Models with only B (AuroraGPT) or no flags (Devstral) have no tool calling and
# are excluded. This table drives scripts/alcf_qualify_models.py.
ALCF_CANDIDATES: list[dict] = [
    # preset          model id                                    flags     note
    {"preset": "alcf-sophia",  "model": "openai/gpt-oss-120b",
     "flags": "B,R,T,H", "note": "only Sophia model with all four flags; Harmony-native"},
    {"preset": "alcf-sophia",  "model": "openai/gpt-oss-20b",
     "flags": "B,R,T,H", "note": "same flags, smaller/faster — cheap-turn fallback"},
    {"preset": "alcf-sophia",  "model": "google/gemma-4-31B-it",
     "flags": "R,T,H",   "note": "reasoning + tools + hot; small for APEXA's scope"},
    {"preset": "alcf-sophia",  "model": "google/gemma-4-E4B-it",
     "flags": "R,T,H",   "note": "smallest hot Gemma 4"},
    {"preset": "alcf-sophia",  "model": "google/gemma-3-27b-it",
     "flags": "B,T",     "note": "no reasoning flag; cold start"},
    {"preset": "alcf-sophia",  "model": "nvidia/nemotron-3-super-120b",
     "flags": "R,T",     "note": "reasoning + tools but NOT hot → 10-15 min cold start"},
    {"preset": "alcf-sophia",  "model": "arcee-ai/Trinity-Large-Thinking-W4A16",
     "flags": "R,T",     "note": "reasoning + tools; 4-bit quantized; not hot"},
    {"preset": "alcf-sophia",  "model": "meta-llama/Llama-3.3-70B-Instruct",
     "flags": "B,T",     "note": "mature tool calling, no reasoning flag"},
    {"preset": "alcf-sophia",  "model": "meta-llama/Llama-4-Scout-17B-16E-Instruct",
     "flags": "B,T",     "note": "long context MoE"},
    # Minerva: always-hot, tool calling UNCONFIRMED — the reason to measure.
    {"preset": "alcf-minerva", "model": "inkling-bf16",
     "flags": "H(+T*)",  "note": "no T flag in docs, but CHAINS — measured 3.8s, fastest"},
    {"preset": "alcf-minerva", "model": "nemotron-3-ultra",
     "flags": "H(+T*)",  "note": "no T flag in docs, but CHAINS — measured 3.9s"},
]


def preset_for_model(model_id: str) -> Optional[str]:
    """Which preset serves this model id, or None if it isn't an ALCF candidate.

    Lets the CLI's ``model <name>`` switch **endpoint and model together**: moving
    from ``claudeopus5`` to ``inkling-bf16`` is not a model change, it is a change
    of cluster, credential, and base URL. Without this the CLI would silently ask
    argo-proxy for an ALCF model and 404.
    """
    mid = (model_id or "").strip()
    for c in ALCF_CANDIDATES:
        if c["model"] == mid:
            return c["preset"]
    # Tolerate a bare id for a namespaced model ("gpt-oss-120b" → "openai/gpt-oss-120b").
    for c in ALCF_CANDIDATES:
        if c["model"].rsplit("/", 1)[-1] == mid:
            return c["preset"]
    # Tolerate a WRONG vendor prefix too ("openai/gemma-4-31B-it"): the bare name
    # is unambiguous across the catalogue, and copying the prefix from the previous
    # line is an easy slip. The switch prints what it resolved to, so the
    # correction is never silent.
    bare = mid.rsplit("/", 1)[-1]
    for c in ALCF_CANDIDATES:
        if c["model"].rsplit("/", 1)[-1] == bare:
            return c["preset"]
    return None


def canonical_model_id(model_id: str) -> str:
    """Expand a bare ALCF model name to the id the endpoint actually serves."""
    mid = (model_id or "").strip()
    for c in ALCF_CANDIDATES:
        if c["model"] == mid:
            return mid
    bare = mid.rsplit("/", 1)[-1]
    for c in ALCF_CANDIDATES:
        if c["model"].rsplit("/", 1)[-1] == bare:
            return c["model"]
    return mid


def active_endpoint() -> OpenAIEndpoint:
    """Resolve the configured endpoint.

    ``APEXA_LLM_PRESET`` picks the preset; ``APEXA_LLM_BASE_URL`` overrides its URL.
    An endpoint that cannot do tool calling is rejected here — APEXA has no
    non-tool mode, so failing at configuration time is far kinder than failing at
    the first query.
    """
    name = (os.environ.get("APEXA_LLM_PRESET") or DEFAULT_PRESET).strip().lower()
    cls = PRESETS.get(name)
    if cls is None:
        raise EndpointRejected(
            f"unknown APEXA_LLM_PRESET {name!r}. Choose one of: "
            f"{', '.join(sorted(PRESETS))}."
        )
    ep = cls()
    override = (os.environ.get("APEXA_LLM_BASE_URL") or "").strip()
    if override:
        ep.base_url = override
    if not ep.tool_calling:
        raise EndpointRejected(
            f"{ep.name} cannot be used by APEXA: {ep.notes} "
            f"Every APEXA capability is a tool call."
        )
    if ep.wire not in SUPPORTED_WIRES:
        raise EndpointRejected(
            f"{ep.name} speaks the {ep.wire!r} wire, which APEXA has no provider "
            f"for (supported: {', '.join(SUPPORTED_WIRES)}). {ep.notes}"
        )
    return ep


def describe_active() -> str:
    try:
        ep = active_endpoint()
    except EndpointRejected as e:
        return f"(unusable: {e})"
    return f"{ep.name} → {ep.base_url}"


def suggest_models(model_id: str, limit: int = 5) -> list[str]:
    """Closest candidate ids for a mistyped model name (setup + CLI error paths)."""
    q = set(re.findall(r"[a-z0-9]+", (model_id or "").lower()))
    if not q:
        return []
    scored = []
    for c in ALCF_CANDIDATES:
        t = set(re.findall(r"[a-z0-9]+", c["model"].lower()))
        overlap = len(q & t)
        if overlap:
            scored.append((-overlap, c["model"]))
    scored.sort()
    return [m for _, m in scored[:limit]]
