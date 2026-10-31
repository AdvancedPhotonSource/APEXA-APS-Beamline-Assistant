"""Canonical retrieval service shared by the MCP wrapper and local tools.

Loading and retrieval run outside the async MCP loop. Public tool schemas stay
in the server; embedding/index provenance and filtering live here.
"""
import os
import json
import logging
import traceback
import threading
from pathlib import Path
from typing import Optional

_knowledge_base = None
_load_lock = threading.Lock()

def _apply_offline_hf_env() -> bool:
    """Force HuggingFace fully offline when APEXA offline mode is requested, so the
    embedder loads ONLY from the local cache and never reaches the network.

    This is the hard requirement for air-gapped beamline machines: the embed model
    must already be pre-staged in ~/.cache/huggingface, and with these flags set a
    missing-network machine loads it from cache instead of hanging/failing on an HF
    HEAD request. Enable with APEXA_OFFLINE=1 (or set HF_HUB_OFFLINE=1 directly).
    Idempotent; safe to call before every model load. Returns True if offline."""
    # Network tier is authoritative: anything below `web` must never reach HF.
    # APEXA_OFFLINE stays supported as the legacy switch.
    try:
        from apexa_network import apply_offline_env
        if apply_offline_env():
            return True
    except Exception:
        pass
    truthy = ("1", "true", "yes", "on")
    if (os.environ.get("APEXA_OFFLINE", "").lower() in truthy
            or os.environ.get("HF_HUB_OFFLINE", "").lower() in truthy):
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        return True
    return False


def _initialize_knowledge_base():
    """Get or initialize the knowledge base (RAG with ChromaDB)"""
    global _knowledge_base
    if _knowledge_base is None:
        try:
            offline = _apply_offline_hf_env()  # must run before SentenceTransformer import
            # No third-party telemetry from a beamline host, at any tier, and no
            # model-loading INFO spam in the operator's terminal. Both must be set
            # BEFORE the imports below.
            os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
            os.environ.setdefault("CHROMA_TELEMETRY_ENABLED", "False")
            # HuggingFace tokenizers spawns worker threads on first use. Every
            # MIDAS tool then fork()s via subprocess.run, and forking a process
            # that already has tokenizer threads risks a DEADLOCK — the library
            # warns and silently disables parallelism when it detects this. In
            # APEXA that ordering is guaranteed: a RAG query warms the embedder,
            # then a calibration/integration forks. Set it explicitly BEFORE the
            # tokenizer is ever created so behaviour is deterministic rather than
            # rescued after the fact.
            os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
            for _noisy in ("sentence_transformers", "chromadb", "chromadb.telemetry",
                           "transformers", "posthog", "httpx"):
                logging.getLogger(_noisy).setLevel(logging.WARNING)
            import chromadb
            from sentence_transformers import SentenceTransformer
            from pathlib import Path

            kb_path = Path(__file__).parent / "knowledge_base"
            chroma_path = kb_path / "chroma_db"

            if chroma_path.exists():
                model_name = os.environ.get("APEXA_EMBED_MODEL",
                                            "nomic-ai/nomic-embed-text-v1.5")
                # Provenance policy. This is the LIVE retrieval path and builds
                # its own embedder, so guarding only the indexer would leave it
                # open to whatever APEXA_EMBED_MODEL names.
                from apexa_model_policy import assert_allowed as _assert_model_ok
                _assert_model_ok(model_name, role="embedder")
                from knowledge_base.lifecycle import load_embedder
                embedder, embedding = load_embedder(SentenceTransformer, model_name, offline=offline)
                _knowledge_base = {
                    "client": chromadb.PersistentClient(path=str(chroma_path)),
                    "embedder": embedder,
                    "model_name": model_name,
                    "embedding_identity": embedding,
                    "kb_path": kb_path,
                    "offline": offline,
                    "available": True
                }
            else:
                _knowledge_base = {"available": False, "error": "Knowledge base not indexed"}
        except Exception as e:
            _knowledge_base = {"available": False, "error": str(e)}

    return _knowledge_base



