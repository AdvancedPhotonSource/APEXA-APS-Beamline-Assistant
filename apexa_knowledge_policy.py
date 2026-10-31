"""Optional hash-pinned study knowledge boundary; normal operation is unchanged.

This constrains application-managed injection/retrieval, not arbitrary shell
access, external tools or network services. A reviewer declaration is not proof
that the selected material is free of study answers.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re

REPO = Path(__file__).resolve().parent
MANIFEST_ENV = "APEXA_KNOWLEDGE_MANIFEST"
DIGEST_ENV = "APEXA_KNOWLEDGE_MANIFEST_SHA256"
_SHA = re.compile(r"[0-9a-f]{64}")


class KnowledgePolicyError(ValueError):
    pass


def _require(condition, message):
    if not condition:
        raise KnowledgePolicyError(message)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, "Study manifest contains duplicate JSON keys")
        result[key] = value
    return result


def load_manifest(path: str | Path, expected_sha256: str | None = None) -> dict:
    path = Path(path)
    _require(path.is_file() and not path.is_symlink(), "Study manifest must be a regular, nonsymlink file")
    _require(path.stat().st_size <= 2_000_000, "Study manifest exceeds 2 MB")
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if expected_sha256 is not None:
        _require(bool(_SHA.fullmatch(expected_sha256)) and digest == expected_sha256,
                 "Study manifest SHA-256 does not match its frozen digest")
    try:
        record = json.loads(raw, object_pairs_hook=_unique_object,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
    except (ValueError, UnicodeError) as exc:
        raise KnowledgePolicyError("Study manifest is not valid unique-key JSON") from exc
    _require(isinstance(record, dict) and type(record.get("schema_version")) is int and record["schema_version"] == 1, "Study manifest requires schema_version 1")
    _require(not set(record) - {"schema_version", "study_id", "reviewed_by", "review_note", "files", "retrieval"},
             "Unknown study manifest fields")
    for key in ("study_id", "reviewed_by", "review_note"):
        _require(isinstance(record.get(key), str) and 0 < len(record[key].strip()) <= 2000,
                 f"Study manifest requires a nonempty {key} declaration")
    files = record.get("files")
    _require(isinstance(files, dict) and len(files) <= 5000, "files must map at most 5000 repository-relative paths to SHA-256")
    for name, sha in files.items():
        p = PurePosixPath(name)
        _require(not p.is_absolute() and str(p) == name and ".." not in p.parts and "\\" not in name and bool(p.parts),
                 "Manifest file paths must be normalized relative POSIX paths without traversal")
        _require(isinstance(sha, str) and bool(_SHA.fullmatch(sha)), "Every allowed file requires a lowercase SHA-256")
    retrieval = record.get("retrieval")
    if retrieval is not None:
        _require(isinstance(retrieval, dict) and set(retrieval) == {"collection_name", "corpus_revision", "embedding", "sources"},
                 "retrieval requires collection_name, corpus_revision, embedding and sources")
        _require(isinstance(retrieval["collection_name"], str) and bool(re.fullmatch(r"[A-Za-z0-9_-]{3,160}", retrieval["collection_name"])),
                 "Invalid frozen retrieval collection name")
        _require(isinstance(retrieval["corpus_revision"], str) and bool(_SHA.fullmatch(retrieval["corpus_revision"])),
                 "Frozen corpus_revision must be a SHA-256")
        _require(isinstance(retrieval["embedding"], dict) and bool(retrieval["embedding"]), "Frozen embedding identity is required")
        sources = retrieval["sources"]
        _require(isinstance(sources, list) and len(sources) <= 1000, "retrieval.sources must contain at most 1000 approved source/hash pairs")
        seen = set()
        for source in sources:
            _require(isinstance(source, dict) and set(source) == {"source_id", "file_sha256"}
                     and all(isinstance(v, str) and bool(_SHA.fullmatch(v)) for v in source.values()),
                     "Each retrieval source requires exact source_id and file_sha256")
            pair = (source["source_id"], source["file_sha256"])
            _require(pair not in seen, "Duplicate retrieval source/hash pair")
            seen.add(pair)
    return {**record, "manifest_sha256": digest}


def current_policy() -> dict | None:
    path, digest = os.environ.get(MANIFEST_ENV), os.environ.get(DIGEST_ENV)
    if path is None and digest is None:
        return None
    _require(bool(path) and bool(digest), "Study policy requires both manifest path and frozen SHA-256")
    # Re-read at every boundary: a warm content cache must not bypass a policy
    # change or a modified manifest. Files are small and study-only.
    return load_manifest(path, digest)


def approved_file_bytes(path: str | Path, *, policy: dict, root: Path | None = None) -> bytes | None:
    root = Path(REPO if root is None else root).resolve()
    path = Path(path)
    resolved = path.resolve()
    _require(resolved.is_relative_to(root), "Study knowledge file escapes the repository root")
    # Resolve symlinks before lookup; an alias cannot authorize different bytes.
    relative = resolved.relative_to(root).as_posix()
    expected = policy["files"].get(relative)
    if expected is None:
        return None
    raw = resolved.read_bytes()
    _require(hashlib.sha256(raw).hexdigest() == expected, "Approved knowledge file changed after manifest review")
    return raw


def check_knowledge_file(path: str | Path) -> bool:
    policy = current_policy()
    return policy is None or approved_file_bytes(path, policy=policy) is not None


def read_knowledge_text(path: str | Path) -> str:
    policy = current_policy()
    if policy is None:
        return Path(path).read_text(encoding="utf-8")
    raw = approved_file_bytes(path, policy=policy)
    return raw.decode("utf-8") if raw is not None else ""


def retrieval_scope() -> dict | None:
    policy = current_policy()
    if policy is None:
        return None
    retrieval = policy.get("retrieval")
    _require(retrieval is not None and bool(retrieval["sources"]), "Retrieval is not approved by this study manifest")
    clauses = [{"$and": [{"source_id": s["source_id"]}, {"file_hash": s["file_sha256"]}]} for s in retrieval["sources"]]
    return {**retrieval, "where": clauses[0] if len(clauses) == 1 else {"$or": clauses},
            "manifest_sha256": policy["manifest_sha256"]}


def verify_retrieval_provenance(provenance: dict) -> None:
    scope = retrieval_scope()
    if scope is None:
        return
    _require(provenance.get("verified_generation") is True and provenance.get("embedding_verified") is True,
             "Study retrieval requires a verified generation and embedding identity")
    _require(all(provenance.get(k) == scope[k] for k in ("collection_name", "corpus_revision", "embedding")),
             "Active retrieval generation differs from the frozen study manifest")


def verify_retrieval_hit(document: str, metadata: dict) -> None:
    scope = retrieval_scope()
    if scope is None:
        return
    pair = (metadata.get("source_id"), metadata.get("file_hash"))
    approved = {(s["source_id"], s["file_sha256"]) for s in scope["sources"]}
    _require(pair in approved, "Retrieval returned an unapproved study source")
    _require(isinstance(document, str) and hashlib.sha256(document.encode()).hexdigest() == metadata.get("content_sha256"),
             "Retrieved content does not match its indexed SHA-256")
