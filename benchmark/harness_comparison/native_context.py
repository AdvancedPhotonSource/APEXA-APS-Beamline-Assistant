"""Snapshot native APEXA context and stage unedited upstream MIDAS resources.

This is experiment infrastructure. It does not replace APEXA's procedures,
retriever or tools, and never exports APEXA-specific material to another arm.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil

MODE = "native_apexa_upstream_midas"


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def checked_files(root, files):
    root = Path(root).resolve()
    if not isinstance(files, dict) or not files:
        raise ValueError("A context snapshot requires nonempty file hashes")
    for relative, expected in files.items():
        name = PurePosixPath(relative)
        path = root / relative
        if (name.is_absolute() or ".." in name.parts or str(name) != relative
                or path.is_symlink() or not path.resolve().is_relative_to(root)):
            raise ValueError("Context paths must stay inside their snapshot")
        if not path.is_file() or sha(path) != expected:
            raise ValueError(f"Frozen context changed: {relative}")


def load(path, *, expected_sha256=None, native_repo=None, check_midas=True):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("Context receipt must be a regular file")
    if expected_sha256 is not None and sha(path) != expected_sha256:
        raise ValueError("Context receipt hash changed")
    record = json.loads(path.read_text())
    if record.get("schema_version") != 1 or record.get("mode") != MODE:
        raise ValueError("Unsupported native context receipt")
    if native_repo is not None:
        checked_files(native_repo, record["apexa"]["files"])
    if check_midas:
        checked_files(record["midas"]["root"], record["midas"]["files"])
    return record


def stage_midas(record, workspace):
    """Preserve MIDAS paths and skill bytes; add only resource-location pointers."""
    root = Path(record["midas"]["root"])
    checked_files(root, record["midas"]["files"])
    staged = []
    for relative in sorted(record["midas"]["files"]):
        name = PurePosixPath(relative)
        if not (name.is_relative_to("manuals") or name.is_relative_to(".claude/skills")):
            raise ValueError("External resource is not an upstream MIDAS manual or skill")
        destinations = [workspace / relative]
        if name.is_relative_to(".claude/skills"):
            skill = name.relative_to(".claude/skills")
            destinations += [workspace / ".agents/skills" / skill,
                             workspace / ".pi/skills" / skill]
        for target in destinations:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / relative, target)
            staged.append(target.relative_to(workspace).as_posix())
    pointer = ("MIDAS's own technique capsules are under ./manuals; start at "
               "./manuals/calibrate-integrate/README.md for detector calibration. "
               "Unedited upstream MIDAS skills are installed in the project skill directories.\n")
    for name in ("AGENTS.md", "CLAUDE.md"):
        (workspace / name).write_text(pointer)
        staged.append(name)
    return staged


def private_database(repo, profile):
    """Give native Chroma its own writable copy without changing corpus content."""
    original = Path(repo) / "knowledge_base/chroma_db"
    if not original.is_dir():
        return None
    target = Path(profile) / "native-chroma"
    shutil.copytree(original, target, symlinks=False)
    return target
