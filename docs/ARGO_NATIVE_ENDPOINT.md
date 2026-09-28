# Argo native OpenAI endpoints — measured 2026-09-28

Argo Gateway now serves OpenAI- and Anthropic-shaped endpoints directly, so the
**argo-proxy sidecar is no longer required**. Everything below was measured against
production, not read off the announcement.

```
https://apps.inside.anl.gov/argoapi/v1/chat/completions   OpenAI-compatible
https://apps.inside.anl.gov/argoapi/v1/responses          Responses API (Codex)
https://apps.inside.anl.gov/argoapi/v1/messages           Anthropic Messages (Claude Code)
https://apps.inside.anl.gov/argoapi/v1/models
https://apps.inside.anl.gov/argoapi/v1/embeddings
```

`api_key` is still the ANL username. The legacy `/api/v1/resource/chat/` endpoint is
unchanged and still works, so `APEXA_LLM_MODE=argo` is unaffected by any of this.

## Running APEXA without the sidecar

Two environment variables. No code change.

```bash
export APEXA_LLM_MODE=proxy
export APEXA_LLM_BASE_URL=https://apps.inside.anl.gov/argoapi/v1
```

What this removes from a beamline host: the argo-proxy install, the systemd unit, the
pinned loopback port, and the `APEXA_LLM_STRICT` startup refusal that existed because an
unreachable sidecar was a silent-downgrade hazard.

## Model ids: display names, not compact ids

Native Argo serves `Claude Opus 5`, `GPT-5.6 Sol`, `Gemini 3.5 Flash` — spaces and
capitals — where argo-proxy and legacy `/chat/` use `claudeopus5`, `gpt56sol`,
`gemini35flash`. **No mapping is needed**: `OpenAICompatProvider`'s `_norm` tier folds
case and punctuation, and resolves all of them. Verified:

```
claudeopus5 -> 'Claude Opus 5'      gpt56sol -> 'GPT-5.6 Sol'
gemini35flash -> 'Gemini 3.5 Flash' claudesonnet5 -> 'Claude Sonnet 5'  gpt54 -> 'GPT-5.4'
```

37 models served. `ARGO_MODEL=claudeopus5` in `.env` continues to work.

## The one real constraint: Claude requires streaming

Argo's Anthropic path **refuses every non-streaming request**, immediately — it is not a
duration limit:

| path | Claude Opus 5 | GPT-5.6 Sol |
|---|---|---|
| `/v1/chat/completions`, non-streaming | **500** `Streaming is required for operations that may take longer than 10 minutes` (fails in 3.1 s, before any work) | OK — a 29.5 s call returned 6569 chars |
| `/v1/chat/completions`, streaming | OK | OK |
| `/v1/messages` (base `…/argoapi`, SDK appends `/v1/messages`) | OK | n/a |

`OpenAICompatProvider` issues non-streaming chat completions, so **Claude models do not
work on this transport as it stands.** Three ways out, in increasing effort:

1. Use a GPT or Gemini default on this transport (`gpt56sol` and `gemini35flash` both
   pass Gate 0).
2. Add `stream=True` for Anthropic models in the provider and reassemble the deltas.
3. Route Claude to `/v1/messages` with the Anthropic SDK.

Option 2 is the one that keeps a single code path and keeps `claudeopus5` as the default.

## Gate 0 result (2026-09-28)

`scripts/gate0_argo_proxy_smoke.py --base-url https://apps.inside.anl.gov/argoapi/v1`

```
GPT-5.6 Sol       PASS  multi-turn OK — final answer cites 61
Gemini 3.5 Flash  PASS  multi-turn OK — final answer cites 61
Claude Opus 5     FAIL  500, streaming required (see above)
```

A full structured exchange was also driven through `OpenAICompatProvider` itself: it
resolved the model, auto-dropped `temperature` when Argo rejected it, emitted
`list_directory({'path': '/data/scan1'})`, consumed the `role:"tool"` result and answered
"Total: 3 files." That is the whole transport contract the sidecar existed to provide.

## Before this reaches a beamline

Not yet done, and deliberately so:

- a full agent-loop session (ledger, tool surface, guardrails) — Gate 0 is two turns
- the Claude streaming decision above
- copland, which must not be touched until the laptop has had a clean real session.
  Note copland reaches the ANL internal network, so this endpoint should be available to
  it; unlike the dependency upgrade, this needs no wheels downloaded.

## Why this matters beyond convenience

The text `TOOL_CALL:` protocol, the drift-tolerant parser and the regex guard cluster all
exist because the legacy `/chat/` endpoint could not carry a structured tool *result*.
That constraint is now gone at the source rather than worked around by a local
translator. The SC'26 paper documents the constraint as it stood; any future write-up
should say it was a property of that transport during that window, not a standing fact.
