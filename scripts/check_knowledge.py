#!/usr/bin/env python3
"""Check native retrieval, pinned index identity and source freshness.

Run after deployment/indexing and before scientific sessions that need retrieval.
This calls the existing retrieval service, never an LLM or an automatic rebuild.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

QUERIES = [
    ("calibrate-integrate", "How should detector calibration be verified against the raw powder rings?"),
    ("ff-hedm", "How should RingThresh and spot quality be selected for FF-HEDM reconstruction?"),
    ("nf-hedm", "Which geometry and frame metadata checks precede NF-HEDM reconstruction?"),
]


def weight_alias_issues():
    """Nomic's pinned upstream code resolves main again for its weight file."""
    if os.environ.get('APEXA_EMBED_MODEL', 'nomic-ai/nomic-embed-text-v1.5') != 'nomic-ai/nomic-embed-text-v1.5':
        return []
    revision = os.environ.get('APEXA_EMBED_REVISION', '')
    hub = Path(os.environ.get('HF_HUB_CACHE',
        str(Path(os.environ.get('HF_HOME', str(Path.home()/'.cache/huggingface'))) / 'hub')))
    alias = hub/'models--nomic-ai--nomic-embed-text-v1.5/refs/main'
    if not revision or not alias.is_file() or alias.read_text().strip() != revision:
        return ['Nomic weight-cache alias must equal the explicit APEXA_EMBED_REVISION before loading']
    return []


def check():
    record = {"checked_unix": time.time(), "model_generations": 0,
              "queries": [], "issues": [], "passed": False}
    record['issues'].extend(weight_alias_issues())
    if record['issues']:
        return record
    from apexa_knowledge_service import query_knowledge
    from knowledge_base.index_knowledge import _source_snapshot
    pointer = REPO / "knowledge_base/data/hedm_knowledge-active.json"
    if not pointer.is_file():
        record["issues"].append("No verified active-generation record; rebuild under pinned embedding settings")
    else:
        active = json.loads(pointer.read_text())
        record["active_generation"] = active
        actual = hashlib.sha256(json.dumps(_source_snapshot(REPO / "knowledge_base"), sort_keys=True).encode()).hexdigest()
        record["source_manifest_sha256"] = actual
        if actual != active.get("stats", {}).get("source_manifest_sha256"):
            record["issues"].append("Source documents changed or moved since indexing; rebuild before relying on retrieval")
    for technique, question in QUERIES:
        started = time.monotonic()
        response = json.loads(query_knowledge(question, max_results=3, technique=technique))
        provenance = response.get("retrieval", {}).get("provenance", {})
        hits = response.get("excerpts", [])
        valid_hits = bool(hits) and all(
            hit.get("technique") == technique
            and hashlib.sha256(hit.get("excerpt", "").encode()).hexdigest() == hit.get("content_sha256")
            for hit in hits)
        verified = (provenance.get("verified_generation") is True
                    and provenance.get("embedding_verified") is True)
        result = {"technique": technique, "question": question,
                  "elapsed_seconds": round(time.monotonic()-started, 3),
                  "status": response.get("status"), "hits": hits,
                  "provenance_verified": verified, "hit_integrity_verified": valid_hits,
                  "error": response.get("details", response.get("error"))}
        record["queries"].append(result)
        if response.get("status") != "success" or not valid_hits or not verified:
            record["issues"].append(f"{technique}: retrieval unavailable, empty or unverified")
    record["passed"] = not record["issues"]
    return record


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", type=Path, help="Write a new JSON receipt; never overwrite an older check")
    args = ap.parse_args()
    try:
        result = check()
    except Exception as exc:
        result = {"passed": False, "model_generations": 0, "error": f"{type(exc).__name__}: {exc}"}
    text = json.dumps(result, indent=2)
    if args.output:
        with args.output.open("x") as stream:
            stream.write(text + "\n")
    print(text)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
