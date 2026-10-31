#!/usr/bin/env python3
"""
Hybrid dense + sparse retrieval over the APEXA ChromaDB knowledge base.

Dense (embedding) retrieval is strong on paraphrase and concept, weak on exact
tokens (a specific flag name, a filename, a parameter key). Sparse BM25 is the
mirror image. Fusing their rankings with Reciprocal Rank Fusion (RRF) recovers
both, and — crucially for a beamline host — the sparse side needs **no model and
no network**: it is built directly from the document texts already stored in
Chroma (``collection.get``), tokenized with a stdlib regex. BM25 itself is
vendored below (``BM25Okapi``, numpy-only — numpy is already a base dep), so this
module adds **no** pip dependency and keeps ``uv sync`` offline-clean even on an
air-gapped host that never cached a wheel. (It formerly imported ``rank-bm25``;
that package is pure-Python at runtime, but its wheel still has to be *fetched*
from PyPI at install time, which broke ``uv sync`` on copland. The inlined class
reproduces rank-bm25's Okapi formula — k1=1.5, b=0.75, epsilon=0.25 negative-idf
flooring — so retrieval scores are unchanged.)

Design contract:
  * FAIL-OPEN. Any failure (empty corpus, Chroma quirk) raises
    ``HybridUnavailable``; the caller falls back to the plain dense path, which is
    byte-for-byte the pre-existing behaviour.
  * The ``where`` filter (type / technique scoping) is applied to BOTH candidate
    sets *before* the merge, so hybrid composes cleanly with technique-scoped
    retrieval — it never widens the result set past the filter.
  * A sparse-only hit has no embedding distance, so its ``distance`` is ``None``
    (surfaced as ``similarity: null`` upstream) — never a fabricated 0.0.
"""
from __future__ import annotations

import math
import re
import json
import threading
from collections import OrderedDict
from typing import Dict, List, Optional

import numpy as np

# RRF constant. 60 is the value from the original Cormack et al. (2009) paper and
# the community default; large enough that the tail ranks still contribute, small
# enough that the head dominates. Ablation may revisit it.
RRF_K = 60

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")

# Only immutable published generations are cached. Legacy/mutable collections
# must be read each time; a stable count alone cannot detect replaced documents.
_SPARSE_CACHE = OrderedDict()
_CACHE_LOCK = threading.RLock()
_CACHE_BYTES = 64 * 1024 * 1024


def clear_sparse_cache():
    with _CACHE_LOCK:
        _SPARSE_CACHE.clear()


def _sparse_corpus(collection, where):
    metadata = getattr(collection, "metadata", None) or {}
    revision = metadata.get("corpus_revision") if metadata.get("apexa_immutable") else None
    key = (str(getattr(collection, "id", None) or getattr(collection, "name", "")),
           revision, json.dumps(where, sort_keys=True)) if revision else None
    with _CACHE_LOCK:
        if key is not None and key in _SPARSE_CACHE:
            _SPARSE_CACHE.move_to_end(key)
            return _SPARSE_CACHE[key][0]
        corpus = collection.get(where=where, include=["documents", "metadatas"])
        docs = corpus.get("documents") or []
        ids = corpus.get("ids") or []
        metas = corpus.get("metadatas") or []
        if not ids or len(ids) != len(docs) or len(ids) != len(metas):
            raise HybridUnavailable("empty or inconsistent corpus for the given filter")
        tokenized = [_tokenize(doc) for doc in docs]
        value = (corpus, BM25Okapi(tokenized))
        # Conservative accounting for retained text, metadata and Python tokens.
        weight = sum(len(doc.encode()) + len(json.dumps(meta).encode()) +
                     128 * len(tokens) + 512
                     for doc, meta, tokens in zip(docs, metas, tokenized))
        if key is not None and weight <= _CACHE_BYTES:
            while _SPARSE_CACHE and (len(_SPARSE_CACHE) >= 8 or
                    sum(item[1] for item in _SPARSE_CACHE.values()) + weight > _CACHE_BYTES):
                _SPARSE_CACHE.popitem(last=False)
            _SPARSE_CACHE[key] = (value, weight)
        return value


