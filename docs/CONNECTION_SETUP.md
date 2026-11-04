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

Windows accepts the same arguments after `.\docs\setup_user.ps1`. **ANL Argo is
the direct default** for a new setup. Other connections remain grouped by access
type. An existing connection remains the default, including non-Argo providers:

| Access type | Next choices | Works in APEXA WebUI? |
| --- | --- | --- |
| 1. ANL Argo | ANL username, then Argo model | Yes |
| 2. Personal API key | OpenAI, Anthropic, Google Gemini | Yes |
| 3. Local / self-hosted models | Ollama on this machine, other compatible model server | Yes |
| 4. Other university / facility | ALCF, AmSC, other university/facility gateway | Yes |
| 5. External app integration | Codex or Claude Code tool-configuration preview | Uses that app's chat, not APEXA WebUI |

For Argo, select **1** (or press Enter on a new/Argo setup), enter your ANL
username, then choose a model by name/number, such as **GPT-5.6 Sol** or
**Claude Sonnet 5.5**. APEXA fills in Argo's model ID. Existing model choices are
preserved on Enter. Argo needs the ANL
network or VPN and uses your ANL username; no personal API key or proxy is needed.
The short model list contains examples, not a live catalog or an availability
guarantee. The WebUI's **LLM connection → Model** dropdown offers the same named
choices. **Other model (advanced)** is available for an unlisted model; ordinary
setup does not require knowing or typing an ID. The CLI still accepts direct IDs
for experienced users.

For ALCF, first choose **Sophia or Minerva**, then a model by name. Recorded
examples include GPT-OSS and Gemma on Sophia, and Inkling on Minerva. AmSC also
offers named Claude models. The CLI and WebUI fill in each host's own ID;
advanced entry remains available. ALCF is the hosting service, not a model family.
For Ollama, choose **Gemma, GPT-OSS or another installed model**, then enter the
exact local model tag (see `ollama list`). A family name does not install a model
or establish that its build supports tool calls. Setup does not download models,
query the current provider catalog or run inference; verify availability later.
You can still type a preset name directly as a shortcut.

Option **5** replaces the old flat menu's options 10/11; typing `tools` still
opens it as a shortcut. Both that preview and `--mcp-config`
only print configuration; they do not change `.env`, the APEXA connection, any
client configuration or login state.

For a new personal API connection, explicitly choose **OpenAI**, **Anthropic**
or **Google Gemini**; there is no preselected provider. A saved API provider is
offered as the default when returning to setup. Text in brackets is what Enter
accepts; typing a different provider number overrides it. Setup confirms the
selected provider before asking for its API key.

**Paste the key in the hidden prompt immediately after choosing the provider.**
For Anthropic the sequence is:

```text
Access type [argo]: 2
Which provider / host?: 2
Selected provider: Anthropic
Paste your Anthropic API key/token (hidden):
```

Characters do not appear while you type or paste; press Enter afterward. Enter
on an empty key prompt keeps a matching saved key or lets you configure the key
later. The key is stored in the OS keyring only after the final save confirmation.

Then **choose a model by name/number**. APEXA fills in its API ID; there is no need
to look up or type one. For example, Anthropic offers Claude Sonnet 5.5, Haiku
5.5 and Opus 5.5. Enter accepts the displayed default (your saved model when
present). The WebUI offers the same named choices in a **Model** dropdown.
**Other model (advanced)** allows an exact ID for models outside the short list;
direct ID entry in the terminal still works. Existing model choices, including
unlisted IDs, are preserved. The WebUI shows an unlisted saved selection as
**Current model**; editing its ID requires choosing **Other model (advanced)**.
Empty advanced input asks again in the terminal. Ctrl+C cancels setup.

The offline choices were checked against provider documentation on **2026-11-03**:

