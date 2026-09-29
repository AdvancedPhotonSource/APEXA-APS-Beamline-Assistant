"""Shared SSH transport + multi-host registry for APEXA remote execution.

Why this module exists
----------------------
APEXA runs on one host (e.g. `chiltepin`), but the real synchrotron data and the
MIDAS compute frequently live on separate analysis hosts (e.g. `copland`, and
different hosts for different beamlines/techniques). The typed MIDAS MCP tools
run their work as local subprocesses, so they cannot see remote data. This module
provides the transport (SSH/scp) and the *decision* — given a data path, which
host should the work run on — so a tool can route its execution to where the data
already lives.

Design constraints (deliberate):
  * **stdlib only** — `os`, `json`, `shlex`, `subprocess`, `functools`. No repo
    imports, so both MCP servers (core + midas) can import it as a leaf module
    with zero circular-import risk.
  * **No command allowlist here.** The core server keeps its allowlist and applies
    it to model-supplied free-text commands BEFORE calling `remote_run`. The midas
    server's commands are server-constructed from typed tool args (not model
    free-text), so they need no allowlist. Keeping the allowlist out of the
    transport is what lets both servers share this module.
  * **Fail-closed.** When it cannot prove a path is remote (or the remote host is
    unreachable), it does not silently guess — callers get an explicit signal so
    they can surface an honest error instead of running on the wrong host.

The SSH pattern (login shell `bash -lc`, BatchMode, ConnectTimeout, rc==255 = ssh
layer failure) is factored out of `beamline_core_server.run_remote_command` so
both that tool and the midas tools share one implementation.
"""

from __future__ import annotations

import json
import os
import platform
import socket
import shlex
import subprocess
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

# ── SSH options — identical to the pattern previously inlined in the core server ──
_SSH_OPTS = [
    "-o", "BatchMode=yes",              # never prompt — fail fast instead of hanging
    "-o", "ConnectTimeout=10",
    "-o", "StrictHostKeyChecking=accept-new",
]

DEFAULT_HOST_ENV = "APEXA_ANALYSIS_HOST"   # existing knob; default "copland"



def local_hostnames() -> set:
    """Every name by which this machine may be known in the registry.

    Includes the short and fully-qualified forms plus anything the operator lists
    in ``APEXA_LOCAL_HOSTNAMES`` (comma-separated), for sites where the registry
    key is an alias rather than the real hostname.
    """
    names = set()
    for n in (socket.gethostname(), socket.getfqdn(), platform.node()):
        if n:
            n = n.strip().lower()
            names.add(n)
            names.add(n.split(".", 1)[0])
    for n in (os.environ.get("APEXA_LOCAL_HOSTNAMES") or "").split(","):
        n = n.strip().lower()
        if n:
            names.add(n)
            names.add(n.split(".", 1)[0])
    names.discard("")
    return names


def is_local_host(name: str) -> bool:
    """True when ``name`` refers to the machine we are already running on.

    Without this the registry happily routes a run to the host it is already on:
    APEXA deployed ON the analysis host sees its data-root prefix, resolves the
    owning host, and SSHes to itself. That is not merely wasteful -- the loopback
    login shell is a *different environment*, typically without the conda/venv
    activation MIDAS needs, so the run fails with `midas-pipeline: command not
    found` even though the binary is on PATH in the process doing the dispatch.
    Observed on copland, 2026-08-26.
    """
    if not name:
        return False
    n = name.strip().lower()
    # A registry record's `host` is an ssh TARGET, so it may carry a user@ prefix
    # and/or a domain ("s1iduser@copland.xray.aps.anl.gov"). Compare on the bare
    # hostname, or APEXA deployed on that very machine fails to recognise itself
    # and SSHes to itself -- the loopback login shell lacks the MIDAS activation,
    # so the run then fails with `command not found` on a host where the binary is
    # plainly on PATH.
    if "@" in n:
        n = n.rsplit("@", 1)[1]
    locals_ = local_hostnames()
    return n in locals_ or n.split(".", 1)[0] in locals_


def resolve_host(host: str = "") -> str:
    """The concrete host name to ssh to: explicit arg, else env, else 'copland'."""
    return host or os.environ.get(DEFAULT_HOST_ENV) or "copland"