class HybridUnavailable(RuntimeError):
    """Raised when hybrid retrieval cannot run; caller falls back to dense."""


def _tokenize(text: str) -> List[str]:
    """Lowercase alphanumeric/underscore tokens. Keeps ``ff_midas`` and
    ``RingThresh`` retrievable as whole tokens (underscores kept, case folded)."""
    return [t.lower() for t in _TOKEN_RE.findall(text or "")]


class BM25Okapi:
    """Okapi BM25, vendored (numpy-only) to avoid a PyPI dependency.

    A faithful reimplementation of ``rank_bm25.BM25Okapi``: same defaults
    (k1=1.5, b=0.75, epsilon=0.25) and the same negative-IDF flooring
    (words whose IDF would go negative are floored to ``epsilon * average_idf``),
    so ``get_scores`` returns the same ranking as the former dependency. Kept
    in-tree because the wheel — though pure-Python — must still be fetched at
    ``uv sync`` time, which fails on an air-gapped beamline host.

    ``corpus`` is a list of already-tokenized documents (list[list[str]]).
    """

    def __init__(self, corpus: List[List[str]], k1: float = 1.5,
                 b: float = 0.75, epsilon: float = 0.25) -> None:
        self.k1, self.b, self.epsilon = k1, b, epsilon
        self.corpus_size = len(corpus)
        self.doc_len = [len(doc) for doc in corpus]
        self.avgdl = (sum(self.doc_len) / self.corpus_size) if self.corpus_size else 0.0
        self.doc_freqs: List[Dict[str, int]] = []
        nd: Dict[str, int] = {}          # word -> number of docs containing it
        for doc in corpus:
            freqs: Dict[str, int] = {}
            for w in doc:
                freqs[w] = freqs.get(w, 0) + 1
            self.doc_freqs.append(freqs)
            for w in freqs:
                nd[w] = nd.get(w, 0) + 1
        self.idf: Dict[str, float] = {}
        self._calc_idf(nd)
        postings = {}
        for index, freqs in enumerate(self.doc_freqs):
            for word, frequency in freqs.items():
                posting = postings.setdefault(word, ([], []))
                posting[0].append(index)
                posting[1].append(frequency)
        self.postings = {word: (np.asarray(indices, dtype=int), np.asarray(freqs, dtype=float))
                         for word, (indices, freqs) in postings.items()}
        self.lengths = np.asarray(self.doc_len, dtype=float)

    def _calc_idf(self, nd: Dict[str, int]) -> None:
        idf_sum = 0.0
        negatives: List[str] = []
        for word, freq in nd.items():
            idf = math.log(self.corpus_size - freq + 0.5) - math.log(freq + 0.5)
            self.idf[word] = idf
            idf_sum += idf
            if idf < 0:
                negatives.append(word)
        average_idf = (idf_sum / len(self.idf)) if self.idf else 0.0
        floor = self.epsilon * average_idf
        for word in negatives:
            self.idf[word] = floor

    def get_scores(self, query: List[str]) -> "np.ndarray":
        score = np.zeros(self.corpus_size)
        if self.avgdl <= 0:
            return score
        for q in query:
            if q not in self.postings:
                continue
            indices, q_freq = self.postings[q]
            score[indices] += self.idf.get(q, 0.0) * (
                q_freq * (self.k1 + 1)
                / (q_freq + self.k1 * (1 - self.b + self.b * self.lengths[indices] / self.avgdl))
            )
        return score


