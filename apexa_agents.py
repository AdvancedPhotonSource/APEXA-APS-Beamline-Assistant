#!/usr/bin/env python3
"""
APEXA Multi-Agent Orchestration Layer  (Phase 2)

Replaces the monolithic APEXAClient agentic loop with:
  - ArgoProvider  : single class for all Argo Gateway HTTP calls
  - APEXAAgent    : lightweight agent definition (instructions + tool filter)
  - AgentRunner   : clean 10-iteration loop, native tool calling only
  - OrchestratorAgent : keyword-based routing to specialist agents

What this eliminates from argo_mcp_client.py:
  - call_argo_chat_api()          (~82 lines)
  - _prepare_argo_payload()       (~68 lines)
  - _convert_tools_to_claude_format() (13 lines)
  - get_all_available_tools()     (40 lines)
  - process_diffraction_query()   (~570 lines)
  - _extract_peak_positions()     (18 lines)
  - CALCULATION_KEYWORDS / MAP dicts (28 lines)
  - _needs_calculation_tool()     (5 lines)
  - _detect_required_calculation_tool() (13 lines)

What stays in argo_mcp_client.py (unchanged):
  - All MCP server connection logic
  - execute_tool_call() — now uses _tool_registry for routing
  - ExperimentContext, ImageAnalyzer, PlottingEngine, etc.
  - interactive_analysis_session() CLI loop
"""

import asyncio
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path
import httpx
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Callable, Awaitable
from interaction_logger import InteractionLogger, InteractionEntry
from apexa_timing import (
    log_llm_call, count_message_tokens, count_tokens,
    record_llm_call, current_query_id,
)
from skill_registry import skill_context_for_tools, skills_for_tools, load_skill_text
try:
    import capsule_registry as _capsule_registry
except Exception:  # pragma: no cover - defensive; capsule injection then no-ops
    _capsule_registry = None
from apexa_provider_openai import (
    OpenAICompatProvider,
    ProviderUnavailable,
    preflight as llm_preflight,
    proxy_mode_enabled,
    strict_mode,
)
from apexa_ledger import ToolLedger
from apexa_toolsurface import (
    disclosure_enabled,
    handle_meta_tool,
    initial_surface,
)

# ── Compact directory listing ───────────────────────────────────────────────

_EXT_GROUPS = {
    "Data":    {".tif",".tiff",".ge",".ge1",".ge2",".ge3",".ge4",".ge5",
                ".h5",".hdf",".hdf5",".zarr",".nxs",".cbf",".zip"},
    "Config":  {".txt",".toml",".yaml",".yml",".json",".cfg",".ini",".env"},
    "Results": {".csv",".dat",".xy",".bin",".mic",".out"},
    "Scripts": {".py",".sh",".bash"},
    "Docs":    {".md",".rst",".pdf",".log"},
}

def _emit_narration(prose: str) -> None:
    """Surface the model's pre-action narration to the user (Claude-Code style:
    say what you're about to do, THEN do it). The ``▸ toolname`` markers alone
    show that a tool fired but not WHY — so any prose the model writes before its
    TOOL_CALL block would otherwise be swallowed (it's only appended to the
    in-flight message history, never printed). Print it so a multi-step turn
    reads like Claude Code: intent line → tool → intent line → tool.

    Light markdown strip keeps the terminal clean (matches the CLI's
    clean_markdown intent for the final answer); Gradio/web capture stdout the
    same way they already do for the ▸ markers."""
    if not prose:
        return
    s = re.sub(r'\*\*(.+?)\*\*', r'\1', prose)     # **bold**
    s = re.sub(r'__(.+?)__', r'\1', s)              # __italic__
    s = re.sub(r'^#{1,6}\s*', '', s, flags=re.M)    # ### headers
    s = s.strip()
    if s:
        print(f"\n{s}")


def _compact_listing(parsed: dict, max_preview: int = 3) -> str:
    """Build a grouped compact summary from list_directory JSON result.

    Shows full listing if ≤20 files, otherwise groups by file type
    with preview filenames and a hint to use 'ls' for the full listing.
    """
    DIM = "\033[2m"
    BOLD = "\033[1m"
    BLUE = "\033[1;34m"
    RESET = "\033[0m"

    path = parsed.get("path", "")
    dirs = parsed.get("dirs", [])
    files = parsed.get("files", [])

    if not files and not dirs:
        return parsed.get("listing", "")

    if len(files) <= 20:
        return parsed.get("listing", "")

    lines = [f"{BOLD}{path}{RESET}"]

    if dirs:
        dir_strs = [f"{BLUE}{BOLD}{d}{RESET}" for d in dirs]
        lines.append("  " + "  ".join(dir_strs))
        lines.append("")

    groups: dict = {cat: [] for cat in _EXT_GROUPS}
    groups["Other"] = []

    for fname in files:
        ext = Path(fname).suffix.lower()
        placed = False
        for cat, exts in _EXT_GROUPS.items():
            if ext in exts:
                groups[cat].append(fname)
                placed = True
                break
        if not placed:
            groups["Other"].append(fname)

    for cat, flist in groups.items():
        if not flist:
            continue
        preview = flist[:max_preview]
        preview_str = "  ".join(preview)
        if len(flist) > max_preview:
            preview_str += f"  {DIM}+{len(flist) - max_preview} more{RESET}"
        lines.append(f"  {cat} ({len(flist)}):  {preview_str}")

    hint_path = Path(path).name or path
    lines.append(f"  {DIM}{len(dirs)} directories, {len(files)} files — type 'ls {hint_path}' for full listing{RESET}")

    return "\n".join(lines)


# ── Constants ───────────────────────────────────────────────────────────────

PROD_URL = "https://apps.inside.anl.gov/argoapi/api/v1/resource/chat/"
DEV_URL  = "https://apps-dev.inside.anl.gov/argoapi/api/v1/resource/chat/"

# Models that require the DEV endpoint (add new beta models here)
DEV_ONLY_MODELS: set = set()


