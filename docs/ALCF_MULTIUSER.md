# Multi-user APEXA via ALCF Inference Service

How to let beamline users run APEXA **under their own credentials** instead of a
shared beamline account, using ALCF's on-prem inference service.

Why this works: ALCF is OpenAI-compatible and authenticates each user with **their
own Globus identity**. Since APEXA's `OpenAICompatProvider` speaks plain OpenAI
protocol, pointing at ALCF needs **no code change** — only configuration. Inference
is attributed to the user who ran it, and no beamline data leaves ANL.

> Read `ARGO_PROXY_DEPLOYMENT.md` first for the transport model and the
> `APEXA_LLM_STRICT` fail-closed behaviour. This document covers only what differs.

---

## The three problems, separated

| Problem | Solution |
|---|---|
| **LLM credential** — who is billed/attributed | Per-user Globus token (ALCF) |
| **Unix account** — who runs MIDAS, reads data | Their own SSH login |
| **Isolation** — sessions, motors, deletions | One APEXA process per user |

They're independent. Sessions already live in `Path.home()/.apexa/sessions`, so
**per-user processes isolate for free** — a shared read-only APEXA install plus each
user's own `.env` is the whole deployment.

Do **not** use the shared web UI for multi-user: `web_server.py` has no
authentication of any kind, and the deletion permission callback is single-flight
("one beamline user"), so confirmation prompts would cross between users.

Also drop the motor server from a general user's `servers.config`. EPICS `caput`
carries no per-user identity, so concurrent motion is hazardous regardless of Unix
accounts. (Progressive disclosure already keeps motor tools out of the default tool
surface, but removing the server is the real control.)

---

## 1. Each user authenticates once

```bash
uv sync --extra alcf        # globus-sdk + openai, already in uv.lock
# ALCF's helper — docs.alcf.anl.gov/services/inference-endpoints
uv run python inference_auth_token.py authenticate
```

**Use `uv run`, not bare `python`.** APEXA resolves the token by invoking the
helper with `sys.executable` (`_default_token_cmd`), i.e. the `.venv` interpreter —
so `globus_sdk` has to be importable *there*. A bare `python` hits the system
interpreter, which typically has neither the package nor any relation to the
environment APEXA will use, and the failure surfaces later as a 401 rather than as
a missing module.

`uv sync --extra alcf` installs from the existing lockfile and does **not** modify
`uv.lock` — safe on an air-gapped host, unlike `uv sync --upgrade` (see
`OFFLINE_DEPLOYMENT.md` §0.0).

Access tokens last 48 h and auto-refresh; a full re-auth is required every 30 days
(`--force`). **Restart APEXA after re-authenticating** — the token is cached in
process, so a `model` switch alone will keep using the stale one.

## 2. Qualify the models — before trusting any of them

ALCF's capability flags (**B**=batch, **R**=reasoning, **T**=tool calling,
**H**=always hot) say what a model *should* do. This measures what it *does*:

```bash
python scripts/alcf_qualify_models.py            # all candidates
python scripts/alcf_qualify_models.py --preset alcf-minerva -v
```

Four probes, hardest last:

1. **reachable** — endpoint answers
2. **tool_call** — turn 1 returns a structured call, not prose about calling one
3. **multi-turn** — a `role:"tool"` result is accepted and used
4. **chaining** — calls tool A, reads A's **result**, uses it to choose tool B's
   arguments

**Probe 4 is the one that matters.** It mirrors APEXA's real loop
(`search_tools` → `load_tools` → the real tool). A `T` flag only promises a model can
*emit* a call. A model that passes 1–3 but fails 4 will handle "list this directory"
and fail an FF-HEDM workflow — do not deploy it.

Non-always-hot models cold start in 10–15 min, hence the 600 s default timeout.

## 3. Point APEXA at a qualified model

Per-user `.env` (never a shared file — the token is a credential):

```bash
APEXA_LLM_MODE=proxy
APEXA_LLM_PRESET=alcf-sophia
ARGO_MODEL=openai/gpt-oss-120b
APEXA_LLM_TOKEN_CMD="python inference_auth_token.py get_access_token"
APEXA_LLM_STRICT=1
```

`APEXA_LLM_TOKEN_CMD` — not a static key — is what keeps a long beamline session
alive: the credential is re-resolved per request (cached ~10 min), so a token
refreshed on disk is picked up without restarting APEXA.

---

## Model guidance (ALCF flags, read 2026-08-18)

| Model | Flags | Fit |
|---|---|---|
| `openai/gpt-oss-120b` | **B,R,T,H** | **Best fit** — the only model with all four. Harmony-native, which is the format ALCF requires for tool calls. |
| `openai/gpt-oss-20b` | B,R,T,H | Fast fallback for cheap turns |
| `google/gemma-4-31B-it` | R,T,H | Viable; small for APEXA's scope |
| `nvidia/nemotron-3-super-120b` | R,T | No H → 10–15 min cold start |
| `arcee-ai/Trinity-Large-Thinking-W4A16` | R,T | No H; 4-bit quantized |
| `meta-llama/Llama-3.3-70B-Instruct` | B,T | No reasoning flag |
| `inkling-bf16`, `nemotron-3-ultra` (Minerva) | H | Tool calling **unconfirmed** — qualify first |
| `argonne/AuroraGPT-*` | B | **No tool calling — cannot run APEXA** |
| `mistralai/Devstral-2-123B` | — | No tool-calling flag |
| Metis (any) | — | ALCF documents tool calling as unsupported; preset is rejected |

**Set expectations honestly.** `gpt-oss-120b` benchmarks around o4-mini class. That
is capable, but it is **not** Opus 5 or GPT-5.6, and the gap is widest exactly where
APEXA lives: long-horizon workflows with many tool calls and error recovery. Short
single-tool turns will feel fine; a full FF reconstruction will not feel the same.

**Context is the sharper limit.** The only figure ALCF publishes is `65536` in an
example config — 64 K against Opus 5's 1 M. Confirm per model:

```
GET /resource_server/sophia/models?model_id=openai/gpt-oss-120b
```

This is why progressive tool disclosure is load-bearing here rather than a nicety:
it cut the request from 81 tool schemas + an 11,027-char preamble to ~11 schemas +
627 chars, which is what makes a 64 K-context model workable at all.

## Suggested tiering

| Users | Endpoint | Why |
|---|---|---|
| External / facility users | ALCF `gpt-oss-120b` | Own Globus identity, on-prem, always hot |
| Staff running heavy FF work | Argo `claudeopus5` | Where the reasoning gap costs science |
| High-frequency cheap turns | ALCF `gpt-oss-20b` | Fast, hot |

## 4. Validate the science, not just the loop

Qualification proves the agent loop works. It does **not** prove the analysis is
right. Before letting users near real data:

```bash
uv run python benchmark/eval_safety.py     # 0/200 — blocking
uv run python benchmark/eval_harness.py    # compare vs benchmark/results/
```

Expect lower task accuracy than the Argo frontier numbers. Record the gap — it is
the honest basis for telling users what the open-model path can and cannot do.
