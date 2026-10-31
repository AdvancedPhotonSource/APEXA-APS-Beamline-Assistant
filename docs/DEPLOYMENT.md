# Running APEXA as an always-on service

Goal: one persistent APEXA **web service** on a beamline machine (e.g. chiltepin /
chutoro), reachable in a browser on the APS network — so users don't launch it
per session. `web_server.py` defaults to
`127.0.0.1:8001`, uses the prebuilt frontend (no Node needed at runtime), and spawns the
`core` / `midas` / `motor` MCP servers itself.

The application currently has a shared MCP client and no application login.
Use one trusted operator per instance. Remote operators can use an SSH tunnel:
`ssh -N -L 8001:127.0.0.1:8001 user@beamline-host`, then open
`http://127.0.0.1:8001`. For network service, place an authenticated reverse proxy
in front of localhost and protect **both HTTP and WebSocket** routes. Authentication
alone does not provide session isolation. See [APS readiness review](APS_READINESS.md)
for the remaining production gates.

---

## Recommendation

**Run it as a systemd *user* service.** Rationale:

- **No root required** — beamline accounts rarely have sudo. A user unit lives in
  `~/.config/systemd/user/` and is managed entirely by the owning account.
- **Survives logout and reboot** once linger is enabled (`loginctl enable-linger`).
- **Auto-restart on crash** (`Restart=on-failure`) + real logs via `journalctl`.

Use a **system** service instead only if it must start at boot with *no* user ever
logged in, or run under a shared service account — that needs sudo (see below).

Use **tmux** only for a quick, throwaway "keep it up for the afternoon" — it dies
on reboot and won't auto-restart. Documented last as a fallback.

---

## Prerequisites (once, in the repo on the target machine)

```bash
cd /path/to/APEXA-APS-Beamline-Assistant
# Check out the release commit validated for this beamline before installing.
uv sync --locked         # install the release's dependency versions
./setup_user.sh          # writes .env (ANL_USERNAME, ARGO_MODEL, APEXA_MIDAS_BIN, optional MIDAS_PATH)
test -f frontend/dist/index.html && echo "frontend OK"   # prebuilt, shipped
```

Smoke-test in the foreground before daemonizing:

```bash
.venv/bin/python3 web_server.py
# → open http://127.0.0.1:8001 locally or through the SSH tunnel
```

> **Air-gapped / no-internet machine?** The RAG knowledge base needs its ~523 MB
> embedding model pre-staged, and off-machine data must be mounted/staged locally.
> See [`OFFLINE_DEPLOYMENT.md`](OFFLINE_DEPLOYMENT.md).

---

## Install — systemd user service (recommended)

```bash
cd /path/to/APEXA-APS-Beamline-Assistant
mkdir -p ~/.config/systemd/user
sed "s#__APEXA_DIR__#$PWD#g" deploy/apexa-web.service \
    > ~/.config/systemd/user/apexa-web.service

systemctl --user daemon-reload
systemctl --user enable --now apexa-web      # start now + on login
loginctl enable-linger "$USER"               # keep running when logged out / after reboot

systemctl --user status apexa-web            # should read "active (running)"
```

Browse to `http://127.0.0.1:8001` locally or through the SSH tunnel.

### Day-to-day operations

```bash
systemctl --user restart apexa-web           # after a config change
systemctl --user stop apexa-web
journalctl --user -u apexa-web -f            # live logs
journalctl --user -u apexa-web --since "1 hour ago"
```

### Updating to a new version

```bash
cd /path/to/APEXA-APS-Beamline-Assistant
# Check out the next validated release commit; retain the previous release for rollback.
uv sync --locked
systemctl --user restart apexa-web
```

---

## System service variant (boot without a logged-in user; needs sudo)

```bash
sudo sed "s#__APEXA_DIR__#/path/to/APEXA-APS-Beamline-Assistant#g" \
    deploy/apexa-web.service | sudo tee /etc/systemd/system/apexa-web.service
```

Then edit `/etc/systemd/system/apexa-web.service`:

- Uncomment `User=` / `Group=` (a dedicated account that owns the repo and `.venv` —
  **not** root).
- Change `WantedBy=default.target` → `WantedBy=multi-user.target`.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now apexa-web
sudo systemctl status apexa-web
sudo journalctl -u apexa-web -f
```

---

## Fallback — tmux (quick, non-persistent)

```bash
tmux new -s apexa
cd /path/to/APEXA-APS-Beamline-Assistant && .venv/bin/python3 web_server.py
# detach: Ctrl-b then d      reattach: tmux attach -t apexa
```

Dies on reboot, no auto-restart — use only for short-lived hosting.

---

## Decisions to settle with the beamline / supervisors

1. **Argo identity (the real policy call).** Argo Gateway authenticates with
   `ANL_USERNAME` from `.env`. A single shared service = **all traffic under one
   username** (a service account). That's fine for a shared console/demo, but if
   per-user attribution or quota matters you need a login layer that swaps the
   username per session. Raise this explicitly — it's policy, not code.

2. **Network exposure.** Keep the default `127.0.0.1:8001` backend. Use an SSH
   tunnel or an authenticated reverse proxy with TLS. An explicit override to
   `0.0.0.0` exposes the backend to any host that can route to it.

3. **Security surface.** APEXA can run shell commands and drive motors via its MCP
   tools. Require authenticated access for shared network hosting, including on
   the internal network. Scope the deployment account and enabled tools to the
   intended analysis workflow.

---

## Health check

```bash
curl -sS http://localhost:8001/ >/dev/null && echo "APEXA web up"
```

Wire this into a monitoring check or a `systemd` timer if you want alerting.
This checks HTTP availability only; it does not establish MCP, model, storage,
or MIDAS readiness.
