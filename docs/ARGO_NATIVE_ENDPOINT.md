# Argo native OpenAI endpoint — APEXA's LLM transport

Argo Gateway serves OpenAI- and Anthropic-shaped endpoints directly. APEXA talks to them
with no local component: **the argo-proxy sidecar has been removed**. Everything below
was measured against production, not read off an announcement — first on 2026-09-28,
then re-measured and corrected on 2026-10-26.

```
https://apps.inside.anl.gov/argoapi/v1/chat/completions   OpenAI-compatible  ← APEXA uses this
https://apps.inside.anl.gov/argoapi/v1/messages           Anthropic Messages (Claude Code)
https://apps.inside.anl.gov/argoapi/v1/responses          Responses API (Codex)
https://apps.inside.anl.gov/argoapi/v1/models             live model list
https://apps.inside.anl.gov/argoapi/v1/embeddings
```

`api_key` is the ANL username. ANL internal network or VPN required. The legacy
`/api/v1/resource/chat/` endpoint is unchanged, so `APEXA_LLM_MODE=argo` still works.

## Configuration

Nothing to install or run. The `anl` profile is the default and selects it:

```
DEFAULT_PRESET = "argo"     → https://apps.inside.anl.gov/argoapi/v1
profile `anl`               → preset argo, APEXA_LLM_MODE=proxy
```

An operator who explicitly sets `APEXA_LLM_MODE=argo` keeps the legacy transport — the
profile never silently changes an existing install's transport.

### WebUI startup and replies that do not execute

The WebUI now applies the same connection-profile initialization as the CLI before
connecting MCP servers. Profile resolution runs off the event loop. Previously,
only the interactive CLI initialized profiles, so a WebUI with no explicit mode
could keep using legacy `/chat/` even when the ANL profile selected structured
`/v1` for the CLI. A pinned `APEXA_LLM_PRESET` and an explicit legacy mode retain
their existing precedence; the change does not rewrite `.env`.

For the ANL profile, launch explicitly with:

```bash
APEXA_PROFILE=anl APEXA_LLM_MODE=proxy ./start_web_viewer.sh
```

Restart an existing backend with Ctrl+C in its terminal before launching the
replacement, then reload the browser. Refreshing the browser alone cannot load
Python changes. A separately pinned preset or base URL still takes precedence;
check those settings if the startup profile/endpoint differs from the intended one.

Repeated `Argo 400 with tools` warnings show that legacy requests were rejected
and retried without native tools; that log line alone does not identify the
gateway's validation error. `/api/status` returning 200 every 15 seconds is the
browser's health poll, not evidence of an active analysis. `active_chat_turns`
reports the server's current in-flight chat tasks.

An inspection that ends with only “Listing the current working directory” or
“Inspecting the configured environment” may have executed nothing. The agent now
checks terse inspection acknowledgments against this turn's execution ledger on
both transports. For an explicit inspection request with no dispatched tool, it
allows one corrective model turn within the existing iteration budget. If the
same acknowledgment persists, it returns an explicit **No inspection ran** result.
It never fabricates or automatically dispatches a tool call, and permission gates
remain in effect. This is a narrow recovery check, not a general completion or
scientific-validity classifier. A corrected request can also report missing
information or a blocker instead of executing.

These startup/recovery paths are covered by offline tests with mocked profile
resolution and scripted model responses. They do not establish that a particular
live gateway/model or beamline executor is currently healthy.

### Moving a host off the sidecar

Remove these from `.env` if present, then stop the sidecar:

```
APEXA_LLM_BASE_URL=http://127.0.0.1:44497/v1   ← must go; see below
APEXA_LLM_PRESET=argo-proxy                    ← tolerated: mapped to `argo` with a notice
APEXA_PROFILE=anl-service                      ← tolerated: mapped to `anl` with a notice
```

A leftover `APEXA_LLM_BASE_URL` pointing at the sidecar's port is **refused at startup with
the fix in the message**. Left alone it would override the native URL and fail to connect,
which reads as "Argo is down" rather than "delete one line". The startup banner shows the
URL actually in use — `…/argoapi/v1` is correct.

## Model ids: display names, not compact ids

`/v1/models` lists `Claude Opus 5.5`, `GPT-5.6 Sol`, `Gemini 3.5 Flash` — spaces and
capitals — where APEXA and the legacy endpoint use `claudeopus55`, `gpt56sol`,
`gemini35flash`. **No mapping is needed**: `OpenAICompatProvider._norm` folds case and
punctuation and resolves every one. 40 models served on 2026-10-26.

The listing still includes models Argo's docs mark deprecated (e.g. `Claude Opus 4.1`).
Listed is not the same as supported — APEXA's picker follows the docs, not the listing.

## Claude and streaming — corrected 2026-10-26

**The 2026-09-28 version of this document was wrong.** It said Argo's Anthropic path
"refuses every non-streaming request … it is not a duration limit", and concluded that
Claude could not run on this transport. It is a duration limit, and Claude runs fine.

The refusal comes from the Anthropic SDK behind Argo, which estimates a call's duration
from `max_tokens` and refuses a non-streaming call it expects to exceed 10 minutes. The
SDK's estimate puts that at **21,333 tokens**, and Argo matches it exactly:

```
max_completion_tokens   non-streaming   (Claude Opus 5.5, native endpoint, 2026-10-26)
  (unset → model max)   500  Streaming is required for operations that may take longer than 10 minutes
  8,192                 OK
  21,333                OK
  21,334                500
  32,000                500
```

The 2026-09-28 test sent **no** `max_tokens`, so Argo used the model maximum and tripped
the guard — which is why every Claude call looked refused. GPT models are unaffected.

What APEXA does:

- `OpenAICompatProvider` sends `DEFAULT_MAX_TOKENS = 16000`, below the cutoff, so the
  everyday path is a plain non-streaming call.
- If a call is refused anyway (a larger limit, or a future change at Argo), the provider
  **switches that session to streaming on the first refusal** and reassembles the stream —
  including split and parallel tool-call fragments — into the same shape
  `_to_agent_response` already parses. Detected from the response, not from a model-name
  table, so a new model is covered the day it ships. The refusal arrives as a 500, which
  would otherwise be classed transient and retried unchanged until the retries ran out.

Both paths verified live with a multi-turn tool exchange (two parallel tool calls on the
streamed path).

## Gate 0 (2026-10-26)

`scripts/gate0_llm_smoke.py` — now sends the provider's `max_tokens`, so it tests what
APEXA sends:

```
claudeopus55    PASS   multi-turn OK
claudeopus5     PASS   multi-turn OK
claudesonnet55  PASS   multi-turn OK
claudehaiku55   PASS   multi-turn OK
gpt55           PASS   multi-turn OK
gpt56sol        PASS   multi-turn OK
gemini35flash   PASS   multi-turn OK
```

## Why this matters beyond convenience

The text `TOOL_CALL:` protocol, the drift-tolerant parser and the regex guard cluster all
exist because the legacy `/chat/` endpoint could not carry a structured tool *result*.
That constraint is now gone at the source rather than worked around by a local
translator. The SC'26 paper documents the constraint as it stood; any future write-up
should say it was a property of that transport during that window, not a standing fact.

## Still open

- A full agent-loop session on copland (ledger, tool surface, guardrails) over this
  transport — Gate 0 is two turns.
- Deleting the legacy regex guards together with `ArgoProvider`, once `proxy` has soaked.
