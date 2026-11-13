"""Write final decisions with byte-level artifact checks, not scientific certification."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile


def file_sha256(path):
    path = Path(path)
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f"Artifact must be a nonempty regular file: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _workspace_path(value, root):
    path = Path(value).expanduser()
    path = (root / path).resolve() if not path.is_absolute() else path.resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"Artifact is outside workspace_root: {value}")
    return path


def validate_manifest_artifacts(path, root, output):
    """Check the direct artifact list of an adopted JSON manifest before writing."""
    if path.suffix.lower() != ".json":
        return 0
    document = json.loads(path.read_text())
    if not isinstance(document, dict):
        return 0
    entries = document.get("artifacts", [])
    # Native scientific receipts name themselves without a self-hash, and keep
    # hashes of their other files in a separate mapping.
    if isinstance(entries, dict) and isinstance(document.get("artifacts_sha256"), dict):
        hashes = document["artifacts_sha256"]
        entries = [{"path": value, "sha256": hashes.get(value)}
                   for value in entries.values() if _workspace_path(value, root) != path]
    if not isinstance(entries, list):
        raise ValueError("Adopted JSON uses an unsupported artifacts schema; use "
                         "result_details and artifact_paths to write a task manifest")
    if "artifact_path" in document and "artifact_sha256" in document:
        # Legacy finalize_result envelopes use paths relative to their own
        # directory, unlike task manifests' workspace-relative artifact lists.
        target = Path(document["artifact_path"])
        if not target.is_absolute():
            target = path.parent / target
        entries = [*entries, {"path": str(target), "sha256": document["artifact_sha256"]}]
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise ValueError("Each manifest artifact needs path and sha256")
        target = _workspace_path(entry["path"], root)
        if target in (path, output):
            raise ValueError("Circular manifest/receipt reference; do not include the "
                             "manifest itself or the receipt being written in artifacts")
        if target in seen:
            raise ValueError(f"Duplicate manifest artifact: {entry['path']}")
        seen.add(target)
        actual = file_sha256(target)
        if actual != str(entry.get("sha256", "")).lower():
            raise ValueError(f"Artifact hash mismatch: {entry['path']}")
    return len(seen)


def write_result_manifest(status, summary, artifact_path, manifest_path,
                          *, result_details=None, artifact_paths=None,
                          workspace_root=""):
    """Preserve the legacy single-file format or build a task manifest atomically."""
    root = Path(workspace_root or Path.cwd()).expanduser().resolve()
    output = Path(manifest_path).expanduser().resolve()
    details = dict(result_details or {})
    reserved = {"schema_version", "scientific_status", "summary", "written_by",
                "artifact_path", "artifact_sha256", "artifacts", "validation",
                "manifest_path", "manifest_sha256"}
    if reserved.intersection(details):
        raise ValueError("result_details cannot override reserved fields: "
                         + ", ".join(sorted(reserved.intersection(details))))
    record = {**details, "schema_version": 1, "scientific_status": status,
              "summary": summary, "written_by": "finalize_result"}
    checked = 0
    if artifact_paths is not None or result_details is not None:
        if not output.is_relative_to(root):
            raise ValueError("Task manifest must be within workspace_root")
        entries, seen = [], set()
        for value in artifact_paths or []:
            path = _workspace_path(value, root)
            if path == output:
                raise ValueError("A manifest cannot include its own hash")
            if path in seen:
                raise ValueError(f"Duplicate artifact path: {value}")
            seen.add(path)
            digest = file_sha256(path)
            validate_manifest_artifacts(path, root, output)
            entries.append({"path": path.relative_to(root).as_posix(), "sha256": digest})
        record["artifacts"] = entries
        checked += len(entries)
    if artifact_path:
        artifact = Path(artifact_path).expanduser().resolve()
        if artifact == output:
            raise ValueError("The adopted artifact and output manifest must be different files")
        digest = file_sha256(artifact)
        checked += 1 + validate_manifest_artifacts(artifact, root, output)
        try:
            relative = artifact.relative_to(output.parent).as_posix()
        except ValueError:
            relative = str(artifact)
        record.update(artifact_path=relative, artifact_sha256=digest)
    if status == "accepted" and not artifact_path and not record.get("artifacts"):
        raise ValueError("An accepted decision needs artifact_path or nonempty artifact_paths")
    record["validation"] = {
        "artifact_integrity": "verified_at_finalization" if checked else "no_artifact_adopted",
        "files_checked": checked,
        "scientific_interpretation": "not_evaluated_by_hash_checks",
    }
    data = (json.dumps(record, indent=2, allow_nan=False) + "\n").encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, prefix=f".{output.name}.",
                                         suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {"manifest_path": str(output), "manifest_sha256": hashlib.sha256(data).hexdigest(),
            "manifest": record,
            "hash_scope": "manifest_sha256 hashes the written manifest; artifact_sha256 "
                          "inside it hashes the adopted artifact. Do not edit either after finalization."}
