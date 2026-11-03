# APEXA on Windows

Use a browser connected to a Linux APEXA instance, WSL2 for a local Linux runtime,
or the native Windows coordinator. Scientific engines are qualified separately.
The former claim that every native Windows feature was first-class was too broad.

## WSL2 local installation

In administrator PowerShell:

```powershell
wsl --install -d Ubuntu
```

Restart if requested. In Ubuntu, clone a reviewed release under the Linux
filesystem (for example `~/Git/APEXA`), install uv using its official Linux
instructions, then:

```bash
uv sync --locked
uv run --no-sync python scripts/platform_smoke.py
./start_web_viewer.sh
```

Open `http://localhost:8001` in the Windows browser. Keep active SQLite/lock state
off `/mnt/c` and synchronized folders. Configure **gear → LLM connection** using
[connection setup](CONNECTION_SETUP.md). ANL accounts are required only for Argo.
Native scientific packages and WSL GPU drivers need separate qualification.

## Native Windows coordinator

Install Git and uv (`winget install --id=astral-sh.uv -e`), reopen the terminal,
and clone the release. uv selects Python from `.python-version`.

```powershell
uv sync --locked
uv run --no-sync python scripts/platform_smoke.py
.\start_web_viewer.ps1
```

The `.bat` launchers also work. Node/npm are needed only to rebuild the frontend;
committed `frontend/dist` is sufficient to run it. For rebuilding, use Node 24,
`npm ci` and `npm run build` inside `frontend`.

MIDAS and PVAccess extras are platform-excluded on native Windows. Installing
`--extra midas` does not install that stack there. Use WSL2 or a qualified Linux
worker. EPICS devices and physical alignment are not commissioned by installation.

## Verification

Quality CI includes Ubuntu, macOS and Windows offline Python tests and a portable
startup/storage smoke check. Check the result for the exact release being used.
POSIX-only tests disclose skips. Native Windows CI is not a WSL2 qualification:
run the same smoke command inside the actual WSL installation.

See [Slurm execution](REMOTE_EXECUTION.md) and
[shared deployment](SHARED_DEPLOYMENT.md). Real provider, engine, reference-data,
load and recovery acceptance remain separate from installation checks.