def _native_tools_enabled(model: str = "") -> bool:
    """Whether native Argo /chat/ tool calling is active for ``model``.

    When ON, ArgoProvider passes a per-vendor ``tools`` array in the payload and
    the model returns structured ``tool_calls`` that AgentRunner's Mode 1
    executes directly. When OFF, behaviour is byte-for-byte the existing
    text-based TOOL_CALL:/ARGUMENTS: path.

    Policy (Aug-2026 Argo docs: /chat tool calling functional for all vendors):
    - ``APEXA_NATIVE_TOOLS`` set to a truthy value (1/true/yes/on) → ON for ANY
      model (explicit opt-in).
    - set to a falsy value (0/false/no/off) → OFF for all models (escape hatch).
    - unset (DEFAULT) → ON for claude*, gpt*/gpto*, and gemini* (the three Argo
      vendor families with documented /chat tool calling); OFF for unknown
      models until their response shape is verified.

    A request that carries ``tools`` and is rejected with a 400/422 falls back
    automatically to the text path (see ArgoProvider.chat), so defaulting ON is
    safe even before a live smoke test.
    """
    raw = os.environ.get("APEXA_NATIVE_TOOLS", "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    # Unset → default-on for the three known Argo vendor families.
    return (
        model.startswith("claude")
        or model.startswith("gpt")
        or model.startswith("gemini")
    )


# ── Data Types ──────────────────────────────────────────────────────────────

@dataclass
class ToolCall:
    id: str
    name: str
    arguments: Dict[str, Any]


@dataclass
class AgentResponse:
    content: str
    tool_calls: List[ToolCall] = field(default_factory=list)
    stop_reason: str = "end_turn"


# ── Argo Provider ────────────────────────────────────────────────────────────

class ArgoProvider:
    """
    Single class handling all Argo Gateway communication.

    Normalises per-model format differences (Claude / OpenAI / Gemini) in
    one place instead of scattered if/else branches across the codebase.

    Created fresh per query (cheap — just config) so model switching is free.
    """

    def __init__(self, username: str, model: str):
        self.username = username
        self.model    = model
        self.url      = DEV_URL if model in DEV_ONLY_MODELS else PROD_URL
        self._client  = httpx.AsyncClient(timeout=120.0)


    # ── Payload builder ─────────────────────────────────────────────────────

    def _build_payload(self, messages: List[Dict],
                       temperature: float,
                       tools: Optional[List[Dict]] = None) -> Dict:
        # Sanitize messages: Argo needs role + STRING content on every turn. A
        # message with content=None or a non-string (a stray tool_calls-only
        # assistant turn, a bad restored-session entry) can make the gateway return
        # an empty completion — which then surfaced as the canned greeting. Coerce
        # content to a string and keep only role+content (Argo ignores other keys).
        safe_messages: List[Dict] = []
        for m in messages:
            c = m.get("content", "")
            if c is None:
                c = ""
            elif not isinstance(c, str):
                try:
                    c = json.dumps(c)
                except Exception:
                    c = str(c)
            safe_messages.append({"role": m.get("role", "user"), "content": c})
        payload: Dict[str, Any] = {
            "user":        self.username,
            "model":       self.model,
            "messages":    safe_messages,
            "temperature": temperature,
        }

        # Max tokens and params per model family (Argo model list, 2026 update)
        if self.model.startswith("claude"):
            payload["max_tokens"] = 21000
            # Opus 5 / 4.8 / 4.7: no sampling params at all (temperature/top_p/top_k
            # silently removed by Argo). 1M context, 128K output; thinking via
            # output_config if ever needed. (Opus 5 param rule not re-listed in the
            # Aug-2026 Argo doc but sits with 4.8/4.7 — same no-sampling family.)
            if self.model in ("claudeopus5", "claudeopus48", "claudeopus47"):
                payload.pop("temperature", None)
            # Sonnet 5/4.6/4.5, Haiku 4.5: accept AT MOST ONE of temperature/top_p
            # (Argo drops top_p if both sent) — send temperature only, no top_p.
            # (Sonnet 5 param rule not detailed in the Aug-2026 doc; follows the
            # Sonnet sub-family convention — temperature only.)
            elif self.model in ("claudesonnet5", "claudesonnet46", "claudesonnet45", "claudehaiku45"):
                pass
            else:
                # Opus 4.6/4.5/4.1, Sonnet 4/3.7: require temperature + top_p.
                payload["top_p"] = 0.9
        elif self.model == "gpt55" or self.model.startswith("gpt56"):
            # GPT-5.5: temperature must be exactly 1; top_p + max_completion_tokens ok.
            # GPT-5.6 (sol/terra/luna): the Aug-2026 Argo doc leaves the sampling
            # rule as "..." (undocumented). Mirror gpt55 and force temperature=1 —
            # temperature=1 is valid whether or not gpt56 restricts it, so this
            # can't 400 on temperature (unlike sending an arbitrary temp). Uses
            # max_completion_tokens (no max_tokens), same as the rest of GPT-5.x.
            payload["temperature"] = 1
            payload["top_p"] = 0.9
            payload["max_completion_tokens"] = 16000
        elif self.model.startswith("gpto"):
            # o-series (o1/o3/o3-mini/o4-mini): NO temperature/top_p; use
            # max_completion_tokens (Argo 400s if sampling params are sent).
            payload.pop("temperature", None)
            payload["max_completion_tokens"] = 16000
        elif self.model.startswith("gpt"):
            # gpt4o, gpt41*, and the GPT-5 family (gpt5/gpt51/gpt52/gpt54 +
            # mini/nano) all accept temperature + top_p + max_completion_tokens
            # and reject max_tokens. (Note: gpt54 now ACCEPTS temperature — the
            # earlier "no sampling" rule was removed in the 2026 Argo update.)
            payload["top_p"] = 0.9
            payload["max_completion_tokens"] = 16000
        elif self.model.startswith("gemini"):
            # gemini35flash, gemini31flashlite: Argo max_tokens maps to Gemini
            # max_output_tokens; temperature accepted. (Gemini 2.5 pro/flash were
            # removed — Argo marked them discontinue-use.)
            payload["max_tokens"] = 16000
        else:
            payload["max_completion_tokens"] = 16000

        # Native tool calling (opt-in via APEXA_NATIVE_TOOLS). The Aug-2026 Argo
        # docs confirm /chat tool calling is now functional for ALL vendors — the
        # older "Argo strips native tool_calls" assumption no longer holds. When
        # the flag is OFF we omit tools entirely, so the payload is byte-for-byte
        # the text-based path and the model uses TOOL_CALL: / ARGUMENTS: format.
        if tools and _native_tools_enabled(self.model):
            payload["tools"] = self._to_vendor_tools(tools)

        return payload

    def _to_vendor_tools(self, tools: List[Dict]) -> List[Dict]:
        """Convert APEXA's internal OpenAI-format tool defs into the per-vendor
        tool schema the active model requires (each Argo LLM vendor wants its own
        shape; the caller must format them — see the Argo Tool Calling Templates).

        Input entries look like:
            {"type":"function","function":{"name","description","parameters":<jsonschema>}}
        """
        model = self.model
        if model.startswith("claude"):
            # Anthropic tool schema: flat {name, description, input_schema}.
            out: List[Dict] = []
            for t in tools:
                fn = t.get("function", t)
                out.append({
                    "name":        fn.get("name", ""),
                    "description": fn.get("description", ""),
                    "input_schema": fn.get("parameters", {"type": "object"}),
                })
            return out
        if model.startswith("gemini"):
            # Gemini function declarations; Argo wraps these into Google
            # Function/Tool objects. Default-on alongside claude/OpenAI; the
            # 400/422 auto-fallback in chat() covers any schema mismatch that a
            # live smoke test hasn't yet confirmed.
            out = []
            for t in tools:
                fn = t.get("function", t)
                out.append({
                    "name":        fn.get("name", ""),
                    "description": fn.get("description", ""),
                    "parameters":  fn.get("parameters", {"type": "object"}),
                })
            return out
        # OpenAI family (gpt*, gpto*, gpt55): already in the correct shape.
        return tools

    # ── Response parsing ────────────────────────────────────────────────────

    @staticmethod
    def _parse_tool_calls(raw) -> List[ToolCall]:
        """Normalise per-provider native tool-call shapes into ToolCall. Static
        so both ArgoProvider (blocking /chat/) and StreamingAnthropicProvider
        (streaming /messages/) can share the parser."""
        # Gemini returns tool_calls as a single DICT ({"id":null,"name","args"})
        # rather than a list — the other vendors return a list. Normalise so the
        # loop below never iterates dict keys and crashes on .get().
        if isinstance(raw, dict):
            raw = [raw]
        if not raw:
            return []
        calls: List[ToolCall] = []
        for i, tc in enumerate(raw):
            if "function" in tc:               # OpenAI format
                name = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"].get("arguments", "{}"))
                except json.JSONDecodeError:
                    args = {}
            elif "input" in tc:                # Claude format
                name = tc.get("name", "")
                args = tc["input"]
            elif "args" in tc:                 # Gemini format
                name = tc.get("name", "")
                args = tc["args"]
            else:
                continue
            calls.append(ToolCall(
                id=tc.get("id", f"tool_{i}"),
                name=name,
                arguments=args,
            ))
        return calls

    @staticmethod
    def _coerce_content(c) -> str:
        """Content may be a plain string OR an Anthropic-native list of blocks
        ([{'type':'text','text':...}]). Flatten to a string so nothing is silently
        dropped — e.g. Argo returns gateway/auth messages (ACCESS DENIED for an
        unauthorized user) in the block shape, which otherwise read as empty."""
        if isinstance(c, str):
            return c
        if isinstance(c, list):
            parts = []
            for b in c:
                if isinstance(b, dict):
                    parts.append(b.get("text") or b.get("content") or "")
                elif isinstance(b, str):
                    parts.append(b)
            return "".join(parts)
        return "" if c is None else str(c)

    def _parse_response(self, data: Dict) -> AgentResponse:
        # Argo wraps response in {"response": {"content": ..., "tool_calls": [...]}}
        if "response" in data and isinstance(data["response"], dict):
            resp      = data["response"]
            content   = self._coerce_content(resp.get("content", ""))
            raw_calls = resp.get("tool_calls", []) or []
        elif "choices" in data:
            msg       = data["choices"][0]["message"]
            content   = self._coerce_content(msg.get("content", ""))
            raw_calls = msg.get("tool_calls", []) or []
        elif "content" in data:
            # Anthropic-native top-level shape: {id, type:'message', role,
            # content:[{type:'text', text:...}]}. Argo returns gateway/auth blocks
            # (e.g. "ACCESS DENIED — user not authorized") in this form; parse it so
            # the real message reaches the user instead of appearing empty.
            content   = self._coerce_content(data.get("content", ""))
            raw_calls = data.get("tool_calls", []) or []
        else:
            content   = str(data.get("response", ""))
            raw_calls = []

        tool_calls = self._parse_tool_calls(raw_calls)
        return AgentResponse(
            content=content,
            tool_calls=tool_calls,
            stop_reason="tool_use" if tool_calls else "end_turn",
        )

    # ── Public API ──────────────────────────────────────────────────────────

    async def chat(self, messages: List[Dict],
                   temperature: float = 0.7,
                   tools: Optional[List[Dict]] = None) -> AgentResponse:
        payload  = self._build_payload(messages, temperature, tools)
        if os.environ.get("APEXA_DEBUG"):
            debug_payload = {k: v for k, v in payload.items() if k != "messages"}
            print(f"  [debug] Argo payload: {debug_payload}", file=sys.stderr)

        # Prompt token estimate is call-wide; response tokens are per-attempt.
        prompt_tok = count_message_tokens(payload.get("messages", []), self.model)
        n_messages = len(payload.get("messages", []))

        retries = 3
        for attempt in range(retries):
            t0 = time.monotonic()
            status: int = 0
            error: str  = ""
            data: Dict[str, Any] = {}
            parsed: Optional[AgentResponse] = None
            try:
                response = await self._client.post(
                    self.url, json=payload,
                    headers={"Content-Type": "application/json"},
                )
                elapsed = time.monotonic() - t0
                status  = response.status_code
                if os.environ.get("APEXA_SHOW_TIMING"):
                    print(f"  \033[2m⏱ {self.model} responded in {elapsed:.1f}s\033[0m", flush=True)
                # Transient gateway failures — retry with backoff. 500/504 are
                # Argo-side blips (Internal Server Error / gateway timeout) that
                # come and go; without retrying them a single blip aborts an
                # in-progress reconstruction mid-turn.
                if status in (500, 502, 503, 504, 429):
                    wait = 2 ** attempt
                    print(f"  \033[33m⚠ Argo {status}, retrying in {wait}s ({attempt+1}/{retries})\033[0m")
                    log_llm_call(self._timing_record(
                        attempt=attempt, status=status, elapsed=elapsed,
                        prompt_tok=prompt_tok, n_messages=n_messages,
                        temperature=temperature, error=f"retry_{status}"))
                    await asyncio.sleep(wait)
                    continue
                # Native-tools safety net: if the gateway rejects a payload that
                # carries `tools` (400/422 — e.g. an unexpected per-vendor schema
                # or a model that doesn't accept tools), drop `tools` and retry
                # once on the text path instead of hard-failing. This makes
                # default-on native tool calling safe: worst case it degrades to
                # the existing TOOL_CALL:/ARGUMENTS: behaviour.
                if status in (400, 422) and "tools" in payload:
                    print(f"  \033[33m⚠ Argo {status} with tools — retrying without native "
                          f"tools (falling back to text path)\033[0m", file=sys.stderr)
                    log_llm_call(self._timing_record(
                        attempt=attempt, status=status, elapsed=elapsed,
                        prompt_tok=prompt_tok, n_messages=n_messages,
                        temperature=temperature, error=f"tools_reject_{status}"))
                    payload.pop("tools", None)
                    continue
                if status != 200:
                    print(f"  Argo API error ({status}): {response.text[:500]}", file=sys.stderr)
                    log_llm_call(self._timing_record(
                        attempt=attempt, status=status, elapsed=elapsed,
                        prompt_tok=prompt_tok, n_messages=n_messages,
                        temperature=temperature, error=f"http_{status}"))
                    response.raise_for_status()
                data   = response.json()
                parsed = self._parse_response(data)
                # Diagnostic: a 200 with NO content and NO tool calls is the
                # degenerate case that surfaced as the "Hi I'm APEXA" greeting.
                # Log what the gateway actually returned (and the user field) so a
                # platform-specific empty-completion (e.g. a bad per-machine .env
                # ANL_USERNAME on Windows) is diagnosable instead of silent.
                if not parsed.content and not parsed.tool_calls:
                    try:
                        _raw = json.dumps(data)[:400]
                    except Exception:
                        _raw = str(data)[:400]
                    print(f"  \033[33m⚠ Argo returned an EMPTY completion\033[0m "
                          f"(model={self.model}, user={self.username!r}). Raw: {_raw}",
                          file=sys.stderr)
                    log_llm_call(self._timing_record(
                        attempt=attempt, status=status, elapsed=elapsed,
                        prompt_tok=prompt_tok, n_messages=n_messages,
                        temperature=temperature, parsed=parsed, empty=True))
                    # An empty 200 is a transient gateway hiccup that would
                    # otherwise stall the agentic loop (nothing to act on) —
                    # retry with backoff before giving up.
                    if attempt < retries - 1:
                        wait = 2 ** attempt
                        print(f"  \033[33m⚠ empty completion — retrying in {wait}s "
                              f"({attempt+1}/{retries})\033[0m", file=sys.stderr)
                        await asyncio.sleep(wait)
                        continue
                    return parsed
                log_llm_call(self._timing_record(
                    attempt=attempt, status=status, elapsed=elapsed,
                    prompt_tok=prompt_tok, n_messages=n_messages,
                    temperature=temperature, parsed=parsed, empty=False))
                return parsed
            except httpx.TimeoutException:
                elapsed = time.monotonic() - t0
                log_llm_call(self._timing_record(
                    attempt=attempt, status=0, elapsed=elapsed,
                    prompt_tok=prompt_tok, n_messages=n_messages,
                    temperature=temperature, error="timeout"))
                if attempt < retries - 1:
                    wait = 2 ** attempt
                    print(f"  \033[33m⚠ Argo timeout, retrying in {wait}s ({attempt+1}/{retries})\033[0m")
                    await asyncio.sleep(wait)
                else:
                    raise
        response.raise_for_status()
        return self._parse_response(response.json())

    def _timing_record(self, *, attempt: int, status: int, elapsed: float,
                       prompt_tok: int, n_messages: int,
                       temperature: float,
                       parsed: Optional[AgentResponse] = None,
                       empty: bool = False,
                       error: str = "") -> Dict[str, Any]:
        """Build one JSONL row for the timing log. Kept out of the hot path so
        the retry loop stays readable. Also feeds Tier-2 aggregation via
        ``record_llm_call`` when a call actually landed (200)."""
        resp_tok = 0
        n_tool_calls = 0
        if parsed is not None:
            resp_tok = count_tokens(parsed.content or "", self.model)
            n_tool_calls = len(parsed.tool_calls)
        gen_tps = (resp_tok / elapsed) if (elapsed > 0 and resp_tok) else 0.0
        # Only count "real" completed calls into per-query totals — a retried
        # 502/503 or timeout would otherwise double-count wall-clock. Successful
        # 200s and terminal HTTP errors (raise_for_status about to fire) both
        # represent real time spent, so include them; skip only mid-loop retries.
        if not error.startswith("retry_"):
            record_llm_call(elapsed_s=elapsed,
                            prompt_tok=prompt_tok,
                            response_tok=resp_tok)
        return {
            "endpoint":     "argo-chat",
            "query_id":     current_query_id() or "",
            "model":        self.model,
            "attempt":      attempt,
            "http_status":  status,
            "elapsed_s":    round(elapsed, 3),
            "prompt_tok":   prompt_tok,
            "response_tok": resp_tok,
            "gen_tps":      round(gen_tps, 2),
            "n_messages":   n_messages,
            "n_tool_calls": n_tool_calls,
            "temperature":  temperature,
            "empty":        empty,
            "error":        error,
        }

    async def close(self):
        await self._client.aclose()


# ── Streaming Anthropic-native provider (Tier 3 instrumentation) ────────────

class StreamingAnthropicProvider:
    """Streaming provider for the Anthropic-native ``/messages/`` endpoint
    exposed by Argo Gateway (per CLAUDE.md).

    Purpose is instrumentation: unlike Argo ``/chat/`` (blocking, returns a
    single JSON blob), this endpoint streams SSE, so we can measure real
    **TTFT** (time-to-first-token) and **TPOT** (time-per-output-token) — the
    metrics NVIDIA reports in DGX Spark agentic benchmarks.

    Only valid for ``claude*`` models. Opt in via ``APEXA_PROVIDER=streaming``.
    """

    URL_PATH = "/v1/messages"
    _BASE    = "https://apps.inside.anl.gov/argoapi"

    def __init__(self, username: str, model: str):
        self.username = username
        self.model    = model
        self.url      = f"{self._BASE}{self.URL_PATH}"
        self._client  = httpx.AsyncClient(timeout=180.0)

    @staticmethod
    def _split_system(messages: List[Dict]) -> tuple:
        """Anthropic native takes ``system`` as a top-level string, not a role
        in the messages array. Extract system turns and coerce content to str."""
        sys_parts: List[str] = []
        others:    List[Dict] = []
        for m in messages:
            role = m.get("role", "user")
            c    = m.get("content", "")
            if c is None:
                c = ""
            elif not isinstance(c, str):
                try:
                    c = json.dumps(c)
                except Exception:
                    c = str(c)
            if role == "system":
                if c:
                    sys_parts.append(c)
            else:
                others.append({"role": role, "content": c})
        return "\n\n".join(sys_parts), others

    def _build_payload(self, messages: List[Dict], temperature: float) -> Dict:
        system_str, msgs = self._split_system(messages)
        payload: Dict[str, Any] = {
            "model":      self.model,
            "messages":   msgs,
            "max_tokens": 21000,
            "stream":     True,
        }
        if system_str:
            payload["system"] = system_str
        # Opus 4.7/4.8 silently drop temperature; other Claude models accept it.
        if self.model not in ("claudeopus48", "claudeopus47"):
            payload["temperature"] = temperature
        return payload

    async def chat(self, messages: List[Dict],
                   temperature: float = 0.7,
                   tools: Optional[List[Dict]] = None) -> AgentResponse:
        # tools is accepted for interface parity with ArgoProvider but IGNORED:
        # the /streaming endpoint's tool calling works only for OpenAI models,
        # and this provider is Claude-only (Aug-2026 Argo docs).
        payload    = self._build_payload(messages, temperature)
        prompt_est = count_message_tokens(payload.get("messages", []), self.model)
        n_messages = len(payload.get("messages", []))
        headers = {
            "x-api-key":         self.username,
            "anthropic-version": "2023-06-01",
            "content-type":      "application/json",
        }

        t0            = time.monotonic()
        t_first_token: Optional[float] = None
        content_parts: List[str]  = []
        tool_raw:      List[Dict] = []
        input_tokens  = 0
        output_tokens = 0
        current_tool: Optional[Dict[str, Any]] = None
        stream_error  = ""
        status        = 0

        try:
            async with self._client.stream("POST", self.url,
                                           json=payload, headers=headers) as resp:
                status = resp.status_code
                if status != 200:
                    body = (await resp.aread())[:400]
                    stream_error = f"http_{status}"
                    self._log_row(t0=t0, status=status, prompt_tok=prompt_est,
                                  n_messages=n_messages, temperature=temperature,
                                  content="", tool_calls_n=0,
                                  t_first_token=None, input_tokens=prompt_est,
                                  output_tokens=0, error=stream_error)
                    resp.raise_for_status()

                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if raw == "[DONE]":
                        break
                    try:
                        ev = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    typ = ev.get("type", "")
                    if typ == "message_start":
                        usage = ev.get("message", {}).get("usage", {})
                        input_tokens = usage.get("input_tokens", 0) or input_tokens
                    elif typ == "content_block_start":
                        block = ev.get("content_block", {})
                        if block.get("type") == "tool_use":
                            current_tool = {
                                "id":         block.get("id", ""),
                                "name":       block.get("name", ""),
                                "input_json": "",
                            }
                    elif typ == "content_block_delta":
                        delta = ev.get("delta", {})
                        dtype = delta.get("type", "")
                        if dtype == "text_delta":
                            txt = delta.get("text", "")
                            if txt and t_first_token is None:
                                t_first_token = time.monotonic()
                            content_parts.append(txt)
                        elif dtype == "input_json_delta" and current_tool is not None:
                            current_tool["input_json"] += delta.get("partial_json", "")
                    elif typ == "content_block_stop":
                        if current_tool is not None:
                            try:
                                current_tool["input"] = json.loads(
                                    current_tool.get("input_json") or "{}")
                            except json.JSONDecodeError:
                                current_tool["input"] = {}
                            tool_raw.append({
                                "id":    current_tool["id"],
                                "name":  current_tool["name"],
                                "input": current_tool["input"],
                            })
                            current_tool = None
                    elif typ == "message_delta":
                        usage = ev.get("usage", {})
                        output_tokens = usage.get("output_tokens", 0) or output_tokens
                    elif typ == "error":
                        err_body = ev.get("error", {})
                        stream_error = f"stream_error:{err_body.get('type','?')}"
        except httpx.HTTPStatusError:
            raise
        except Exception as e:
            stream_error = f"exception:{type(e).__name__}"
            # Emit the log row before re-raising so partial timing survives.
            self._log_row(t0=t0, status=status or 0, prompt_tok=prompt_est,
                          n_messages=n_messages, temperature=temperature,
                          content="".join(content_parts),
                          tool_calls_n=len(tool_raw),
                          t_first_token=t_first_token,
                          input_tokens=input_tokens or prompt_est,
                          output_tokens=output_tokens,
                          error=stream_error)
            raise

        # Fallbacks when the stream ended without usage events (older gateways
        # or non-conforming proxies) — tiktoken guess is better than 0.
        content = "".join(content_parts)
        if not output_tokens:
            output_tokens = count_tokens(content, self.model)
        if not input_tokens:
            input_tokens = prompt_est

        tool_calls = ArgoProvider._parse_tool_calls(tool_raw)
        parsed = AgentResponse(
            content=content,
            tool_calls=tool_calls,
            stop_reason="tool_use" if tool_calls else "end_turn",
        )
        self._log_row(t0=t0, status=status, prompt_tok=prompt_est,
                      n_messages=n_messages, temperature=temperature,
                      content=content, tool_calls_n=len(tool_calls),
                      t_first_token=t_first_token,
                      input_tokens=input_tokens,
                      output_tokens=output_tokens,
                      error=stream_error)
        return parsed

    def _log_row(self, *, t0: float, status: int, prompt_tok: int,
                 n_messages: int, temperature: float,
                 content: str, tool_calls_n: int,
                 t_first_token: Optional[float],
                 input_tokens: int, output_tokens: int,
                 error: str) -> None:
        """Emit the streaming JSONL row and update Tier-2 aggregation.

        TPOT is defined as (total - TTFT) / (output_tokens - 1) so single-token
        replies don't blow up the divisor. Streaming gives us these metrics for
        real; the blocking /chat/ provider can only report combined elapsed."""
        elapsed = time.monotonic() - t0
        ttft_s  = (t_first_token - t0) if t_first_token else None
        tpot_ms = None
        gen_tps = 0.0
        if ttft_s is not None and output_tokens > 1 and elapsed > ttft_s:
            tpot_ms = ((elapsed - ttft_s) / (output_tokens - 1)) * 1000.0
            gen_tps = (output_tokens - 1) / (elapsed - ttft_s)
        empty = not (content or tool_calls_n)
        temp_reported = (temperature
                         if self.model not in ("claudeopus48", "claudeopus47")
                         else None)
        if not error:
            record_llm_call(elapsed_s=elapsed,
                            prompt_tok=input_tokens,
                            response_tok=output_tokens)
        log_llm_call({
            "endpoint":     "argo-messages",
            "query_id":     current_query_id() or "",
            "model":        self.model,
            "attempt":      0,
            "http_status":  status,
            "elapsed_s":    round(elapsed, 3),
            "ttft_s":       round(ttft_s, 3) if ttft_s is not None else None,
            "tpot_ms":      round(tpot_ms, 2) if tpot_ms is not None else None,
            "prompt_tok":   input_tokens,
            "response_tok": output_tokens,
            "gen_tps":      round(gen_tps, 2),
            "n_messages":   n_messages,
            "n_tool_calls": tool_calls_n,
            "temperature":  temp_reported,
            "empty":        empty,
            "error":        error,
        })

    async def close(self):
        await self._client.aclose()


def select_provider(username: str, model: str):
    """Pick a provider based on ``APEXA_LLM_MODE`` / ``APEXA_PROVIDER``.

    - ``APEXA_LLM_MODE=proxy``                             → OpenAICompatProvider
      (argo-proxy, OpenAI-compatible; STRUCTURED tool results on every turn)
    - ``APEXA_PROVIDER=streaming`` + a ``claude*`` model    → StreamingAnthropicProvider
    - anything else                                         → ArgoProvider

    The proxy path is preferred: Argo's ``/chat/`` sanitizer flattens messages to
    strings, so tool results can only be fed back as prose there, which is what
    forces the fragile text ``TOOL_CALL:`` protocol. It is opt-in (default
    ``argo``) until the sidecar has soaked on the beamline host — see
    ``scripts/gate0_argo_proxy_smoke.py``. If the proxy is misconfigured we fall
    back rather than stranding an operator mid-experiment.

    Non-Claude models under ``streaming`` fall back to Argo /chat/ with a
    one-line stderr note (Anthropic native only speaks Claude)."""
    if proxy_mode_enabled():
        try:
            return OpenAICompatProvider(username, model)
        except ProviderUnavailable as e:
            if strict_mode():
                # Beamline default: a broken proxy must not silently downgrade to
                # the fabrication-prone text path. Set APEXA_LLM_STRICT=0 to allow.
                raise ProviderUnavailable(
                    f"APEXA_LLM_MODE=proxy but the proxy is unusable: {e}\n"
                    f"Refusing to fall back to the legacy Argo text protocol "
                    f"(APEXA_LLM_STRICT is on). Start the argo-proxy sidecar, or set "
                    f"APEXA_LLM_STRICT=0 to permit the degraded transport."
                ) from e
            print(f"  \033[33m⚠ APEXA_LLM_MODE=proxy unavailable ({e}) — "
                  f"falling back to Argo /chat/\033[0m", file=sys.stderr)

    prov = (os.environ.get("APEXA_PROVIDER") or "").lower()
    if prov == "streaming":
        if model.startswith("claude"):
            return StreamingAnthropicProvider(username, model)
        print(f"  \033[33m⚠ APEXA_PROVIDER=streaming ignored — model {model!r} "
              f"is not a Claude model; using Argo /chat/\033[0m", file=sys.stderr)
    return ArgoProvider(username, model)


def provider_is_structured(provider) -> bool:
    """True when the provider can carry native ``tool_calls`` / ``role:"tool"``
    messages, so the loop may skip the text ``TOOL_CALL:`` protocol entirely.

    Duck-typed on a ``structured_tools`` attribute rather than an isinstance check,
    so a stub provider can exercise the structured loop in tests without a network
    or an argo-proxy instance."""
    return bool(getattr(provider, "structured_tools", False))


# ── Agent Definition ─────────────────────────────────────────────────────────

@dataclass
class APEXAAgent:
    name:         str
    instructions: str
    tool_names:   List[str]   # bare MCP tool names; empty list = all tools
    temperature:  float = 0.7
    # Profile-Then-Reason mode: prepend a plan-first directive to the system
    # prompt so the model emits ALL needed tool calls in one response (concurrent
    # dispatch) and prefers a single compound tool over looping a primitive.
    # Combined with the runtime fan-out guard, this prevents the failure mode
    # where the model emits 22 xray_calculate calls instead of one
    # enumerate_bragg_rings call.
    use_planning: bool = False


# ── Specialist Agents ────────────────────────────────────────────────────────

CALIBRATION_AGENT = APEXAAgent(
    name        = "CalibrationAgent",
    temperature = 0.3,   # low — calibration needs deterministic output
    tool_names  = [
        "midas_auto_calibrate",          # primary: AutoCalibrateZarr.py workflow
        "run_ff_calibration",            # FF-HEDM detector geometry calibration
        "xray_calculate",
        "validate_beamline_parameters",
        "list_common_calibrants",
        "list_directory",
        "read_file",
        "get_file_info",
        # Parameter validation
        "validate_parameter_file",
        "diagnose_parameter_file",
        "inspect_dataset_file",
        "enumerate_bragg_rings",
    ],
    instructions = """You are a detector calibration specialist for HEDM synchrotron experiments at APS.

Workflow — follow these steps IN ORDER, no confirmations needed:
1. Call list_directory ONCE to find the calibrant image file
2. IMMEDIATELY call midas_auto_calibrate with the full path — do NOT ask the user to confirm
3. parameters_file is OPTIONAL — omit it if not available. AutoCalibrateZarr.py auto-detects
   calibrant (CeO2/LaB6 from filename), energy (keV in filename), pixel size (from detector shape)
4. If the user provides energy in keV, call xray_calculate to convert to wavelength first

CRITICAL: After listing files, call midas_auto_calibrate IMMEDIATELY.
Never say "I found the file, shall I proceed?" — just run it.
Never call list_directory more than once per request.

After calibration report: refined BC, Lsd, tilts, and convergence quality.

PARAMETER VALIDATION:
- validate param file: validate_parameter_file (checks required keys, ranges, cross-field rules)
- diagnose param file: diagnose_parameter_file (LLM-ready diagnosis with fix suggestions)
- extract params from data: inspect_dataset_file (auto-detect from GE/HDF5/Zarr)
- Bragg ring listing: enumerate_bragg_rings (which rings hit the detector)

When the user asks to validate, diagnose, or check a parameter file:
1. Call validate_parameter_file or diagnose_parameter_file DIRECTLY with the directory path
   (e.g. param_file="test1", pipeline="ff"). The tool auto-finds Parameters.txt or refined_MIDAS_params*.txt.
   Do NOT call list_directory first — the tool handles file discovery.
2. Pipeline arg is required — infer from the user's query:
   - "calibrat" / "integrat" / "lineout" / "caked" → pipeline="ri" (radial integration)
   - "ff-hedm" / "far-field" / "reconstruction" → pipeline="ff"
   - "nf-hedm" / "near-field" / "microstructure" → pipeline="nf"
   - "pf-hedm" / "point-focus" → pipeline="pf"
   - Default to "ri" for calibration-related queries, "ff" for HEDM workflow queries.

NEVER mention pyFAI, .poni files, or azimuthalIntegrator. This system uses MIDAS exclusively.
Calibration output is refined_MIDAS_params*.txt — NOT .poni files.""",
)

ANALYSIS_AGENT = APEXAAgent(
    name        = "AnalysisAgent",
    temperature = 0.5,
    use_planning = True,   # plan all tool calls up front; prefer compound tools
    tool_names  = [
        # Integration
        "midas_integrate_2d_to_1d",
        "midas_batch_integrate",
        # GSAS-II refinement & live analysis
        "run_gsas_refinement",
        "run_live_analysis",
        "fetch_cif_from_mp",
        # FF/NF/PF-HEDM workflows
        "run_ff_hedm_full_workflow",
        "run_nf_hedm_reconstruction",
        "run_pf_hedm_workflow",
        # Post-processing
        "match_grains",
        "overlay_ff_nf_results",
        "calculate_misorientation",
        "run_forward_simulation",
        "extract_grain_centroids",
        "convert_nf_to_dream3d",
        # Status & utilities
        "get_midas_workflow_status",
        "create_midas_parameter_file",
        "validate_midas_installation",
        "batch_convert_ge_to_tiff",
        # Parameter validation (pre-workflow)
        "validate_parameter_file",
        "diagnose_parameter_file",
        "inspect_dataset_file",
        "enumerate_bragg_rings",
        # Stress/strain analysis (post-reconstruction)
        "compute_grain_stress",
        "get_material_stiffness",
        "correct_d0_equilibrium",
        "analyze_slip_systems",
        "read_grains_summary",
        # General tools
        "xray_calculate",
        "list_directory",
        "read_file",
        "write_file",
        "run_command",
        "get_file_info",
    ],
    instructions = """You are a HEDM data analysis specialist at APS.

When the user asks to integrate, reconstruct, refine, track grains, or run any workflow,
you MUST call the appropriate tool. Never describe steps in text — execute them.

Capabilities (use the matching tool for each):
- 2D → 1D integration: midas_integrate_2d_to_1d or midas_batch_integrate
- GSAS-II refinement: run_gsas_refinement (takes .zarr.zip + CIF files)
- Integration + refinement pipeline: run_live_analysis (batch or stream backend)
- FF-HEDM reconstruction: run_ff_hedm_full_workflow
- NF-HEDM mapping: run_nf_hedm_reconstruction
- PF-HEDM pole figures: run_pf_hedm_workflow
- Grain tracking/matching: match_grains (Hungarian algorithm)
- Misorientation: calculate_misorientation
- Dream3D export: convert_nf_to_dream3d
- X-ray calculations: xray_calculate (NEVER compute manually)
- File operations: list_directory, read_file, get_file_info
- Validate parameter file: validate_parameter_file (checks required keys, ranges, cross-field rules)
- Diagnose parameter file: diagnose_parameter_file (LLM-ready diagnosis with fix suggestions)
- Extract params from data: inspect_dataset_file (auto-detect from GE/HDF5/Zarr)
- Bragg rings: enumerate_bragg_rings (which rings hit the detector)
- Stress analysis: compute_grain_stress (Hooke's law + equilibrium from Grains.csv)
- Material lookup: get_material_stiffness (elastic constants for Au, Cu, Al, Fe, Ni, Ti, W, Si, CeO2)
- d0 correction: correct_d0_equilibrium (two-step isotropic strain + stress correction)
- Slip systems: analyze_slip_systems (Schmid factors, Taylor factor, yield proximity)
- Grain summary: read_grains_summary (statistics of a Grains.csv file)

PARAMETER VALIDATION — When the user asks to validate, diagnose, or check a parameter file:
1. Call validate_parameter_file or diagnose_parameter_file DIRECTLY with the directory path
   (e.g. param_file="test1", pipeline="ff"). The tool auto-finds Parameters.txt or refined_MIDAS_params*.txt.
   Do NOT call list_directory first — the tool handles file discovery.
2. Pipeline arg is required — infer from the user's query:
   - "calibrat" / "integrat" / "lineout" / "caked" → pipeline="ri" (radial integration)
   - "ff-hedm" / "far-field" / "reconstruction" → pipeline="ff"
   - "nf-hedm" / "near-field" / "microstructure" → pipeline="nf"
   - "pf-hedm" / "point-focus" → pipeline="pf"
   - Default to "ri" for integration/calibration queries, "ff" for HEDM workflow queries.

PRE-WORKFLOW VALIDATION — rules for heavyweight HEDM reconstruction (ff, nf, pf):
1. SKIP validate_parameter_file entirely when data_file (.MIDAS.zip or .zarr.zip) is explicitly provided.
   The tool passes --skip-validation to midas-pipeline automatically; file-discovery keys
   (RawFolder, FileStem, StartNr, EndNr) are not required in zarr mode.
   → Just call run_ff_hedm_full_workflow directly with the provided paths.

2. CALL validate_parameter_file ONLY when no data_file is given AND the user has only
   a Parameters.txt + raw frame directory. If validation fails on file-discovery keys
   and a zarr is available, skip to the zarr path (rule 1 above).

3. Do NOT validate before: midas_integrate_2d_to_1d, midas_auto_calibrate, run_gsas_refinement

4. If validation finds real geometry errors (wrong Lsd, BC, Wavelength, SpaceGroup),
   call diagnose_parameter_file and fix those before proceeding.

When the user says "retry", "rerun", or "redo" → call the requested tool IMMEDIATELY. Do NOT validate first.

Standard workflow:
  1. list_directory to find data files
  2. midas_integrate_2d_to_1d for 2D → 1D (produces .zarr.zip)
  4. run_gsas_refinement for peak fitting / lattice refinement on .zarr.zip
  5. Or run_live_analysis for combined integration + refinement in one step
  6. run_ff_hedm_full_workflow or run_nf_hedm_reconstruction
  7. Post-process: match_grains, overlay_ff_nf_results, extract_grain_centroids
  8. Export: convert_nf_to_dream3d

POST-RECONSTRUCTION STRESS ANALYSIS — After FF-HEDM or NF-HEDM completes:
1. Call read_grains_summary to understand the grain population
2. Call get_material_stiffness to look up the material (user must specify material)
3. Call compute_grain_stress with the Grains.csv and material name
4. If d0 correction is needed, call correct_d0_equilibrium
5. For plasticity analysis, call analyze_slip_systems with the load direction
Always report: grain count, mean/std von Mises stress, hydrostatic shift, d0 correction magnitude.

GSAS-II refinement workflow:
  1. If no CIF file → call fetch_cif_from_mp to download one (you have this tool)
  2. Read the CIF path from the fetch result
  3. IMMEDIATELY call run_gsas_refinement with:
     - data_file=<.zarr.zip path>
     - cif_files=[<CIF path>]
     - two_theta_limits=[2.0, 15.0] (ALWAYS set — without limits Rwp will be ~100%)
     - n_cpus=8 (parallelize across histograms)
  4. To RUN a GSAS-II refinement, use run_gsas_refinement — never drive GSAS-II
     itself via run_command. (This is ONLY about invoking a refinement. Reading,
     listing, or grepping files whose NAME or PATH contains "GSAS-II" — e.g. a
     reference dir integrated_data_GSAS-II/ or a colleague's GSAS-II_*.py script —
     is completely fine via run_command/read_file/list_directory.)

CRITICAL: After calling a tool, read the result carefully. Do NOT call list_directory
to verify files you already know about. Use the paths from the tool results directly.

Always report: grains found, convergence quality, Rwp, output file paths.

NEVER mention pyFAI, .poni files, or azimuthalIntegrator. This system uses MIDAS exclusively.
Only report data from actual tool results — never hallucinate file contents or parameters.""",
)

KNOWLEDGE_AGENT = APEXAAgent(
    name        = "KnowledgeAgent",
    temperature = 0.6,
    tool_names  = [
        "query_hedm_knowledge",
        "get_material_properties",
        "get_typical_hedm_parameters",
        "estimate_parameters_from_image",
        "list_common_calibrants",
        "xray_calculate",
        "fetch_cif_from_mp",
        "enumerate_bragg_rings",
        "get_material_stiffness",
    ],
    instructions = """You are an HEDM knowledge expert. You answer from indexed sources, not from memory.

MANDATORY: For ANY conceptual, methodology, or "what is / how does / explain" question,
your FIRST action MUST be a TOOL_CALL to query_hedm_knowledge. Do NOT answer first and
search later.

Reading the tool result:
- The tool returns JSON with "results_count", "excerpts", and "references".
- Each excerpt has fields: source, citation, page, similarity, excerpt.
- "similarity" is in [0,1]. Anything >= 0.30 is a usable match. Anything >= 0.60 is
  a strong match. Do NOT dismiss a match just because the wording differs from your
  expectation — the chunk text and citation are what matter.

How to write the answer:
- If results_count > 0 AND at least one excerpt has similarity >= 0.30:
    1. Build the answer from the excerpt text. Quote or paraphrase the chunks.
    2. Cite EVERY substantive claim inline using the citation field, formatted as
       "(FirstAuthor Year, p.PAGE)" — e.g. "(Bernier 2020, p.36)".
    3. End with a "References:" section listing each unique citation verbatim from
       the tool's "references" list.
    4. Do NOT add background facts the excerpts don't support. If the excerpts are
       narrow, the answer should be narrow.
- If results_count == 0 OR every similarity < 0.30:
    Open with: "No matching sources in the knowledge base — answering from general
    background:" then give the answer. Do NOT fabricate citations.

Never invent citations. Never paraphrase a source you didn't retrieve. If unsure
which excerpts are strong, list them all and let similarity speak for itself.

Other tools:
- get_material_properties for crystallographic data (lattice params, space groups, d-spacings)
- get_typical_hedm_parameters for recommended parameter ranges
- estimate_parameters_from_image to estimate beam parameters from diffraction images
- list_common_calibrants for calibrant materials
- xray_calculate for ANY calculation (NEVER compute manually)
- fetch_cif_from_mp to download CIF files from Materials Project for any material

When the user asks for a CIF file, call fetch_cif_from_mp IMMEDIATELY with the formula.
Report: formula, space group, crystal system, stability, and file path.

When in doubt, call the tool. A grounded "I don't have a source for that" beats a
fluent answer with no citation.""",
)

MOTOR_AGENT = APEXAAgent(
    name        = "MotorAgent",
    temperature = 0.2,   # very low — motor commands must be precise and deterministic
    tool_names  = [
        "get_motor_position",
        "get_motor_status",
        "move_motor_absolute",
        "move_motor_relative",
        "stop_motor",
        "set_motor_velocity",
        "jog_motor",
        "tweak_motor",
        "get_motor_limits",
        "set_motor_limits",
        "set_motor_description",
        "list_motors",
        "home_motor",
    ],
    instructions = """You are a motor control specialist for EPICS-based beamline instruments at APS.

Default IOC prefix is "20idMotSim". Motor PV names: "m1", "m2", etc.
The prefix parameter defaults automatically — you do NOT need to specify it.

MOTOR NAMES: Users can refer to motors by PV name (m1, m2, ...) OR by description
(e.g. "Sample X", "detector z"). The tools auto-resolve descriptions to PV names
via the EPICS DESC field. If the user uses a descriptive name, pass it directly
to the tool — it will resolve automatically.
If unsure which motor the user means, call list_motors first to see all descriptions.

⚠️ CRITICAL — ALWAYS call the tool, NEVER just describe what you would do:
1. User asks to MOVE → call move_motor_absolute (or move_motor_relative) IMMEDIATELY.
   The tool checks limits internally — do NOT call get_motor_status first.
2. User asks POSITION → call get_motor_position.
3. User asks STATUS → call get_motor_status.
4. User says STOP → call stop_motor IMMEDIATELY.
5. For small steps → use tweak_motor or move_motor_relative.
6. If a move is rejected for limits → call get_motor_limits to show the range.
7. NEVER say "I can move it" — CALL THE TOOL.
8. NEVER call get_motor_status before a move — it wastes a round-trip.
9. For multiple motors → call move_motor_absolute for EACH one. Do ALL of them.

After each move report: target, final RBV, and units.""",
)

VISUALIZATION_AGENT = APEXAAgent(
    name        = "VisualizationAgent",
    temperature = 0.3,
    tool_names  = [
        "run_midas_viewer",
        "plot_lineout_series_contour",
        "list_directory",
        "get_file_info",
    ],
    instructions = """You are a visualization specialist for HEDM diffraction data at APS.

Pick the RIGHT tool for the request — do NOT hand-roll plots with run_command:
- A SERIES of 1D patterns → one contour/waterfall/operando plot (a folder of
  *.xye/*.xy/*.dat, "contour", "waterfall", "operando", "stack", "time/frame vs
  2θ") → use plot_lineout_series_contour. MIDAS has NO series viewer and .xye is
  NOT a MIDAS-native lineout format — its Qt lineout viewers open BLANK on .xye —
  so do NOT route .xye or a series through run_midas_viewer.
- A SINGLE MIDAS-native artifact (one *.zarr.zip, *_corr.csv, Grains.csv, .mic,
  raw .tif/.ge/.h5, live .bin) → use run_midas_viewer (it handles MIDAS paths/Python).
Do NOT use run_command or check_environment for plotting — these tools do everything.
Your job is to LAUNCH/produce the visualization, not analyze data.

⚠️ CRITICAL: Call run_midas_viewer EXACTLY ONCE per request. Pick the single BEST viewer. NEVER launch multiple viewers.

STEP 1: Find the data file. Call list_directory ONCE on the most specific path given.
  - If the user gives an integration/ path → list that directory directly. Do NOT also list the parent.
  - Integration outputs (*_lineout.xy, *.zarr.zip, *_lineout.bin) are in <dir>/integration/
  - Calibration outputs (*_corr.csv) are in the calibration directory
  - Always prefer *.zarr.zip over plain *.hdf or *.caked.hdf — the zarr archive is the complete output
  - ONE list_directory call is sufficient. If the user gave the path, trust it.
STEP 2: Match the file to the correct viewer — pick ONE:

| File pattern | viewer name | When to use |
|---|---|---|
| a FOLDER of *.xye/*.xy (a series) | plot_lineout_series_contour | Contour / waterfall / operando plot across many patterns → PNG + interactive HTML |
| *_corr.csv | plot_calibrant_results | Calibration fit, calibration QC, lattice-vs-η |
| *.zarr.zip (integration output) | plot_caked_peaks | BEST for integration results — shows 2D heatmap + 1D profile together |
| *_lineout.xy (2-col from MIDAS integrator) | plot_caked_peaks on the *.zarr.zip | No dedicated viewer for 2-col lineout; use zarr viewer instead |
| *_lineout.xy (4-col from extract_lineouts) | plot_lineout_results | Only for extract_lineouts.py output — 4 columns (2θ, raw, bg, corrected) |
| compare calibrant vs sample lineouts | plot_lineout_comparison | Use --paramFN for ring position overlay |
| *_lineout.bin (live) | live_viewer | Real-time GPU streaming monitor |
| *_caked.hdf.zarr.zip | plot_caked_peaks | Caked data, integrated image, 2D heatmap (PREFERRED for caked data) |
| *_caked_peaks.h5 | plot_caked_peaks | Peak fitting results |
| Raw .tif/.ge/.h5 | ff_asym_qt | Raw detector image, diffraction image, ring overlays |
| Grains.csv + .zarr | interactiveFFplotting | FF-HEDM grain map, grain results |
| .mic/.map (NF) | nf_qt | NF-HEDM microstructure |

DISAMBIGUATION — when the user request is ambiguous, pick ONE using these rules:
- "contour" / "waterfall" / "operando" / "stack" / "series" over a FOLDER of
  *.xye/*.xy patterns → plot_lineout_series_contour (NOT run_midas_viewer)
- "calibrated image" / "calibration results" / "calibration fit" → plot_calibrant_results
- "caked image" / "caked data" / "integrated data" / "integration result" → plot_caked_peaks
- "integration results" / "show integration" / "lineout" / "1D profile" → plot_caked_peaks on *.zarr.zip (NOT plot_lineout_results — that only works with 4-col extract_lineouts.py output)
- "compare lineouts" / "calibrant vs sample" → plot_lineout_comparison
- "peak fitting results" (4-col .xy from extract_lineouts.py) → plot_lineout_results
- RULE: whenever a *.zarr.zip exists alongside a *_lineout.xy, always prefer the zarr viewer (plot_caked_peaks) — it shows more information
- "raw image" / "diffraction image" / "detector image" → ff_asym_qt
- "grain map" / "grain results" / "FF results" → interactiveFFplotting
- "microstructure" / "NF results" → nf_qt
- For caked .zarr.zip files: ALWAYS use plot_caked_peaks (interactive Qt viewer with heatmap + profile + peak table). Do NOT use plot_integrator_peaks (that is a diagnostic scatter plot, not an interactive viewer).
- If still ambiguous, prefer the MOST PROCESSED result: caked > lineout > raw.

STEP 3: Call run_midas_viewer ONCE with the viewer name and data file path. That's it.

Example:
  User: "plot the calibration results in test1"
  → list_directory to find *_corr.csv
  → run_midas_viewer(viewer="plot_calibrant_results", data_file="/path/to/file_corr.csv")

Notes:
- Pass param_file if refined_MIDAS_params*.txt is available (enables 2θ/Q axes)
- For live_viewer: pass extra_args="--nRBins 2000" (capital R, capital B)
- viz_caking.py: DO NOT USE — use plot_caked_peaks instead
- plot_integrator_peaks: diagnostic scatter only — prefer plot_caked_peaks for interactive viewing

After launching, report ONE line: which viewer was launched and which file. Do NOT read or summarize the data — the GUI shows it.""",
)


# ── Single unified agent (APEXA_AGENT_MODE=single) ────────────────────────────
# One agent, full toolset, one sectioned prompt merging the 5 specialists. This
# is the Claude-Code-style path: the model reasons over a persistent full-context
# transcript and picks the right tools itself — no keyword routing, no regex
# intent-gates. Shared rules are stated ONCE; each domain is a section.
APEXA_AGENT = APEXAAgent(
    name        = "APEXA",
    temperature = 0.4,   # deterministic enough for actions; tool-grounded answers
    tool_names  = [],    # empty = all registered tools
    instructions = """You are APEXA, an autonomous assistant for HEDM synchrotron
experiments at APS. You have ONE persistent conversation with the scientist:
prior tool calls and their results are in the transcript above — that is your
memory. Reason over it, then act.

## Operating style — work like a senior colleague, not a report generator
- BE TERSE AND ACT. Reason in a few words, then DO the thing. Default to the shortest
  reply that moves the task forward. Prefer doing over describing.
- NARRATE THEN ACT (Claude-Code style): before you emit a tool call — or a batch of
  them in one response — write ONE short line saying what you're about to do and why
  (e.g. "Reading Leighann's script to get the dark grid." / "Integrating the 192 JL_Nb
  frames to APEXA_benchmark/…"). The user sees that line; a tool that fires with no
  lead-in looks like nothing is happening. One line is enough — do NOT expand it into a
  plan or re-explain after the result.
- When the user has already told you what to do — named the action, its inputs, and/or
  the output, or said "go / do it / run / perform / proceed", or approved this step —
  EXECUTE NOW: at most one line of what you're doing, then the tool call. Do NOT
  re-explain, re-plan, re-list options, or ask again.
- Do NOT reprint files, parameters, plans, or results the transcript already shows —
  refer to them in a phrase ("the RBin0p5 param file"). No boxed multi-section essays,
  no headers for a single answer, minimal formatting.
- Answer questions in 1–3 sentences; expand only when asked. If a tool already handles
  something (e.g. midas_integrate_series writes xye/ and fxye/), say so in a line — don't
  describe how you'd hand-roll it.
- MULTI-STEP TASKS: when a request chains steps ("remove the old files, then compare, then
  update the report"), do them IN ORDER, ONE clear action at a time, and USE each result
  to decide the next. Batch where a single call covers it: delete many files with ONE
  command (a glob/`rm -rf dir`), compare two directories with ONE compare call — don't fire
  a call per file or re-issue a call whose result you already have. When you have the data
  the step needs, MOVE ON; when all steps are done, STOP and write the answer. Never re-run
  a comparison or listing "to be sure" — the first successful result stands.

## Core behaviour
- SMALL TALK & META: for a greeting ("hi", "hello", "hey"), a thanks, or a
  question about what you are or what you can do, just reply in 1–2 friendly
  sentences (for "what can you do?", give a brief capability hint). Do NOT call
  any tool, do NOT list directories, and never report an "analysis" for these.
- ANSWER FROM THE TRANSCRIPT FIRST. If the user asks about something you already
  did (its outcome, how you computed it, what to do next, "did it work?"), answer
  from the tool results already in the transcript. Do NOT re-run tools or
  re-discover files to re-derive what you already know.
- VERIFY EMPIRICAL DISCREPANCIES — DON'T HYPOTHESIZE. The above applies to what you
  DID (settings, which files, what a manifest recorded). It does NOT apply when the
  user reports the RESULT looks wrong: "the plot shows almost no dark subtraction",
  "did the dark actually get subtracted?", "these intensities look off", "the peak
  is missing". A manifest records what was INTENDED, not what the data shows — so
  that is a NEW empirical question. Do NOT answer with a list of possible causes.
  INSPECT and settle it: read the manifest AND the actual frames (inspect_dataset_file
  on the sample and its dark, compare mean/max counts before vs after, check the dark
  dataset path actually holds nonzero data), then report ONE definite finding with the
  numbers. "Here are 5 things that might be happening" is not an answer when a tool
  can settle it in one call.
- A QUESTION or STATUS prompt is NOT a command to run anything. If the message is
  interrogative or a status check ("what's happening?", "where are we?", "what's
  the status/outcome?", "is it done?", "why…?"), ANSWER it in prose from the
  transcript and STOP. Do NOT list directories, inspect files, or launch
  calibration/integration/etc. to "find out" — just answer.
- Read-only / lookup tools (list_directory, read_file, get_file_info,
  inspect_dataset_file, xray_calculate, query_hedm_knowledge, get_motor_position,
  validate/diagnose, viewers) — just run them, no confirmation.
- ACT ON CLEAR REQUESTS; confirm only when truly needed. If the action and its key
  inputs/output are specified — or the user said "go/do it/run/perform", or already
  approved this step — EXECUTE NOW: one line of what you're doing, then the TOOL_CALL.
  Do NOT re-present a plan, re-list options, or wait again for something already asked
  for. Present-a-plan-then-WAIT (no TOOL_CALL in the plan message; wait for "go")
  applies ONLY to: (a) a genuinely AMBIGUOUS request, (b) a DESTRUCTIVE op (deleting or
  overwriting existing data), or (c) a MOTOR/hardware command. For those:
    • AMBIGUOUS or consequential → DISCUSS OPTIONS. Ground it first with
      recommend_workflow on the input path, then present, compactly:
        (1) recommended settings + WHY, and 1–2 alternatives with their trade-offs
            (e.g. dark_source file vs embedded; dark_kind after vs before;
             local-cpu vs remote-gpu; RBin/EtaBin choices; v1 vs v2 engine),
        (2) the key input parameters (image/params/dark, HDF5 data location),
        (3) the OUTPUT location AND formats (e.g. xye/ + fxye/), and
        (4) for batch/long jobs, the compute tier + rough cost.
      Then wait for "go" or an adjustment.
    • FULLY SPECIFIED (action + output location both given, e.g. "integrate this
      series, dark_after, write to /scratch/.../APEXA_benchmark") → skip the
      options discussion: state in ONE line what you'll do + the EXACT output
      path, then the TOOL_CALL. No "shall I proceed?" nagging.
- OUTPUT LOCATION: if the user didn't specify where to write and there's no clear
  MIDAS default, ASK in the plan ("write to <MIDAS default> or a path you choose?").
  Do NOT invent a folder scheme, and NEVER run to a default then copy (see the
  write-where-asked rule).
- RECOMMEND & SUMMARIZE on request: when the user asks "what should I do with
  this data?", "what can you do?", "what are my options?", or points you at a
  file/dir without a verb, call recommend_workflow (path for a data-specific
  recommendation; empty path for a grouped capability summary) and relay its
  grounded recommendation + alternatives. Don't invent tools or parameters — cite
  what recommend_workflow returns. This is advisory: it runs nothing.
- Stay on the user's CURRENT dataset/directory AND current stage. Do not drift to
  an unrelated scan, and do not wander into a later pipeline stage (e.g. GSAS-II
  refinement, fetch_cif_from_mp) that the user did not ask for — even if an earlier
  plan mentioned it. Debugging calibration means calibration only.
- When you must write a Python script (debug/test/analysis for calibration,
  integration, or refinement), import the vetted primitives instead of hand-rolling
  I/O: `import sys; sys.path.insert(0, "<APEXA repo>"); import apexa_lib as ax` →
  ax.load_image / ax.load_dark (never hand-pick an HDF5 dataset — that grabs
  metadata like /WM/ADCoreVersion instead of /exchange/data), ax.read_params /
  ax.write_params / ax.compare_geometry, ax.read_lineout, ax.read_manifest.
- Prefer a compound tool that returns many values in ONE call over looping a
  primitive; emit independent calls together.
- GROUND parameters, then APPLY them — reading is not applying, and do not assume a
  convention. Whose settings apply is the USER's choice: if they name a reference to
  reproduce, read the actual values from THAT source (their script/params/notes — not
  prose or memory) and PASS them into the call; if there is no reference, ask for
  their preferred settings or use documented defaults — never silently inherit
  calibration defaults. For integration the grid is the usual trap: the calibration
  param file's RMin/RMax/RBinSize give a DIFFERENT range than intended. Specify the
  grid in whatever convention the user/reference uses — midas_integrate_series takes
  it in radius (r_min/r_max), 2θ (two_theta_min/max), OR Q (q_min/q_max) with
  n_channels, and converts to the integrator's radius grid. Pilot ONE frame and
  confirm the grid before running the full series.
- VERIFY before claiming parity. When comparing to a reference, call
  compare_integrated_series(dir_a, reference_dir) — it's convention-agnostic (compares
  the files' own x-axis). If grid_match is false the outputs are NOT comparable: re-run
  on the reference grid; do not report agreement, peak offsets, or a verdict. Never
  state numeric agreement ("center within 0.04 px", "strain 12 vs 28 µε") you did not
  obtain from a tool that actually read both sources THIS turn.
- ANNOUNCE side effects up front (Claude-Code style): before ANY tool that writes
  files or runs a batch/long job (calibration, integration, workflows, refinement,
  motor moves, conversions), state in ONE line what you'll do and the EXACT output
  path/folder, then call the tool. Tools also print/return their resolved output
  path — cite that; never invent an output filename or claim a location you didn't
  get back from a tool.
- WRITE WHERE THE USER ASKED — never run-to-default-then-copy. When the user names
  a destination (e.g. "put it in APEXA_benchmark"), pass that path DIRECTLY as the
  tool's output arg (`result_folder=`, `output_dir=`, etc.) on the ONE call, so the
  tool writes there. Do NOT let the tool write to its default location and then
  shuffle files with run_command/cp/mkdir afterward — that hand-copying is where
  outputs get half-moved (manifest only), then "the data is missing". If you didn't
  pass the destination, the correct fix is to RE-CALL the tool with the right
  `result_folder`, not to copy its output around.
- KEEP THE DATA TREE CLEAN: never write helper/debug scripts, temp params, or
  scratch files into the user's data or output directories. Throwaway working files
  go to the APEXA scratch dir ($APEXA_SCRATCH, else $TMPDIR/apexa_scratch) — the
  server exposes `_apexa_scratch_dir()`. Better: use an MCP tool instead of
  hand-writing a script at all.
- BE QUICK: reach straight for the tool that does the job; don't spend turns
  probing with run_command / repeated inspection when a tool already discovers what
  it needs (paths, darks, geometry). Fewer decisive calls beat many exploratory
  ones — that is what keeps APEXA responsive.
- Report ONLY what tools actually returned. Never fabricate paths, counts, or
  parameter values. Tool results are ground truth; if one contradicts a user
  claim, the tool is correct.
- You ARE an agent with WORKING tools that return real output. To inspect a file
  or directory, CALL the tool (list_directory, read_file, run_command,
  inspect_dataset_file) — never claim you "can't execute/read/access" anything, and
  never ask the user to run a command and paste the output. When the user names a
  directory, call list_directory on that path (do NOT run the bare path as a shell
  command). Default to acting; only report a non-action when a tool was genuinely
  blocked or errored (quote the error).
- Tools are SYNCHRONOUS — there is NO background job and you are NOT re-invoked to
  "report later". Never say an operation was "launched/submitted" or that you will
  "report when it completes": emit the tool call now and you get its result in the
  same reply.
- NEVER mention pyFAI, .poni files, or azimuthalIntegrator — this system uses
  MIDAS exclusively (calibration output is refined_MIDAS_params*.txt).

## Calibration
Find the calibrant image (list_directory once), then call midas_auto_calibrate
with the ORIGINAL image path (pass output_dir only if the user gave a location —
otherwise ask per the plan-and-confirm rule; the tool symlinks the image into
output_dir and auto-resolves the dark, so do NOT copy/move images by hand with
run_command). parameters_file is optional — AutoCalibrateZarr auto-detects
calibrant (CeO2/LaB6), energy (keV in filename), and pixel size. If energy is in
keV, use xray_calculate to get wavelength. Report refined BC, Lsd, tilts,
convergence quality, and the APEXA_calibration.json manifest path. (Attenuated
calibrants: att0 may saturate; if a mid-att like att3 fits no rings, try a lower
att such as att1/att2.)

## Integration & analysis
- 2D→1D: midas_integrate_2d_to_1d (single file), midas_integrate_series (MANY
  separate files — a scan/series: call it ONCE, never loop the single-file tool),
  or midas_batch_integrate (a frame range inside one file). For a directory of
  files, use midas_integrate_series with max_files=3 first (pilot), then the full set.
- FF/NF/PF-HEDM: run_ff_hedm_full_workflow / run_nf_hedm_reconstruction /
  run_pf_hedm_workflow. Post-process: match_grains, calculate_misorientation,
  overlay_ff_nf_results, extract_grain_centroids, convert_nf_to_dream3d.
- Stress (post-reconstruction): read_grains_summary → get_material_stiffness →
  compute_grain_stress → correct_d0_equilibrium / analyze_slip_systems.
- Parameter checks: validate_parameter_file / diagnose_parameter_file take a
  directory path directly (they auto-find Parameters.txt / refined_MIDAS_params*);
  the pipeline arg is required (ri / ff / nf / pf — infer from the request). SKIP
  validation when a .zarr.zip/.MIDAS.zip data_file is given, or for integration/
  calibration/refinement, or on "retry"/"rerun".
- The standard pipeline ENDS at the integrated lineout/zarr + QC ("refinement-
  ready"). Do NOT auto-advance into GSAS-II refinement after integration — stop
  and report the integration result. Run refinement ONLY when the user explicitly
  asks for it in their message.
- GSAS-II refinement (only when explicitly requested): if no CIF, fetch_cif_from_mp
  first, then run_gsas_refinement with data_file=<.zarr.zip>, cif_files=[<cif>],
  two_theta_limits=[2.0,15.0] (ALWAYS set — without limits Rwp ≈ 100%), n_cpus=8.
  Never use run_command for GSAS.

## Knowledge (conceptual / "what is" / "how does" / "explain")
Your FIRST action MUST be query_hedm_knowledge — answer from indexed sources, not
memory. Build the answer from the returned excerpts (similarity ≥0.30 usable,
≥0.60 strong); cite every substantive claim inline as "(FirstAuthor Year, p.PAGE)"
and end with a "References:" section. If results_count==0 or all <0.30, open with
"No matching sources in the knowledge base — answering from general background:"
and never fabricate citations. Also: get_material_properties, get_typical_hedm_parameters,
xray_calculate (never compute X-ray math by hand), fetch_cif_from_mp.

## Visualization
Use run_midas_viewer for ALL plotting (never run_command). Find the file
(list_directory once on the most specific path), pick the SINGLE best viewer, call
run_midas_viewer EXACTLY ONCE. Viewer choice: *_corr.csv → plot_calibrant_results;
integration *.zarr.zip / caked → plot_caked_peaks (preferred, prefer zarr over a
2-col *_lineout.xy); 4-col extract_lineouts *_lineout.xy → plot_lineout_results;
calibrant-vs-sample → plot_lineout_comparison; live *_lineout.bin → live_viewer;
raw .tif/.ge/.h5 → ff_asym_qt; Grains.csv+.zarr → interactiveFFplotting; NF .mic/.map
→ nf_qt. Prefer most-processed (caked > lineout > raw). Do NOT read the data file;
report one line (viewer + file). For a simple "what's this image?" question, answer
in text (inspect_dataset_file or its filename/stats) — do NOT launch a GUI viewer.

## Motor control
Default IOC prefix "20idMotSim" (auto — don't specify). Users may name motors by PV
(m1, m2) or description ("Sample X") — pass either; tools auto-resolve via DESC (use
list_motors if unsure). Always call the tool, never just describe: move →
move_motor_absolute/relative (checks limits internally — do NOT get_motor_status
first); position → get_motor_position; stop → stop_motor; small step → tweak_motor;
rejected for limits → get_motor_limits. Multiple motors → call for EACH. Report
target, final RBV, units.""",
)


# ── Tool-use system preambles ────────────────────────────────────────────────

# Structured transport (argo-proxy). The model receives real tool schemas and
# emits real tool_calls, so none of the format coaching in _TOOL_PREAMBLE below
# is needed — describing a text protocol here would only invite the model to
# emulate one we no longer parse. Keep this SHORT: it is a per-request cost, and
# the behavioural rules that matter are enforced in code (execution ledger,
# deletion permission gate, handbook guardrails), not hoped for in a prompt.
_STRUCTURED_PREAMBLE = """You are APEXA, connected to live MCP servers at a synchrotron beamline.

Use the provided tools to do real work. Report ONLY what tools actually returned —
never invent file paths, counts, or parameter values. If you have not run a tool,
say so plainly instead of describing what it would have shown; fabricated values are
dangerous at a beamline. Tool calls are synchronous: there is no background job and
you will not be re-invoked later, so never promise to report something "once it
finishes" — call the tool now and report the result.

When a tool fails, read the error and adapt. Do not repeat an identical failing call.
"""

_TOOL_PREAMBLE = """⚠️ CRITICAL: YOU HAVE TOOLS — USE THEM.

You are APEXA, connected to live MCP servers with real analysis tools.
When the user gives a COMMAND (calibrate, integrate, list files, calculate,
run workflow), you MUST call the appropriate tool IMMEDIATELY.

⚠️ TOOL CALLING FORMAT — YOU MUST USE THIS EXACT FORMAT:

TOOL_CALL: tool_name
ARGUMENTS: {"param1": "value1", "param2": "value2"}

Examples of CORRECT behavior:

User: "Calculate d-spacing for (110) plane in bcc iron"
✅ CORRECT:
TOOL_CALL: xray_calculate
ARGUMENTS: {"calculation_type": "d_from_hkl", "h": 1, "k": 1, "l": 0, "material": "Fe"}

User: "List files in the current directory"
✅ CORRECT (use CWD as absolute path):
TOOL_CALL: list_directory
ARGUMENTS: {"path": "<CWD>"}

User: "Calibrate the CeO2 image in test5"
✅ CORRECT (prepend CWD to relative path):
TOOL_CALL: list_directory
ARGUMENTS: {"path": "<CWD>/test5"}

User: "Validate the parameter file in /home/user/data/scan1"
✅ CORRECT (absolute path — use EXACTLY as given, do NOT prepend CWD):
TOOL_CALL: list_directory
ARGUMENTS: {"path": "/home/user/data/scan1"}

User: "Convert 61.332 keV to wavelength"
✅ CORRECT:
TOOL_CALL: xray_calculate
ARGUMENTS: {"calculation_type": "energy_to_wavelength", "energy_kev": 61.332}

User: "Show me the lineout for CeO2 integration in test1"
✅ CORRECT:
TOOL_CALL: list_directory
ARGUMENTS: {"path": "<CWD>/test1/integration"}
[find *_lineout.xy, then:]
TOOL_CALL: run_midas_viewer
ARGUMENTS: {"viewer": "plot_lineout_results", "data_file": "/full/path/to/lineout.xy", "param_file": "/full/path/to/refined_MIDAS_params_CeO2.txt"}

User: "Plot the caked output"
✅ CORRECT (after finding *_caked.hdf.zarr.zip):
TOOL_CALL: run_midas_viewer
ARGUMENTS: {"viewer": "plot_caked_peaks", "data_file": "/full/path/to/file.caked.hdf.zarr.zip"}

User: "Plot calibration results in test1"
✅ CORRECT (after finding *_corr.csv):
TOOL_CALL: run_midas_viewer
ARGUMENTS: {"viewer": "plot_calibrant_results", "data_file": "/full/path/to/file_corr.csv"}

User: "Refine the caked output with GSAS-II using the CeO2 CIF"
✅ CORRECT (ALWAYS include two_theta_limits and n_cpus):
TOOL_CALL: run_gsas_refinement
ARGUMENTS: {"data_file": "/path/to/CeO2_caked.hdf.zarr.zip", "cif_files": ["/path/to/CeO2.cif"], "two_theta_limits": [2.0, 15.0], "n_cpus": 8}

User: "Fetch a CIF file for CeO2"
✅ CORRECT:
TOOL_CALL: fetch_cif_from_mp
ARGUMENTS: {"formula": "CeO2"}

User: "Run integration and refinement on the scan data"
✅ CORRECT:
TOOL_CALL: run_live_analysis
ARGUMENTS: {"backend": "batch", "param_file": "/path/to/params.txt", "data_file": "/path/to/data.h5", "cif_files": ["/path/to/phase.cif"]}

User: "Move motor m1 to 25.5"
✅ CORRECT:
TOOL_CALL: move_motor_absolute
ARGUMENTS: {"motor": "m1", "position": 25.5}

User: "Where is motor m1?"
✅ CORRECT:
TOOL_CALL: get_motor_position
ARGUMENTS: {"motor": "m1"}

❌ WRONG — NEVER do these:
- NEVER calculate d = a/√(h²+k²+l²) yourself — call xray_calculate
- NEVER say "you can use ls" or "here's how to do it in Python"
- NEVER say "Let me proceed" or "I can move it" without actually calling a tool
- NEVER describe what you WOULD do — DO IT with TOOL_CALL
- NEVER read_file to show plot data — launch the viewer with run_midas_viewer
- NEVER use run_command for MIDAS viewers — always use run_midas_viewer tool
- NEVER use run_command for GSAS-II, refinement, or peak fitting — always use run_gsas_refinement
- NEVER use run_command for integration + refinement pipelines — always use run_live_analysis
- NEVER try to construct Python paths manually — run_midas_viewer handles paths
- NEVER prepend CWD to an absolute path the user gave you
- NEVER switch back to CWD mid-chain — if the user said "/some/path", ALL subsequent
  tool calls for that request must use "/some/path" (not CWD)
- NEVER guess filenames — call list_directory on the target directory first, then use
  the EXACT filenames from the listing
- NEVER mention pyFAI, .poni files, or azimuthalIntegrator — this system uses MIDAS only
- NEVER hallucinate tools, files, or parameters that don't exist in tool results
- NEVER claim to have read data from a file you didn't actually call read_file on
- Only report information that came from actual tool results, not from your training data

⛔ ANTI-HALLUCINATION — READ THIS CAREFULLY:
NEVER generate fake tool results. NEVER fabricate parameter values (Lsd, Wavelength,
LatticeConstant, BC, PixelSize, etc.). NEVER produce validation reports, file contents,
or analysis results from your training data. If the user asks you to validate, diagnose,
read, or analyze a file — you MUST call the actual tool first. If you don't know a value,
call the tool to look it up. Your training data is NOT a substitute for real tool results.
Presenting fabricated data as real results is DANGEROUS at a beamline — wrong values can
damage equipment or ruin experiments.

🔒 TOOL RESULTS ARE GROUND TRUTH — NEVER CAPITULATE:
When a tool result and a user's claim conflict, the TOOL IS CORRECT. Do NOT:
- Apologise for what the tool returned
- Agree with the user's count/value/filename if it contradicts the tool
- Re-run a tool just because the user challenges its output
- Say "you're right" when the tool result proves otherwise

DO say: "The tool returned [X]. I'm confident that's correct — the listing shows
[exact tool output]." Then offer to re-run the tool ONCE if the user insists,
and again report the tool result verbatim.

This applies especially to: file counts, frame numbers, lattice parameters, beam
centre coordinates, detector distances, and calibration residuals. These values come
from measurements — the instrument does not make counting errors.

The ONLY exception: if you made an arithmetic error SUMMARISING a tool result
(e.g., the tool returned 20 files and you wrote 21), then correct yourself and
state the correct value from the tool result. Do NOT apologise beyond one word.

✅ SHELL UTILITIES VIA run_command — USE THESE FREELY:
run_command is available for grep, awk, sed, find, wc, sort, uniq, diff,
head, tail, cat, ls, du, stat, and other standard utilities.
Use them whenever they are faster or more precise than a dedicated tool.

Pipes (|), semicolons (;), &&, ||, and redirections (>, >>) all work.
Each executable in the pipeline must be in the allowed list.

CORRECT patterns:
  # Count .h5 files in a directory
  TOOL_CALL: run_command
  ARGUMENTS: {"command": "find /path -name '*.h5' -type f | wc -l"}

  # Search inside a parameter file and preview
  TOOL_CALL: run_command
  ARGUMENTS: {"command": "grep -n 'Wavelength\\|LatticeConstant\\|Lsd' /path/refined_params.txt"}

  # Find the most recently modified parameter file
  TOOL_CALL: run_command
  ARGUMENTS: {"command": "find /path -name 'refined_MIDAS_params*.txt' | sort -t_ -k1 | tail -n 1"}

  # Count unique grain orientations in a CSV
  TOOL_CALL: run_command
  ARGUMENTS: {"command": "awk -F, 'NR>1 {print $3}' /path/Grains.csv | sort | uniq | wc -l"}

  # Preview a large file and filter for errors
  TOOL_CALL: run_command
  ARGUMENTS: {"command": "grep -i 'error\\|warning\\|failed' /path/autocal.log | head -n 20"}

  # Check sizes of all result files
  TOOL_CALL: run_command
  ARGUMENTS: {"command": "ls -lh /path/*.csv && du -sh /path/integration/"}

  # Compare two parameter files
  TOOL_CALL: run_command
  ARGUMENTS: {"command": "diff /path/old_params.txt /path/new_params.txt"}

  # Save grep output to a file
  TOOL_CALL: run_command
  ARGUMENTS: {"command": "grep 'Wavelength' /path/params.txt > /path/wavelength_check.txt"}

  # Multi-command inline script via bash -c (equivalent to Claude Code's Bash tool)
  TOOL_CALL: run_command
  ARGUMENTS: {"command": "bash -c 'mkdir -p /path/ceo2_att3 && cp /path/Ceria_63keV_900mm_100x100_att3_1p0s_012220.h5 /path/ceo2_att3/'"}

  # Loop over files to create output directories for each calibrant
  TOOL_CALL: run_command
  ARGUMENTS: {"command": "bash -c 'for f in /path/Ceria_*.h5; do name=$(basename $f .h5); mkdir -p /path/cal_$name; done'"}

  # Check convergence quality across all calibration runs
  TOOL_CALL: run_command
  ARGUMENTS: {"command": "bash -c 'for f in /path/*.corr.csv; do echo \"=== $f ===\"; tail -1 $f; done'"}

NOTE: rm/rmdir/unlink are NOT in the allowed list — deletion via run_command
will return "Command not allowed". Use the write_file tool or ask the user
to delete manually from the terminal.

❌ SPECIFIC WRONG EXAMPLES:
User: "run GSAS-II refinement on the integrated data"
WRONG: TOOL_CALL: run_command  ARGUMENTS: {"command": "GSAS-II ..."}
RIGHT: TOOL_CALL: run_gsas_refinement  ARGUMENTS: {"data_file": "/path/to.zarr.zip", "cif_files": ["/path/to.cif"], "two_theta_limits": [2.0, 15.0], "n_cpus": 8}

User: "refine the caked data"
WRONG: TOOL_CALL: run_command  ARGUMENTS: {"command": "python gsas_ii_refine.py ..."}
WRONG: TOOL_CALL: run_gsas_refinement  ARGUMENTS: {"data_file": "...", "cif_files": ["..."]}  ← missing two_theta_limits!
RIGHT: TOOL_CALL: run_gsas_refinement  ARGUMENTS: {"data_file": "/path/to.zarr.zip", "cif_files": ["/path/to.cif"], "two_theta_limits": [2.0, 15.0], "n_cpus": 8}

RULES:
1. For ANY X-ray calculation → TOOL_CALL: xray_calculate
2. For file listing → TOOL_CALL: list_directory
3. For reading files → TOOL_CALL: read_file
4. For calibration → TOOL_CALL: midas_auto_calibrate  ARGUMENTS: {"image_file": "/path/to/file.h5"}
   (parameter is image_file, NOT data_file — always use image_file)
5. For integration → TOOL_CALL: midas_integrate_2d_to_1d
6. For GSAS-II refinement → TOOL_CALL: run_gsas_refinement (needs .zarr.zip + CIF)
7. For combined integration + refinement → TOOL_CALL: run_live_analysis
8. For fetching CIF files → TOOL_CALL: fetch_cif_from_mp
9. For HEDM workflows → TOOL_CALL: the appropriate workflow tool
10. For visualization/plotting → TOOL_CALL: run_midas_viewer (pass viewer name + data file)
11. For motor control → TOOL_CALL: the appropriate motor tool (move, get_position, stop, etc.)

Only generate text WITHOUT a TOOL_CALL when:
- User says hello/greeting
- User asks a conceptual question ("what is HEDM?", "explain calibration")
- User asks what you can do

⚡ BE DECISIVE — DO NOT ASK FOR PERMISSION:
When you have enough context to make a recommendation, MAKE IT and proceed.
Do NOT end responses with "Would you like me to...?" or "Shall I...?" or
"Do you want me to...?" — these stall the workflow and frustrate operators.
Instead: state your recommendation, state why, then either execute it (if
it passes the strategy gate) or say "Type 'go' to proceed."
The operator is in charge — they will redirect you if your recommendation is wrong.

PATH HANDLING — CRITICAL:
- ALWAYS use ABSOLUTE paths in tool arguments
- When the user says "test5", they mean "<CWD>/test5" (where <CWD> is shown below)
- When the user says "current directory", they mean the CWD shown below
- Convert ALL relative paths to absolute by prepending the CWD
- If the user provides an absolute path (starts with /), use it as-is
- NEVER pass just a filename — always include the full directory path

"""

# Plan-first preamble for agents with use_planning=True (currently Analysis).
# Teaches the model the REASON → ACT pattern used by Claude Code and modern
# agentic harnesses: explore/observe first, write a structured plan, then
# execute. The plan and execution happen in the same response — no extra
# round-trips — but the plan MUST precede the first long-running tool call.
_PLAN_FIRST_PREAMBLE = """🧭 ACT ON DIRECT REQUESTS. PLAN ONLY WHEN IT'S GENUINELY COMPLEX.

If the user gives a concrete, actionable instruction you can already carry out
— e.g. "write the report", "integrate cell1", "compare these two files",
"list X", "read Y" — just DO IT NOW: emit the tool call(s) with at most ONE
sentence of rationale. Do NOT produce a multi-phase master plan, and do NOT end
with "Type go to proceed" for something you can already execute. Prefer the
dedicated tool (e.g. write_file to write a report) over shell scripts.

Use the full SITUATION / GAP / PLAN structure below ONLY when the task is
genuinely ambiguous or multi-stage in a way where a wrong move is costly:
a choice among inputs (which calibrant/attenuation), a destructive or
irreversible step, or when you must ask the user to pick. In those cases —
and only those — follow this pattern EXACTLY, in ONE response:

┌─────────────────────────────────────────────────────────────────┐
│ SITUATION: [what you observe — files present, calibrant types,  │
│            conditions, what is already done vs. missing]        │
│                                                                 │
│ GAP: [what is needed before execution can start — e.g., no     │
│       parameter file found; calibration must precede           │
│       integration; which files are candidates and why]          │
│                                                                 │
│ PLAN:                                                           │
│   Step 1. [action] — [rationale: why this file, why this order]│
│   Step 2. [action] — [rationale]                               │
│   Step N. ...                                                   │
│                                                                 │
│ Executing step 1:                                               │
│ TOOL_CALL: tool_name                                            │
│ ARGUMENTS: {...}                                                │
└─────────────────────────────────────────────────────────────────┘

Rules:
- SITUATION and GAP must be filled from ACTUAL tool results or the
  conversation history. NEVER fill them from training-data assumptions.
- PLAN must name the specific files/calibrants chosen and WHY
  (e.g., "att3 CeO2 — mid-range attenuation avoids saturation at att0
  and underexposure at att6").
- If the choice is genuinely ambiguous (multiple equally-valid options),
  state both and ask ONE question. Do not ask permission on every step.
- Emit ALL independent tool calls concurrently in the same TOOL_CALL block.
- After each tool result, update your plan if needed, then continue.

⚠️ COMPOUND OVER PRIMITIVE — REQUIRED:
If you are about to emit the same tool more than twice with sequential
parameters (e.g., xray_calculate for hkl=(1,1,1) then (2,0,0) …), STOP.
A compound tool exists. Find it (description says "all", "enumerate",
"batch", "rings", "summary") and use it ONCE.

After you receive tool results, your DEFAULT next action is to ANSWER or
continue the plan. Only emit another TOOL_CALL block if information you
genuinely need is NOT in any prior tool result.

"""

# Tools that are long-running, irreversible, or require a file/strategy choice.
# Before any of these is dispatched, the runner requires that the model wrote
# at least _STRATEGY_MIN_WORDS words of reasoning prose in the same response.
# If not, the call is rejected and the model is asked to state its strategy
# first. This is the APEXA equivalent of Claude Code's pattern of requiring
# the model to explain before acting on any Bash/Edit call.
#
# Motor motion tools are deliberately excluded here — they have their own
# hardware safety gate and their own confirmation flag (confirm_large_move).
# Adding them here would double-gate and slow down valid motor commands.
_PLAN_REQUIRED_TOOLS: frozenset = frozenset({
    # Calibration (choice of file, calibrant, energy, Lsd)
    "midas_auto_calibrate",
    "run_ff_calibration",
    # Integration (choice of data file, param file, output format)
    "midas_integrate_2d_to_1d",
    "midas_batch_integrate",
    # Refinement (choice of data + CIF + limits)
    "run_gsas_refinement",
    # Combined pipeline (choice of backend, param file, data file, CIF)
    "run_live_analysis",
    # HEDM reconstruction workflows (long-running, choice of param file)
    "run_ff_hedm_full_workflow",
    "run_nf_hedm_reconstruction",
    "run_pf_hedm_workflow",
})

# Strategy gate: before dispatching a _PLAN_REQUIRED_TOOLS call the runner
# checks that the model wrote a plan in the REASON→ACT format. Detection
# looks for any of these structural markers rather than raw word count,
# so "Using att3." (a choice with no rationale) does NOT pass.
# A plan passes if it contains at least one of:
#   - "SITUATION:" or "GAP:" or "PLAN:" (explicit template markers)
#   - "because" / "since" / "in order to" (causal reasoning)
#   - "step 1" / "first," / "first I" (ordered sequence)
#   - "no parameter file" / "calibrat" + "before integrat" (domain sequencing)
# Word-count fallback: ≥20 words of prose regardless of markers (a complete
# sentence with reasoning will naturally reach this).
_STRATEGY_MIN_WORDS = 20   # fallback if no structural markers found
_STRATEGY_MARKERS = re.compile(
    r'(?:'
    r'SITUATION:|GAP:|PLAN:'                          # explicit template
    r'|(?:because|since|in order to|so that)\b'       # causal connective
    r'|step\s+1\b|(?:^|\.\s+|\n)first[,\s]'          # sequence marker
    r'|no\s+param(?:eter)?\s+file'                    # domain gap
    r'|calibrat\w+\s+(?:before|first|must)'           # domain sequencing
    r'|integrat\w+\s+(?:requires?|needs?)\s+calibrat' # domain dependency
    r')',
    re.I | re.MULTILINE,
)

# ── Agent Runner ─────────────────────────────────────────────────────────────

ExecuteToolFn = Callable[[str, Dict], Awaitable[str]]
OnToolResultFn = Optional[Callable[[str, Dict, str], Awaitable[None]]]


class AgentRunner:
    """
    Agentic loop with dual-mode tool calling:
      1. Native API tool_calls (when Argo returns them)
      2. Text-based TOOL_CALL: / ARGUMENTS: parsing (fallback for string responses)

    Delegates tool *execution* back to APEXAClient.execute_tool_call() so that
    ErrorPreventor, SmartCache, ProactiveSuggestions, and ExperimentContext
    all continue to work without any changes.
    """

    # Regex for text-based tool calls
    _TOOL_CALL_RE = re.compile(
        r'TOOL_CALL:\s*(\S+)\s*\n\s*ARGUMENTS:\s*(\{.*?\})',
        re.DOTALL
    )

    def __init__(self, execute_tool_fn: ExecuteToolFn):
        self._execute = execute_tool_fn

    # ── Helpers ─────────────────────────────────────────────────────────────

    def _filter_tools(self, tool_names: List[str],
                      all_tools: List[Dict]) -> List[Dict]:
        if not tool_names:
            return all_tools
        names = set(tool_names)
        return [t for t in all_tools if t["function"]["name"] in names]

    # Bare `TOOL_CALL: name` (any following args parsed separately — Format B).
    _TOOL_CALL_NAME_RE = re.compile(r'TOOL_CALL:\s*([A-Za-z_]\w*)')
    # `ARGUMENTS:` marker (Format A) — the JSON after it is extracted with a
    # brace-balanced scan, not a regex, so nested objects/large commands work.
    _ARGUMENTS_RE = re.compile(r'\s*\n?\s*ARGUMENTS:\s*', re.IGNORECASE)
    # Anthropic native tool block that leaks through Argo as plain text (Format C).
    _TOOL_USE_XML_RE = re.compile(r'<tool_use>(.*?)</tool_use>', re.DOTALL)
    # A `key: value` or `key = value` argument line (Format B body).
    _KV_LINE_RE = re.compile(r'^[-*\s]*([A-Za-z_]\w*)\s*[:=]\s*(.+?)\s*$')
    # Tool-call ATTEMPT markers — used by the anti-confabulation guard to detect
    # that the model tried to call a tool even though nothing parsed/executed.
    _TOOL_ATTEMPT_RE = re.compile(
        r'(TOOL_CALL\s*:|ARGUMENTS\s*:|<tool_call|<tool_use|<invoke\b'
        r'|<built-in function|<function\b|🛠️)', re.IGNORECASE)

    # Phantom-launch / false-deferred-completion guard. APEXA tools are
    # SYNCHRONOUS — there is no background job and the agent is not re-invoked to
    # "report later". A final answer that promises a deferred result ("I'll
    # report … as soon as it completes") or claims a job was "launched/submitted"
    # WITHOUT a tool actually running this turn is a confabulation (observed: the
    # model narrated a calibration launch, ran nothing, and promised a report).
    _PHANTOM_ASYNC_RE = re.compile(
        r"((i'?ll|i will|will|going to)\b[^.\n]{0,80}\b"
        r"(report|update|share|provide|post|send|let you know|come back|follow up|circle back)\b"
        r"[^.\n]{0,90}\b(as soon as|once|when|after|upon|as it)\b[^.\n]{0,50}\b"
        r"(complete|completes|completed|finish|finishes|finished|done|ready|returns?|runs?)\b"
        r"|\b(launched|submitted to|kicked off|now running|is running|is now running|"
        r"running in the background|queued the|started the)\b[^.\n]{0,80}\b"
        r"(report|results?|update|as soon as|once it|when it|shortly|momentarily))",
        re.IGNORECASE)

    # Refusal-to-execute guard. The model falsely claims it lacks the ability to
    # run commands / read files (Opus/Sonnet drift, esp. after compaction), or
    # punts the work to the user ("please run … and paste the output"). APEXA has
    # working tools — this is a self-limitation hallucination, not a real limit.
    _REFUSAL_RE = re.compile(
        r"(i(?:\s+am|'m)?\s+(?:not\s+(?:\w+\s+)?able|unable)\s+to\s+(?:execute|run|read|access|list|open|get)"
        r"|i\s+can(?:not|'t|\s+not)\s+(?:actually\s+)?(?:execute|run|read|access|list|open)"
        r"|(?:please\s+|you\s+(?:can\s+)?)?run\s+(?:the|these|this|it|them)\b[^.\n]{0,50}"
        r"(?:command|commands|yourself|and\s+paste|then\s+paste)"
        r"|paste\s+(?:back\s+)?(?:the|its|that|these)\s+(?:output|results?|contents?|listing)"
        r"|only\s+you\s+can\s+(?:run|produce|execute)"
        r"|no\s+(?:genuine|real|live|actual)\s+(?:file\s+|directory\s+)?(?:contents?|output|listings?)"
        r"|i\s+don'?t\s+(?:actually\s+)?have\s+(?:live|confirmed|fresh|real)\b)",
        re.IGNORECASE)

    @staticmethod
    def _extract_balanced_json(s: str, start: int):
        """From the first '{' at/after `start`, return the brace-balanced JSON
        object substring, respecting double-quoted strings and escapes (so
        nested {} inside a "command" string — e.g. a Python heredoc — don't
        break extraction). Returns None if no balanced object is found.

        This replaces the old non-greedy `\\{.*?\\}` regex, which truncated at
        the first '}' and silently dropped large/nested tool calls — causing the
        model to then confabulate results for a call that never executed.
        """
        i = s.find('{', start)
        if i < 0:
            return None
        depth, in_str, esc = 0, False, False
        for j in range(i, len(s)):
            ch = s[j]
            if in_str:
                if esc:
                    esc = False
                elif ch == '\\':
                    esc = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        return s[i:j + 1]
        return None

    @staticmethod
    def _coerce_arg_value(s: str):
        """Coerce a bare string argument value to int/float/bool/null when it
        clearly is one; otherwise return the de-quoted string. Keeps paths as
        strings (they don't parse as numbers) but turns energy_kev: 61.332 into
        a float, matching what ARGUMENTS:{json} would have produced."""
        v = s.strip()
        if len(v) >= 2 and v[0] in "\"'" and v[-1] == v[0]:
            return v[1:-1]                       # de-quote, keep as string
        low = v.lower()
        if low in ("true", "false"):
            return low == "true"
        if low in ("null", "none"):
            return None
        try:
            return int(v)
        except ValueError:
            pass
        try:
            return float(v)
        except ValueError:
            pass
        return v

    def _parse_text_tool_calls(self, text: str) -> List[ToolCall]:
        """Extract tool calls from model text output, tolerant of format drift.

        Argo strips native tool_calls, so this text path is the only way tools
        execute. Models (notably claudeopus47) drift between three surface forms;
        we accept all of them:
          A. TOOL_CALL: name  +  ARGUMENTS: {json}        (canonical)
          B. TOOL_CALL: name  +  key: value lines         (no ARGUMENTS json)
          C. <tool_use><tool_name>..</tool_name><parameters>..</parameters></tool_use>
        """
        calls: List[ToolCall] = []
        n = 0
        consumed_spans: List[tuple] = []   # (start,end) of Format-A matches

        # ── Format A: TOOL_CALL: name + ARGUMENTS: {json} (primary) ──────────
        # Brace-balanced extraction (not regex) so large/nested-JSON commands
        # parse instead of being silently truncated-and-dropped.
        for nm in self._TOOL_CALL_NAME_RE.finditer(text):
            name = nm.group(1).strip()
            am = self._ARGUMENTS_RE.match(text, nm.end())
            if not am:
                continue   # no ARGUMENTS: here — let Format B try this one
            json_str = self._extract_balanced_json(text, am.end())
            if json_str is None:
                continue
            try:
                args = json.loads(json_str)
            except json.JSONDecodeError:
                continue
            if not isinstance(args, dict):
                continue
            calls.append(ToolCall(id=f"text_tc_{n}", name=name, arguments=args))
            n += 1
            consumed_spans.append((nm.start(), nm.end()))

        # ── Format B: TOOL_CALL: name followed by bare key:value lines ───────
        for nm in self._TOOL_CALL_NAME_RE.finditer(text):
            # Skip TOOL_CALL occurrences already handled by Format A.
            if any(s <= nm.start() < e for s, e in consumed_spans):
                continue
            name = nm.group(1).strip()
            args: Dict = {}
            for line in text[nm.end():].splitlines():
                ls = line.strip()
                if not ls:
                    if args:
                        break          # blank line ends a populated arg block
                    continue
                if ls.startswith("<") or ls.upper().startswith("TOOL_CALL:"):
                    break              # next call / XML / prose — stop
                kv = self._KV_LINE_RE.match(ls)
                if not kv:
                    break              # first non key:value line ends the block
                key = kv.group(1)
                if key.lower() in ("tool_call", "arguments"):
                    break
                args[key] = self._coerce_arg_value(kv.group(2))
            if args:
                calls.append(ToolCall(id=f"text_tc_{n}", name=name, arguments=args))
                n += 1

        # ── Format C: <tool_use> XML block ──────────────────────────────────
        for m in self._TOOL_USE_XML_RE.finditer(text):
            body = m.group(1)
            nm = re.search(r'<tool_name>\s*(.*?)\s*</tool_name>', body, re.DOTALL)
            if not nm:
                continue
            name = nm.group(1).strip()
            args = {}
            pm = re.search(r'<parameters>(.*?)</parameters>', body, re.DOTALL)
            if pm:
                pbody = pm.group(1).strip()
                try:
                    parsed = json.loads(pbody)
                    if isinstance(parsed, dict):
                        args = parsed
                except json.JSONDecodeError:
                    for em in re.finditer(r'<(\w+)>(.*?)</\1>', pbody, re.DOTALL):
                        args[em.group(1)] = self._coerce_arg_value(em.group(2).strip())
            calls.append(ToolCall(id=f"text_tc_{n}", name=name, arguments=args))
            n += 1

        # ── Format D (last resort): decorated UI-style rendering ─────────────
        # The model sometimes mimics APEXA's own tool-call UI instead of
        # emitting a real call, e.g.:
        #     │ 🛠️ │ list_directory │ │
        #     ─── json ───
        #     { "path": "..." }
        #     ───────────
        # Only run when nothing else matched: find a line that is JUST a tool
        # name (after stripping box-drawing/emoji/punctuation) followed within a
        # few lines by a JSON object.
        if not calls:
            _NON_TOOL_WORDS = {"json", "python", "bash", "arguments",
                               "parameters", "tool", "tool_use", "tool_name",
                               "status", "result", "note", "output"}
            lines = text.splitlines()
            for li, line in enumerate(lines):
                bare = re.sub(r'[^A-Za-z0-9_]+', ' ', line).strip()
                if not re.fullmatch(r'[a-z][a-z0-9_]{2,40}', bare):
                    continue
                if bare in _NON_TOOL_WORDS:
                    continue
                # Look ahead for a balanced {...} JSON object, skipping blank /
                # fence lines (e.g. "─── json ───").
                buf, depth, started = [], 0, False
                for nxt in lines[li + 1:li + 12]:
                    s = nxt.strip()
                    if not started and (not s or set(s) <= set("─-—= json")):
                        continue
                    for ch in nxt:
                        if ch == "{":
                            depth += 1
                            started = True
                        if started:
                            buf.append(ch)
                        if ch == "}":
                            depth -= 1
                    if started and depth <= 0:
                        break
                    if not started:
                        break   # first real line wasn't a JSON opener — give up
                if started and depth <= 0 and buf:
                    try:
                        args = json.loads("".join(buf))
                    except json.JSONDecodeError:
                        continue
                    if isinstance(args, dict):
                        calls.append(ToolCall(id=f"text_tc_{n}", name=bare,
                                              arguments=args))
                        n += 1

        return calls

    def _strip_tool_calls_from_text(self, text: str) -> str:
        """Remove tool-call blocks (all 3 formats) to get the prose part."""
        # Format A: TOOL_CALL: name + ARGUMENTS: {json}
        clean = self._TOOL_CALL_RE.sub('', text)
        # Format C: <tool_use>...</tool_use>
        clean = self._TOOL_USE_XML_RE.sub('', clean)
        # Format B: a bare `TOOL_CALL: name` and its trailing key:value lines.
        out_lines: List[str] = []
        skipping = False
        for line in clean.splitlines():
            ls = line.strip()
            if ls.upper().startswith("TOOL_CALL:"):
                skipping = True            # drop this line + following kv lines
                continue
            if skipping:
                if not ls:
                    skipping = False       # blank line ends the kv block
                    continue
                if self._KV_LINE_RE.match(ls):
                    continue               # still inside the arg block — drop
                skipping = False           # prose resumes — keep this line
            out_lines.append(line)
        clean = "\n".join(out_lines).strip()
        # Also remove common preamble patterns the model adds before tool calls
        clean = re.sub(r'(?:I\'ll|Let me|Let\'s)\s+.*?(?:\.|:)\s*$', '', clean, flags=re.MULTILINE).strip()
        return clean

    def _assistant_message(self, resp: AgentResponse, model: str,
                           structured: bool = False) -> Dict:
        """Format assistant message (with tool calls) for conversation history.

        ``structured=True`` (the argo-proxy / OpenAI-compatible path) emits a real
        assistant turn carrying ``tool_calls``, which the next request pairs with
        ``role:"tool"`` results. Every other branch below exists only because Argo
        ``/chat/``'s sanitizer flattens content to a string, forcing tool intent to
        be replayed as prose.
        """
        if structured:
            msg: Dict[str, Any] = {"role": "assistant", "content": resp.content or ""}
            if resp.tool_calls:
                msg["tool_calls"] = [
                    {
                        "id":   tc.id,
                        "type": "function",
                        "function": {"name": tc.name,
                                     "arguments": json.dumps(tc.arguments)},
                    }
                    for tc in resp.tool_calls
                ]
            return msg
        if _native_tools_enabled(model):
            # Native /chat path: feed the assistant's tool intent back as a flat
            # STRING so the payload sanitizer (which json-dumps non-string
            # content) can't corrupt it, and Argo's undocumented follow-up-turn
            # format is sidestepped. Mirrors the _persist_buffer convention.
            parts: List[str] = []
            if resp.content:
                parts.append(resp.content)
            for tc in resp.tool_calls:
                parts.append(f"TOOL_CALL: {tc.name}\nARGUMENTS: {json.dumps(tc.arguments)}")
            return {"role": "assistant", "content": "\n".join(parts)}
        if model.startswith("claude"):
            blocks: List[Dict] = []
            if resp.content:
                blocks.append({"type": "text", "text": resp.content})
            for tc in resp.tool_calls:
                blocks.append({
                    "type":  "tool_use",
                    "id":    tc.id,
                    "name":  tc.name,
                    "input": tc.arguments,
                })
            return {"role": "assistant", "content": blocks}
        else:
            return {
                "role":    "assistant",
                "content": resp.content,
                "tool_calls": [
                    {
                        "id":   tc.id,
                        "type": "function",
                        "function": {
                            "name":      tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in resp.tool_calls
                ],
            }

    def _tool_result_message(self, tc: ToolCall, result: str,
                             model: str, structured: bool = False) -> Dict:
        """Format tool result for next API call (model-specific).

        ``structured=True`` returns the standard OpenAI tool turn — the thing Argo
        ``/chat/`` cannot carry, and the reason the text protocol existed.
        """
        if structured:
            return {"role": "tool", "tool_call_id": tc.id, "content": result}
        if _native_tools_enabled(model):
            # Native /chat path: flat-text user turn for ALL vendors — survives
            # the payload sanitizer and matches the flat-text assistant turn
            # above. (Argo /chat flattens everything to {role, content:str}.)
            return {
                "role":    "user",
                "content": f"[Tool Result for {tc.name}]\n{result}",
            }
        if model.startswith("claude"):
            return {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": tc.id, "content": result}
                ],
            }
        else:
            # For text-based tool calls, feed result back as a user message
            # so the model can process it in the next turn
            return {
                "role":    "user",
                "content": f"[Tool Result for {tc.name}]\n{result}",
            }

    @staticmethod
    def _looks_like_hallucinated_result(text: str) -> bool:
        """Detect model responses that look like fabricated tool output.

        Returns True when the text contains patterns characteristic of fake
        validation reports or parameter value listings that should only come
        from actual tool calls.
        """
        t = text.lower()
        hallucination_markers = [
            "validation result",
            "parameter file validation",
            "validation report",
            "diagnostic report",
            "parameter analysis",
        ]
        param_value_patterns = 0
        for kw in ["lsd:", "wavelength:", "latticeconstant:", "pixelsize:",
                    "bc_x:", "bc_y:", "spacegroupnum:", "ringthresh:",
                    "omegastart:", "omegastep:", "wedge:"]:
            if kw in t:
                param_value_patterns += 1

        has_marker = any(m in t for m in hallucination_markers)
        has_many_params = param_value_patterns >= 3

        return has_marker and has_many_params

    @staticmethod
    def _extract_dir_file_count(messages: List[Dict]) -> Optional[int]:
        """Scan conversation messages for the most recent list_directory result
        and return the total file count it reported, or None if not found.

        Used by _check_count_hallucination to verify that the model's stated
        file/frame count matches what the tool actually returned.
        """
        # list_directory results appear as user messages containing JSON with
        # a "total_files" key, or as the compact listing text "[Tool Result
        # for list_directory]".  Try both forms.
        for msg in reversed(messages):
            content = msg.get("content", "")
            if not isinstance(content, str):
                continue
            if "[Tool Result for list_directory]" not in content and "list_directory" not in content:
                continue
            # Try JSON form first
            m = re.search(r'"total_files"\s*:\s*(\d+)', content)
            if m:
                return int(m.group(1))
            # Fall back to compact-listing summary line: "N directories, M files"
            m = re.search(r'(\d+)\s+(?:directories?,\s*)?(\d+)\s+files?', content)
            if m:
                return int(m.group(2))
        return None

    @staticmethod
    def _check_count_hallucination(text: str, messages: List[Dict]) -> Optional[str]:
        """Return a rejection message if the model asserted a specific file /
        frame count that contradicts what list_directory actually reported.

        This catches the failure mode where the model reads '920 files' from
        the tool result but writes '360 TIFF images' (or any other fabricated
        number) because its training data associates 'aero' with 360-frame
        HEDM rotation scans.

        Returns None if no contradiction is found (including if no prior
        list_directory result exists in the conversation).
        """
        actual = AgentRunner._extract_dir_file_count(messages)
        if actual is None:
            return None

        # Extract ALL integers from the model's text that appear near
        # count-like context words.  We cast a fairly wide net so we don't
        # miss paraphrases like "135+ frames", "00000 to 00359", "N images".
        count_patterns = [
            # "360 TIFF images", "920 files", "135 frames"
            re.compile(r'\b(\d+)\s*\+?\s*(?:tiff|tif|image|frame|file|scan|projection)s?\b', re.I),
            # "numbered from 00000 to 00359"
            re.compile(r'\b00000\s+to\s+0*(\d+)\b', re.I),
            # "total frames: 360"
            re.compile(r'(?:total|frame|file|image)\s+count[:\s]+(\d+)', re.I),
            # "360° with 1° steps" → 360 could come from rotation claim; too
            # broad — only flag if the number also appears in a file-count context
            # (handled by the patterns above).
        ]
        claimed_counts = set()
        for pat in count_patterns:
            for m in pat.finditer(text):
                claimed_counts.add(int(m.group(1)))

        # Allow a ±1 tolerance (sometimes the agent drops header/footer rows).
        for claimed in claimed_counts:
            if abs(claimed - actual) > 1:
                return (
                    f"⛔ COUNT HALLUCINATION DETECTED.\n\n"
                    f"You stated **{claimed}** files/frames but the `list_directory` "
                    f"tool returned **{actual}** files.\n\n"
                    "You MUST NOT invent counts, frame ranges, or angular steps from "
                    "your training data. Report ONLY what the tool told you:\n"
                    f"  • Total files: {actual}\n"
                    "  • File naming pattern: (from the listing above)\n\n"
                    "Rewrite your answer using only the tool result. "
                    "Do NOT claim to know the omega range, frame count, or "
                    "rotation geometry unless a parameter file or metadata tool "
                    "confirmed it."
                )
        return None

    @staticmethod
    def _select_history(history: List[Dict], max_msgs: int) -> List[Dict]:
        """Pick a compact slice of conversation history.

        Naive `history[-max_msgs:]` drops the original user query that
        established context. Keep that first user message AND the most recent
        (max_msgs - 1) messages so payload stays small but the agent doesn't
        forget what the session is about.
        """
        if len(history) <= max_msgs:
            return list(history)
        first_user_idx = next(
            (i for i, m in enumerate(history) if m.get("role") == "user"),
            None,
        )
        if first_user_idx is None or first_user_idx >= len(history) - (max_msgs - 1):
            return list(history[-max_msgs:])
        return [history[first_user_idx]] + list(history[-(max_msgs - 1):])

    # ── Stage-scoped guardrails (Component C helpers) ─────────────────────────

    @staticmethod
    def _maybe_stage_skill_msg(tool_name: str, injected_skills: set) -> Optional[Dict]:
        """(C1) Stage-scoped skill loading.

        When the model uses a tool whose canonical Agent Skill has not yet been
        injected this turn, return a system message carrying that skill body —
        so as a single turn walks the pipeline (calibrate → integrate → refine)
        it picks up each stage's verified handbook procedure at the moment it
        enters that stage. Query-time skill matching only sees the opening
        request; this follows the ACTUAL stage. Returns None when the tool maps
        to no new skill. Mutates `injected_skills` (dedup set)."""
        try:
            new = [s for s in skills_for_tools([tool_name]) if s not in injected_skills]
        except Exception:
            return None
        if not new:
            return None
        blocks = []
        for s in new:
            injected_skills.add(s)          # mark injected even if body is empty
            body = load_skill_text(s)
            if body:
                blocks.append(f"### Canonical procedure: {s}\n{body}")
        if not blocks:
            return None
        return {
            "role": "system",
            "content": (
                "CANONICAL PROCEDURE for the tool you just used — the verified "
                "handbook/notebook steps (exact flags, units, traps). Follow it "
                "for the remaining steps of this stage:\n\n" + "\n\n".join(blocks)
            ),
        }

    @staticmethod
    def _maybe_capsule_msg(tool_name: str, injected_techniques: set,
                           result: Optional[str] = None,
                           arguments: Optional[Dict] = None) -> Optional[Dict]:
        """(C3) Stage-scoped technique-capsule loading.

        The moment the model fires a tool that belongs to a MIDAS technique,
        inject that technique's handbook SPINE (scope, step ORDER, hard rules,
        halt conditions, traps) if it has not been loaded this turn. This is the
        "learn the technique itself" layer at the dispatch moment: the verified
        workflow procedure lands in context before the model runs the next step.

        Technique resolution, in precedence:
          1. THE BRIDGE — a machine-readable ``capsule_technique`` in the tool
             RESULT payload (recommend_workflow/inspect_dataset_file emit it after
             recognizing the raw data). This is what converts "the data looks like
             NF" into "load the NF methodology" — and it fires after the FIRST tool
             (recommend_workflow), i.e. BEFORE calibrate/integrate/workflow tools.
          2. the tool NAME (technique_for_tool) — the original signal, for tools
             whose name already carries the technique (run_ff_hedm_full_workflow …).
          3. the RAW COMMAND (technique_for_command) — when the agent hand-drives a
             MIDAS CLI via run_command/run_remote_command (e.g. a typed workflow
             tool was blocked by a locality rule), the tool name is generic, so we
             recover the technique from the command text. This keeps the handbook
             (scope, ORDER, hard rules, traps) in context on the raw path too —
             exactly where reconstructions have historically walked into silent
             traps (Hbeam 0, collapsed BoxSize, stale RingThresh) unguarded.

        The model can also pull a spine explicitly via learn_technique; all paths
        share the ``injected_techniques`` dedup set so a spine is injected at most
        once per technique per turn. Returns None when nothing resolves or the
        spine is already loaded. Fail-open."""
        if _capsule_registry is None:
            return None
        tech = ""
        # 1) Bridge: read the technique the classifier recognized from the data.
        if result:
            try:
                payload = json.loads(result) if isinstance(result, str) else result
                if isinstance(payload, dict):
                    cand = payload.get("capsule_technique")
                    if not cand:
                        for rec in (payload.get("recommendations") or []):
                            if isinstance(rec, dict) and rec.get("capsule_technique"):
                                cand = rec["capsule_technique"]
                                break
                    if cand and _capsule_registry.has_technique(cand):
                        tech = _capsule_registry._canon(cand)
            except Exception:
                tech = ""
        # 2) Fall back to the tool name.
        if not tech:
            try:
                tech = _capsule_registry.technique_for_tool(tool_name)
            except Exception:
                tech = ""
        # 3) Raw MIDAS command hand-driven via run_command/run_remote_command.
        if not tech and arguments:
            try:
                cmd = arguments.get("command") or arguments.get("cmd") or ""
                if cmd:
                    tech = _capsule_registry.technique_for_command(cmd)
            except Exception:
                tech = ""
        if not tech or tech in injected_techniques:
            return None
        injected_techniques.add(tech)   # mark even if body is empty
        try:
            body = _capsule_registry.spine_context(tech)
            halt = _capsule_registry.halt_checklist(tech)
        except Exception:
            return None
        if not body:
            return None
        halt_block = (f"\n\nHALT CONDITIONS — stop and ask the user if any apply "
                      f"(do not proceed on assumption):\n{halt}") if halt else ""
        return {
            "role": "system",
            "content": (
                "TECHNIQUE HANDBOOK for the workflow you just entered — the verified "
                "MIDAS procedure (scope, step order, hard rules, traps). It is the "
                "source of truth for the remaining steps; follow it and open each "
                "phase doc (open_phase) as you reach it." + body + halt_block
            ),
        }

    @staticmethod
    def _maybe_verifier_msg(tool_name: str, arguments: Dict) -> Optional[Dict]:
        """(C2) Post-stage verifier feedback.

        After an FF reconstruction tool resolves, run the independent on-disk
        output verifier (`handbook_guardrails.verify_ff_outputs`) and, if it
        finds degenerate/empty artifacts, return a user message listing the
        failed invariant checks (with their notebook citations) — so the model
        reacts to ground-truth output inspection rather than the tool's own
        success summary. Returns None when the tool is not an FF reconstruction,
        no result folder is given, or every check passed."""
        if tool_name not in ("run_ff_hedm_full_workflow", "run_ff_pipeline"):
            return None
        args = arguments or {}
        rf = args.get("result_folder") or args.get("resultFolder") or ""
        if not rf:
            return None
        try:
            import handbook_guardrails as _hg
            report = _hg.verify_ff_outputs(rf)
        except Exception:
            return None
        if report.get("status") != "fail":
            return None
        lines = []
        for layer in report.get("layers", []):
            for chk in layer.get("checks", []):
                if chk.get("ok") is False:
                    cite = f"  [{chk['cite']}]" if chk.get("cite") else ""
                    lines.append(
                        f"  • L{layer.get('layer')} {chk.get('name')}: "
                        f"{chk.get('detail')}{cite}"
                    )
        if not lines:
            return None
        return {
            "role": "user",
            "content": (
                "⛔ INDEPENDENT OUTPUT CHECK — the FF reconstruction wrote "
                "degenerate or empty artifacts on disk (this is a direct file "
                "inspection, not the tool's own summary). These are the "
                "silent-failure chains the handbook warns about:\n"
                + "\n".join(lines) + "\n\n"
                "Do NOT report success. Diagnose the ROOT cause (a missing or "
                "degenerate parameter upstream typically produces an empty "
                "InputAll.csv / 0 seeds / a 4-byte IndexBest), fix the parameter "
                "file, and re-run."
            ),
        }

    # ── Main loop ────────────────────────────────────────────────────────────

    async def run(self, agent: APEXAAgent, query: str,
                  provider: ArgoProvider, all_tools: List[Dict],
                  history: Optional[List[Dict]] = None,
                  max_iterations: int = 10,
                  log_entry: Optional[InteractionEntry] = None,
                  on_tool_result: OnToolResultFn = None,
                  history_summary: str = "",
                  transcript: Optional[List[Dict]] = None,
                  single_mode: bool = False,
                  extra_system_context: str = "",
                  preloaded_techniques: Optional[set] = None) -> str:

        tools = self._filter_tools(agent.tool_names, all_tools)

        # Structured transport (argo-proxy / OpenAI-compatible): tool intent and
        # tool results travel as real `tool_calls` / `role:"tool"` messages on
        # EVERY turn. That makes the text TOOL_CALL: protocol — and the drift
        # mitigations built around it — unnecessary, so both are skipped below.
        structured = provider_is_structured(provider)

        # Progressive tool disclosure. Only for the unified agent (tool_names=[],
        # which otherwise means "all 81 schemas, every request") and only on the
        # structured path, where the model can reliably call the two meta-tools.
        # A specialist with an explicit tool list already has a scoped surface.
        _disclosure = structured and not agent.tool_names and disclosure_enabled()
        if _disclosure:
            tools = initial_surface(all_tools)

        # Execution ledger: the integrity primitive. Records what actually ran so
        # the final answer can be checked against execution facts instead of
        # regexes over prose (see apexa_ledger.ToolLedger).
        ledger = ToolLedger()
        ledger.add_grounding(query)
        # Bounded correction attempts, so a model that cannot ground its answer
        # degrades to an explicitly-flagged answer instead of looping.
        _ledger_retries = 0
        _MAX_LEDGER_RETRIES = 2

        # Build system message: strong preamble + agent-specific instructions.
        # Agents with use_planning=True get the plan-first preamble prepended,
        # which (a) tells the model to batch all needed tool calls in one
        # response and (b) explicitly forbids primitive-tool fan-out where a
        # compound tool exists.
        #
        # _TOOL_PREAMBLE documents the text TOOL_CALL: format (~11K chars). On the
        # structured path the model gets real tool schemas, so shipping it would
        # only invite the model to emulate a protocol we no longer parse.
        cwd = str(Path.cwd())
        plan_pre = _PLAN_FIRST_PREAMBLE if getattr(agent, "use_planning", False) else ""
        preamble = _STRUCTURED_PREAMBLE if structured else _TOOL_PREAMBLE

        # Tell the model what it actually IS. Asked "what model are you using?",
        # gpt-oss-120b on ALCF answered "OpenAI's GPT-4 architecture (June 2024)" —
        # a confabulation, and one APEXA can simply prevent, since it knows the
        # answer. Same principle as the ledger: ground the claim in a fact we hold.
        _ident = f"\nYou are currently running on model '{getattr(provider, 'model', 'unknown')}'"
        try:
            from apexa_llm_endpoints import active_endpoint
            _ep = active_endpoint()
            _ident += f" served by {_ep.name} ({_ep.base_url})"
        except Exception:
            pass
        _ident += (". If asked which model or endpoint you are using, report exactly "
                   "this and do not guess from your training data.\n")

        system_content = (plan_pre + preamble + _ident
                          + f"\nCurrent working directory (CWD): {cwd}\n"
                          + agent.instructions)

        # Append tool catalog with parameters so the model knows what to call
        if tools:
            tool_entries = []
            for t in tools:
                fn = t["function"]
                name = fn["name"]
                desc = fn["description"][:120]
                params = fn.get("parameters", {})
                req = params.get("required", [])
                props = params.get("properties", {})
                if props:
                    param_strs = []
                    for pname, pinfo in list(props.items())[:6]:
                        ptype = pinfo.get("type", "string")
                        marker = " (required)" if pname in req else ""
                        param_strs.append(f"      {pname}: {ptype}{marker}")
                    param_block = "\n".join(param_strs)
                    tool_entries.append(f"  - {name}: {desc}\n    Parameters:\n{param_block}")
                else:
                    tool_entries.append(f"  - {name}: {desc}")
            system_content += f"\n\nYour available tools:\n" + "\n".join(tool_entries)

        # Pre-load the canonical Agent Skill(s) for this specialist's tools into
        # the system prompt — the "learning" layer. This lands the verified
        # handbook procedure (exact flags/units/traps) in context BEFORE the model
        # edits a parameter file or fires a tool, closing the gap where skills
        # were only *named* (in recommend_workflow) but never read. Empty for the
        # unified agent (tool_names=[]); that path relies on the deterministic
        # lint gate + RAG instead. Transient guidance: it lives only in the
        # system prompt, never in the persisted transcript buffer.
        try:
            skill_block = skill_context_for_tools(agent.tool_names)
            if skill_block:
                system_content += skill_block
        except Exception:
            pass  # never let skill loading break a query

        # Query-matched skills for single mode: the unified agent has
        # tool_names=[] (so the block above is empty), so the caller
        # (_process_single_loop) pre-computes the relevant skill block by
        # matching the query against the orchestrator keyword map and passes it
        # in here. Same "learning layer, before the model acts" intent.
        if extra_system_context:
            system_content += extra_system_context

        messages = [{"role": "system", "content": system_content}]
        # Compacted summary of older turns (from the orchestrator). Injected as
        # a second system message so it is never dropped by _select_history and
        # always frames the recent verbatim turns. Carries load-bearing facts
        # (energy, paths, calibration params) from beyond the recent window.
        if history_summary:
            messages.append({
                "role": "system",
                "content": (
                    "Summary of earlier conversation in this session "
                    "(older turns, compacted — treat as established context):\n"
                    + history_summary
                ),
            })
        # ── Single-loop mode: persistent full-fidelity transcript ────────────
        # The orchestrator owns `transcript` (its conversation_history) and has
        # ALREADY appended the current {user: query}. It holds prior turns with
        # their tool-call + tool-result messages verbatim — the curated context.
        # Do NOT trim with _select_history (that re-drops tool results, the very
        # data we need to "remember"). Old turns are folded into history_summary
        # by the orchestrator's compaction, injected as the summary system msg.
        if single_mode and transcript is not None:
            messages.extend(transcript)
        else:
            if history:
                # Motor/viz agents need less history (repetitive commands confuse the model)
                hist_limit = 4 if agent.name in ("MotorAgent", "VisualizationAgent") else 8
                selected = self._select_history(history, hist_limit)
                messages.extend(selected)
            messages.append({"role": "user", "content": query})

        # In single mode we persist a CLEAN reconstruction of this turn's tool
        # exchanges (+ final answer) into `transcript`, excluding the in-flight
        # guard/coaching nags that only matter for the current turn.
        _persist_buffer: List[Dict] = []
        def _persist(final_text: str) -> str:
            if single_mode and transcript is not None:
                transcript.extend(_persist_buffer)
                if final_text and final_text.strip():
                    transcript.append({"role": "assistant", "content": final_text})
            return final_text

        last_tool_name = None          # track repeated tool calls (consecutive)
        _last_tool_args = None         # track repeated tool arguments
        repeat_count   = 0
        # Cumulative tool-call counter across ALL iterations of this user turn.
        # Catches iterative fan-out (model emits one primitive call per turn
        # for N turns) which the per-response guard misses.  When any single
        # tool reaches the threshold, we send the same compound-tool redirect.
        _turn_tool_counts: Counter = Counter()
        # Also track unique argument sets per tool so we can distinguish
        # legitimate multi-file reads from true fan-out.
        _turn_tool_args: dict = {}     # tool_name → set of frozen arg strings
        _FANOUT_THRESHOLD = 3          # same as per-response guard
        # Tools where calling N times with N DISTINCT arguments is legitimate.
        # Fan-out only fires when the SAME arguments recur, not on distinct inputs.
        # midas_auto_calibrate is included: calibrating 7 files with 7 different
        # paths is the correct multi-file pattern (no "batch calibrate" tool exists).
        _MULTI_ARG_OK_TOOLS = {
            "read_file", "get_file_info", "run_command",
            # run_remote_command is the SSH sibling of run_command and the primary
            # tool for driving MIDAS on the remote analysis host (e.g. copland).
            # A fresh reconstruction setup naturally issues many DISTINCT remote
            # commands — locate the *_FF.par, read field 9, check disk, probe the
            # MIDAS env — which is exploration, not thrashing. Omitting it here is
            # what truncated a live FF-HEDM session at "5× run_remote_command".
            "run_remote_command",
            "midas_auto_calibrate", "midas_integrate_2d_to_1d",
            "run_gsas_refinement",
            # Read-only inspection tools: calling them on N distinct paths/files
            # is normal exploration, not fan-out. Only repeated IDENTICAL args
            # trip the guard (the arg-diversity check below enforces that).
            "list_directory", "read_document", "inspect_dataset_file",
            "diagnose_parameter_file", "validate_parameter_file",
        }
        # Shell/remote investigation drives real pipelines: many DISTINCT commands
        # are progress, not thrash (locate scripts, source the env, probe files,
        # launch the run — each a different command). These are judged by argument
        # DIVERSITY, not the strict "100% unique" rule above: a repeated probe or
        # two while setting up a remote run must never force-finalize a live
        # session. Requiring every call to be unique (as the strict rule did) is
        # what re-truncated an FF-HEDM setup at "5× run_remote_command" the moment
        # any single command recurred. The iteration cap remains the backstop for
        # genuinely runaway loops.
        _SHELL_INVESTIGATION_TOOLS = {"run_command", "run_remote_command"}
        # Track which _PLAN_REQUIRED_TOOLS have been gate-rejected this turn.
        # On a second rejection of the same tool, strengthen the message to
        # emphasise that plan + TOOL_CALL must be in the SAME response.
        _plan_gate_strikes: Counter = Counter()
        # Whether the conversation history contains a plan from a prior turn.
        # Used to skip the strategy gate when the user approved an existing plan.
        _prior_plan_in_history = any(
            bool(_STRATEGY_MARKERS.search(m.get("content") or ""))
            for m in (history or [])
            if m.get("role") == "assistant"
        )
        _approval_re = re.compile(
            r'\b(yes|go|proceed|ok|sure|start|execute|run\s+it|do\s+it|'
            r'go\s+with\s+this|go\s+ahead|sounds\s+good|let\'?s\s+do\s+it)\b',
            re.I,
        )
        _query_is_approval = bool(_approval_re.search(query)) and len(query.split()) <= 10
        _confab_strikes = 0   # anti-confabulation guard: failed tool-call attempts
        _async_strikes = 0    # phantom-launch guard: false "launched / will report later"
        _refusal_strikes = 0  # refusal guard: false "I can't execute / you run it"
        _empty_strikes = 0    # empty-response guard: model returned nothing this turn

        # ── Stage-scoped guardrails (Component C) ────────────────────────────
        # As the model moves through pipeline stages within ONE turn, (C1) load
        # the canonical skill for each NEW tool it uses — extending the
        # once-per-turn preload to follow the actual stage — and (C2) run the
        # independent output verifier after an FF reconstruction so the model
        # reacts to on-disk invariants (0 seeds, empty InputAll) rather than the
        # tool's own summary. Behind APEXA_STAGE_GUARDRAILS (default on); fully
        # fail-open. Seed the injected-skills set with what was already preloaded
        # into the system prompt so we never re-inject the specialist's own skills.
        _stage_guardrails_on = os.environ.get(
            "APEXA_STAGE_GUARDRAILS", "1").strip().lower() not in ("0", "false", "no", "off")
        _injected_skills: set = set()
        try:
            _injected_skills.update(skills_for_tools(agent.tool_names))
        except Exception:
            pass
        # (C3) Technique-capsule spines injected this turn (dedup shared with the
        # learn_technique meta-tool via handle_meta_tool). Seeded with any spine
        # the caller already front-loaded into the system prompt (single mode,
        # query-scored) so the same technique is not re-injected mid-loop when its
        # first FF/NF/PF tool fires.
        _injected_techniques: set = set(preloaded_techniques or ())

        for _ in range(max_iterations):
            # `tools` is passed unconditionally; ArgoProvider only attaches it to
            # the payload when APEXA_NATIVE_TOOLS is set, so the OFF path is
            # byte-for-byte identical to the text-based flow.
            response = await provider.chat(messages, agent.temperature, tools=tools)

            # ── Mode 1: Native API tool_calls ──
            if response.tool_calls:
                ledger.note_emitted(tc.id for tc in response.tool_calls)
                # Cross-iteration fan-out check for native tool_calls. Skipped on
                # the structured path: it is a drift mitigation, and interrupting a
                # model that is legitimately iterating (e.g. walking a directory
                # tree during a debug session) is the exact regression the old
                # thrash floor caused.
                _turn_tool_counts.update(tc.name for tc in response.tool_calls)
                # Track distinct arg fingerprints on the NATIVE path too. The
                # thrash floor (Mode-2 block) judges shell/remote tools by arg
                # diversity, but it reads a cross-mode counter: if the count is
                # bumped here without also recording args, a native-driven turn
                # (claude* uses native tool_calls) shows "N calls / 1 distinct"
                # and false-trips on legitimately diverse commands.
                for _tc in response.tool_calls:
                    _turn_tool_args.setdefault(_tc.name, set()).add(
                        json.dumps(_tc.arguments, sort_keys=True))
                _worst_tool, _worst_count = _turn_tool_counts.most_common(1)[0]
                if _worst_count >= _FANOUT_THRESHOLD and not single_mode and not structured:
                    print(f"  \033[33m⚠ cumulative fan-out:\033[0m {_worst_count}× {_worst_tool}")
                    messages.append(self._assistant_message(response, provider.model))
                    messages.append({
                        "role": "user",
                        "content": (
                            f"⛔ CUMULATIVE FAN-OUT: you have now called `{_worst_tool}` "
                            f"{_worst_count} times across this turn. "
                            "Use the compound tool that returns all values in ONE call instead. "
                            "Check your tool list for a tool whose description says 'all', "
                            "'enumerate', 'batch', 'rings', 'summary', 'report', or 'inspect'. "
                            "Call it ONCE and then ANSWER the user."
                        ),
                    })
                    continue
                messages.append(self._assistant_message(response, provider.model, structured))
                _emit_narration(response.content or "")   # narration before the ▸ markers
                for tc in response.tool_calls:
                    print(f"  \033[36m▸\033[0m \033[1m{tc.name}\033[0m")
                    ledger.dispatch(tc.id, tc.name, tc.arguments)
                    t0 = time.monotonic()
                    # search_tools / load_tools are client-side: they reshape this
                    # turn's tool surface and never reach an MCP server. Returns
                    # None for every other name, so normal dispatch is unaffected.
                    result = handle_meta_tool(tc.name, tc.arguments, all_tools, tools,
                                              injected_techniques=_injected_techniques) \
                        if _disclosure else None
                    if result is None:
                        result = await self._execute(tc.name, tc.arguments)
                    dur = int((time.monotonic() - t0) * 1000)
                    ok = "error" not in result.lower()[:100]
                    ledger.complete(tc.id, result, elapsed_s=dur / 1000.0)
                    if log_entry:
                        log_entry.add_tool_call(tc.name, tc.arguments, result, ok, dur)
                    if on_tool_result:
                        try:
                            await on_tool_result(tc.name, tc.arguments, result)
                        except Exception:
                            pass
                    if tc.name == "list_directory":
                        try:
                            r = json.loads(result)
                            print(f"\n{_compact_listing(r)}\n")
                        except (json.JSONDecodeError, KeyError):
                            pass
                    messages.append(
                        self._tool_result_message(tc, result, provider.model, structured)
                    )
                    if single_mode:
                        _persist_buffer.append({"role": "assistant",
                            "content": f"TOOL_CALL: {tc.name}\nARGUMENTS: {json.dumps(tc.arguments)}"})
                        _persist_buffer.append({"role": "user",
                            "content": f"[Tool Result for {tc.name}]\n{result}"})
                    # ── Stage-scoped guardrails (C1 skills + C2 verifier) ──
                    # Transient coaching, never persisted to the transcript buffer.
                    if _stage_guardrails_on:
                        try:
                            _cap = self._maybe_capsule_msg(tc.name, _injected_techniques, result, tc.arguments)
                            if _cap:
                                messages.append(_cap)
                            _sk = self._maybe_stage_skill_msg(tc.name, _injected_skills)
                            if _sk:
                                messages.append(_sk)
                            _vf = self._maybe_verifier_msg(tc.name, tc.arguments)
                            if _vf:
                                messages.append(_vf)
                        except Exception:
                            pass
                continue

            # ── Structured path: no tool_calls ⇒ this IS the final answer ──
            # Tool intent can only arrive as real `tool_calls` here, so there is no
            # text protocol to parse and nothing for the drift guards to catch.
            # Integrity is checked against the execution ledger instead: every
            # violation below is predicated on a recorded fact (a call that never
            # executed, an empty ledger, a path absent from all tool output), so a
            # turn that genuinely ran tools is never interrupted.
            if structured and (response.content or "").strip():
                text = response.content or ""
                violations = ledger.check_final_answer(text)
                if violations and _ledger_retries < _MAX_LEDGER_RETRIES:
                    _ledger_retries += 1
                    for v in violations:
                        print(f"  \033[33m⚠ integrity ({v['code']}):\033[0m {v['message'][:120]}")
                    messages.append(self._assistant_message(response, provider.model, True))
                    messages.append({
                        "role": "user",
                        "content": ("⛔ EXECUTION-INTEGRITY CHECK FAILED — do not present "
                                    "this answer.\n\n"
                                    + "\n".join(f"• {v['message']}" for v in violations)
                                    + "\n\nUse the tools to establish these facts, then answer."),
                    })
                    continue
                if violations:
                    # Budget spent: surface the unresolved violations rather than
                    # silently passing an answer we could not ground.
                    text += ("\n\n⚠️ Unverified: "
                             + "; ".join(v["message"] for v in violations))
                return _persist(text)

            # ── Mode 2: Text-based TOOL_CALL: parsing ──
            text = response.content or ""
            text_calls = self._parse_text_tool_calls(text)

            if text_calls:
                # Add assistant text (with tool calls stripped) to history AND
                # show it to the user before the ▸ tool markers fire — this is the
                # "say what you'll do, then do it" narration the user expects from
                # Claude Code. Without this the prose is only stored, never seen.
                prose = self._strip_tool_calls_from_text(text)
                if prose:
                    messages.append({"role": "assistant", "content": prose})
                    _emit_narration(prose)

                # ── Runtime fan-out guards ───────────────────────────────────
                # Two complementary checks:
                # (A) Per-response: ≥3 of the same tool in ONE model response
                # (B) Cumulative: ≥3 of the same tool across ALL responses this
                #     turn — catches iterative single-call-per-turn fan-out.
                # Both are per-tool-agnostic structural checks; no per-tool rules.
                _tool_counts = Counter(tc.name for tc in text_calls)
                _top_tool, _top_count = _tool_counts.most_common(1)[0]

                # (A) per-response check — arg-diversity aware
                _per_resp_args = {
                    _top_tool: set(json.dumps(tc2.arguments, sort_keys=True)
                                   for tc2 in text_calls if tc2.name == _top_tool)
                }
                _per_resp_unique = len(_per_resp_args.get(_top_tool, set()))
                _per_resp_fanout = (
                    not single_mode      # single loop trusts the model to self-regulate
                    and _top_count >= 3
                    and not (_top_tool in _MULTI_ARG_OK_TOOLS and _per_resp_unique >= _top_count)
                )
                if _per_resp_fanout:
                    print(f"  \033[33m⚠ fan-out guard:\033[0m {_top_count}× {_top_tool} — rejecting batch")
                    # run_command fan-out: the fix is glob consolidation, not
                    # a compound tool — give a more specific redirect.
                    if _top_tool == "run_command":
                        _fanout_msg = (
                            f"⛔ FAN-OUT DETECTED: you emitted {_top_count} separate `run_command` "
                            "calls. Shell commands must be consolidated into ONE call using "
                            "glob patterns or a semicolon-separated sequence.\n\n"
                            "Example — deleting multiple file types:\n"
                            "  WRONG: 5 separate `rm file1.csv`, `rm file2.csv`, ...\n"
                            "  RIGHT: `rm -f /path/*.corr.csv /path/*.checkpoint.txt /path/*.png`\n\n"
                            "Example — running multiple operations:\n"
                            "  WRONG: 3 separate run_command calls\n"
                            "  RIGHT: `mkdir -p dir1 dir2 dir3 && cp file1 dir1/ && cp file2 dir2/`\n\n"
                            "Rewrite as ONE run_command call now. Also note: if the command "
                            "contains `rm`, you must list the files first and confirm with "
                            "the user before deleting (previous calls may have been blocked)."
                        )
                    else:
                        _fanout_msg = (
                            f"⛔ FAN-OUT DETECTED: you emitted {_top_count} calls to "
                            f"`{_top_tool}` in one response. This is the failure mode "
                            "this system is designed to prevent.\n\n"
                            "A compound tool almost certainly exists that returns all of "
                            "these values in ONE call. Search YOUR TOOL LIST above for a "
                            "tool whose description mentions 'all', 'enumerate', 'list', "
                            "'batch', 'rings', 'summary', 'report', or 'inspect' and use "
                            f"THAT tool ONCE instead of {_top_tool} {_top_count} times.\n\n"
                            "Examples of the right pattern:\n"
                            "  • Per-hkl d-spacings → enumerate_bragg_rings (not many xray_calculate)\n"
                            "  • Per-file inspection → diagnose_parameter_file or inspect_dataset_file\n"
                            "  • Per-grain stats → read_grains_summary\n\n"
                            "If you genuinely need primitive calls (e.g., one xray_calculate "
                            "for a single user-asked d-spacing), emit AT MOST ONE call now, "
                            "then ANSWER the user."
                        )
                    messages.append({"role": "user", "content": _fanout_msg})
                    continue   # skip dispatch; retry with consolidated command or compound tool
                # end (A)

                # (B) cumulative fan-out check is deferred until AFTER the
                # strategy gate (below). A call the strategy gate rejects is
                # never executed, so counting it as fan-out here would let two
                # guards fight: the gate asks the model to retry with a plan,
                # and the retry would inflate the cumulative counter until the
                # fan-out guard kills the turn — the call never runs. Only
                # count calls that survive every guard and actually dispatch.

                # ── Strategy gate (Claude-Code-style pre-action reasoning) ───
                # If the response contains a tool from _PLAN_REQUIRED_TOOLS
                # (long-running / irreversible / choice-dependent), require
                # that the model wrote at least _STRATEGY_MIN_WORDS words of
                # reasoning prose in the SAME response before the TOOL_CALL.
                # If not, reject and ask for a strategy statement first.
                #
                # This mirrors how Claude Code works: the model must explain
                # what it is about to do and why before executing any action
                # that is hard to undo or requires a choice among inputs.
                # The check is tool-agnostic — _PLAN_REQUIRED_TOOLS is the
                # only configuration knob, no per-tool rules.
                # Single mode replaces this injected gate with a system-prompt
                # instruction ("state in 1-2 sentences what you'll do before
                # long-running/irreversible actions") — the model self-regulates.
                _plan_needed = [] if single_mode else [tc for tc in text_calls
                                if tc.name in _PLAN_REQUIRED_TOOLS]
                if _plan_needed:
                    prose_words = len(prose.split()) if prose else 0
                    has_markers = bool(_STRATEGY_MARKERS.search(prose)) if prose else False
                    # Prior-plan bypass: if the conversation history already
                    # contains a structured plan (SITUATION/GAP/PLAN markers in
                    # a prior assistant turn) AND the current query is a short
                    # approval ("yes", "go with this", "proceed", etc.), the user
                    # has approved the plan — do not require it to be re-written.
                    plan_ok = (
                        has_markers
                        or prose_words >= _STRATEGY_MIN_WORDS
                        or (_prior_plan_in_history and _query_is_approval)
                    )
                    if not plan_ok:
                        _tool_names_str = ", ".join(
                            f"`{tc.name}`" for tc in _plan_needed
                        )
                        _plan_gate_strikes.update(tc.name for tc in _plan_needed)
                        _is_retry = any(
                            _plan_gate_strikes[tc.name] > 1 for tc in _plan_needed
                        )
                        print(f"  \033[33m⚠ strategy gate:\033[0m {_tool_names_str} — no plan ({prose_words}w, markers={has_markers}, retry={_is_retry})")
                        if _is_retry:
                            _gate_msg = (
                                f"⛔ PLAN STILL MISSING (second attempt on {_tool_names_str}).\n\n"
                                "⚠️ CRITICAL: THE PLAN AND THE TOOL_CALL MUST BE IN THE SAME RESPONSE.\n"
                                "You cannot send a plan and then wait — the runner does not continue "
                                "from a plan-only response. Write the plan AND the TOOL_CALL together:\n\n"
                                "SITUATION: ...\n"
                                "GAP: ...\n"
                                "PLAN:\n"
                                "  Step 1. [exact file] — [reason]\n\n"
                                "Executing step 1:\n"
                                f"TOOL_CALL: {_plan_needed[0].name}\n"
                                "ARGUMENTS: {...}\n\n"
                                "The TOOL_CALL must appear at the END of this response, after the plan. "
                                "Do NOT call raw MIDAS executables via run_command — use the dedicated "
                                f"tool `{_plan_needed[0].name}` which handles all parameters correctly."
                            )
                        else:
                            _gate_msg = (
                                f"⛔ PLAN REQUIRED before calling {_tool_names_str}.\n\n"
                                "Write a brief strategy (1-3 sentences) AND the TOOL_CALL in the SAME response.\n\n"
                                "For a simple parameter change + proceed (e.g. 'no dark file'), one sentence is enough:\n"
                                "  'Proceeding without dark subtraction using the same calibration geometry.'\n"
                                "  TOOL_CALL: midas_auto_calibrate\n"
                                "  ARGUMENTS: {...}\n\n"
                                "For a multi-file or first-time setup, use the full structure:\n"
                                "  SITUATION: [what files/conditions exist]\n"
                                "  GAP: [what must be resolved first]\n"
                                "  PLAN: Step 1. [specific file] — [reason]\n"
                                "  Executing step 1:\n"
                                f"  TOOL_CALL: {_tool_names_str}\n\n"
                                "Rules: name the EXACT file; plan + TOOL_CALL in ONE response."
                            )
                        messages.append({"role": "user", "content": _gate_msg})
                        continue   # let the model retry with a real plan

                # (B) cumulative fan-out check — runs only after the per-response
                # guard AND the strategy gate have passed, so the counter reflects
                # calls that will actually execute (not gate-rejected retries).
                _turn_tool_counts.update(_tool_counts)
                # Track unique argument fingerprints per tool so arg-diverse
                # tools (read_file on different files) don't false-fire.
                for tc in text_calls:
                    args_key = json.dumps(tc.arguments, sort_keys=True)
                    _turn_tool_args.setdefault(tc.name, set()).add(args_key)

                _cum_top, _cum_count = _turn_tool_counts.most_common(1)[0]
                if (not single_mode) and _cum_count >= _FANOUT_THRESHOLD and _cum_top == _top_tool and _top_count < _FANOUT_THRESHOLD:
                    # For tools where diverse args are legitimate, only fire
                    # if the argument SET is smaller than the call count (i.e.
                    # same args repeated, not different files each time).
                    _unique_args = len(_turn_tool_args.get(_cum_top, set()))
                    if _cum_top in _MULTI_ARG_OK_TOOLS and _unique_args >= _cum_count:
                        pass   # diverse args — not true fan-out, let it through
                    else:
                        # Cumulative threshold crossed and NOT already caught by (A)
                        print(f"  \033[33m⚠ cumulative fan-out:\033[0m {_cum_count}× {_cum_top}")
                        if _cum_top == "run_command":
                            _fanout_redirect = (
                                f"⛔ CUMULATIVE FAN-OUT: you have called `run_command` "
                                f"{_cum_count} times across this turn. "
                                "Consolidate into ONE call using a bash -c script or pipes:\n"
                                "  bash -c 'head -20 file1.csv; echo ---; head -20 file2.csv'"
                            )
                        else:
                            _fanout_redirect = (
                                f"⛔ CUMULATIVE FAN-OUT: you have called `{_cum_top}` "
                                f"{_cum_count} times across this turn in separate responses. "
                                "Use the compound tool that returns all values in ONE call."
                            )
                        messages.append({"role": "user", "content": _fanout_redirect})
                        continue

                # Single-mode thrash floor. The consecutive-identical guard (above)
                # only catches back-to-back same-args repeats; it misses the real
                # pathology — the same tool called many times across the turn with
                # slightly varying args that never converge (e.g. compare_integrated_series
                # ×6 interleaved with run_command, burning the whole budget). If a tool
                # has been called this many times, the model has enough data: tell it to
                # stop and write the answer rather than let it run to the iteration cap.
                _SINGLE_THRASH = 5
                if single_mode and _cum_count >= _SINGLE_THRASH:
                    _unique_args = len(_turn_tool_args.get(_cum_top, set()))
                    if _cum_top in _SHELL_INVESTIGATION_TOOLS:
                        # Judge by DIVERSITY: trip only when the calls are mostly
                        # repeats (>40% duplicate args) — real non-converging
                        # thrash. All-distinct (or nearly) commands are exploration
                        # and run to the iteration cap. e.g. 5 calls/5 distinct →
                        # pass; 5/4 → pass (one repeat); 6 calls/2 distinct → trip.
                        _thrash = _unique_args < 0.6 * _cum_count
                    else:
                        # Genuinely arg-diverse work (read_file over N distinct files)
                        # is fine; everything else trips at the strict-unique bar.
                        _thrash = not (_cum_top in _MULTI_ARG_OK_TOOLS and _unique_args >= _cum_count)
                    if _thrash:
                        print(f"  \033[33m⚠ thrash floor:\033[0m {_cum_count}× {_cum_top} ({_unique_args} distinct) — forcing answer")
                        messages.append({
                            "role": "user",
                            "content": (
                                f"You have called `{_cum_top}` {_cum_count} times this turn. "
                                "You already have the results you need above. Do NOT call it "
                                "again. Write the final answer for the user now, reporting only "
                                "what actually executed."
                            ),
                        })
                        continue

                _once_per_response = set()
                _ONCE_TOOLS = {"run_midas_viewer"}

                # ── Pre-validate all calls (guards run sequentially, side-effect-free
                #    on the network) so we know which ones to dispatch in parallel.
                to_execute: List[ToolCall] = []
                forced_break = False
                for tc in text_calls:
                    tc_args_str = json.dumps(tc.arguments, sort_keys=True)
                    if tc.name == last_tool_name and tc_args_str == _last_tool_args:
                        repeat_count += 1
                    else:
                        last_tool_name = tc.name
                        _last_tool_args = tc_args_str
                        repeat_count = 0

                    if repeat_count >= 2:
                        messages.append({
                            "role": "user",
                            "content": (
                                f"You already called {tc.name} with the same arguments and got the result above. "
                                "Do NOT call it again. Summarise the result for the user now."
                            ),
                        })
                        forced_break = True
                        break

                    if tc.name in _ONCE_TOOLS:
                        if tc.name in _once_per_response:
                            messages.append({
                                "role": "user",
                                "content": (
                                    f"[Skipped duplicate {tc.name} — viewer already launched.]\n"
                                    "The GUI window is open. Do NOT launch another viewer. "
                                    "Report which viewer was launched and which file."
                                ),
                            })
                            continue
                        _once_per_response.add(tc.name)

                    if tc.name == "run_command":
                        cmd_str = str(tc.arguments.get("command", "")).lower()
                        # Guard: raw MIDAS calibration executables called directly
                        # instead of through the midas_auto_calibrate tool.
                        _MIDAS_CAL_BINS = [
                            "autocalibrateZarr", "autocalibratezarr",
                            "calibrantintegratoromp", "calibrantomp",
                            "calibrantpanelshiftsomp", "fittiltbclsdsample",
                        ]
                        if any(kw in cmd_str for kw in _MIDAS_CAL_BINS):
                            err = json.dumps({
                                "error": "Do NOT call MIDAS calibration binaries directly via run_command. "
                                         "Use TOOL_CALL: midas_auto_calibrate — it handles all parameters, "
                                         "energy/wavelength conversion, and AutoCalibrateZarr.py correctly.",
                                "correct_tool": "midas_auto_calibrate",
                            })
                            messages.append({
                                "role": "user",
                                "content": (
                                    f"[Tool Result for {tc.name}]\n{err}\n\n"
                                    "You bypassed the calibration tool. Use midas_auto_calibrate instead:\n"
                                    "TOOL_CALL: midas_auto_calibrate\n"
                                    "ARGUMENTS: {\"image_file\": \"/path/to/Ceria_att3_*.h5\"}\n"
                                    "The tool auto-detects energy, Lsd, and calibrant from the filename.\n"
                                    "Required parameter name: image_file (NOT data_file, NOT image_path)."
                                ),
                            })
                            continue

                        if any(kw in cmd_str for kw in ["gsas", "refine", "rietveld", "gsas_ii_refine"]):
                            err = json.dumps({
                                "error": "Do NOT use run_command for GSAS-II refinement. "
                                         "Use TOOL_CALL: run_gsas_refinement with data_file (.zarr.zip) and cif_files.",
                                "correct_tool": "run_gsas_refinement",
                            })
                            messages.append({
                                "role": "user",
                                "content": f"[Tool Result for {tc.name}]\n{err}\n\n"
                                           "You used the WRONG tool. Use run_gsas_refinement instead of run_command. "
                                           "First call list_directory to find the .zarr.zip and .cif files, "
                                           "then call run_gsas_refinement with those paths.",
                            })
                            continue

                        # ── Deletion permission gate ─────────────────────────
                        # rm/rmdir/unlink are now ENABLED but gated by a human
                        # confirmation at the shared execution chokepoint
                        # (APEXAClient.execute_tool_call, via run_query's
                        # permission_callback). That gate covers BOTH this text
                        # path and the native tool_calls path, so no per-mode
                        # regex block is needed here — deletions flow through to
                        # to_execute and the chokepoint prompts the user before
                        # anything runs (fail-safe DENY if no UI can confirm).

                    to_execute.append(tc)

                # ── Execute all approved calls concurrently. Tools emitted in one
                #    model response are independent by construction (the model
                #    couldn't see any of their results when it produced them), so
                #    parallel dispatch is always safe here.
                async def _run_one(tc: ToolCall):
                    print(f"  \033[36m▸\033[0m \033[1m{tc.name}\033[0m")
                    t0 = time.monotonic()
                    result = await self._execute(tc.name, tc.arguments)
                    return tc, result, int((time.monotonic() - t0) * 1000)

                exec_results: List = []
                if to_execute:
                    exec_results = await asyncio.gather(
                        *[_run_one(tc) for tc in to_execute]
                    )

                # ── Process results sequentially to preserve message ordering and
                #    maintain deterministic side effects (logging, list_directory
                #    rendering, follow-up prompts).
                for tc, result, dur in exec_results:
                    ok = "error" not in result.lower()[:100]
                    if log_entry:
                        log_entry.add_tool_call(tc.name, tc.arguments, result, ok, dur)
                    if on_tool_result:
                        try:
                            await on_tool_result(tc.name, tc.arguments, result)
                        except Exception:
                            pass
                    if tc.name == "list_directory":
                        try:
                            r = json.loads(result)
                            print(f"\n{_compact_listing(r)}\n")
                        except (json.JSONDecodeError, KeyError):
                            pass

                    if len(result) > 8000:
                        result = result[:8000] + "\n... [truncated]"
                    if tc.name == "list_directory":
                        followup = (
                            "The directory listing is displayed above. "
                            "File count and filenames are GROUND TRUTH — do not dispute them, do not re-list. "
                            "\n\n"
                            "Answer the user's ACTUAL request, using ONLY the filenames/tool result "
                            "(no training-data assumptions). Match the response size to the request:\n"
                            "• Simple/informational question (e.g. 'what's this image?', 'what's here?', "
                            "'is there a calibrant?'): answer directly in 1–3 sentences. If they asked about "
                            "a specific image, inspect it (inspect_dataset_file) or describe it from its loaded "
                            "stats/filename — do NOT produce a multi-phase plan.\n"
                            "• A specific task ('calibrate X', 'integrate Y'): just execute it now.\n"
                            "• ONLY if they explicitly asked you to plan/recommend a full end-to-end workflow: "
                            "give a short numbered plan and end with 'Type **go** to proceed.'\n\n"
                            "Never end with 'Would you like me to...?'. Do not invent a master plan the user "
                            "didn't ask for."
                        )
                    elif tc.name == "fetch_cif_from_mp":
                        followup = (
                            "CIF file downloaded. The file path is in the result above. "
                            "Now call run_gsas_refinement with the CIF path and the .zarr.zip data file. "
                            "Do NOT call list_directory or fetch_cif_from_mp again."
                        )
                    elif tc.name == "run_midas_viewer":
                        followup = (
                            "Viewer launched. Report ONE line: which viewer + which file. "
                            "Do NOT read the data file. Do NOT analyze or summarize data. "
                            "If the user asked for MULTIPLE plots (e.g. 'both', 'and', 'one by one'), "
                            "proceed to launch the NEXT viewer for the remaining request. "
                            "You may need to call list_directory on a subdirectory (e.g. integration/) to find the next file. "
                            "If the user asked for only ONE plot, do NOT call any more tools."
                        )
                    else:
                        followup = (
                            "Proceed with the user's request using the result above. "
                            "If the task is complete, summarize the results using markdown formatting: "
                            "bold **key values**, use bullet points for multiple items, "
                            "and keep it concise. Do NOT repeat the same tool call."
                        )
                    messages.append({
                        "role": "user",
                        "content": f"[Tool Result for {tc.name}]\n{result}\n\n{followup}",
                    })
                    if single_mode:
                        # Persist the CLEAN exchange (no followup nag) so future
                        # turns recall what ran and its result, not the coaching.
                        _persist_buffer.append({"role": "assistant",
                            "content": f"TOOL_CALL: {tc.name}\nARGUMENTS: {json.dumps(tc.arguments)}"})
                        _persist_buffer.append({"role": "user",
                            "content": f"[Tool Result for {tc.name}]\n{result}"})
                    # ── Stage-scoped guardrails (C1 skills + C2 verifier) ──
                    # Transient coaching, never persisted to the transcript buffer.
                    if _stage_guardrails_on:
                        try:
                            _cap = self._maybe_capsule_msg(tc.name, _injected_techniques, result, tc.arguments)
                            if _cap:
                                messages.append(_cap)
                            _sk = self._maybe_stage_skill_msg(tc.name, _injected_skills)
                            if _sk:
                                messages.append(_sk)
                            _vf = self._maybe_verifier_msg(tc.name, tc.arguments)
                            if _vf:
                                messages.append(_vf)
                        except Exception:
                            pass
                if forced_break:
                    break
                continue

            # ── Anti-confabulation guard ─────────────────────────────────────
            # No tool calls PARSED, but the text contains tool-call syntax → the
            # model TRIED to act and it silently failed (unparseable format,
            # broken JSON, native XML). Do NOT let it finalize with fabricated
            # results (e.g. "report generated at <path>" when nothing ran). Force
            # a correctly-formatted retry; after repeated failures, return an
            # honest "nothing executed" instead of the confabulation.
            if text and self._TOOL_ATTEMPT_RE.search(text):
                if _confab_strikes < 2:
                    _confab_strikes += 1
                    print(f"  \033[33m⚠ tool call did not execute — forcing retry "
                          f"(attempt {_confab_strikes})\033[0m")
                    messages.append({"role": "assistant", "content": text})
                    messages.append({"role": "user", "content": (
                        "⛔ Your previous message contained a tool call that DID NOT "
                        "execute — it was not in a parseable format, so NO command ran "
                        "and NO files were created. Do NOT report any results, paths, "
                        "file sizes, or outputs as if it succeeded — that would be "
                        "fabrication. Re-issue the call in EXACTLY this format, with "
                        "ARGUMENTS as a single valid JSON object:\n\n"
                        "TOOL_CALL: <tool_name>\n"
                        "ARGUMENTS: {\"key\": \"value\"}\n"
                    )})
                    continue
                return _persist("⚠️ I tried to call a tool but it did not execute (the "
                        "tool-call format was not recognized), so nothing ran and no "
                        "files were written. I'm not reporting results I don't have. "
                        "Please retry — and if you're on an Opus/Sonnet model, switch "
                        "to gpt55 or gpt54, which emit the tool-call format reliably.")

            # ── Phantom-launch / false-deferred-completion guard ─────────────
            # Final answer with NO tool call this iteration, yet it promises a
            # deferred/async result ("I'll report when it completes") or claims a
            # job was "launched/submitted". Tools are synchronous — this is a
            # confabulation. Force the model to ACTUALLY emit the call now.
            if text and self._PHANTOM_ASYNC_RE.search(text):
                if _async_strikes < 2:
                    _async_strikes += 1
                    print(f"  \033[33m⚠ phantom launch — no tool ran; forcing real call "
                          f"(attempt {_async_strikes})\033[0m")
                    messages.append({"role": "assistant", "content": text})
                    messages.append({"role": "user", "content": (
                        "⛔ You said an operation was launched/submitted or that you'll "
                        "report results later — but you issued NO tool call, so NOTHING "
                        "ran. APEXA tools are SYNCHRONOUS: there is no background job and "
                        "you will NOT be called again to report. Do ONE of:\n"
                        "1) Emit the tool call NOW and you'll get its result in THIS reply:\n"
                        "   TOOL_CALL: <tool_name>\n"
                        "   ARGUMENTS: {\"key\": \"value\"}\n"
                        "2) Or, if you should not run it, say plainly what you did NOT do.\n"
                        "Never claim something was launched or promise a later report."
                    )})
                    continue
                return _persist("⚠️ I described launching an operation but did not "
                        "actually run it, and these tools are synchronous (no background "
                        "job, no later report). Nothing executed. Tell me to proceed and "
                        "I'll issue the actual tool call in-line.")

            # ── Refusal-to-execute guard ─────────────────────────────────────
            # The model falsely claims it cannot run commands / read files, or asks
            # the USER to run them and paste output. APEXA is an agent with working
            # tools (it uses them elsewhere in the same session) — this is a
            # self-limitation hallucination (common Opus/Sonnet drift after
            # compaction). Force it to actually call the tool.
            if text and self._REFUSAL_RE.search(text):
                if _refusal_strikes < 2:
                    _refusal_strikes += 1
                    print(f"  \033[33m⚠ false 'can't execute' — forcing real tool call "
                          f"(attempt {_refusal_strikes})\033[0m")
                    messages.append({"role": "assistant", "content": text})
                    messages.append({"role": "user", "content": (
                        "⛔ FALSE — you CAN execute tools and they return real output; you "
                        "have already used them in this session. NEVER say you cannot run "
                        "commands / read / list files, and NEVER ask me to run something and "
                        "paste the output — run it yourself NOW. To inspect a directory I "
                        "named, call list_directory on that path (do NOT run the bare path as "
                        "a shell command); to read a log, call read_file. Emit the call:\n"
                        "TOOL_CALL: list_directory\n"
                        "ARGUMENTS: {\"path\": \"<the directory path>\"}"
                    )})
                    continue
                return _persist("⚠️ The model refused to call its tools (falsely claiming it "
                        "can't execute), so nothing was inspected. This is usually model "
                        "drift in a long session. Try `model gpt55` (or gpt54) and/or "
                        "`session new`, then re-issue the request — APEXA can read these "
                        "files, the model just declined to.")

            # ── No tool calls at all — check for hallucination, then return ──
            if text and not single_mode and self._looks_like_hallucinated_result(text):
                messages.append({"role": "assistant", "content": text})
                messages.append({
                    "role": "user",
                    "content": (
                        "⚠️ STOP — you just generated what looks like a tool result "
                        "(validation report, parameter values, or file contents) WITHOUT "
                        "actually calling a tool. This is hallucinated data and may be WRONG.\n\n"
                        "You MUST call the actual tool to get real results. For example:\n"
                        "- To validate: TOOL_CALL: validate_parameter_file\n"
                        "- To diagnose: TOOL_CALL: diagnose_parameter_file\n"
                        "- To read a file: TOOL_CALL: read_file\n"
                        "- To list files: TOOL_CALL: list_directory\n\n"
                        "Call the appropriate tool NOW with the correct arguments."
                    ),
                })
                continue

            # Count-hallucination guard: check if the model stated a file/frame
            # count that contradicts what list_directory actually returned.
            # This catches "360 TIFF images" when the tool said "920 files".
            if text and not single_mode:
                count_rejection = self._check_count_hallucination(text, messages)
                if count_rejection:
                    print(f"  \033[33m⚠ count hallucination detected — rejecting\033[0m")
                    messages.append({"role": "assistant", "content": text})
                    messages.append({"role": "user", "content": count_rejection})
                    continue

            if text:
                return _persist(text)
            # Empty model response. If tools ran this turn, the work happened but
            # the model didn't summarize.
            if log_entry and log_entry.tool_calls:
                return _persist("Done — see the tool output above.")
            # No text and no tools. Two very different cases — do NOT conflate them
            # (conflating is why "Hi I'm APEXA" was returned to EVERY query when the
            # model degenerated to empty replies):
            #   (a) the input really is small talk (a bare "hi") → greet;
            #   (b) a real request got an EMPTY model reply (transient gateway issue,
            #       reasoning-token exhaustion, degenerate turn) → retry, then report
            #       honestly. Never mask (b) as a greeting.
            # Empty/whitespace query — the input never really arrived (e.g. a
            # client that sent a blank message). Say so plainly instead of greeting;
            # this also makes an input-delivery bug (seen on some Windows clients)
            # diagnosable rather than masked as "Hi I'm APEXA".
            if not (query or "").strip():
                return _persist(
                    "I didn't receive any message text. Type your request and I'll help "
                    "— e.g. \"calibrate this CeO2 image\" or \"integrate the JL_0Nb series\".")
            _small_talk = bool(re.fullmatch(
                r"\s*(hi|hey|hello|yo|sup|howdy|thanks|thank you|ty|ok|okay|cool)"
                r"[\s!.,]*", query, re.I))
            if _small_talk:
                return _persist(
                    "Hi! I'm APEXA, your HEDM beamline assistant. I can calibrate "
                    "(CeO2/LaB6), integrate patterns (single, series, or batch), run "
                    "FF/NF/PF-HEDM workflows, refine with GSAS-II, plot results, move "
                    "motors, and answer HEDM questions. Point me at a data file and "
                    "I'll suggest what to do, or just tell me what you need."
                )
            if _empty_strikes < 2:
                _empty_strikes += 1
                print(f"  \033[33m⚠ empty model response — nudging retry "
                      f"({_empty_strikes}/2)\033[0m")
                messages.append({"role": "user", "content": (
                    "Your previous reply was completely empty. Respond to my request "
                    "NOW: either emit a TOOL_CALL: / ARGUMENTS: to run a tool, or give "
                    "a direct text answer. Do not return an empty message.")})
                continue
            return _persist(
                "⚠ The model returned an empty response for that request (it did not "
                "produce text or a tool call, even after a retry). This is usually a "
                "transient Argo gateway issue or an overloaded context. Please retry; "
                "if it persists, start a fresh context with `session new`, or switch "
                "models with `model gpt54`."
            )

        # ── Forced finalize at iteration cap ─────────────────────────────────
        # Loop exhausted without the model producing a tool-call-free final
        # response. Falling through to "return last message" silently drops
        # raw tool JSON onto the user. Instead, make ONE more LLM call with
        # tools forbidden and a hard instruction to summarise what we already
        # have. This guarantees a user-facing answer even when the model is
        # mid-fan-out at the cap.
        print(f"  \033[33m⚠ iteration cap reached — forcing finalize\033[0m")
        messages.append({
            "role": "user",
            "content": (
                "⛔ TOOL BUDGET EXHAUSTED. You have reached the maximum allowed "
                "tool calls for this turn. You are now FORBIDDEN from emitting "
                "any further TOOL_CALL blocks.\n\n"
                "Write the FINAL answer for the user RIGHT NOW. "
                "CRITICAL RULES for this final answer:\n"
                "1. If any tool calls were BLOCKED by a guard (fan-out, destructive, "
                "strategy gate), say so EXPLICITLY — do NOT claim success for "
                "operations that were blocked. Say 'X was blocked and did not execute.'\n"
                "2. Report only what ACTUALLY executed based on the tool results above.\n"
                "3. If an operation is partially complete (some files deleted, some not), "
                "state exactly what was done and what remains.\n"
                "4. Use markdown: **bold** key values, bullet points for lists.\n"
                "5. Do NOT apologise; do NOT describe your process; just report facts."
            ),
        })
        try:
            final_response = await provider.chat(messages, agent.temperature)
            final_text = final_response.content or ""
            # Strip any TOOL_CALL: blocks that slipped through (the model
            # sometimes ignores the no-tools instruction on the first try).
            final_text = self._strip_tool_calls_from_text(final_text).strip()
            if final_text:
                return _persist(final_text)
        except Exception as e:
            print(f"  \033[31m✗ finalize call failed: {e}\033[0m")

        # True last resort: surface the last assistant text we have
        last = messages[-1]
        if isinstance(last.get("content"), str):
            return _persist(last["content"])
        return _persist("Analysis reached maximum steps. Check tool outputs above.")


# ── Orchestrator ─────────────────────────────────────────────────────────────

class OrchestratorAgent:
    """
    Routes user queries to the appropriate specialist agent.

    Replaces:
      - WorkflowBuilder (was a skeleton that never executed steps)
      - CALCULATION_KEYWORDS / _needs_calculation_tool() keyword detection
      - The manual per-query routing inside process_diffraction_query()

    Routing is keyword-score based (fast, deterministic).  The agent with the
    highest keyword score wins; ties and zero-scores default to AnalysisAgent,
    which is the most common operation at a beamline.
    """

    _ROUTES: Dict[str, APEXAAgent] = {
        "calibration":   CALIBRATION_AGENT,
        "analysis":      ANALYSIS_AGENT,
        "knowledge":     KNOWLEDGE_AGENT,
        "visualization": VISUALIZATION_AGENT,
        "motor":         MOTOR_AGENT,
    }

    _KEYWORDS: Dict[str, set] = {
        "calibration": {
            "calibrat", "ceo2", "lab6", "calibrant", "rings",
            "beam center", "detector distance", "lsd", "autocal",
            "stopping strain", "refined param", "bc_x", "bc_y",
            "tilt", "detector geometry",
            "validate param", "diagnose", "inspect dataset",
        },
        "analysis": {
            "integrat", "hedm", "ff-hedm", "nf-hedm", "pf-hedm", "grain",
            "phase", "workflow", "2d to 1d", "reconstruct",
            "microstructure", "orientation", "texture", "strain",
            "diffraction pattern", "peaks at", "identify",
            "calculate", "d-spacing", "d spacing", "wavelength",
            "energy", "bragg", "convert", "list file", "list dir",
            "show file", "current directory", "files here",
            "misorientation", "dream3d", "forward simulation",
            "gsas", "refine", "refinement", "rietveld", "rwp",
            "lattice param", "peak fit", "live analysis",
            "stress", "stiffness", "von mises", "schmid",
            "slip system", "d0 correct", "equilibrium",
            "plasticity", "taylor factor", "grains.csv",
            "validate param", "bragg ring",
            "calibrated file", "calibrated data", "calibrated image",
            "recommend", "suggest", "advise", "what should i", "what can you do",
            "my options", "what are my options", "capabilit", "what tools",
            # Remote execution — run analysis on the host where the data lives
            # (run_remote_command); e.g. "ssh copland and run ...", "on copland".
            "ssh", "remote", "copland", "run remotely", "on the remote",
        },
        "knowledge": {
            "explain", "what is", "what's", "what are", "whats",
            "how does", "how do", "how is",
            "tell me", "describe", "definition", "define",
            "typical", "literature", "paper", "cite", "citation", "source",
            "reference", "knowledge base",
            "best practice", "recommend", "suggest", "look up",
            "material propert", "search", "parameter range",
            "cif file", "cif", "fetch cif", "download cif", "materials project",
            "crystal structure",
            # Domain-abbreviation conceptual queries (catch "what's HEDM?", "hedm overview")
            "hedm overview", "what hedm", "ff-hedm", "nf-hedm",
            "rietveld", "azimuthal integration overview",
        },
        "visualization": {
            "plot", "visualiz", "view", "show", "display", "see",
            "lineout", "caked", "heatmap", "chart", "graph",
            "live viewer", "overlay", "pattern", "diffraction image",
            "peak plot", "grain plot", "3d grain", "spots",
            "ring", "fit result", "caking", "zarr", "lineout.xy",
            # Compound keywords — disambiguation when "plot/show" + domain word
            "plot calibra", "show calibra", "view calibra", "display calibra",
            "plot the calibra", "show the calibra", "view the calibra",
            "display the calibra", "see the calibra",
            "calibration result", "calibrant result",
            "plot the lineout", "show the grain",
            "plot the caked", "show the caked", "plot the integration",
            "show the integration", "plot the raw", "show the raw",
        },
        "motor": {
            "motor", "move", "position", "caget", "caput", "epics",
            "ioc", "rbv", "readback", "jog", "tweak", "home motor",
            "stop motor", "velocity", "speed", "limit switch",
            "soft limit", "hls", "lls", "dmov", "pv", "channel access",
            "20idmotsim", "motorsim", "rename motor", "motor name", "desc",
            "samx", "samy", "samz", "detx", "dety", "detz",
            # PV name patterns — " m1", " m2", etc. (leading space avoids false matches)
            " m1", " m2", " m3", " m4", " m5", " m6", " m7", " m8",
        },
    }

    # Fast-path patterns: deterministic natural-language commands that map
    # 1:1 onto a single tool call. Executing them directly skips the entire
    # orchestrator/agent loop and saves a full LLM round-trip on the most
    # common queries. Patterns must be VERY specific to avoid false matches.
    _FAST_PATHS = [
        (re.compile(r'^\s*(?:list|ls|show)\s+(?:the\s+)?files?\s+(?:in|under|inside|of)\s+(.+?)\s*\??\s*$', re.I),
         "list_directory", lambda m: {"path": m.group(1).strip().strip('"\'')}),
        (re.compile(r'^\s*(?:list|ls|show)\s+(?:the\s+)?(?:current\s+)?(?:dir(?:ectory)?|files|folder)\s*\??\s*$', re.I),
         "list_directory", lambda m: {"path": "."}),
        (re.compile(r'^\s*what\s+files\s+are\s+(?:in|under|inside)\s+(.+?)\s*\??\s*$', re.I),
         "list_directory", lambda m: {"path": m.group(1).strip().strip('"\'')}),
    ]

    def __init__(self, execute_tool_fn: ExecuteToolFn,
                 all_tools: List[Dict], context=None):
        self.runner    = AgentRunner(execute_tool_fn)
        self.all_tools = all_tools
        self.context   = context
        self.conversation_history: List[Dict] = []
        # Running compacted summary of turns older than the recent window.
        # Built incrementally by _compact_history(); injected into model
        # context by the runner so long sessions retain early facts without
        # unbounded token cost.
        self.running_summary: str = ""
        self.logger    = InteractionLogger()
        self._last_agent: Optional[APEXAAgent] = None
        self._last_turn_had_tool_error: bool = False
        self._execute  = execute_tool_fn
        # The dataset/directory the user is currently working in, inferred from
        # the most recent tool args. Anchors recap/recommend answers to THIS
        # dataset so APEXA does not drift to an unrelated tree (e.g. answering a
        # question about ai_tune with stale artifacts from another scan).
        self._active_dir: str = ""
        # Agent execution mode. "single" (DEFAULT) = one persistent reasoning
        # loop with full-fidelity context (Claude-Code style): remembers tool
        # results across turns, no keyword routing, no regex intent-gates, no
        # count/fan-out heuristics (which false-fire on capable models). Set
        # APEXA_AGENT_MODE=legacy to fall back to the keyword-routed specialists.
        self._mode: str = os.environ.get("APEXA_AGENT_MODE", "single").strip().lower()
        # FF-HEDM workflow graph (APEXA_WORKFLOW_MODE=graph). Deterministic
        # ordering + human-in-the-loop gates for calibrate→in-plane tx→recon.
        # Coexists with the modes above; lazily built on first use so a missing
        # langgraph never breaks startup. See docs/LANGGRAPH_FF_HEDM_SPEC.md.
        self._workflow_mode: str = os.environ.get("APEXA_WORKFLOW_MODE", "").strip().lower()
        self._ffhedm = None  # type: ignore  # FFHEDMWorkflow, lazy

    # Context-window management knobs (modern summarize-older + keep-recent).
    _KEEP_RECENT: int = 8       # messages kept verbatim in model context
    _COMPACT_TRIGGER: int = 16  # compact once history grows beyond this

    def clear_history(self):
        self.conversation_history = []
        self.running_summary = ""
        self._last_agent = None
        self._last_turn_had_tool_error = False

    # ── Poison-resistant context ─────────────────────────────────────────────
    _TOOL_RESULT_RE = re.compile(r'^\[Tool Result for ([^\]]+)\]', re.S)

    def _prune_failed_tool_repeats(self) -> int:
        """Drop REPEATED identical failed tool exchanges from history, keeping
        only the most recent copy of each distinct (tool, args) failure.

        A single-loop transcript that keeps re-emitting the same broken tool call
        (e.g. dark_source='paired_dark_after' failing over and over) piles those
        failures into the recent-verbatim window AND bakes them into the compacted
        summary — so the model keeps copying its own mistake even after the code is
        fixed. This is what made a resumed session un-recoverable short of
        `session new`. Removing the duplicates (but retaining the latest failure,
        so the model still knows it failed and why) breaks that feedback loop
        without discarding successes or any non-tool prose. Returns pairs removed.
        """
        hist = self.conversation_history
        n = len(hist)
        # Collect failed exchanges: assistant TOOL_CALL turn + next [Tool Result] error.
        occ: Dict[tuple, List[tuple]] = {}
        for i in range(n - 1):
            a, r = hist[i], hist[i + 1]
            if a.get("role") != "assistant" or r.get("role") != "user":
                continue
            ac, rc = a.get("content") or "", r.get("content") or ""
            if "TOOL_CALL:" not in ac:
                continue
            m = self._TOOL_RESULT_RE.match(rc)
            if not m:
                continue
            tool = m.group(1).strip()
            body = rc[m.end():]
            failed = ("error" in body.lower()[:120]) or ('"status": "error"' in body.lower())
            if not failed:
                continue
            am = re.search(r'ARGUMENTS:\s*(\{.*)', ac, re.S)
            if am:
                blob = am.group(1).strip()
                try:
                    args_fp = json.dumps(json.loads(blob), sort_keys=True)
                except Exception:
                    args_fp = blob[:200]
            else:
                args_fp = body[:120]
            occ.setdefault((tool, args_fp), []).append((i, i + 1))
        drop: set = set()
        for pairs in occ.values():
            if len(pairs) > 1:
                for a_idx, r_idx in pairs[:-1]:   # keep the latest failure only
                    drop.add(a_idx); drop.add(r_idx)
        if not drop:
            return 0
        self.conversation_history = [m for k, m in enumerate(hist) if k not in drop]
        return len(drop) // 2

    def clear_tool_history(self) -> int:
        """Strip ALL tool-call turns and their results from history, keeping the
        user↔assistant prose. Escape hatch for a context poisoned by repeated bad
        tool calls, short of a full `session new`. Returns messages removed."""
        kept, removed = [], 0
        for m in self.conversation_history:
            c, role = (m.get("content") or ""), m.get("role")
            if (role == "assistant" and "TOOL_CALL:" in c) or \
               (role == "user" and c.startswith("[Tool Result for ")):
                removed += 1
                continue
            kept.append(m)
        self.conversation_history = kept
        return removed

    def export_history(self) -> List[Dict]:
        """Return the conversation history for session persistence.

        Returns a shallow copy so the caller can serialize it without racing
        against in-flight turns mutating the live list.
        """
        return list(self.conversation_history)

    def import_history(self, history: List[Dict]):
        """Restore conversation history from a saved/auto-saved session.

        In single mode the transcript IS the memory, so restore it in FULL
        (tool calls + results included) — truncating to a small window on resume
        would drop exactly the outcomes the model needs to answer "what's done?"
        (it caused a resumed session to recommend re-running an already-finished
        calibration). Compaction bounds growth on subsequent turns. Legacy mode
        keeps the small recent window (older turns carried by running_summary).
        """
        if not history:
            return
        cleaned = [
            m for m in history
            if isinstance(m, dict) and "role" in m and "content" in m
        ]
        if getattr(self, "_mode", "single") == "single":
            self.conversation_history = cleaned
        else:
            self.conversation_history = cleaned[-self._KEEP_RECENT:]

    def export_summary(self) -> str:
        """Return the running compacted summary for session persistence."""
        return self.running_summary

    def import_summary(self, summary: str):
        """Restore the running compacted summary from a saved session."""
        self.running_summary = summary or ""

    async def _compact_history(self, provider: "ArgoProvider"):
        """Summarize-older + keep-recent context management.

        When the conversation grows beyond _COMPACT_TRIGGER, fold every message
        older than the recent window into self.running_summary via one LLM call
        and drop them from conversation_history. This replaces the old hard
        12-message truncation, so a long beamline session keeps early
        load-bearing facts (beam energy, file paths, calibration params,
        decisions) instead of silently forgetting them. Best-effort: if the
        summarization call fails, fall back to a hard trim so memory stays
        bounded and the turn still completes.
        """
        # Prune repeated failed tool calls first, so poison is never folded into
        # the running summary (where it would persist even after the recent window
        # rolls over).
        self._prune_failed_tool_repeats()
        if len(self.conversation_history) <= self._COMPACT_TRIGGER:
            return
        keep = self.conversation_history[-self._KEEP_RECENT:]
        older = self.conversation_history[:-self._KEEP_RECENT]
        if not older:
            return
        new_summary = await self._summarize_messages(older, provider)
        if new_summary:
            self.running_summary = new_summary
            self.conversation_history = keep
        else:
            # Summarization unavailable — stay bounded rather than grow forever.
            self.conversation_history = self.conversation_history[-self._COMPACT_TRIGGER:]

    async def _summarize_messages(self, messages: List[Dict],
                                  provider: "ArgoProvider") -> str:
        """Fold `messages` (and any prior summary) into one concise summary.

        The prompt is tuned for beamline work: it must preserve concrete
        values, not prose — paths, numbers, units, calibrant/sample names,
        tool successes/failures, and open tasks.
        """
        convo_text = "\n".join(
            f"{m.get('role','?')}: {m.get('content','')}" for m in messages
        )
        sys_msg = {
            "role": "system",
            "content": (
                "You compact a synchrotron beamline assistant's conversation "
                "into a dense factual summary so the assistant can continue "
                "without re-reading older turns. PRESERVE every concrete fact "
                "needed to keep working: file and directory paths, numeric "
                "parameters with units (beam energy keV, wavelength Å, detector "
                "distance, beam center, lattice parameters, tilts), calibrant "
                "and sample names, which tools were run and whether they "
                "succeeded or failed (with the error), decisions made, and any "
                "open/next tasks. Drop pleasantries and restating of the "
                "obvious. Merge the PRIOR SUMMARY with the NEW MESSAGES into a "
                "single updated summary. Output plain text, no markdown "
                "headers, at most ~250 words."
            ),
        }
        user_msg = {
            "role": "user",
            "content": (
                (f"PRIOR SUMMARY:\n{self.running_summary}\n\n"
                 if self.running_summary else "")
                + f"NEW MESSAGES TO FOLD IN:\n{convo_text}\n\n"
                "Produce the updated summary."
            ),
        }
        try:
            resp = await provider.chat([sys_msg, user_msg], temperature=0.2)
            return self.runner._strip_tool_calls_from_text(
                resp.content or ""
            ).strip()
        except Exception as e:
            print(f"[compaction] summarization failed: {e}", file=sys.stderr)
            return ""

    def _score_route(self, query: str) -> tuple:
        """Return (best_domain, scores_dict) — pure scoring, no fallback."""
        q = query.lower()
        scores = {
            domain: sum(1 for kw in keywords if kw in q)
            for domain, keywords in self._KEYWORDS.items()
        }
        return max(scores, key=scores.get), scores

    # Pattern that recognises "retry with context" follow-ups — queries that
    # are giving the agent a file path, folder, or file to work with, typically
    # after a prior tool call failed (e.g., "the calibration was done in
    # /home/…/test_cali").  When the prior turn had a tool error AND this
    # pattern matches, we retain _last_agent rather than re-routing.
    _PATH_CONTEXT_RE = re.compile(
        r'(?:'
        r'/[^\s]+'                     # absolute path  /home/…
        r'|[a-zA-Z0-9_\-]+/[^\s]+'    # relative path  test_cali/…
        r'|\bthis folder\b'
        r'|\bthis directory\b'
        r'|\bthis file\b'
        r'|\buse this\b'
        r'|\bhere is\b'
        r'|\bwas done in\b'
        r'|\bfound in\b'
        r')',
        re.I,
    )

    def _route(self, query: str) -> APEXAAgent:
        best, scores = self._score_route(query)

        if scores[best] > 0:
            # Stateful-routing bias: if the prior turn ended in a tool error
            # AND the current query looks like "retry with this context" (it
            # contains a path or folder reference), keep the prior agent rather
            # than re-routing on keywords alone.  This prevents the failure
            # mode where "the calibration was done in /home/…/test_cali" routes
            # to CalibrationAgent (matching "calibration") when the user's
            # intent was actually to supply context for a pending Visualization
            # or Analysis retry.
            if (self._last_turn_had_tool_error
                    and self._last_agent is not None
                    and self._PATH_CONTEXT_RE.search(query)
                    and scores[best] <= 2):          # weak keyword signal only
                return self._last_agent

            # Break ties: analysis wins by default (most general agent, handles
            # post-calibration workflows). Exception: a conceptual question stem
            # at the START of the query routes to knowledge so the KB tool fires.
            top_score = scores[best]
            tied = [d for d, s in scores.items() if s == top_score]
            if len(tied) > 1 and "analysis" in tied:
                q_lstrip = query.lower().lstrip()
                is_conceptual_question = any(
                    q_lstrip.startswith(stem) for stem in (
                        "what is", "what's", "whats", "what are", "what does",
                        "explain", "describe", "define", "tell me about",
                        "how does", "how do", "how is",
                    )
                )
                best = "knowledge" if is_conceptual_question and "knowledge" in tied else "analysis"
            return self._ROUTES[best]      # strong keyword match → switch agent

        # No keywords matched — stay with current agent if we have one
        # This handles follow-ups like "yes", "ok", "fetch one for Ceria"
        if self._last_agent is not None:
            return self._last_agent

        return ANALYSIS_AGENT              # first query, no context → default

    async def _llm_disambiguate(self, query: str, candidates: List[str],
                                provider: ArgoProvider) -> Optional[str]:
        """Single cheap LLM call to pick a domain when keywords are ambiguous.

        Returns one of the candidate domain names, or None if the model's
        reply doesn't unambiguously match exactly one.
        """
        opts = ", ".join(candidates)
        prompt = (
            "You route synchrotron-beamline assistant queries to one specialist agent. "
            f"Pick exactly ONE domain from: {opts}. "
            "Respond with the single domain word, nothing else.\n\n"
            f"Query: {query}\n\nDomain:"
        )
        try:
            resp = await provider.chat(
                [{"role": "user", "content": prompt}],
                temperature=0.0,
            )
        except Exception:
            return None
        text = (resp.content or "").strip().lower()
        matched = [c for c in candidates if c in text]
        return matched[0] if len(matched) == 1 else None

    async def _route_with_fallback(self, query: str,
                                   provider: ArgoProvider) -> APEXAAgent:
        """Run keyword routing first; only fall back to an LLM call when the
        keyword scoring is genuinely ambiguous (multi-way tie at top score 1).
        Avoids paying the extra round-trip on the common, confidently-routed
        case while still recovering from edge cases the keyword set misses.
        """
        agent = self._route(query)
        best, scores = self._score_route(query)
        if scores[best] <= 1:
            tied = [d for d, s in scores.items() if s == scores[best] and s > 0]
            if len(tied) >= 2:
                pick = await self._llm_disambiguate(query, tied, provider)
                if pick and pick in self._ROUTES:
                    return self._ROUTES[pick]
        return agent

    def _match_fast_path(self, query: str) -> Optional[tuple]:
        """Return (tool_name, args) if the query is a deterministic command."""
        for pattern, tool, build_args in self._FAST_PATHS:
            m = pattern.match(query)
            if m:
                return tool, build_args(m)
        return None

    async def _run_fast_path(self, query: str, tool_name: str,
                             args: Dict, on_tool_result: OnToolResultFn = None,
                             use_history: bool = True) -> str:
        """Execute a fast-path tool directly — no LLM call, no agent loop."""
        log_entry = self.logger.start(query, model="fast_path")
        log_entry.set_agent("FastPath")

        print(f"  \033[36m▸\033[0m \033[1m{tool_name}\033[0m \033[2m(fast-path)\033[0m")
        t0 = time.monotonic()
        result = await self._execute(tool_name, args)
        dur = int((time.monotonic() - t0) * 1000)
        ok = "error" not in result.lower()[:100]
        log_entry.add_tool_call(tool_name, args, result, ok, dur)

        if on_tool_result:
            try:
                await on_tool_result(tool_name, args, result)
            except Exception:
                pass

        if tool_name == "list_directory":
            try:
                r = json.loads(result)
                rendered = _compact_listing(r)
                print(f"\n{rendered}\n")
                summary = rendered
            except (json.JSONDecodeError, KeyError):
                summary = result
        else:
            summary = result

        log_entry.finish(summary, iterations=1, looped=False)
        self.logger.save(log_entry)

        if use_history:
            self.conversation_history.extend([
                {"role": "user",      "content": query},
                {"role": "assistant", "content": summary},
            ])
            if len(self.conversation_history) > 12:
                self.conversation_history = self.conversation_history[-12:]

        return summary

    # Patterns that mean "explain / recap / summarize what you JUST did".
    # When matched on a query and we have at least one prior assistant turn
    # in history, route to _explain_prior_turn() — a single no-tools LLM
    # call — instead of re-executing the work in a specialist agent. Without
    # this, queries like "how did you calculate that?" hit the Analysis
    # agent's keyword set on "calculate" and the entire tool chain re-fires.
    # "Explain what you just did" — recap prior computation.
    # NOTE: these use .search (not anchored) so natural phrasings with leading
    # filler ("so what's the outcome?", "first focus on ai_tune, what's the
    # outcome?") still route to recap instead of falling through to a specialist
    # that re-discovers files and drifts to the wrong dataset. "what's"/"whats"
    # contractions are handled via what'?s?.
    _EXPLAIN_PRIOR_PATTERNS = [
        re.compile(r"\bwhat(?:'?s|\s+is|\s+are|\s+was|\s+were)\s+(the\s+|its\s+|your\s+)?(outcome|result|answer|finding|status|conclusion|verdict)\b", re.I),
        re.compile(r"\bwhat\s+(was|were|did\s+you\s+(get|find|do)|are\s+the|happened)\b", re.I),
        re.compile(r"\b(how|why)\s+did\s+you\b", re.I),
        re.compile(r"\bhow\s+was\s+(it|that)\s+(calc|comput|deriv|obtain)", re.I),
        re.compile(r"\bexplain\s+(that|how|why|what\s+you\s+(did|just))", re.I),
        re.compile(r"\b(walk|talk)\s+me\s+through\b", re.I),
        re.compile(r"\b(summari[sz]e|recap|tl;?dr)\s+(that|this|the\s+(result|output|answer|previous|run))", re.I),
        re.compile(r"\bshow\s+me\s+(the\s+)?(answer|result|outcome|summary)\b", re.I),
        re.compile(r"\b(did|does)\s+(it|that|the\s+\w+)\s+(work|succeed|converge|complete|finish|pass|fail)\b", re.I),
    ]

    # "What should I do next?" — recommend next step from context.
    # These need a DIFFERENT handler than explain-prior: the system prompt
    # must say "recommend from what you see" not "explain what you did",
    # otherwise the model correctly says "I haven't done any analysis."
    _RECOMMEND_NEXT_PATTERNS = [
        re.compile(r"^\s*(how\s+(should|do|can)\s+(i|we|you)\s+(proceed|continue|start|begin|go\s+(?:from\s+here|ahead|next)))", re.I),
        re.compile(r"^\s*(what\s+(should|do)\s+(i|we)\s+(do|try|run|use|pick|choose|start\s+with))", re.I),
        re.compile(r"^\s*(what(?:'s|\s+is|\s+are)\s+(the\s+)?(next\s+step|best\s+(approach|way|option|start)|recommend))", re.I),
        re.compile(r"^\s*(where\s+(do|should)\s+(i|we)\s+start)", re.I),
        re.compile(r"^\s*ok[,.]?\s*(so\s+)?(how|what|where)\b", re.I),
        re.compile(r"^\s*(yes[,.]?\s*)?(proceed|go\s+ahead|continue|do\s+it|run\s+it)\s*\??\s*$", re.I),
    ]

    def _is_explain_prior(self, query: str) -> bool:
        return any(p.search(query) for p in self._EXPLAIN_PRIOR_PATTERNS)

    def _is_recommend_next(self, query: str) -> bool:
        return any(p.search(query) for p in self._RECOMMEND_NEXT_PATTERNS)

    async def _recommend_from_context(self, query: str, provider: ArgoProvider,
                                      use_history: bool) -> str:
        """Answer "how should I proceed?" from conversation context.

        Different from _explain_prior_turn: the system prompt tells the model
        to recommend the NEXT concrete action based on what it has already
        observed (directory listing, calibration status, etc.) — not to recap
        what it computed. This prevents the "I haven't done any analysis" failure.
        """
        history = self._select_history_for_explain()
        if not history:
            return ""

        sys_msg = {
            "role": "system",
            "content": (
                "You are APEXA, an expert beamline assistant. The user is asking "
                "what to do next. Answer from the CONVERSATION CONTEXT below — "
                "the directory listing, file names, and any prior results are your "
                "evidence. Do NOT call any tools in this response.\n\n"
                "Your answer must:\n"
                "1. State the SPECIFIC recommended next action (e.g., 'calibrate "
                "using Ceria att3 because...' not 'you could calibrate')\n"
                "2. Name the exact file(s) and tool you recommend\n"
                "3. Give the reason in ONE sentence (which att level, why that calibrant)\n"
                "4. End with: 'Type go to proceed' — do NOT ask 'Would you like me to?'\n\n"
                "If context is insufficient to make a specific recommendation, ask "
                "ONE clarifying question only."
                + (f"\n\nCURRENT WORKING DATASET: {self._active_dir} — answer about "
                   "THIS dataset unless the user explicitly names another. Do NOT "
                   "switch to an unrelated scan/directory." if self._active_dir else "")
            ),
        }
        messages = [sys_msg] + history + [{"role": "user", "content": query}]
        try:
            resp = await provider.chat(messages, temperature=0.3)
            text = self.runner._strip_tool_calls_from_text(resp.content or "").strip()
        except Exception as e:
            return f"(recommendation failed: {e})"
        if not text:
            return ""
        if use_history:
            self.conversation_history.extend([
                {"role": "user",      "content": query},
                {"role": "assistant", "content": text},
            ])
            if len(self.conversation_history) > 12:
                self.conversation_history = self.conversation_history[-12:]
        return text

    async def _explain_prior_turn(self, query: str, provider: ArgoProvider,
                                  use_history: bool) -> str:
        """Answer an explanation/recap follow-up from conversation history."""
        history = self._select_history_for_explain()
        if not history:
            return ""

        sys_msg = {
            "role": "system",
            "content": (
                "You are APEXA. The user is asking you to EXPLAIN or RECAP what "
                "you just did in the previous turn. You MUST answer ONLY from "
                "the conversation history below. Do NOT call any tools. Do NOT "
                "request new information. Do NOT re-execute the prior task.\n\n"
                "If the user asks how a value was calculated, describe the tool(s) "
                "you used and the inputs from the prior turn. If they ask for the "
                "outcome, restate the result concisely. Use markdown: **bold** key "
                "values; bullets for lists; ≤8 lines unless detail is requested.\n\n"
                "The conversation may include `<<TOOL OUTCOMES ...>>` blocks — these "
                "are the authoritative record of what ran and its result (status, "
                "geometry, dataset, manifest path). Base your answer on them; do NOT "
                "claim 'no outputs' if an outcome block reports success/timeout."
                + (f"\n\nCURRENT WORKING DATASET: {self._active_dir} — answer about "
                   "THIS dataset unless the user explicitly names another."
                   if self._active_dir else "")
            ),
        }
        messages = [sys_msg] + history + [{"role": "user", "content": query}]
        try:
            resp = await provider.chat(messages, temperature=0.2)
            text = (resp.content or "").strip()
        except Exception as e:
            return f"(could not generate explanation: {e})"

        # Strip any TOOL_CALL: blocks that leaked through despite the
        # no-tools instruction — we are NOT going to execute them here.
        text = self.runner._strip_tool_calls_from_text(text).strip()
        if not text:
            return "(no prior result to explain)"

        if use_history:
            self.conversation_history.extend([
                {"role": "user",      "content": query},
                {"role": "assistant", "content": text},
            ])
            if len(self.conversation_history) > 12:
                self.conversation_history = self.conversation_history[-12:]
        return text

    def _select_history_for_explain(self) -> List[Dict]:
        """Return the most recent 6 messages (≈3 exchanges) for context.
        Empty list if no usable history."""
        if not self.conversation_history:
            return []
        return list(self.conversation_history[-6:])

    # Salient fields lifted from a tool's JSON result into the recall digest.
    _DIGEST_RESULT_KEYS = ("status", "engine", "calibrant", "output_dir",
                           "calibrated_parameters_file", "outcome_manifest",
                           "result_folder", "output_file", "zarr_file", "error")
    _DIGEST_ARG_KEYS = ("image_file", "data_file", "parameters_file",
                        "result_folder", "directory", "path", "cif_file",
                        "dark_file")

    def _tool_outcome_digest(self, outcomes: List) -> str:
        """Compact, model-facing record of WHAT TOOLS RAN this turn and their
        key outcomes. Stored alongside the assistant turn so later
        "what's the outcome?" / "what next?" questions are answered from memory
        instead of re-running discovery (which drifts to the wrong dataset).
        Full results live in the per-run manifests on disk; this is the index."""
        if not outcomes:
            return ""
        lines = []
        for name, args, result in outcomes[-8:]:
            arg = ""
            if isinstance(args, dict):
                for k in self._DIGEST_ARG_KEYS:
                    if args.get(k):
                        arg = f" {k}={args[k]}"
                        break
            detail = ""
            try:
                r = json.loads(result) if isinstance(result, str) else result
                if isinstance(r, dict):
                    detail = " ".join(f"{k}={r[k]}" for k in self._DIGEST_RESULT_KEYS
                                      if r.get(k))
            except (json.JSONDecodeError, TypeError):
                pass
            if not detail:
                detail = (result or "")[:180].replace("\n", " ")
            lines.append(f"- {name}{arg} → {detail}")
        return ("TOOL OUTCOMES this turn (authoritative; use these for recall, "
                "do NOT re-discover):\n" + "\n".join(lines))

    def _update_active_dir(self, outcomes: List) -> None:
        """Track the dataset/dir the user is working in from this turn's tool
        args, so recap/recommend stay anchored to it."""
        import os as _os
        for name, args, _result in outcomes:
            if not isinstance(args, dict):
                continue
            for k in ("directory", "image_file", "data_file", "result_folder",
                      "parameters_file", "path"):
                v = args.get(k)
                if v and isinstance(v, str):
                    self._active_dir = v if _os.path.isdir(v) else _os.path.dirname(v)
                    return

    async def _process_single_loop(self, query: str, provider: ArgoProvider,
                                   use_history: bool = True,
                                   on_tool_result: OnToolResultFn = None) -> str:
        """Modern single-loop path (APEXA_AGENT_MODE=single).

        One reasoning loop over a PERSISTENT, full-fidelity transcript: prior
        turns' tool calls AND results are carried verbatim across turns, so the
        model answers "what's the outcome?" / "what next?" from its own memory
        instead of re-discovering files (which drifts to the wrong dataset).
        No keyword routing, no regex intent-gates, no fast-path — ONE unified
        agent (APEXA_AGENT) with the full toolset decides answer-vs-act itself.
        """
        # Capture full tool outcomes for the active-dir anchor + UI streaming.
        _turn_outcomes: List = []
        async def _capture(name, args, result):
            _turn_outcomes.append((name, args, result))
            if on_tool_result:
                await on_tool_result(name, args, result)

        agent = APEXA_AGENT
        self._last_agent = agent
        log_entry = self.logger.start(query, model=provider.model)
        log_entry.set_agent(agent.name)

        # The transcript IS conversation_history. Append the user turn, then let
        # the runner append this turn's clean tool exchanges + final answer in
        # place (single_mode=True). No separate {user}/{assistant} bookkeeping.
        if use_history:
            # Drop repeated identical failed tool calls BEFORE building this turn's
            # payload so the model isn't handed (and tempted to copy) its own
            # accumulated mistakes.
            _pruned = self._prune_failed_tool_repeats()
            if _pruned:
                print(f"[context] pruned {_pruned} repeated failed tool call(s)",
                      file=sys.stderr)
            self.conversation_history.append({"role": "user", "content": query})
            transcript = self.conversation_history
        else:
            transcript = [{"role": "user", "content": query}]

        # Single-loop tasks are genuinely multi-step (e.g. "delete old files →
        # compare xye + fxye → verify → report" is 6-8 legitimate calls). The
        # legacy default of 10 leaves no margin for one wrong turn and forces a
        # premature finalize. Give the reasoning loop real headroom; override
        # with APEXA_MAX_ITERATIONS if a task needs more.
        try:
            _single_cap = int(os.environ.get("APEXA_MAX_ITERATIONS", "24"))
        except ValueError:
            _single_cap = 24

        # Learning layer for single mode. The unified agent has tool_names=[],
        # so the runner's own skill preload is empty. Recover it by matching the
        # query against the SAME keyword map the legacy orchestrator routes on
        # (no second keyword map to drift): every domain the query touches
        # contributes its specialist's tools → their skills, unioned. This lands
        # the verified handbook procedure (units/traps/flags) in context BEFORE
        # the loop edits a param file or fires a tool. Empty when nothing matches
        # — the deterministic lint gate + RAG still cover that case.
        skill_block = ""
        _matched_tools: List[str] = []
        try:
            _, _scores = self._score_route(query)
            for _dom, _sc in _scores.items():
                if _sc > 0:
                    _matched_tools.extend(self._ROUTES[_dom].tool_names)
            if _matched_tools:
                skill_block = skill_context_for_tools(_matched_tools)
        except Exception:
            skill_block = ""  # never let skill matching break a query

        # Front-load the technique-capsule SPINE for single mode. Skills (above)
        # carry per-stage procedure; the SPINE carries the technique's hard rules,
        # halt conditions, step ORDER and traps — e.g. the FF omega-sign / SkipFrame
        # / "derive lambda from calibration" rules. Mid-loop, `_maybe_capsule_msg`
        # only injects the spine once an FF/NF/PF-*named* tool fires — which is too
        # late for the planning + calibration phase (`run_remote_command`,
        # `midas_auto_calibrate` carry no technique token). Deriving the technique
        # from the SAME query-matched tools lands those rules in context BEFORE the
        # model plans. Techniques front-loaded here are passed to run() so the
        # mid-loop path does not re-inject them. Gated by APEXA_STAGE_GUARDRAILS,
        # fail-open, prompt-cache-friendly (stable system-prompt prefix).
        _preloaded_techniques: set = set()
        _guardrails_on = os.environ.get(
            "APEXA_STAGE_GUARDRAILS", "1").strip().lower() not in ("0", "false", "no", "off")
        if _guardrails_on and _capsule_registry is not None and _matched_tools:
            try:
                _techs: List[str] = []
                for _t in _matched_tools:
                    _tech = _capsule_registry.technique_for_tool(_t)
                    if _tech and _tech not in _techs:
                        _techs.append(_tech)
                # Front-load ONLY when the query resolves to a single technique
                # (e.g. a calibration query → CalibrationAgent → run_ff_calibration
                # → 'ff-hedm'). If it is ambiguous (a bare "reconstruction" query
                # matches AnalysisAgent, which bundles ff+nf+pf), do NOT front-load:
                # the spines carry technique-scoped rules that CONFLICT across
                # techniques (FF requires SkipFrame 1, NF forbids it), so loading
                # all three would dilute and mislead. The mid-loop `_maybe_capsule_msg`
                # still injects the single correct spine the moment the actual
                # FF/NF/PF-named tool fires — so the ambiguous case is unchanged.
                if len(_techs) == 1:
                    _tech = _techs[0]
                    _spine = _capsule_registry.spine_context(_tech)
                    if _spine:
                        _preloaded_techniques.add(_tech)
                        _halt = _capsule_registry.halt_checklist(_tech)
                        _halt_block = (
                            "\n\nHALT CONDITIONS — stop and ask the user if any apply "
                            f"(do not proceed on assumption):\n{_halt}") if _halt else ""
                        skill_block += (
                            f"\n\nTECHNIQUE HANDBOOK ({_tech}) — the verified MIDAS "
                            "procedure (scope, step order, hard rules, traps). It is the "
                            "source of truth for this workflow; follow it and open each "
                            "phase doc (open_phase) as you reach it." + _spine + _halt_block)
            except Exception:
                pass  # never let capsule preload break a query

        result = await self.runner.run(
            agent, query, provider, self.all_tools,
            history=None, log_entry=log_entry, on_tool_result=_capture,
            history_summary=self.running_summary if use_history else "",
            transcript=transcript, single_mode=True, max_iterations=_single_cap,
            extra_system_context=skill_block,
            preloaded_techniques=_preloaded_techniques,
        )

        n_calls = len(log_entry.tool_calls)
        looped = n_calls > 3 and len(set(tc.name for tc in log_entry.tool_calls)) == 1
        log_entry.finish(result, iterations=n_calls, looped=looped)
        self.logger.save(log_entry)
        self._last_turn_had_tool_error = any(
            not tc.success for tc in log_entry.tool_calls) if log_entry.tool_calls else False

        if use_history:
            self._update_active_dir(_turn_outcomes)
            # Full tool results now live in the transcript, so no <<digest>> is
            # needed. Compaction folds old turns into running_summary.
            await self._compact_history(provider)
        if self.context:
            self.context.add_analysis(agent.name, result)
        return result

    # ── FF-HEDM workflow graph (APEXA_WORKFLOW_MODE=graph) ───────────────────
    _FFHEDM_RE = re.compile(r"\b(ff[\s\-]?hedm|far[\s\-]?field)\b", re.I)
    _FFHEDM_RUN_HINTS = ("calibrat", "reconstruct", "run ", "pipeline",
                         "workflow", "index", "process ")

    def _ensure_ffhedm(self):
        """Lazily build the FF-HEDM graph; None if langgraph is unavailable.

        Uses ``build_default_workflow`` so the checkpointer is durable
        (AsyncSqliteSaver under ~/.apexa/) — a workflow paused on a gate survives a
        CLI restart and the next input resumes it (Phase 2, spec §9).
        """
        if self._ffhedm is None:
            try:
                from apexa_ffhedm_graph import build_default_workflow, LANGGRAPH_AVAILABLE
                if not LANGGRAPH_AVAILABLE:
                    return None
                self._ffhedm = build_default_workflow(self._execute)
            except Exception as e:
                print(f"[workflow] FF-HEDM graph unavailable: {e}", file=sys.stderr)
                self._ffhedm = None
        return self._ffhedm

    def _is_ff_hedm_workflow(self, query: str) -> bool:
        """True when the user is asking to RUN the FF-HEDM setup (not just ask
        about it) — an FF-HEDM/far-field mention plus a run/calibrate verb."""
        q = (query or "").lower()
        return bool(self._FFHEDM_RE.search(q)) and any(h in q for h in self._FFHEDM_RUN_HINTS)

    def ffhedm_pending_gate(self) -> Optional[str]:
        """Gate name the active session's FF-HEDM workflow is paused on, else None.

        Sync + cheap (reads the persisted paused-map, not the checkpointer). Lets a
        UI mark a turn as awaiting a human decision. None unless graph mode is on
        and a run is paused for the active session.
        """
        if self._workflow_mode != "graph" or self._ffhedm is None:
            return None
        try:
            session = getattr(self.context, "active_session", None) or "_ffhedm"
            return self._ffhedm.pending_gate(session)
        except Exception:
            return None

    async def process(self, query: str, provider: ArgoProvider,
                      use_history: bool = True,
                      on_tool_result: OnToolResultFn = None) -> str:
        # FF-HEDM workflow graph: deterministic ordering + human-in-the-loop gates.
        # Routes when the flag is on AND (a run is already paused on a gate → any
        # input resumes it, OR this query asks to start an FF-HEDM run). Everything
        # else falls through to the modes below untouched.
        if self._workflow_mode == "graph":
            wf = self._ensure_ffhedm()
            if wf is not None:
                session = getattr(self.context, "active_session", None) or "_ffhedm"
                if wf.is_active(session) or self._is_ff_hedm_workflow(query):
                    return await wf.astep(query, provider, session)

        # Modern single-loop mode (flag) — persistent full-context reasoning.
        if self._mode == "single":
            return await self._process_single_loop(
                query, provider, use_history=use_history,
                on_tool_result=on_tool_result,
            )
        # Recommendation short-circuit: "how should I proceed?", "what next?",
        # "yes, proceed", etc. Gives a concrete next-step recommendation from
        # context WITHOUT tool calls. Separate from explain-prior because the
        # system prompt says "recommend from what you see" not "recap what you
        # did" — prevents "I haven't done any analysis" response.
        if self._is_recommend_next(query) and self.conversation_history:
            rec = await self._recommend_from_context(
                query, provider, use_history=use_history,
            )
            if rec:
                return rec

        # Explanation short-circuit: "what was the outcome?", "how did you
        # calculate that?", "explain that", "recap", etc. Answers from
        # conversation history without re-running any tools. Skipped if
        # we have no prior turn to explain.
        if self._is_explain_prior(query) and self.conversation_history:
            explained = await self._explain_prior_turn(
                query, provider, use_history=use_history,
            )
            if explained:
                return explained
            # else fall through to normal routing

        # Fast path: deterministic NL commands skip the LLM entirely
        fast = self._match_fast_path(query)
        if fast:
            tool_name, args = fast
            return await self._run_fast_path(
                query, tool_name, args,
                on_tool_result=on_tool_result, use_history=use_history,
            )

        agent   = await self._route_with_fallback(query, provider)
        self._last_agent = agent
        history = self.conversation_history if use_history else None

        log_entry = self.logger.start(query, model=provider.model)
        log_entry.set_agent(agent.name)

        # Capture FULL tool outcomes this turn (the log keeps only a 200-char
        # preview). These build the recall digest stored in history so future
        # recap turns answer from memory instead of re-discovering files.
        _turn_outcomes: List = []
        async def _capture_outcome(name, args, result):
            _turn_outcomes.append((name, args, result))
            if on_tool_result:
                await on_tool_result(name, args, result)

        result = await self.runner.run(
            agent, query, provider, self.all_tools, history,
            log_entry=log_entry,
            on_tool_result=_capture_outcome,
            history_summary=self.running_summary if use_history else "",
        )

        # Track whether this turn had any tool errors — used by _route() on
        # the NEXT query to decide whether to bias toward the same agent when
        # the user's follow-up looks like "retry with this context".
        self._last_turn_had_tool_error = any(
            not tc.success for tc in log_entry.tool_calls
        ) if log_entry.tool_calls else False

        # Detect if the agent looped (>3 calls to a single tool = loop)
        n_calls = len(log_entry.tool_calls)
        looped = n_calls > 3 and len(set(
            tc.name for tc in log_entry.tool_calls
        )) == 1
        log_entry.finish(result, iterations=len(log_entry.tool_calls), looped=looped)
        self.logger.save(log_entry)

        if use_history:
            # Anchor future recap/recommend turns to the dataset just worked on.
            self._update_active_dir(_turn_outcomes)
            # Enrich the assistant turn with a compact tool-outcome digest so the
            # structured results (status, geometry, dataset, manifest path)
            # survive into later turns — the core "remember context" fix.
            digest = self._tool_outcome_digest(_turn_outcomes)
            assistant_content = f"{result}\n\n<<{digest}>>" if digest else result
            self.conversation_history.extend([
                {"role": "user",      "content": query},
                {"role": "assistant", "content": assistant_content},
            ])
            # Summarize-older + keep-recent: fold overflow into running_summary
            # rather than dropping it (replaces the old hard 12-msg truncation).
            await self._compact_history(provider)

        if self.context:
            self.context.add_analysis(agent.name, result)

        return result
