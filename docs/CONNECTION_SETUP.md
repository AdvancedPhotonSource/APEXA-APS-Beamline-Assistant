# LLM connection setup

Open **gear → LLM connection** in the compact WebUI header. Select a provider,
enter its exact model/deployment ID, and choose **Verify connection**. This sends
two small synthetic requests to that endpoint and may consume API credits. It
checks a structured tool call and matching tool result without sending any
conversation, detector data or scientific files.

**Save for restart** writes non-secret settings to `~/.apexa/connection.json`
(`APEXA_HOME` overrides the directory). Keys use the server's OS keyring, bound
to endpoint and settings revision. They are not returned by the API, persisted
in the browser, or written to the JSON. If a headless host has no usable keyring,
use `APEXA_LLM_API_KEY` in its protected process environment. Restart APEXA after
saving; running work keeps its current connection.

Explicit `APEXA_PROFILE`, `APEXA_LLM_PRESET`, `APEXA_LLM_BASE_URL` or
`APEXA_LLM_MODE` settings take precedence over saved configuration. The dialog
reports this instead of silently overriding site policy. Existing CLI profiles
in `~/.apexa/profiles.toml` still work: `profile list`, `profile <name>`, and
`profile key <preset>`.

A generic `APEXA_LLM_API_KEY` or token helper belongs to its configured endpoint.
CLI profile changes refuse to carry it to a different endpoint. Use scoped
keyring/provider keys, or set the new profile and credential together before
restarting. `scripts/profile_smoke.py` uses the same native tool-result verifier
as the dialog. If saved JSON is corrupt, move `connection.json` aside in the
private state directory and reopen settings; the original file is not discarded.

| Provider | Preset | Credential |
| --- | --- | --- |
| ANL Argo | `argo` | ANL username; internal network/VPN |
| OpenAI API | `openai` | Platform API key |
| Anthropic API | `anthropic` | API key; native Messages protocol |
| Gemini API | `gemini` | API key; OpenAI-compatible transport |
| University/lab gateway | `custom` | Gateway key and HTTPS base URL |
| ALCF | `alcf-sophia`, `alcf-minerva` | Authorized Globus token/helper |
| AmSC | `mag` | Project token |
| Local Ollama | `ollama` | Installed model with tool support; no key |

ANL_USERNAME is required only for Argo. A university chat license is not an API
credential. Subscription sign-in and OpenAI Responses transport are not
implemented in APEXA. See [OpenAI authentication](https://developers.openai.com/codex/auth/).

For an environment-managed university gateway:

```text
APEXA_LLM_PRESET=custom
APEXA_LLM_MODE=proxy
APEXA_LLM_BASE_URL=https://llm.example.edu/v1
ARGO_MODEL=the-site-approved-deployment-id
APEXA_LLM_STRICT=1
```

Supply credentials separately. Public-provider verification requires
`APEXA_NETWORK=web`; `data` permits only loopback verification. A university
OAuth/Entra endpoint without the supported API shape needs a site gateway or
adapter. Provider verification does not establish scientific accuracy.