def _wrap_login(command: str, remote_dir: Optional[str] = None) -> str:
    """Wrap a command in a remote LOGIN shell so the remote PATH/profile is sourced.

    Mirrors the previous inline logic exactly: optional `cd`, then the command,
    the whole thing quoted as a single argv element for the local ssh invocation.
    """
    inner = f"cd {shlex.quote(remote_dir)} && {command}" if remote_dir else command
    return f"bash -lc {shlex.quote(inner)}"


def ssh_hint(host: str = "") -> str:
    """The single source of the actionable rc==255 message (auth/connectivity)."""
    target = resolve_host(host)
    return (
        f"SSH to '{target}' failed (auth or connectivity). This runs "
        f"non-interactively — set up key-based SSH: `ssh-copy-id {target}` and "
        f"confirm `ssh {target} true` works with no password prompt."
    )


def remote_run(host: str, command: str, *, remote_dir: Optional[str] = None,
               timeout: int = 600) -> Dict[str, Any]:
    """Run `command` on a remote host over SSH via a login shell.

    Returns a dict (never raises for a remote non-zero exit):
        {success, return_code, stdout, stderr, host, command, ssh_failed, timed_out}
    `ssh_failed` (return_code 255) means the SSH layer failed, not the remote
    command — callers should surface `ssh_hint(host)`.
    """
    target = resolve_host(host)
    ssh_cmd = ["ssh", *_SSH_OPTS, target, _wrap_login(command, remote_dir)]
    try:
        r = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {
            "success": False, "return_code": None, "timed_out": True,
            "ssh_failed": False, "host": target, "command": command,
            "stdout": "", "stderr": f"remote command timed out after {timeout}s on '{target}'",
        }
    return {
        "success": r.returncode == 0,
        "return_code": r.returncode,
        "stdout": r.stdout,
        "stderr": r.stderr,
        "host": target,
        "command": command,
        "ssh_failed": r.returncode == 255,
        "timed_out": False,
    }


def remote_exists(host: str, path: str, *, timeout: int = 15) -> bool:
    """True iff `path` exists on `host`. SSH failure ⇒ False (caller must not
    treat a False as 'therefore local' without checking reachability)."""
    r = remote_run(host, f"test -e {shlex.quote(path)}", timeout=timeout)
    return bool(r.get("success"))


def remote_read_text(host: str, path: str, *, max_bytes: int = 262144,
                     timeout: int = 30) -> Tuple[bool, str]:
    """Read up to `max_bytes` of a small remote text file (e.g. a param file).

    Returns (ok, text_or_error).
    """
    r = remote_run(host, f"head -c {int(max_bytes)} {shlex.quote(path)}", timeout=timeout)
    if r.get("success"):
        return True, r.get("stdout", "")
    return False, (r.get("stderr") or "remote read failed")


def remote_put(host: str, local_path: str, remote_path: str, *,
               timeout: int = 120) -> Dict[str, Any]:
    """scp a local file to the remote host (rare staging case)."""
    target = resolve_host(host)
    cmd = ["scp", *_SSH_OPTS, local_path, f"{target}:{remote_path}"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"success": False, "stderr": f"scp put timed out after {timeout}s", "host": target}
    return {"success": r.returncode == 0, "return_code": r.returncode,
            "stdout": r.stdout, "stderr": r.stderr, "host": target,
            "ssh_failed": r.returncode == 255}


def remote_get(host: str, remote_path: str, local_path: str, *,
               timeout: int = 120) -> Dict[str, Any]:
    """scp a remote file back to the local host (rare staging case)."""
    target = resolve_host(host)
    cmd = ["scp", *_SSH_OPTS, f"{target}:{remote_path}", local_path]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"success": False, "stderr": f"scp get timed out after {timeout}s", "host": target}
    return {"success": r.returncode == 0, "return_code": r.returncode,
            "stdout": r.stdout, "stderr": r.stderr, "host": target,
            "ssh_failed": r.returncode == 255}


# ──────────────────────────────────────────────────────────────────────────────
# Multi-host registry
#
# A host record is {host, data_roots:[...], activate?, midas_path?}. Data can live
# on several remote hosts (different beamlines/techniques), each with its own data
# roots and its own remote MIDAS environment. The registry maps a data-root prefix
# to the host that owns it; an explicit per-call host= always overrides inference.
# ──────────────────────────────────────────────────────────────────────────────