def get_knowledge_base():
    with _load_lock:
        return _initialize_knowledge_base()


def query_knowledge(
    question: str,
    max_results: int = 3,
    source_type: Optional[str] = None,
    technique: Optional[str] = None,
    retrieval_mode: str = "dense"
) -> str:
    """Query the HEDM knowledge base using semantic search across papers, logbooks, books, and technique capsules.

    This tool searches through indexed research papers, experimental logbooks,
    crystallography textbooks, and vendored MIDAS technique capsules to answer
    questions about HEDM theory, best practices, and past experiments.

    Args:
        question: Natural language question about HEDM, crystallography, calibration, etc.
                 Examples:
                 - "What is a good calibration strain value?"
                 - "How was sample XYZ processed in 2019?"
                 - "Explain Bragg's law for 61keV beam"
        max_results: Number of relevant excerpts to return (default: 3)
        source_type: Filter by document type: "paper", "logbook", "book", "capsule",
                 or None for all
        technique: Filter to a single MIDAS technique capsule id (e.g. "ff-hedm",
                 "nf-hedm", "pf-hedm", "dfxm", "tomo", "xrd-ct", "dct-tt",
                 "calibrate-integrate"), or None for all techniques. Composes with
                 source_type. Only capsule chunks carry a technique tag; non-capsule
                 chunks are tagged "" at index time, so a technique filter narrows to
                 that technique's capsule content.
        retrieval_mode: "dense" (default, semantic embedding search — unchanged
                 behaviour) or "hybrid" (fuse dense with sparse BM25 keyword search
                 via Reciprocal Rank Fusion; better for exact tokens like flag/param
                 names). Hybrid needs no network or model beyond what dense uses, and
                 falls back to dense on any error. The technique/source_type filters
                 apply to both retrievers.

    Returns:
        JSON string with relevant excerpts, sources, and confidence scores
    """
    try:
        from apexa_knowledge_policy import (retrieval_scope, verify_retrieval_provenance,
                                            verify_retrieval_hit)
        study_scope = retrieval_scope()  # Refuse unapproved retrieval before model loading.
        if type(max_results) is not int or not 1 <= max_results <= 50:
            raise ValueError("max_results must be 1..50")
        kb = get_knowledge_base()

        if not kb.get("available"):
            return json.dumps({
                "status": "error",
                "error": "Knowledge base not available",
                "details": kb.get("error", "Not indexed"),
                "suggestion": "Run: python knowledge_base/index_knowledge.py"
            }, indent=2)

        # Encode the question. Some embedders (Nomic) require a task prefix;
        # most others don't. Keep this in sync with EMBED_PREFIXES in
        # knowledge_base/index_knowledge.py.
        _query_prefix_map = {
            "nomic-ai/nomic-embed-text-v1.5": "search_query: ",
            "nomic-ai/nomic-embed-text-v1":   "search_query: ",
        }
        q_pfx = _query_prefix_map.get(kb.get("model_name", ""), "")

        # Get collection
        from knowledge_base.lifecycle import active_collection
        collection, provenance = active_collection(kb["client"],
            kb.get("kb_path", Path(__file__).parent / "knowledge_base"),
            embedding=kb.get("embedding_identity"))
        verify_retrieval_provenance(provenance)
        query_embedding = kb["embedder"].encode(f"{q_pfx}{question}").tolist()

        # Build filter. source_type and technique compose; ChromaDB requires an
        # explicit $and to combine two equality clauses. Neither set → None (the
        # exact contract that shipped before technique existed — additive proof).
        _clauses = []
        if source_type:
            _clauses.append({"type": source_type})
        if technique:
            _clauses.append({"technique": technique})
        if study_scope is not None:
            _clauses.append(study_scope['where'])
        if len(_clauses) >= 2:
            where_filter = {"$and": _clauses}
        elif len(_clauses) == 1:
            where_filter = _clauses[0]
        else:
            where_filter = None

        # Retrieve. "dense" is the original path, preserved byte-for-byte so the
        # default contract is unchanged. "hybrid" fuses dense + sparse BM25 and
        # falls back to dense on any error (fail-open).
        _mode = (retrieval_mode or "dense").strip().lower()
        retrieval_meta = {"mode": "dense"}
        hits = None  # list of (doc, meta, dist_or_None, extra_dict)

        if _mode == "hybrid":
            try:
                from knowledge_base import hybrid_retrieval as _hybrid
                fused = _hybrid.hybrid_search(
                    collection=collection,
                    query_embedding=query_embedding,
                    query_text=question,
                    n_results=max_results,
                    where_filter=where_filter,
                )
                hits = [
                    (r["document"], r["metadata"], r["distance"], {
                        "dense_rank": r["dense_rank"],
                        "sparse_rank": r["sparse_rank"],
                        "rrf": round(r["rrf"], 6),
                    })
                    for r in fused
                ]
                retrieval_meta = {"mode": "hybrid", "rrf_k": _hybrid.RRF_K}
            except Exception as _he:
                # Fail-open to dense; record why so a beamline operator can see it.
                hits = None
                retrieval_meta = {"mode": "dense", "requested_mode": "hybrid",
                                  "hybrid_fallback_reason": str(_he)}

        if hits is None:
            # Dense path (default, and hybrid fallback) — unchanged query.
            results = collection.query(
                query_embeddings=[query_embedding],
                n_results=max_results,
                where=where_filter
            )
            hits = [
                (doc, meta, dist, {})
                for doc, meta, dist in zip(
                    results['documents'][0],
                    results['metadatas'][0],
                    results['distances'][0],
                )
            ]

        # Format results — include citation, page, DOI for proper referencing
        excerpts = []
        for i, (doc, meta, dist, extra) in enumerate(hits, 1):
            verify_retrieval_hit(doc, meta)
            ex = {
                "rank": i,
                "source": meta['source'],
                "type": meta['type'],
                "technique": meta.get('technique', ''),
                "citation": meta.get('citation', meta['source']),
                "bibkey": meta.get('bibkey', ''),
                "title": meta.get('title', ''),
                "authors": meta.get('authors', ''),
                "year": meta.get('year', ''),
                "journal": meta.get('journal', ''),
                "doi": meta.get('doi', ''),
                "page": meta.get('page', 0),
                # Sparse-only hybrid hits have no embedding distance → null, never
                # a fabricated score.
                "similarity": (round(max(0.0, 1 - dist), 3) if dist is not None else None),
                "excerpt": doc,
                "chunk_index": meta.get('chunk_index', 0),
                "source_id": meta.get("source_id"),
                "content_sha256": meta.get("content_sha256"),
                "knowledge_role": meta.get("knowledge_role", "unclassified"),
                "trust_level": meta.get("trust_level", "legacy_unverified"),
            }
            ex.update(extra)
            excerpts.append(ex)

        # Compact reference list (deduped by source) for the LLM to cite cleanly
        seen = set()
        references = []
        for ex in excerpts:
            if ex['source'] in seen:
                continue
            seen.add(ex['source'])
            references.append({
                "source": ex['source'],
                "citation": ex['citation'],
                "bibkey": ex['bibkey'],
                "doi": ex['doi'],
            })

        return json.dumps({
            "status": "success",
            "question": question,
            "results_count": len(excerpts),
            "retrieval": {**retrieval_meta, "provenance": provenance,
                "study_manifest_sha256": study_scope['manifest_sha256'] if study_scope else None},
            "references": references,
            "excerpts": excerpts,
            "instruction_to_assistant": (
                "When answering, cite each excerpt inline using the 'citation' field "
                "(e.g. 'Sharma et al. (2012). J. Appl. Cryst. 45:693–704. DOI:...') and, "
                "when available, reference the page number. Use get_bibtex(source) to "
                "retrieve the BibTeX entry for any cited source."
            )
        }, indent=2)

    except Exception as e:
        return json.dumps({
            "status": "error",
            "error": str(e),
            "traceback": traceback.format_exc()
        }, indent=2)