def hybrid_search(
    collection,
    query_embedding: List[float],
    query_text: str,
    n_results: int = 3,
    where_filter: Optional[dict] = None,
    k: int = RRF_K,
    candidate_pool: Optional[int] = None,
) -> List[Dict]:
    """Return up to ``n_results`` fused hits, most-relevant first.

    Each hit: ``{"document", "metadata", "distance"|None, "dense_rank"|None,
    "sparse_rank"|None, "rrf"}``. ``distance`` is the dense cosine distance when
    the doc was a dense hit, else ``None`` (sparse-only).

    Raises ``HybridUnavailable`` on any condition that should trigger the dense
    fallback (empty corpus, Chroma error).
    """
    if n_results < 1:
        return []

    # Pull the sparse corpus: every doc matching the same filter as the dense
    # side. No embedder, no network — just the stored text.
    try:
        corpus, bm25 = _sparse_corpus(collection, where_filter)
    except Exception as e:
        raise HybridUnavailable(f"collection.get failed: {e}") from e

    corpus_ids = corpus.get("ids") or []
    corpus_docs = corpus.get("documents") or []
    corpus_metas = corpus.get("metadatas") or []
    if not corpus_ids or not corpus_docs:
        raise HybridUnavailable("empty corpus for the given filter")

    # Candidate pool from the dense side: over-fetch so the fusion has room to
    # re-rank. Bounded by the corpus size.
    pool = candidate_pool or max(n_results * 10, 50)
    pool = min(pool, len(corpus_ids))

    try:
        dense = collection.query(
            query_embeddings=[query_embedding],
            n_results=pool,
            where=where_filter,
        )
    except Exception as e:
        raise HybridUnavailable(f"dense query failed: {e}") from e

    dense_ids = (dense.get("ids") or [[]])[0]
    dense_docs = (dense.get("documents") or [[]])[0]
    dense_metas = (dense.get("metadatas") or [[]])[0]
    dense_dists = (dense.get("distances") or [[]])[0]

    # id -> unified record. Seed from the corpus so sparse-only hits have text.
    records: Dict[str, Dict] = {}
    for cid, doc, meta in zip(corpus_ids, corpus_docs, corpus_metas):
        records[cid] = {
            "id": cid, "document": doc, "metadata": meta,
            "distance": None, "dense_rank": None, "sparse_rank": None,
        }

    # Dense ranks (1-indexed).
    for rank, (cid, doc, meta, dist) in enumerate(
        zip(dense_ids, dense_docs, dense_metas, dense_dists), start=1
    ):
        rec = records.get(cid)
        if rec is None:
            # Do not widen a filtered corpus if a mutable collection changes
            # between the two reads or a backend returns out-of-filter data.
            raise HybridUnavailable("dense/sparse corpus changed during retrieval")
        rec["dense_rank"] = rank
        rec["distance"] = dist

    # Sparse ranks (1-indexed) over the whole filtered corpus.
    scores = bm25.get_scores(_tokenize(query_text))
    order = sorted(range(len(corpus_ids)), key=lambda i: scores[i], reverse=True)
    for rank, idx in enumerate(order[:pool], start=1):
        # Only count as a sparse hit if the score is positive (query shares a
        # token with the doc); a 0 score is not evidence and would just add noise.
        if scores[idx] <= 0:
            continue
        records[corpus_ids[idx]]["sparse_rank"] = rank

    # RRF fuse.
    fused = []
    for rec in records.values():
        if rec["dense_rank"] is None and rec["sparse_rank"] is None:
            continue
        rrf = 0.0
        if rec["dense_rank"] is not None:
            rrf += 1.0 / (k + rec["dense_rank"])
        if rec["sparse_rank"] is not None:
            rrf += 1.0 / (k + rec["sparse_rank"])
        rec["rrf"] = rrf
        fused.append(rec)

    if not fused:
        raise HybridUnavailable("no candidates from either retriever")

    # Sort by RRF desc; tie-break by dense rank (embedding relevance) then id for
    # determinism.
    fused.sort(key=lambda r: (
        -r["rrf"],
        r["dense_rank"] if r["dense_rank"] is not None else 1e9,
        r["id"],
    ))
    return fused[:n_results]