_REGISTRY_FILE_ENV = "APEXA_REMOTE_HOSTS_FILE"
_DEFAULT_REGISTRY_BASENAME = "remote_hosts.json"


def _registry_path() -> str:
    """Path to the registry file: env override, else a sibling of this module."""
    p = os.environ.get(_REGISTRY_FILE_ENV)
    if p:
        return os.path.expanduser(p)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        _DEFAULT_REGISTRY_BASENAME)


def _split_roots(raw: str) -> List[str]:
    return [os.path.normpath(x.strip()) for x in raw.split(":") if x.strip()]


@lru_cache(maxsize=1)
def load_remote_registry() -> Dict[str, Any]:
    """Load the host registry once (cached). Fail-open to a synthesized single-host
    registry built from the existing env knobs when no registry file is present.

    Returns {"default_host": str|None, "hosts": {name: record}}.
    """
    path = _registry_path()
    if os.path.isfile(path):
        try:
            with open(path, "r") as fh:
                data = json.load(fh)
            hosts = data.get("hosts") or {}
            # normalize data_roots on every record
            for rec in hosts.values():
                rec["data_roots"] = [os.path.normpath(r) for r in (rec.get("data_roots") or [])]
                rec.setdefault("host", None)
            default = data.get("default_host") or (next(iter(hosts), None))
            return {"default_host": default, "hosts": hosts}
        except Exception:
            # Corrupt registry → fall through to the env-synthesized single host
            # rather than break every tool. Fail-open, same principle as the KB.
            pass

    # ── Backward-compatible single-host fallback (no registry file) ──
    host = os.environ.get(DEFAULT_HOST_ENV) or "copland"
    roots = _split_roots(os.environ.get("APEXA_REMOTE_DATA_ROOTS", "/gdata"))
    rec: Dict[str, Any] = {"host": host, "data_roots": roots}
    activate = os.environ.get("APEXA_REMOTE_MIDAS_ACTIVATE", "").strip()
    if activate:
        rec["activate"] = activate
    midas_path = os.environ.get("APEXA_REMOTE_MIDAS_PATH", "").strip()
    if midas_path:
        rec["midas_path"] = midas_path
    return {"default_host": host, "hosts": {host: rec}}


def _path_under(path: str, root: str) -> bool:
    """True iff `path` is `root` or lives beneath it (segment-aware, no false
    prefix match like /gdata2 under /gdata)."""
    p = os.path.normpath(path)
    r = os.path.normpath(root)
    return p == r or p.startswith(r + os.sep)


def resolve_host_for_path(path: str) -> Optional[str]:
    """Return the host name whose data_roots contain the LONGEST prefix of `path`,
    else None. Longest-prefix wins so a more specific root (…/1ID) beats a broad
    one (/gdata) when both are configured."""
    if not path:
        return None
    reg = load_remote_registry()
    best_host, best_len = None, -1
    for name, rec in reg["hosts"].items():
        for root in rec.get("data_roots", []):
            if _path_under(path, root) and len(root) > best_len:
                best_host, best_len = (rec.get("host") or name), len(root)
    return best_host


def host_record(name: str) -> Dict[str, Any]:
    """The record for a host by name. A host named but not in the registry gets a
    bare record {host:name} (relies on the remote login-shell PATH)."""
    reg = load_remote_registry()
    for key, rec in reg["hosts"].items():
        if (rec.get("host") or key) == name or key == name:
            out = dict(rec)
            out["host"] = rec.get("host") or key
            return out
    return {"host": name}


def default_host() -> Optional[str]:
    return load_remote_registry().get("default_host")


def remote_midas_command(bare: str, record: Optional[Dict[str, Any]]) -> str:
    """Prepend the host record's `activate` snippet (e.g. `conda activate midas_env`
    or `source .../.venv/bin/activate`) to a bare MIDAS command. Empty activate ⇒
    rely on the login-shell PATH."""
    activate = (record or {}).get("activate", "").strip()
    return f"{activate} && {bare}" if activate else bare


def _truthy(val: Optional[str]) -> Optional[bool]:
    if val is None:
        return None
    v = val.strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return None


