#!/usr/bin/env python3
"""Vendor MIDAS per-technique documentation "capsules" into APEXA.

MIDAS ships a self-describing, agent-first doc set per technique under
``manuals/<technique>/`` (``README.md`` spine + ``ENVELOPE.md`` + ``RUNBOOK.md`` +
``phase-*.md`` + ``PARAMETERS.md`` + ``DIAGNOSIS.md`` + ``LAB_NOTEBOOK.md``, all
authored to ``beamreport/DOCS_SPEC.md``). APEXA treats each such directory as a
*technique capsule* it can learn a workflow from and self-adapt to new techniques
that follow the same pattern (see ``capsule_registry.py``).

This script vendors a **synced snapshot** of those capsules into
``knowledge_base/capsules/<technique>/`` so APEXA is self-contained and
offline-safe on a beamline host, and records the exact MIDAS commit it came from
in ``knowledge_base/capsules/_pin.json``. Re-run it to refresh.

WHY VENDOR (not read a live MIDAS checkout): beamline/offline hosts may lack the
MIDAS checkout or have it behind; a pinned copy in APEXA's repo is auditable in
git and reproducible. The trade-off — the copy can go stale — is handled by the
pin file (which commit) + the fact that RUNBOOK/version claims in the docs are
*always* re-verified live (the docs themselves insist on this).

Usage:
    uv run python scripts/sync_midas_capsules.py                 # auto-detect MIDAS
    uv run python scripts/sync_midas_capsules.py --midas /path/to/MIDAS
    uv run python scripts/sync_midas_capsules.py --check         # report drift, write nothing
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CAPSULES_DIR = REPO_ROOT / "knowledge_base" / "capsules"
PIN_FILE = CAPSULES_DIR / "_pin.json"

# A directory under manuals/ is a "capsule" when it carries the spine + the
# envelope — the two files DOCS_SPEC guarantees for every technique doc set. This
# is deliberately structural (not a hardcoded technique list) so a new MIDAS
# technique is vendored automatically the next time this runs.
CAPSULE_MARKERS = ("README.md", "ENVELOPE.md")
# Only vendor text; skip binary assets (images live in manuals/assets/).
#
# ``.py`` is included for the capsules' own executable gates -- currently
# ``calibrate-integrate/floorcheck.py``, which that doc set's install gate
# declares mandatory ("Exit non-zero means stop. These are not conveniences.")
# and which is *behavioural*, deliberately not a version check.
#
# Without it the gate shipped only inside a MIDAS git checkout, so on the
# deployment the MIDAS developer actually recommends -- pip midas-suite, no repo
# clone -- the docs mandated a check that was not present. Observed on copland:
# `python manuals/calibrate-integrate/floorcheck.py` -> No such file or directory.
#
# These are not indexed into RAG (index_capsule walks named .md files only), so
# this ships the gate without putting code into the knowledge base.
TEXT_SUFFIXES = (".md", ".txt", ".py")


def _detect_midas(explicit: str | None) -> Path | None:
    """Resolve a MIDAS checkout that has a manuals/ directory."""
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    if os.environ.get("MIDAS_PATH"):
        candidates.append(Path(os.environ["MIDAS_PATH"]))
    candidates += [
        Path.home() / "Git" / "MIDAS",
        Path.home() / "opt" / "MIDAS",
        Path("/opt/MIDAS"),
    ]
    for c in candidates:
        if c and (c / "manuals").is_dir():
            return c.resolve()
    return None


def _git(midas: Path, *args: str) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(midas), *args],
            capture_output=True, text=True, timeout=15,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _discover_capsules(manuals: Path) -> list[Path]:
    found = []
    for sub in sorted(p for p in manuals.iterdir() if p.is_dir()):
        if all((sub / m).is_file() for m in CAPSULE_MARKERS):
            found.append(sub)
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--midas", help="Path to a MIDAS checkout (else $MIDAS_PATH / common locations)")
    ap.add_argument("--check", action="store_true", help="Report what would change; write nothing")
    args = ap.parse_args()

    midas = _detect_midas(args.midas)
    if not midas:
        print("ERROR: no MIDAS checkout with a manuals/ dir found. Pass --midas <path> "
              "or set MIDAS_PATH.", file=sys.stderr)
        return 2
    manuals = midas / "manuals"
    capsules = _discover_capsules(manuals)
    if not capsules:
        print(f"ERROR: no capsule dirs (need {'+'.join(CAPSULE_MARKERS)}) under {manuals}",
              file=sys.stderr)
        return 2

    commit = _git(midas, "rev-parse", "HEAD") or "unknown"
    remote = _git(midas, "config", "--get", "remote.origin.url") or "unknown"
    print(f"MIDAS: {midas}\n  commit {commit[:12]}  remote {remote}")
    print(f"capsules: {', '.join(c.name for c in capsules)}\n")

    changed = added = removed = 0
    pin: dict = {
        "midas_commit": commit,
        "midas_remote": remote,
        "source_dir": str(manuals),
        "synced_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "techniques": {},
    }

    for cap in capsules:
        name = cap.name
        dst = CAPSULES_DIR / name
        src_files = sorted(
            p for p in cap.iterdir()
            if p.is_file() and p.suffix.lower() in TEXT_SUFFIXES
        )
        existing = {p.name for p in dst.iterdir() if p.is_file()} if dst.is_dir() else set()
        file_hashes: dict[str, str] = {}

        if not args.check:
            dst.mkdir(parents=True, exist_ok=True)

        seen = set()
        for sf in src_files:
            seen.add(sf.name)
            file_hashes[sf.name] = _sha256(sf)
            target = dst / sf.name
            old = _sha256(target) if target.is_file() else None
            if old is None:
                added += 1
                print(f"  + {name}/{sf.name}")
            elif old != file_hashes[sf.name]:
                changed += 1
                print(f"  ~ {name}/{sf.name}")
            if not args.check and old != file_hashes[sf.name]:
                shutil.copy2(sf, target)

        # Prune vendored files no longer present upstream (keeps the mirror honest).
        for stale in sorted(existing - seen):
            removed += 1
            print(f"  - {name}/{stale}")
            if not args.check:
                (dst / stale).unlink()

        pin["techniques"][name] = {
            "n_files": len(file_hashes),
            "files": file_hashes,
        }

    if args.check:
        print(f"\n[check] would add {added}, change {changed}, remove {removed} file(s)")
        return 1 if (added or changed or removed) else 0

    CAPSULES_DIR.mkdir(parents=True, exist_ok=True)
    PIN_FILE.write_text(json.dumps(pin, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {PIN_FILE.relative_to(REPO_ROOT)}")
    print(f"done: +{added} ~{changed} -{removed}  ({len(capsules)} capsules)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
