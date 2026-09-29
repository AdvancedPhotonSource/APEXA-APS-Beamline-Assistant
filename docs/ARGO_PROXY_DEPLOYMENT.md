# Deploying APEXA with the argo-proxy sidecar (beamline server)

> **The sidecar is now OPTIONAL.** Argo serves an OpenAI-compatible `/v1` surface
> natively, so a beamline host no longer needs argo-proxy to get structured tool
> calling — see [`ARGO_NATIVE_ENDPOINT.md`](ARGO_NATIVE_ENDPOINT.md), which also
> documents the one catch (Argo's Anthropic path refuses non-streaming requests, so
> `claudeopus5` does not work on that transport as the provider stands).
>
> This runbook remains correct and is still the right choice when you want the
> gateway on loopback, or for **ALCF multi-user**, which forces proxy mode.
> Note `APEXA_LLM_MODE` defaults to **`argo`** (the legacy `/chat/` endpoint):
> nothing here takes effect until you set it to `proxy`.

APEXA's structured tool-calling path (`APEXA_LLM_MODE=proxy`) can be served by
[argo-proxy](https://github.com/Oaklight/argo-proxy) — an MIT-licensed, on-prem,
OpenAI-compatible front end for the Argo Gateway — or, since Argo began serving that
surface itself, by the gateway directly. This document covers deploying it
on a **beamline server**, which differs from a laptop in three ways that matter.

**Verified 2026-08-14** against argo-proxy 3.2.3 / llm-rosetta 0.8.2, Argo PROD, 51
models served. Multi-turn tool calling confirmed on `claude-opus-5`, `gpt-5.6-sol`,
and `gemini-3.5-flash` (`scripts/gate0_argo_proxy_smoke.py`).

---

## Topology: one sidecar per APEXA host, loopback-only

```
   beamline server
   ┌──────────────────────────────────────────────┐
   │  APEXA  ──http──▶  argo-proxy                │
   │                    127.0.0.1:<pinned port>   │
   └────────────────────────┬─────────────────────┘
                            │ HTTPS (ANL internal)
                            ▼
              https://apps.inside.anl.gov/argoapi
```

**Bind to `127.0.0.1`, not `0.0.0.0`.** argo-proxy authenticates to Argo using the
ANL **username as the API key** — there is no secret. A proxy listening on a routable
interface is an unauthenticated LLM gateway: anyone who can reach the port spends
Argo quota under that identity, with nothing in the audit trail distinguishing them.
`argo-proxy config init` defaults to `host: 0.0.0.0`; change it.

**Pin the port.** `config init` picks a random free port (observed: 62025 on one
machine, and the upstream default is 44497). A random port is fine interactively and
wrong for a deployment — pin it in the config *and* in APEXA's `.env` so the two
cannot drift.

**Requires the ANL internal network or VPN.** A beamline server is normally already
on it; this is usually easier than on a laptop.

---

## 1. Install and configure

```bash
pip install argo-proxy
argo-proxy config init      # then edit the file it writes
```

`~/.config/argoproxy/config.yaml`:

```yaml
config_version: '3'
user: <anl-username>
host: 127.0.0.1                                    # NOT 0.0.0.0
port: 44497                                        # pinned, not random
argo_base_url: 'https://apps.inside.anl.gov/argoapi'   # PROD, not apps-dev
verbose: false                                     # true is noisy for a service
log_to_file: true
```

> `apps-dev.inside.anl.gov` serves only beta models. `DEV_ONLY_MODELS` in
> `apexa_agents.py` is currently empty, so **every** model APEXA uses is on PROD.

## 2. Run it as a service

`/etc/systemd/system/argo-proxy.service` (or a `--user` unit):

```ini
[Unit]
Description=argo-proxy (OpenAI-compatible gateway to Argo) for APEXA
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=<service-account>
ExecStart=/usr/local/bin/argo-proxy serve
Restart=always
RestartSec=5
# Reachable only from this host.
IPAddressAllow=localhost
IPAddressDeny=any

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now argo-proxy
systemctl status argo-proxy
```

APEXA does **not** start the proxy. Keeping them independent means a proxy restart
doesn't disturb a running experiment, and APEXA's startup preflight reports the
proxy's health rather than owning it.

## 3. Point APEXA at it

`.env` on the beamline host:

```bash
ANL_USERNAME=<anl-username>
ARGO_MODEL=claudeopus5

APEXA_LLM_MODE=proxy
APEXA_LLM_BASE_URL=http://127.0.0.1:44497/v1    # must match the pinned port
APEXA_LLM_STRICT=1                              # default when mode=proxy
```

Model ids stay in **APEXA's compact form** (`claudeopus5`). The proxy serves dashed,
`argo:`-prefixed ids (`argo:claude-opus-5`) and often more than one alias per model;
`OpenAICompatProvider._resolve_model` folds case, punctuation, and the `argo:` prefix,
then falls back to a permutation match that refuses to guess when ambiguous. On
startup you'll see:

```
LLM transport: argo-proxy http://127.0.0.1:44497/v1 — model 'claudeopus5' → 'argo:claude-opus-5'
```

## 4. Verify before going live

```bash
uv run python scripts/gate0_argo_proxy_smoke.py \
  --base-url http://127.0.0.1:44497/v1 --api-key <anl-username>
```

Proves a genuine two-turn exchange (`tool_calls` → `role:"tool"` → final answer) per
vendor. Anything other than PASS means do not cut over.

---

## Failure behaviour: fail closed

`APEXA_LLM_STRICT` defaults **on** whenever `APEXA_LLM_MODE=proxy`. If the sidecar is
unreachable at startup, APEXA **refuses to start** rather than silently falling back
to the legacy Argo text protocol.

That is deliberate. The legacy path replays tool results as prose, which is the
format models drift off — the failure mode that produced fabricated calibration
reports and required a cluster of regex guards. Falling back silently would mean a
beamline could run for weeks on the fabrication-prone transport with nobody aware.
Same principle as the deletion permission gate: fail closed on the integrity-relevant
path.

| Situation | Behaviour |
|---|---|
| Proxy reachable, model resolves | Normal start; one dim transport line |
| Proxy down / wrong port, `APEXA_LLM_STRICT=1` | **Startup aborts** with the endpoint and the fix |
| Proxy down, `APEXA_LLM_STRICT=0` | Starts on legacy Argo with a loud degraded-transport warning |
| `APEXA_LLM_MODE=argo` | Legacy path, no proxy needed (pre-refactor behaviour) |

Set `APEXA_LLM_STRICT=0` on a dev laptop where uptime beats guarantees. Leave it on
at the beamline.

## Rollback

```bash
APEXA_LLM_MODE=argo
```

Restores the legacy transport with no code change, for as long as `ArgoProvider`
is retained (one release).

## Troubleshooting

| Symptom | Cause |
|---|---|
| `cannot reach argo-proxy at …: APIConnectionError` | Not running, or `APEXA_LLM_BASE_URL` port ≠ the pinned port. `systemctl status argo-proxy`. |
| `model 'x' is not served by …` with suggestions | Proxy points at `apps-dev` instead of PROD, or the id genuinely changed. `argo-proxy models` lists all. |
| `matches N served models ambiguously` | Proxy serves several aliases that fold identically. Set `ARGO_MODEL` to the exact served id. |
| `does not support /models — using model id verbatim` | Proxy lacks the listing route. Harmless; resolution is skipped. |
| `rejected 'temperature' — dropping it and retrying` | Expected. The provider learns per-model parameter restrictions instead of hardcoding a table. |
| Startup shows no transport line at all | `APEXA_LLM_MODE` isn't `proxy`; you're on the legacy path. |