def decide_exec_host(*data_paths: str, host: str = "") -> Dict[str, Any]:
    """Decide whether a tool should run locally or on a remote host, and which.

    Returns {is_remote, host, record, reason, unreachable}.

    Precedence (fail-closed — never silently run remote on a mere local miss):
      1. Explicit host= (non-empty) → remote on that host.
      2. APEXA_FORCE_REMOTE_EXEC=1 → remote on default host; =0 → local.
      3. Registry prefix match → remote on the owning host. Two data paths owned by
         DIFFERENT hosts → error (never split a run across hosts).
      4. All provided paths exist locally → local (the unchanged path).
      5. A path fails local stat, a default host exists, and it exists remotely →
         remote. SSH failure while probing ⇒ unreachable=True (do NOT fall to local).
      6. Otherwise → local (tool emits its normal honest 'file not found').
    """
    paths = [p for p in data_paths if p]

    # 1. explicit override
    if host:
        if is_local_host(host):
            return {"is_remote": False, "host": host, "record": host_record(host),
                    "reason": f"explicit host={host!r} is THIS machine; running local",
                    "unreachable": False}
        return {"is_remote": True, "host": host, "record": host_record(host),
                "reason": "explicit host= override", "unreachable": False}

    # 2. force flag
    forced = _truthy(os.environ.get("APEXA_FORCE_REMOTE_EXEC"))
    if forced is True:
        h = default_host() or resolve_host()
        return {"is_remote": True, "host": h, "record": host_record(h),
                "reason": "APEXA_FORCE_REMOTE_EXEC=1", "unreachable": False}
    if forced is False:
        return {"is_remote": False, "host": None, "record": None,
                "reason": "APEXA_FORCE_REMOTE_EXEC=0", "unreachable": False}

    # 3. registry prefix match
    matched = {resolve_host_for_path(p) for p in paths}
    matched.discard(None)
    if len(matched) > 1:
        return {"is_remote": False, "host": None, "record": None,
                "reason": f"data paths span multiple hosts {sorted(matched)}; "
                          "a single run cannot be split across hosts — pass an "
                          "explicit host= or co-locate the inputs",
                "unreachable": False, "error": True}
    if len(matched) == 1:
        h = next(iter(matched))
        if is_local_host(h):
            # We ARE the host that owns this data root. Run local, but keep the
            # record so the caller can still apply its `activate` snippet.
            return {"is_remote": False, "host": h, "record": host_record(h),
                    "reason": f"data root owned by {h!r}, which is this machine; "
                              f"running local",
                    "unreachable": False}
        return {"is_remote": True, "host": h, "record": host_record(h),
                "reason": (f"registry data-root prefix match -> {h!r}; this machine "
                           f"answers to {sorted(local_hostnames())}, which does not "
                           f"include it"),
                "diagnosis": {
                    "matched_host": h,
                    "this_machine": sorted(local_hostnames()),
                    "fix_if_wrong": ("If this IS the data host, the registry name "
                                     "does not match its hostname: set "
                                     "APEXA_LOCAL_HOSTNAMES=<name> (or "
                                     "APEXA_FORCE_REMOTE_EXEC=0) and re-run."),
                },
                "unreachable": False}

    # 4. all local
    if paths and all(os.path.exists(os.path.expanduser(p)) for p in paths):
        return {"is_remote": False, "host": None, "record": None,
                "reason": "inputs present locally", "unreachable": False}

    # 5. confirmed-remote fallback (single default host)
    h = default_host()
    if h and is_local_host(h):
        return {"is_remote": False, "host": h, "record": host_record(h),
                "reason": f"default host {h!r} is this machine; running local",
                "unreachable": False}
    if h and paths:
        missing_local = [p for p in paths if not os.path.exists(os.path.expanduser(p))]
        for p in missing_local:
            r = remote_run(h, f"test -e {shlex.quote(p)}", timeout=15)
            if r.get("ssh_failed"):
                return {"is_remote": True, "host": h, "record": host_record(h),
                        "reason": "default host unreachable while probing",
                        "unreachable": True}
            if r.get("success"):
                return {"is_remote": True, "host": h, "record": host_record(h),
                        "reason": "confirmed present on default host", "unreachable": False}

    # 6. default local
    return {"is_remote": False, "host": None, "record": None,
            "reason": "no remote match; running local", "unreachable": False}
