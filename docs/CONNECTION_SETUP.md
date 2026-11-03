# LLM connection setup

## Terminal setup

From the repository root, run `./setup_user.sh` on Linux/macOS, or
`.\docs\setup_user.ps1` in Windows PowerShell. The command is **`./setup_user.sh`**,
not `.setup_user.sh`. Both wrappers and the older `docs/setup_user.sh` entry point
use the same Python wizard in `scripts/setup_user.py`.

Choose the provider first: Argo, OpenAI API, Anthropic API, Gemini API, an
institutional gateway, local Ollama, ALCF or AmSC. Enter the exact model/deployment
ID your endpoint serves. Only Argo asks for an ANL username. API providers ask
for a hidden key; setup stores it in the OS keyring and saves the selected
connection to `~/.apexa/connection.json`, just like the WebUI. Setup makes no
model requests and does not establish that the chosen model supports tool calls.

Use `./setup_user.sh --connection-only` to change the connection without runtime
prompts. Full setup also offers the network tier and local MIDAS paths, or Linux
SSH routing on Windows. Existing runtime settings are preserved by default;
enter `-` to clear an optional setting. The wizard does not probe networks or
install scientific engines. For Argo on Copland, retain `internal` and the
existing native `APEXA_MIDAS_BIN`.

The final summary identifies any old `.env` connection overrides that will be
removed when you save. Unrelated `.env` settings are retained. An existing
exported connection/key/token override in the calling shell stops setup with
instructions to unset it first; this prevents a saved selection being silently
ignored or receiving a credential intended for another endpoint. Headless hosts
without a keyring can leave the key blank, then supply `APEXA_LLM_API_KEY` in the
server's protected environment for the selected endpoint. No plaintext fallback
is created. Restart APEXA after saving.

## Personal chat subscriptions

**Subscription sign-in is not implemented in APEXA.** Neither setup script nor
the WebUI can currently use a ChatGPT or Claude subscription. An API key is a
separate credential with separate billing; do not paste a subscription password
or a CLI session token into the API-key field.

The [official OpenAI authentication documentation](https://developers.openai.com/codex/auth/)
supports ChatGPT subscription login through `codex login` (or
`codex login --device-auth` on a headless host). This authenticates **Codex**;
APEXA does not invoke that client and does not use its credentials. Supporting
that path inside APEXA requires a dedicated integration, including streaming,
tool approvals, cancellation and session handling. API configuration is not a
substitute for that unfinished work.

## WebUI setup

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
credential. OpenAI Responses transport is also not implemented in APEXA.

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
