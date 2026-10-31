"""Verified replay for successful local tool results.

Inputs and nested outputs are fingerprinted; same-output-directory calls are
serialized across processes. External engines must provide an identity callback;
returning None disables reuse when the executing build cannot be established.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import functools
import hashlib
import inspect
import json
import os
from pathlib import Path
import tempfile
import time

MANIFEST = ".apexa_done.json"
_SCHEMA = 2


@functools.lru_cache(maxsize=4096)
def _file_digest(path, device, inode, size, mtime_ns, ctime_ns):
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _fingerprint(path):
    p = Path(path).expanduser().resolve(strict=True)
    if not p.is_file():
        raise ValueError(f"Not a regular file: {p}")
    before = p.stat()
    digest = _file_digest(str(p), before.st_dev, before.st_ino, before.st_size,
                          before.st_mtime_ns, before.st_ctime_ns)
    after = p.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise ValueError(f"File changed while fingerprinting: {p}")
    return {"path": str(p), "bytes": after.st_size, "sha256": digest}


def local_engine_identity(paths):
    """Fingerprint actual engine executables/release manifests; unknown => None.

    A Python executable alone does not identify packages installed into its env.
    Include qualified package/build manifests in paths as well.
    """
    try:
        files = [_fingerprint(p) for p in paths]
        return {"files": files} if files else None
    except (OSError, ValueError):
        return None


def _normalize(value):
    if isinstance(value, dict):
        return {str(k): _normalize(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    if isinstance(value, (str, os.PathLike)) and value:
        p = Path(value).expanduser()
        try:
            if p.is_file():
                return _fingerprint(p)
            if p.is_dir():
                # A directory may contain changing acquisitions or output written
                # by this call. Without an immutable manifest, bypass reuse.
                raise ValueError(f"Directory input requires an immutable manifest: {p}")
        except OSError:
            raise ValueError("Input identity unavailable") from None
    return value


def compute_key(tool, salient):
    blob = json.dumps([_SCHEMA, tool, _normalize(salient)], sort_keys=True, default=str, allow_nan=False)
    return hashlib.sha256(blob.encode()).hexdigest()


def _load(anchor):
    try:
        data = json.loads((Path(anchor) / MANIFEST).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(anchor, data):
    base = Path(anchor)
    fd, name = tempfile.mkstemp(prefix=".apexa_done.", suffix=".tmp", dir=base)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, indent=2, default=str, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, base / MANIFEST)
    finally:
        Path(name).unlink(missing_ok=True)


def _snapshot(anchor):
    out = {}
    base = Path(anchor)
    if not base.exists():
        return out
    for folder, dirs, files in os.walk(base, followlinks=False):
        dirs[:] = [d for d in dirs if not (Path(folder) / d).is_symlink()]
        for name in files:
            if name.startswith(".apexa_done"):
                continue
            p = Path(folder) / name
            if p.is_symlink():
                continue
            st = p.stat()
            out[str(p.relative_to(base))] = (st.st_size, st.st_mtime_ns, st.st_ctime_ns)
    return out


def _outputs_present(anchor, outputs):
    if not isinstance(outputs, dict) or not outputs:
        return False
    base = Path(anchor).resolve()
    try:
        for name, recorded in outputs.items():
            p = (base / name).resolve(strict=True)
            if not p.is_relative_to(base) or not p.is_file():
                return False
            if _fingerprint(p) != recorded or p.stat().st_size == 0:
                return False
        return True
    except (OSError, ValueError):
        return False


@asynccontextmanager
async def _reservation(anchor):
    """Advisory per-output-directory lock, serviced without blocking the loop.

    Not a distributed scheduler: place manifests/locks on a qualified local
    filesystem. A process exit releases the OS lock; cancelled waiters close it.
    """
    Path(anchor).mkdir(parents=True, exist_ok=True)
    stream = open(Path(anchor) / ".apexa_done.lock", "a+b")
    acquired = False
    try:
        if os.name == "nt":
            import msvcrt
            stream.write(b"0")
            stream.flush()
            def attempt():
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            def release():
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            def attempt():
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            def release():
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        while not acquired:
            try:
                attempt()
                acquired = True
            except (BlockingIOError, PermissionError):
                await asyncio.sleep(0.05)
        yield
    finally:
        if acquired:
            release()
        stream.close()


def idempotent(tool, anchor, salient, resume_keys=("resume_from",), engine_identity=None):
    def decorator(fn):
        sig = inspect.signature(fn)
        source = inspect.getsourcefile(fn)

        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            call_kwargs = dict(kwargs)
            force = bool(call_kwargs.get("force"))
            if "force" not in sig.parameters:
                call_kwargs.pop("force", None)
            bound = sig.bind(*args, **call_kwargs)
            bound.apply_defaults()
            kw = dict(bound.arguments)
            bypass_reuse = os.environ.get("APEXA_IDEMPOTENCY", "1") == "0" or force or any(kw.get(k) for k in resume_keys)
            key, facts, anchor_dir, reserve = None, None, "", True
            try:
                anchor_dir = str(Path(anchor(kw)).expanduser().resolve()) if anchor(kw) else ""
                if not anchor_dir:
                    reserve = False
                if kw.get("host"):
                    from apexa_remote_exec import is_local_host
                    if not is_local_host(kw["host"]):
                        reserve = False
                if reserve:
                    # Routing can infer a remote host from the data registry
                    # even without an explicit host argument. Never create a
                    # local lock/output tree for those remote paths.
                    from apexa_remote_exec import is_local_host, resolve_host_for_path
                    paths = [anchor_dir] + [v for k, v in kw.items()
                        if isinstance(v, str) and k in ("image_file", "data_file", "param_file", "parameter_file", "image_dir")]
                    for path in paths:
                        routed = resolve_host_for_path(path)
                        if routed and not is_local_host(routed):
                            reserve = False
                            break
                identity = (await asyncio.to_thread(engine_identity, kw) if engine_identity else {"source": _fingerprint(source)}) if reserve else None
                if identity is not None and not bypass_reuse:
                    inputs = {k: v for k, v in kw.items() if k not in ("output_dir", "result_folder", "force", *resume_keys)}
                    inputs.update(salient(kw))
                    facts = {"arguments": inputs, "engine": identity}
                    key = await asyncio.to_thread(compute_key, tool, facts)
            except (OSError, ValueError, TypeError):
                reserve = bool(anchor_dir)
            if not reserve:
                return await fn(*args, **call_kwargs)
            # Cache setup errors may bypass reuse, but never catch the tool's
            # exception and execute it a second time.
            async with _reservation(anchor_dir):
                manifest = await asyncio.to_thread(_load, anchor_dir)
                entry = manifest.get(key, {}) if key else {}
                if not isinstance(entry, dict):
                    entry = {}
                if entry.get("schema_version") == _SCHEMA and isinstance(entry.get("result"), dict) and await asyncio.to_thread(_outputs_present, anchor_dir, entry.get("outputs")):
                    prior = dict(entry["result"])
                    prior.update(cached=True, cached_at=entry.get("ts"),
                                 idempotent_note="Verified input/engine and output fingerprints match; pass force=True to rerun.")
                    return json.dumps(prior, default=str, allow_nan=False)
                try:
                    before = await asyncio.to_thread(_snapshot, anchor_dir) if key else {}
                except OSError:
                    before = {}
                result = await fn(*args, **call_kwargs)
                try:
                    from apexa_outcomes import classify_result, payload_dict
                    parsed = payload_dict(result)
                    # Only explicit terminal success is reusable; never prose,
                    # queued jobs, failed gates or provisional results.
                    explicit = parsed and (str(parsed.get("status", "")).lower() in ("ok", "success", "completed", "done") or parsed.get("success") is True)
                    if key and explicit and classify_result(parsed).successful:
                        after = await asyncio.to_thread(_snapshot, anchor_dir)
                        changed = [name for name, stat in after.items() if before.get(name) != stat and stat[0] > 0]
                        outputs = await asyncio.to_thread(lambda: {name: _fingerprint(Path(anchor_dir) / name) for name in changed})
                        if outputs and key == await asyncio.to_thread(compute_key, tool, facts):
                            manifest[key] = {"schema_version": _SCHEMA, "tool": tool, "outputs": outputs, "result": parsed, "ts": time.time()}
                            manifest = dict(sorted(manifest.items(), key=lambda item: item[1].get("ts", 0))[-64:])
                            await asyncio.to_thread(_save, anchor_dir, manifest)
                except (OSError, ValueError, TypeError):
                    pass  # Preserve the actual result, even if caching failed.
                return result
        return wrapper
    return decorator
