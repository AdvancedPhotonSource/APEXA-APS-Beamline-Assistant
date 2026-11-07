"""Prevent exporting APEXA integration or mutating its frozen native context."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark/harness_comparison"))
import native_context as nc
import rotation_benchmark as rb


def test_external_harness_gets_upstream_bytes_and_native_skill_paths(tmp_path):
    upstream = tmp_path / "midas"
    upstream.mkdir()
    files = {"manuals/calibrate-integrate/README.md": b"upstream procedure\n",
             ".claude/skills/calibrate-integrate/SKILL.md": b"upstream skill\n"}
    for name, data in files.items():
        path = upstream / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (upstream / "apexa-secret.md").write_text("must not export")
    ws = tmp_path / "trial"
    ws.mkdir()
    record = {"midas": {"root": str(upstream), "files": {p: nc.sha(upstream / p) for p in files}}}
    nc.stage_midas(record, ws)
    assert (ws / "manuals/calibrate-integrate/README.md").read_bytes() == files["manuals/calibrate-integrate/README.md"]
    for folder in [".claude", ".agents", ".pi"]:
        assert (ws / folder / "skills/calibrate-integrate/SKILL.md").read_bytes() == files[".claude/skills/calibrate-integrate/SKILL.md"]
    assert not (ws / "apexa-secret.md").exists()


def test_native_context_change_is_detected_and_no_symlink_can_escape(tmp_path):
    path = tmp_path / "procedure.md"
    path.write_text("native")
    files = {"procedure.md": nc.sha(path)}
    nc.checked_files(tmp_path, files)
    path.write_text("changed")
    with pytest.raises(ValueError, match="Frozen context changed"):
        nc.checked_files(tmp_path, files)
    path.unlink()
    path.symlink_to(tmp_path.parent / "outside.md")
    with pytest.raises(ValueError, match="inside their snapshot"):
        nc.checked_files(tmp_path, files)


def test_database_writes_cannot_change_frozen_native_corpus(tmp_path):
    repo = tmp_path / "repo"
    db = repo / "knowledge_base/chroma_db"
    db.mkdir(parents=True)
    (db / "chroma.sqlite3").write_bytes(b"frozen")
    profile = tmp_path / "profile"
    private = nc.private_database(repo, profile)
    (private / "chroma.sqlite3").write_bytes(b"native runtime writes")
    assert (db / "chroma.sqlite3").read_bytes() == b"frozen"


def test_private_database_mount_is_confined_to_trial(tmp_path, monkeypatch):
    monkeypatch.setattr(rb.sys, "platform", "linux")
    monkeypatch.setattr(rb, "SANDBOX", "/usr/bin/bwrap")
    monkeypatch.setattr(rb, "REPO", tmp_path / "source")
    trial = tmp_path / "trial"
    db = trial / "profile/native-chroma"
    db.mkdir(parents=True)
    argv = rb.sandboxed(["true"], [], writable=[trial], private_knowledge_db=db)
    target = str(rb.REPO / "knowledge_base/chroma_db")
    index = argv.index(target)
    assert argv[index - 2:index + 1] == ["--bind", str(db), target]
    with pytest.raises(ValueError, match="inside a writable trial"):
        rb.sandboxed(["true"], [], writable=[tmp_path / "elsewhere"], private_knowledge_db=db)
