"""Idempotency guard for the heavy MIDAS tools (Phase 0 of the FF-HEDM harness fix).

Prevents the "3× duplicate calibration" failure mode: an open-ended agent loop
re-deciding from scratch each iteration and firing an identical expensive tool
call two or three times on the same input.

Mechanism — a stable content hash of ``(tool, resolved input paths, salient
scientific params)`` is recorded in ``<anchor_dir>/.apexa_done.json`` together
with the FULL prior result AND the concrete top-level output files that run
produced. A later identical call returns the recorded result (``cached: true``)
IFF every recorded output file still exists and is non-empty. If the outputs
were deleted, or any salient input differs, the guard steps aside and the tool
re-runs normally.

Design rules (safety first — a false skip is worse than a redundant run):
  * The guard NEVER raises into the tool. Any internal error → run the tool.
  * A cache hit REPLAYS the exact prior result dict (plus ``cached``/``cached_at``
    keys), so downstream steps still receive every semantic output key
    (e.g. the refined-param-file path) they would from a fresh run.
  * We only record when the tool reports success AND we can name concrete output
    files it created/modified (top-level mtime diff of the anchor dir). No
    verifiable output → no cache entry → next call re-runs.
  * ``resume_from`` / ``force`` and ``APEXA_IDEMPOTENCY=0`` bypass the guard —
    an intentional continue/re-run must never be swallowed.

Escape hatches:
  * env ``APEXA_IDEMPOTENCY=0``         — disable globally.
  * a ``force=True`` kwarg on the call  — one-shot bypass (tools need not declare it).
  * any ``resume_from`` kwarg non-empty — a deliberate resume, always runs.
"""

from __future__ import annotations

import functools
import hashlib
import inspect
import json
import os
import time
from typing import Callable, Dict, List, Optional

MANIFEST = ".apexa_done.json"
_STATUS_OK = ("ok", "success", "completed", "done")


# --------------------------------------------------------------------------- #
# path + hashing helpers
# --------------------------------------------------------------------------- #
def _rp(value):
    """realpath a value that names an existing path; otherwise return it as-is.

    Resolving to a canonical absolute path makes the hash stable across relative
    vs absolute vs symlinked spellings of the same input file.
    """
    try:
        if value and isinstance(value, str):
            expanded = os.path.expanduser(value)
            if os.path.exists(expanded):
                return os.path.realpath(expanded)
    except Exception:
        pass
    return value


