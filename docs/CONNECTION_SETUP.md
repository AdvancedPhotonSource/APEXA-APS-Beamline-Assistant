# LLM connection setup

## Terminal setup

From the repository root, run `./setup_user.sh` on Linux/macOS, or
`.\docs\setup_user.ps1` in Windows PowerShell. The command is **`./setup_user.sh`**,
not `.setup_user.sh`. Both wrappers and the older `docs/setup_user.sh` entry point
use the same Python wizard in `scripts/setup_user.py`.

You can inspect every mechanism and prepare configuration before choosing an
account. No login or credential is required:

```bash
./setup_user.sh --options
./setup_user.sh --mcp-config codex   # Print TOML for Codex; does not install it
./setup_user.sh --mcp-config claude  # Print JSON for Claude Code; does not install it
```

Windows accepts the same arguments after `.\docs\setup_user.ps1`. The setup menu
groups connections by access type, then asks for the provider or host:

| Access type | Next choices | Works in APEXA WebUI? |
| --- | --- | --- |
| 1. University / facility | ANL Argo, ALCF, AmSC, other university/facility gateway | Yes |
| 2. Personal API key | OpenAI, Anthropic, Google Gemini | Yes |
| 3. Local / self-hosted models | Ollama on this machine, other compatible model server | Yes |
| Separate `tools` option | Codex or Claude Code tool-configuration preview | Uses that app's chat, not APEXA WebUI |

For ALCF, first choose **Sophia or Minerva**, then a model from the recorded list
or enter an exact deployment ID. Recorded examples include GPT-OSS and Gemma on
Sophia, and Inkling on Minerva. ALCF is the hosting service, not a model family.
For Ollama, choose **Gemma, GPT-OSS or another installed model**, then enter the
exact local model tag (see `ollama list`). A family name does not install a model
or establish that its build supports tool calls. Setup does not download models,
query the current provider catalog or run inference; verify availability later.
You can still type a preset name directly as a shortcut.

The `tools` option replaces the old flat menu's options 10/11 and explicitly
describes the separate client interface. Both that preview and `--mcp-config`
only print configuration; they do not change `.env`, the APEXA connection, any
client configuration or login state.

Enter the exact model/deployment ID your endpoint serves. Only Argo asks for an ANL username. API providers ask
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

There are two different execution paths:

| Access | Mechanism | Where you interact |
| --- | --- | --- |
| Argo, institutional gateway, personal API account, local model | APEXA's provider transports; configured by the wizard or connection dialog | APEXA WebUI or CLI |
| ChatGPT subscription | Official Codex login; Codex calls APEXA analysis servers over MCP | Codex CLI/IDE or another local Codex client sharing its configuration |
| Claude subscription | Official Claude Code login; Claude Code calls APEXA analysis servers over MCP | Claude Code |

**Subscription login inside APEXA's own WebUI is still unimplemented.** The MCP
export provides a way to use APEXA's tools from an official subscription client.
Its chat history, model selection and approvals remain in that client. It does
not use APEXA's orchestrator, workflow review controls or Canvas/session store.
An API key is a separate credential with separate billing; do not paste a
subscription password or a CLI session token into the API-key field.

### ChatGPT / Codex, when you are ready

1. Install the official Codex client on the host where APEXA and its analysis
   environment are available. Run `codex login`, or `codex login --device-auth`
   on a headless host, and select your eligible ChatGPT account.
2. Run `./setup_user.sh --mcp-config codex`. Merge the printed TOML into your
   active Codex `config.toml` (default `~/.codex/config.toml`, or the configuration
   under your existing `CODEX_HOME`). Keep its other settings; replace existing
   `apexa-*` blocks instead of duplicating them. No credential is in the export.
3. Start Codex and inspect `/mcp`. Only enable the analysis servers you need.
   Use `codex login status` to confirm the selected authentication method.

See the official [OpenAI authentication](https://developers.openai.com/codex/auth/)
and [Codex MCP configuration](https://developers.openai.com/codex/mcp/) documentation.

### Claude / Claude Code, when you are ready

1. Install Claude Code on the APEXA host, start `claude`, and use `/login` to
   choose an eligible Claude subscription account.
2. Print `./setup_user.sh --mcp-config claude` and save the JSON as UTF-8 outside
   the repository, for example `~/.apexa/integrations/claude-mcp.json`.
3. Run `claude --mcp-config ~/.apexa/integrations/claude-mcp.json`, then inspect
   `/mcp`. Use `/status` to confirm the account; existing API/cloud-provider
   credentials can take precedence over a subscription login.

See [Claude Code authentication](https://code.claude.com/docs/en/authentication)
and [Claude Code MCP](https://code.claude.com/docs/en/mcp). Plan eligibility,
workspace policy, networking and usage limits are controlled by the provider.

### What the exported configuration does

The export uses the current APEXA Python environment and checkout's absolute
paths. Regenerate it on each target host or after moving the checkout. Each MCP
process starts in the repository directory and reads its runtime `.env`, so
native MIDAS/GSAS-II/TomoPy paths keep their existing meaning. It exports only
enabled **core, MIDAS, GSAS-II, tomography and synthesis** entries from
`servers.config`; motor and alignment servers are excluded. Knowledge-base
prewarming and unrestricted shell execution are disabled for these processes.

The client owns tool approval; tool-level APEXA checks remain, but this is not
APEXA WebUI workflow approval or an OS sandbox. Scientific packages, reference
qualification and data access still need their usual setup. The export does not
start a server or execute a tool. Offline regression checks cover config parsing,
the launcher and real MCP initialization/tool discovery; they do not establish
successful paid/subscription inference or scientific acceptance.

## WebUI setup

Open **gear → LLM connection** in the compact WebUI header. Choose **Access type**,
then **Provider / host**, enter its exact model/deployment ID, and choose
**Verify connection**. Facility access (including ALCF), personal APIs and local
models all use APEXA's live WebUI after configuration and restart. Verification sends
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
