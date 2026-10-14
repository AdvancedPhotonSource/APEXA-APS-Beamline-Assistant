# APEXA deployment modes

These are configuration recipes, not evidence of commissioning on an APS host.
Hostnames, accounts, installed environments, detector geometry, and model names
must be supplied by the site. Preserve a validated release and its lockfile.

| Mode | APEXA/model location | Data and compute | External retrieval |
|---|---|---|---|
| 1. Connected coordinator | Laptop or ANL host with approved model access | SSH-accessible beamline with no outbound internet | MP/COD on coordinator when its tier is `web` |
| 2. ANL internal | APEXA on beamline, approved ANL-internal model endpoint | Local or configured SSH analysis hosts | Disabled; use staged CIFs |
| 3. Fully disconnected | APEXA and preinstalled model on same host | Local data and MIDAS | Disabled; use staged CIFs/catalogs |

No outbound internet does not imply no incoming SSH route. A truly disconnected
host has no SSH route from another machine; use mode 3 and approved removable
media for staging. The network tier is an application policy, not an OS firewall.

## 1. Coordinator plus SSH

Configure `remote_hosts.json` from `remote_hosts.example.json`, using actual SSH
aliases, data roots, and an absolute native MIDAS environment activation command.
`APEXA_REMOTE_HOSTS_FILE` can point to a site-specific registry. Shell startup
must not try package installs or downloads. Confirm key-based access and host
identity before unattended operation. The existing `run_remote_command` transport
and typed FF dispatch use SSH; do not assume every scientific wrapper has remote
support merely because FF does. NF/PF and other wrappers require individual
dispatch checks on the installed version.

Run the lightweight viewer beside the data using the staged APEXA checkout and
its scientific/web dependencies. It starts no model or MCP client:

```bash
# On the beamline host; foreground smoke test, then supervise with site service tooling:
/path/to/validated-env/bin/python /path/to/APEXA/science_viewer_server.py --port 8020
```

From the coordinator, establish a forward using an approved SSH alias:

```bash
ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 -L 127.0.0.1:8020:127.0.0.1:8020 beamline-alias
```

Then launch the coordinator Web UI with:

```bash
export APEXA_SCIENCE_BASE_URL=http://127.0.0.1:8020
.venv/bin/python web_server.py
```

The scientific API sends typed read/inspect requests through this loopback
forward. Detector frames are read and corrected remotely; preview images,
profiles, and metadata return to the browser. It does not copy a full acquisition
to the coordinator. Enter remote file paths in the sequence viewer's source
fields; the existing file browser itself is local. A dropped tunnel reports an
error, with no fallback to a similarly named local file. Reconnect and reload
the sequence after restarting the remote service (its manifest cache is bounded
and in memory).

MP/COD CIFs fetched by APEXA remain on the coordinator. Their structure view uses
the explicit local science route; ring simulation lets the operator select
whether its CIF is on the coordinator or the detector data host. Raw detector
data does not need to move to simulate ring positions from a local CIF.

SSH command timeouts do not prove the remote process stopped. Before retrying a
long reconstruction, inspect remote process/job state and output manifests. A
durable scheduler with job reconciliation remains a production gate.

## 2. Beamline APEXA plus internal model

Set `APEXA_NETWORK=internal`, leave `APEXA_SCIENCE_BASE_URL` unset for local data,
and configure the site-approved Argo/native/proxy endpoint using the existing
endpoint runbooks. Use strict startup preflight; model and MIDAS availability
must be checked separately. Stage embedding models and the knowledge index.
MP/COD web retrieval is disabled; local CIF inspection and refinement continue.

## 3. Fully disconnected, local model

Stage the model runtime and model weights on a compatible machine before
disconnecting. Qualify tool calling with the selected model; a reachable local
endpoint alone is not proof that it can reliably drive the scientific harness.

```bash
export APEXA_NETWORK=data
export APEXA_LLM_MODE=proxy
export APEXA_LLM_PRESET=ollama
export APEXA_LLM_BASE_URL=http://127.0.0.1:11434/v1
export ARGO_MODEL=your-installed-and-qualified-model
export APEXA_LLM_STRICT=1
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
unset APEXA_SCIENCE_BASE_URL
.venv/bin/python web_server.py
```

Do not run a model pull or package installer during acquisition. Without a local
model, the standalone scientific viewer and deterministic MIDAS commands still
work; conversational agent execution does not. The existing local profile and
endpoint work in this checkout were already present before this review.

## Install and acceptance checklist

1. Stage a release, prebuilt `frontend/dist`, the target-platform Python/cache,
   native MIDAS/GPU libraries, embedding weights/index, and approved CIFs.
2. Run `uv sync --locked --offline` with the same selected extras used to prepare
   the cache. Offline resolution failure means the bundle is incomplete. Do not
   run unpinned `git pull` or install commands at service startup.
3. Record actual MIDAS binary/package versions and run a known small reference
   job on that host. Verify output contents and agreed scientific tolerances.
4. For mode 1, test loss/recovery of SSH and viewer forwarding. For mode 2, test
   internal endpoint loss. For mode 3, verify the installed model with external
   network access absent. Record data handling policy for each mode.
5. Measure preview latency, reconstruction latency, resource use, and restart
   behavior with representative detector files. No target-host latency or
   scientific acceptance result has been measured by this local review.

The Web UI status bar displays the configured network tier and scientific data
location. These labels report configuration; they do not certify reachability.