| Provider | Built-in choices | Official catalog |
| --- | --- | --- |
| OpenAI | GPT-6.1 Sol, GPT-6 Astra, GPT-5.1 | [OpenAI Models](https://developers.openai.com/api/docs/models) |
| Anthropic | Claude Sonnet 5.5, Haiku 5.5, Opus 5.5 | [Claude Models](https://platform.claude.com/docs/en/about-claude/models/overview) |
| Google Gemini | Gemini 3.8 Flash, 3.5 Flash-Lite, 4 Argon | [Gemini Models](https://ai.google.dev/gemini-api/docs/models) |

These are documented tool-capable API models, not an account-specific availability
check. The operator can use **Verify connection** later; setup itself makes no
provider requests. Model IDs and labels are maintained together in
`apexa_llm_endpoints.py` so the terminal and WebUI stay consistent.

Only Argo asks for an ANL username. Setup saves the selected
connection to `~/.apexa/connection.json`, just like the WebUI. Setup makes no
model requests and does not establish that the chosen model supports tool calls.

Use `./setup_user.sh --connection-only` to change the connection without runtime
prompts. Full setup also offers the network tier and local MIDAS paths, or Linux
SSH routing on Windows. Existing runtime settings are preserved by default;
enter `-` to clear an optional setting. The wizard does not probe networks or
install scientific engines. For Argo on Copland, retain `internal` and the
existing native `APEXA_MIDAS_BIN`.

If a configured MIDAS directory is missing, setup explains how to keep it, clear
it with `-`, or replace it. `MIDAS_PATH` is an optional clone for legacy/forward
simulation; a stale path such as `~/opt/MIDAS_canonical` does not mean the native
pip runtime is missing. Setup suggests nearby clones without choosing one for
you. See [MIDAS setup](MIDAS_SETUP.md) for native runtime discovery.

The final summary identifies any old `.env` connection overrides that will be
removed when you save and lists the runtime settings to save or clear.
**At the final prompt, Enter, `y` or `yes` saves; `n` or `no` cancels.** Nothing is
written before confirmation. Older versions defaulted to No: a run ending with
`Cancelled. No configuration files changed.` did not save the selected model.
Unrelated `.env` settings are retained. An existing
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

On Linux/macOS, export and launch from the APEXA directory without copying JSON:

```bash
mkdir -p ~/.apexa/integrations
./setup_user.sh --mcp-config claude > ~/.apexa/integrations/claude-mcp.json
claude --mcp-config ~/.apexa/integrations/claude-mcp.json
```

The interactive option **5** only previews configuration. These export commands
write the separate client configuration; they do not change APEXA's saved LLM.

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

After updating APEXA with `git pull`, **restart the Python web server**, then
refresh the browser. An older running server can serve the newly updated WebUI
files while still lacking their API routes. A connection-settings **HTTP 404**
means the running server does not expose `/api/connection`; it is not an Argo
authentication failure. For a terminal-launched server, stop it with Ctrl+C and
run `./start_web_viewer.sh` again (`.\start_web_viewer.bat` on Windows). For a
managed deployment, restart the APEXA service through its supervisor.

The connection dialog shows loading progress and a specific error if settings
cannot be fetched. After correcting the server, choose **Retry loading settings**;
opening the dialog and retrying only read settings and make no model requests.
If the dialog asks you to restart to load the named model menu, the running
backend still has the older model catalog. Restart the server and reopen the
dialog; refreshing the browser alone cannot update that catalog.

Open **gear → LLM connection** in the compact WebUI header. Choose **Access type**,
then **Provider / host** for non-Argo access, paste the key in the **API key**
password field just below the provider, select a model, and choose
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

## Anthropic API errors and subscription billing

An Anthropic **HTTP 400** can mean the API account has no credit, even when the
key is valid and the selected model appears in the API catalog. APEXA reports
recognized billing, authentication and rate-limit failures with fixed guidance;
it does not display raw provider bodies, keys or prompts. Check API credits in
[Claude Console billing](https://platform.claude.com/settings/billing).
Unused Claude subscription allowance does **not** provide Anthropic API credit.
Use ANL Argo for facility access, or option **5 → Claude Code** to use APEXA tools
from a separately authenticated Claude Code subscription session.

Saving a connection makes no provider requests and cannot establish API credit
availability. **Verify connection** checks an actual synthetic tool round-trip;
Anthropic checks use automatic tool selection and allow up to 4,096 output
tokens per request, including thinking. The check fails if no structured tool
call or matching result is returned. This accommodates Opus/Sonnet 5.5, which
[reject forced tool selection](https://platform.claude.com/docs/en/build-with-claude/tool-use/implement-tool-use).
Normal Anthropic chat preserves native tool/result blocks and signed thinking
across turns. Failed or interrupted streams do not dispatch partial tool calls.

When switching from Argo, full setup preserves an existing network tier and
explains that `internal` blocks public-provider verification. Select `web` when
public access is permitted. Setup does not change an existing tier silently.