def compute_key(tool: str, salient: Dict) -> str:
    """Stable 16-hex-char content hash of a tool call's identity."""
    norm = {k: _rp(v) for k, v in sorted(salient.items()) if v not in ("", None)}
    blob = json.dumps([tool, norm], sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# manifest read / write (best-effort, atomic)
# --------------------------------------------------------------------------- #
def _manifest_path(anchor: str) -> str:
    return os.path.join(os.path.expanduser(anchor), MANIFEST)


def _load(anchor: str) -> Dict:
    try:
        with open(_manifest_path(anchor)) as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(anchor: str, data: Dict) -> None:
    try:
        base = os.path.expanduser(anchor)
        os.makedirs(base, exist_ok=True)
        tmp = _manifest_path(anchor) + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(data, fh, indent=2, default=str)
        os.replace(tmp, _manifest_path(anchor))
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# output-file detection (tool-agnostic: top-level mtime diff of the anchor dir)
# --------------------------------------------------------------------------- #
def _snapshot(anchor: str) -> Dict[str, float]:
    """Top-level ``{name: mtime}`` of the anchor dir; ``{}`` if it is missing.

    Non-recursive on purpose: calibration writes ``refined_*.txt`` at the top
    level, reconstruction writes a ``LayerNr_N/`` dir (its mtime changes when
    populated), and integration writes ``APEXA_integration_series.json`` + per-
    frame dirs at the top level. One scandir is cheap even for large runs.
    """
    out: Dict[str, float] = {}
    try:
        with os.scandir(os.path.expanduser(anchor)) as it:
            for entry in it:
                if entry.name == MANIFEST:
                    continue
                try:
                    out[entry.name] = entry.stat().st_mtime
                except Exception:
                    pass
    except Exception:
        pass
    return out


def _created_or_modified(before: Dict[str, float], after: Dict[str, float]) -> List[str]:
    return sorted(n for n, m in after.items() if before.get(n) != m)


def _outputs_present(anchor: str, names: Optional[List[str]]) -> bool:
    """True iff every recorded output still exists and no plain file is empty."""
    if not names:
        return False
    base = os.path.expanduser(anchor)
    for name in names:
        path = os.path.join(base, name)
        if not os.path.exists(path):
            return False
        try:
            if os.path.isfile(path) and os.path.getsize(path) == 0:
                return False
        except Exception:
            return False
    return True


# --------------------------------------------------------------------------- #
# the decorator
# --------------------------------------------------------------------------- #
def idempotent(
    tool: str,
    anchor: Callable[[Dict], str],
    salient: Callable[[Dict], Dict],
    resume_keys: tuple = ("resume_from",),
):
    """Wrap a heavy async MCP tool with a content-addressed skip-if-done guard.

    Args:
        tool:        stable tool name used in the hash + result annotations.
        anchor:      ``bound_kwargs -> output_dir`` where the manifest + outputs
                     live (e.g. ``result_folder`` or ``output_dir or dir(input)``).
        salient:     ``bound_kwargs -> {param: value}`` — the scientifically
                     meaningful inputs. Volatile knobs (n_cpus, plot flags) are
                     deliberately excluded so they don't bust the cache.
        resume_keys: kwargs whose non-empty value means "deliberate resume" →
                     bypass the guard entirely.

    Place BELOW ``@mcp.tool()`` so FastMCP registers the wrapper; ``functools.wraps``
    exposes the wrapped signature via ``__wrapped__`` so schema introspection is
    unaffected.
    """

    def decorator(fn):
        sig = inspect.signature(fn)

        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            # --- bind + derive identity; any failure → run the tool untouched ---
            try:
                bound = sig.bind_partial(*args, **kwargs)
                bound.apply_defaults()
                kw = dict(bound.arguments)
            except Exception:
                return await fn(*args, **kwargs)

            # --- explicit bypasses -------------------------------------------
            if os.environ.get("APEXA_IDEMPOTENCY", "1") == "0":
                return await fn(*args, **kwargs)
            if kw.get("force"):
                return await fn(*args, **kwargs)
            if any(kw.get(k) for k in resume_keys):
                return await fn(*args, **kwargs)

            try:
                anchor_dir = anchor(kw)
                key = compute_key(tool, salient(kw))
            except Exception:
                return await fn(*args, **kwargs)
            if not anchor_dir:
                return await fn(*args, **kwargs)

            # --- cache hit? replay the exact prior result --------------------
            manifest = _load(anchor_dir)
            entry = manifest.get(key)
            if entry and _outputs_present(anchor_dir, entry.get("outputs")):
                prior = entry.get("result")
                if isinstance(prior, dict):
                    replay = dict(prior)
                    replay["cached"] = True
                    replay["cached_at"] = entry.get("ts")
                    replay["idempotent_note"] = (
                        f"Identical {tool} already completed for these inputs; "
                        "returning the prior result (outputs verified on disk). "
                        "Pass force=True to re-run."
                    )
                    return json.dumps(replay, indent=2, default=str)

            # --- fresh run; capture outputs by top-level mtime diff ----------
            before = _snapshot(anchor_dir)
            result = await fn(*args, **kwargs)
            try:
                parsed = json.loads(result) if isinstance(result, str) else result
                status = str(parsed.get("status", "")).lower() if isinstance(parsed, dict) else ""
                if isinstance(parsed, dict) and status in _STATUS_OK:
                    outputs = _created_or_modified(before, _snapshot(anchor_dir))
                    if outputs:  # only cache when we can verify concrete outputs
                        manifest[key] = {
                            "tool": tool,
                            "outputs": outputs,
                            "result": parsed,
                            "ts": time.time(),
                            "status": status,
                        }
                        _save(anchor_dir, manifest)
            except Exception:
                pass
            return result

        return wrapper

    return decorator
