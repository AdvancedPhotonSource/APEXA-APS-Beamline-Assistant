"""Raw-data write guard — APEXA must not write into the beamline detector data store.

Beamline convention (stated by the 20-ID scientist): on the **beamline machine**
(copland) the detector data store is **`/gdata`**, and within an experiment the raw
frames are collected into a **`data/`** folder; analysis output goes to an `analysis/`
sibling (or a chosen result dir). That data store belongs to the instrument — APEXA
writing there (e.g. a PF run scattering per-scan `Temp/` and `.MIDAS.zip` beside the
raw frames) is never acceptable.

**Scoped to the beamline data store, not every path.** The guard fires ONLY for paths
under a configured beamline data root (default ``/gdata``; override/extend with
``APEXA_PROTECTED_DATA_ROOTS``), and only when the sub-path is a raw-``data/`` location
(a ``data/`` segment) rather than an ``analysis/`` one. This deliberately does NOT trip
on a developer's laptop paths (``~/…/data/…``), so dev/test hosts are unaffected while
the beamline host is protected.

This is a **fail-closed gate**, the same philosophy as the deletion permission gate:
a write whose target resolves inside the protected raw-data store is **refused** with a
message explaining why and telling the agent to use an analysis/output folder.
Deliberate override: ``APEXA_ALLOW_DATA_WRITE=1`` (an operator decision, not the model's).

Stdlib-only leaf module (no repo imports) so any server can import it.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Tuple

# Beamline detector data store(s). Default: copland's /gdata. On a non-beamline host
# this prefix simply never matches, so the guard is a no-op there — exactly what we
# want (the rule is specifically for the beamline machine).
_DEFAULT_DATA_ROOTS = ("/gdata",)


def data_write_allowed() -> bool:
    """True only if the operator has explicitly opted in to data-folder writes."""
    return (os.environ.get("APEXA_ALLOW_DATA_WRITE", "") or "").strip().lower() in (
        "1", "true", "yes", "on")


def _protected_roots() -> list:
    """Beamline data-store prefixes. ``APEXA_PROTECTED_DATA_ROOTS`` (':'-separated)
    REPLACES the default ``/gdata`` when set, so a beamline whose store lives elsewhere
    (or a multi-store host) can point the guard at the right root(s)."""
    raw = (os.environ.get("APEXA_PROTECTED_DATA_ROOTS", "") or "").strip()
    roots = [r.rstrip("/") for r in raw.split(":") if r.strip()] if raw \
        else list(_DEFAULT_DATA_ROOTS)
    return roots


def is_protected_data_path(path) -> Tuple[bool, str]:
    """Is ``path`` inside the beamline raw-data store? Returns (protected, reason).

    Two conditions must BOTH hold: (1) the path is under a configured beamline data
    root (default ``/gdata``), and (2) within that root it is a raw-``data/`` location
    (has a ``data/`` path segment) rather than an ``analysis/`` one — e.g.
    ``/gdata/dm/20ID/HT_HEDM/2026-3/sangid_nov26/data/varexD/...``. This scopes the
    guard to the beamline machine's data store and still lets analysis output live
    under ``/gdata/.../analysis/...``.
    """
    try:
        p = Path(str(path)).expanduser()
    except Exception:  # noqa: BLE001
        return False, ""
    ap = str(p if p.is_absolute() else p.resolve())
    under_root = any(ap == r or ap.startswith(r + "/") for r in _protected_roots())
    if not under_root:
        return False, ""
    parts = [s.lower() for s in Path(ap).parts]
    if "data" not in parts:
        return False, ""  # under the store but e.g. an analysis/ path — allowed
    root_disp = next((r for r in _protected_roots()
                      if ap == r or ap.startswith(r + "/")), "the beamline data store")
    return True, (f"that path is inside the beamline detector data store ({root_disp}) "
                  "in a raw-'data/' folder. That store holds the detector/instrument "
                  "output for the experiment — mixing analysis output into it risks "
                  "overwriting or cluttering the raw data, so APEXA keeps it "
                  "detector-only on the beamline machine.")


def check_write_target(path, what: str = "write") -> Optional[str]:
    """Return a refusal message if writing to ``path`` is blocked, else ``None``.

    Call this at every point APEXA is about to create/write a file or an output
    directory (``write_file``, a MIDAS ``result_folder``/``output_dir``/work dir, a
    ``run_command`` that creates something). ``None`` means allowed.
    """
    if data_write_allowed():
        return None
    protected, reason = is_protected_data_path(path)
    if not protected:
        return None
    return (
        f"⛔ APEXA will not {what} into {path}.\n"
        f"Why: {reason}\n"
        "What to do: point the output at a folder OUTSIDE the data/ tree — the usual "
        "convention is an 'analysis/' folder beside 'data/' (e.g. "
        ".../<experiment>/analysis/...), or any result directory you choose. APEXA will "
        "then create and write there.\n"
        "Override (operator only): set APEXA_ALLOW_DATA_WRITE=1 if you deliberately want "
        "APEXA to write inside the data/ tree."
    )
