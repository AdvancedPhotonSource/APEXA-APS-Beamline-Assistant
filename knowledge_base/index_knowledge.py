#!/usr/bin/env python3
"""
Index knowledge base documents (PDFs, logbooks, books) into ChromaDB with
citation-aware metadata.

For each PDF in knowledge_base/papers/:
  1. If a sibling .bib file exists (e.g. Foo2024.pdf -> Foo2024.bib), parse it
     and use authors/year/journal/doi as chunk metadata.
  2. Otherwise, fall back to PyPDF2 metadata + first-page DOI regex.

Chunks are produced per-page (not per-flat-word-stream) so the query tool can
report a page number alongside the citation.

Usage:
    uv run python knowledge_base/index_knowledge.py
"""

import json
import re
import hashlib
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import sys as _sys
_repo_root = str(Path(__file__).resolve().parent.parent)
if _repo_root not in _sys.path:
    _sys.path.insert(0, _repo_root)

import os as _os

# ChromaDB ships anonymised telemetry to PostHog — an outbound call to a third
# party from a (possibly air-gapped) beamline host. Disable it at any tier, and
# BEFORE importing chromadb (it reads these at import), matching the runtime
# server path (get_knowledge_base in midas_comprehensive_server.py).
_os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
_os.environ.setdefault("CHROMA_TELEMETRY_ENABLED", "False")

try:
    import chromadb
except ImportError:
    chromadb = None

try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    SentenceTransformer = None

# Override via env: APEXA_EMBED_MODEL=nvidia/llama-embed-nemotron-8b uv run ...
EMBED_MODEL = _os.environ.get("APEXA_EMBED_MODEL", "nomic-ai/nomic-embed-text-v1.5")


def _apply_offline_hf_env() -> bool:
    """Force HuggingFace offline when APEXA offline mode is requested, so the embed
    model loads only from the local cache — needed to re-index on an air-gapped
    beamline machine where the model is pre-staged but there's no network. Enable
    with APEXA_OFFLINE=1 (or HF_HUB_OFFLINE=1). Returns True if offline."""
    truthy = ("1", "true", "yes", "on")
    if (_os.environ.get("APEXA_OFFLINE", "").lower() in truthy
            or _os.environ.get("HF_HUB_OFFLINE", "").lower() in truthy):
        _os.environ["HF_HUB_OFFLINE"] = "1"
        _os.environ["TRANSFORMERS_OFFLINE"] = "1"
        return True
    return False


def _load_embedder():
    """Construct the SentenceTransformer, honoring APEXA offline mode."""
    # Provenance policy: APEXA_EMBED_MODEL can name any HuggingFace id, so the
    # check belongs where the model is actually built, not in the prefix table.
    try:
        from apexa_model_policy import assert_allowed
    except ImportError:                       # run as a script from knowledge_base/
        import sys as _s, os as _o
        _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
        from apexa_model_policy import assert_allowed
    assert_allowed(EMBED_MODEL, role="embedder")
    offline = _apply_offline_hf_env()
    from knowledge_base.lifecycle import load_embedder
    return load_embedder(SentenceTransformer, EMBED_MODEL, offline=offline)[0]

# Per-model task prefixes. Nomic requires explicit task tags; most other models
# (Nemotron, E5 in symmetric mode) don't. When in doubt, add an entry here.
# Chinese-origin embedders (e.g. BAAI's BGE family) are excluded by policy --
# see apexa_model_policy.py; the BGE-M3 row that used to sit here was removed.
EMBED_PREFIXES = {
    "nomic-ai/nomic-embed-text-v1.5": ("search_document: ", "search_query: "),
    "nomic-ai/nomic-embed-text-v1":   ("search_document: ", "search_query: "),
    # No prefix for these (default behavior)
    "nvidia/llama-embed-nemotron-8b": ("", ""),
}


def _prefixes() -> tuple[str, str]:
    return EMBED_PREFIXES.get(EMBED_MODEL, ("", ""))


def embed_doc(embedder, text: str) -> list[float]:
    pfx, _ = _prefixes()
    return embedder.encode(f"{pfx}{text}").tolist()


def embed_query(embedder, text: str) -> list[float]:
    _, pfx = _prefixes()
    return embedder.encode(f"{pfx}{text}").tolist()

try:
    import PyPDF2
except ImportError:
    PyPDF2 = None


# ---------------------------------------------------------------------------
# .bib parsing — minimal, single-entry BibTeX
# ---------------------------------------------------------------------------

_BIB_FIELD_RE = re.compile(r"(\w+)\s*=\s*[{\"]([^{}\"]*)[}\"]\s*,?", re.MULTILINE)
_BIB_ENTRY_RE = re.compile(r"@(\w+)\s*\{\s*([^,\s]+)\s*,(.*)\}", re.DOTALL)


def parse_bib(bib_path: Path) -> Optional[Dict[str, str]]:
    """Parse a single-entry .bib file. Returns dict of fields or None on error."""
    try:
        text = bib_path.read_text(encoding="utf-8", errors="ignore")
        entry = _BIB_ENTRY_RE.search(text)
        if not entry:
            return None
        entry_type, bibkey, body = entry.groups()
        fields = {
            "entry_type": entry_type.lower(),
            "bibkey": bibkey.strip(),
        }
        for k, v in _BIB_FIELD_RE.findall(body):
            fields[k.lower()] = " ".join(v.split())
        return fields
    except Exception as e:
        print(f"      ⚠️  Failed to parse {bib_path.name}: {e}")
        return None


def short_authors(authors: str) -> str:
    """'Sharma, H. and Huizenga, R. and Offerman, S.' -> 'Sharma et al.'"""
    if not authors:
        return ""
    parts = [a.strip() for a in re.split(r"\s+and\s+", authors) if a.strip()]
    if not parts:
        return ""
    first_last = parts[0].split(",")[0].strip()
    if len(parts) == 1:
        return first_last
    if len(parts) == 2:
        second_last = parts[1].split(",")[0].strip()
        return f"{first_last} & {second_last}"
    return f"{first_last} et al."


def format_citation(meta: Dict[str, str]) -> str:
    """Render a one-line citation from .bib-style fields."""
    sa = short_authors(meta.get("author", ""))
    head = ""
    if sa and meta.get("year"):
        head = f"{sa} ({meta['year']})"
    elif sa:
        head = sa
    elif meta.get("year"):
        head = f"({meta['year']})"

    journal = ""
    if meta.get("journal"):
        journal = meta["journal"]
        if meta.get("volume"):
            journal += f" {meta['volume']}"
        if meta.get("pages"):
            journal += f":{meta['pages']}"

    parts = [p for p in (head, journal) if p]
    cite = ". ".join(parts)
    if meta.get("doi"):
        cite += f". DOI:{meta['doi']}"
    return cite or meta.get("source", "")


# ---------------------------------------------------------------------------
# PDF metadata fallback
# ---------------------------------------------------------------------------

_DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


def fallback_metadata(reader: "PyPDF2.PdfReader") -> Dict[str, str]:
    """Best-effort citation fields when no .bib sidecar is present."""
    meta: Dict[str, str] = {}
    pdf_meta = reader.metadata or {}
    if pdf_meta.get("/Title"):
        meta["title"] = str(pdf_meta["/Title"]).strip()
    if pdf_meta.get("/Author"):
        meta["author"] = str(pdf_meta["/Author"]).strip()
    if pdf_meta.get("/DOI"):
        meta["doi"] = str(pdf_meta["/DOI"]).strip()

    try:
        first_page_text = reader.pages[0].extract_text() or ""
    except Exception:
        first_page_text = ""

    if "doi" not in meta:
        m = _DOI_RE.search(first_page_text)
        if m:
            meta["doi"] = m.group(0).rstrip(".,;)")
    if "year" not in meta:
        m = _YEAR_RE.search(first_page_text)
        if m:
            meta["year"] = m.group(0)
    return meta


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def chunk_page_text(text: str, max_words: int = 500, overlap: int = 50) -> List[str]:
    """Split a single page into one or more chunks if it exceeds max_words."""
    words = text.split()
    if not words:
        return []
    if len(words) <= max_words:
        return [" ".join(words)]
    chunks: List[str] = []
    step = max(1, max_words - overlap)
    for i in range(0, len(words), step):
        chunk = " ".join(words[i:i + max_words])
        if chunk.strip():
            chunks.append(chunk)
    return chunks


def chunk_flat_text(text: str, chunk_size: int = 500, overlap: int = 50) -> List[str]:
    """Flat-stream chunking for non-paginated docs (logbooks)."""
    words = text.split()
    chunks = []
    step = max(1, chunk_size - overlap)
    for i in range(0, len(words), step):
        chunk = " ".join(words[i:i + chunk_size])
        if chunk.strip():
            chunks.append(chunk)
    return chunks


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(4096), b""):
            h.update(block)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Indexing
# ---------------------------------------------------------------------------

def _citation_metadata(pdf_path: Path, reader: "PyPDF2.PdfReader") -> Dict[str, str]:
    """Return per-paper citation fields, preferring .bib sidecar."""
    bib_path = pdf_path.with_suffix(".bib")
    if bib_path.exists():
        parsed = parse_bib(bib_path)
        if parsed:
            return parsed
        print(f"      ⚠️  .bib present but unparseable, using PDF fallback")
    return fallback_metadata(reader)


def index_paper(
    pdf_path: Path,
    collection,
    embedder,
) -> Tuple[int, Dict[str, str]]:
    """Index one paper, returning (chunk_count, citation_metadata)."""
    with open(pdf_path, "rb") as f:
        reader = PyPDF2.PdfReader(f)
        cite = _citation_metadata(pdf_path, reader)
        citation_str = format_citation({**cite, "source": pdf_path.name})
        h = file_hash(pdf_path)

        # Title-aware embedding: prepend paper title (and topics, when present)
        # to every chunk before encoding. This anchors retrieval to the paper's
        # subject rather than the immediate paragraph wording. The raw chunk is
        # still stored as the document so excerpts read naturally.
        title = cite.get("title", "")
        topics = cite.get("topics", "")
        embed_prefix = title
        if topics:
            embed_prefix = f"{title}. Topics: {topics}" if title else f"Topics: {topics}"

        total = 0
        for page_idx, page in enumerate(reader.pages, start=1):
            # Extraction failures abort the staged build; publishing a partial
            # replacement as complete would silently remove searchable evidence.
            page_text = page.extract_text() or ""
            for sub_idx, chunk in enumerate(chunk_page_text(page_text)):
                meta = {
                    "source": pdf_path.name,
                    "type": "paper",
                    "page": page_idx,
                    "chunk_index": sub_idx,
                    "file_hash": h,
                    "citation": citation_str,
                    "bibkey": cite.get("bibkey", ""),
                    "title": title,
                    "authors": cite.get("author", ""),
                    "year": cite.get("year", ""),
                    "journal": cite.get("journal", ""),
                    "doi": cite.get("doi", ""),
                    "topics": topics,
                    # Non-capsule chunks carry an empty technique tag so a
                    # technique-scoped query narrows to capsule content, while a
                    # no-technique query (the default) still sees everything.
                    "technique": "",
                }
                meta = {k: ("" if v is None else v) for k, v in meta.items()}
                embed_text = f"{embed_prefix}\n\n{chunk}" if embed_prefix else chunk
                embedding = embed_doc(embedder, embed_text)
                collection.add(
                    documents=[chunk],
                    embeddings=[embedding],
                    metadatas=[meta],
                    ids=[f"{pdf_path.stem}_p{page_idx}_c{sub_idx}"],
                )
                total += 1
        return total, cite


def index_logbook(txt_path: Path, collection, embedder) -> int:
    text = txt_path.read_text(encoding="utf-8", errors="ignore")
    chunks = chunk_flat_text(text, chunk_size=300)
    h = file_hash(txt_path)
    for i, chunk in enumerate(chunks):
        embedding = embed_doc(embedder, chunk)
        collection.add(
            documents=[chunk],
            embeddings=[embedding],
            metadatas=[{
                "source": txt_path.name,
                "type": "logbook",
                "page": 0,
                "chunk_index": i,
                "file_hash": h,
                "citation": f"Logbook: {txt_path.name}",
                "bibkey": "",
                "title": txt_path.stem,
                "authors": "",
                "year": "",
                "journal": "",
                "doi": "",
                "topics": "",
                "technique": "",
            }],
            ids=[f"{txt_path.stem}_chunk_{i}"],
        )
    return len(chunks)


def index_book(pdf_path: Path, collection, embedder) -> int:
    with open(pdf_path, "rb") as f:
        reader = PyPDF2.PdfReader(f)
        cite = _citation_metadata(pdf_path, reader)
        citation_str = format_citation({**cite, "source": pdf_path.name})
        h = file_hash(pdf_path)
        title = cite.get("title", "")
        topics = cite.get("topics", "")
        embed_prefix = title
        if topics:
            embed_prefix = f"{title}. Topics: {topics}" if title else f"Topics: {topics}"
        total = 0
        for page_idx, page in enumerate(reader.pages, start=1):
            # Extraction failures abort the staged build; publishing a partial
            # replacement as complete would silently remove searchable evidence.
            page_text = page.extract_text() or ""
            for sub_idx, chunk in enumerate(chunk_page_text(page_text, max_words=700)):
                embed_text = f"{embed_prefix}\n\n{chunk}" if embed_prefix else chunk
                embedding = embed_doc(embedder, embed_text)
                collection.add(
                    documents=[chunk],
                    embeddings=[embedding],
                    metadatas=[{
                        "source": pdf_path.name,
                        "type": "book",
                        "page": page_idx,
                        "chunk_index": sub_idx,
                        "file_hash": h,
                        "citation": citation_str,
                        "bibkey": cite.get("bibkey", ""),
                        "title": cite.get("title", ""),
                        "authors": cite.get("author", ""),
                        "year": cite.get("year", ""),
                        "journal": cite.get("journal", ""),
                        "doi": cite.get("doi", ""),
                        "topics": cite.get("topics", ""),
                        "technique": "",
                    }],
                    ids=[f"{pdf_path.stem}_p{page_idx}_c{sub_idx}"],
                )
                total += 1
    return total


# Capsule files worth indexing, in a stable order. RUNBOOK.md is deliberately
# EXCLUDED — it is volatile host/version state the docs insist is checked live,
# not retrievable "knowledge" (mirrors capsule_registry's stance).
_CAPSULE_FILES = (
    "README.md", "ENVELOPE.md", "PARAMETERS.md", "PF_PARAMETERS.md",
    "DIAGNOSIS.md", "LAB_NOTEBOOK.md",
)
_CAPSULE_PHASE_GLOB = "phase-*.md"


def index_capsule(technique: str, cap_dir: Path, collection, embedder) -> int:
    """Index one vendored MIDAS technique capsule directory.

    Walks the capsule's markdown docs (README/ENVELOPE/PARAMETERS/DIAGNOSIS/
    LAB_NOTEBOOK + phase-*.md), flat-chunks each, and tags every chunk
    type="capsule", technique=<id>. This is what lets query_hedm_knowledge
    scope retrieval to a single technique's methodology. Capsules are local
    .md files, so indexing them is offline-clean.
    """
    files: List[Path] = []
    for fname in _CAPSULE_FILES:
        p = cap_dir / fname
        if p.is_file():
            files.append(p)
    files.extend(sorted(cap_dir.glob(_CAPSULE_PHASE_GLOB)))

    total = 0
    for md in files:
        text = md.read_text(encoding="utf-8", errors="ignore")
        h = file_hash(md)
        source = f"{technique}/{md.name}"
        for i, chunk in enumerate(chunk_flat_text(text, chunk_size=400)):
            embedding = embed_doc(embedder, chunk)
            collection.add(
                documents=[chunk],
                embeddings=[embedding],
                metadatas=[{
                    "source": source,
                    "type": "capsule",
                    "technique": technique,
                    "page": 0,
                    "chunk_index": i,
                    "file_hash": h,
                    "citation": f"MIDAS capsule {source}",
                    "bibkey": "",
                    "title": md.stem,
                    "authors": "",
                    "year": "",
                    "journal": "",
                    "doi": "",
                    "topics": "",
                }],
                ids=[f"capsule_{technique}_{md.stem}_c{i}"],
            )
            total += 1
    return total


def _populate_documents(kb_path: Path, collection, embedder):
    stats = {"papers": 0, "logbooks": 0, "books": 0, "capsules": 0,
             "total_chunks": 0, "papers_with_bib": 0}

    papers_dir = kb_path / "papers"
    if papers_dir.exists():
        print()
        print("\U0001f4c4 Indexing papers...")
        for pdf in sorted(papers_dir.glob("*.pdf")):
            print(f"   Processing: {pdf.name}")
            n, cite = index_paper(pdf, collection, embedder)
            if n <= 0:
                raise ValueError("A selected source produced no searchable text; active index retained")
            has_bib = pdf.with_suffix(".bib").exists()
            tag = " [.bib]" if has_bib else " [PDF metadata fallback]"
            print(f"      ✓ Indexed {n} chunks{tag}")
            print(f"      → {format_citation({**cite, 'source': pdf.name})}")
            stats["papers"] += 1
            stats["total_chunks"] += n
            if has_bib:
                stats["papers_with_bib"] += 1

    logbooks_dir = kb_path / "logbooks"
    if logbooks_dir.exists():
        print()
        print("\U0001f4d3 Indexing logbooks...")
        for txt in sorted(list(logbooks_dir.glob("*.txt")) + list(logbooks_dir.glob("*.md"))):
            print(f"   Processing: {txt.name}")
            n = index_logbook(txt, collection, embedder)
            if n <= 0:
                raise ValueError("A selected source produced no searchable text; active index retained")
            print(f"      ✓ Indexed {n} chunks")
            stats["logbooks"] += 1
            stats["total_chunks"] += n

    books_dir = kb_path / "books"
    if books_dir.exists():
        print()
        print("\U0001f4da Indexing books...")
        for book in sorted(books_dir.glob("*.pdf")):
            print(f"   Processing: {book.name}")
            n = index_book(book, collection, embedder)
            if n <= 0:
                raise ValueError("A selected source produced no searchable text; active index retained")
            print(f"      ✓ Indexed {n} chunks")
            stats["books"] += 1
            stats["total_chunks"] += n

    # Technique capsules — auto-discovered by capsule_registry (structural: any
    # dir with README.md + ENVELOPE.md markers). Iterating the registry (not a
    # glob here) keeps a single source of truth for "what is a capsule".
    try:
        import sys as _sys
        _repo_root = str(Path(__file__).resolve().parent.parent)
        if _repo_root not in _sys.path:
            _sys.path.insert(0, _repo_root)
        import capsule_registry as _capsule_registry
        _techniques = _capsule_registry.available_techniques()
        _capsules_dir = _capsule_registry.CAPSULES_DIR
    except Exception as _e:
        raise RuntimeError("Capsule discovery failed; active index retained") from _e

    if _techniques:
        print()
        print("\U0001f9e9 Indexing technique capsules...")
        for tech in _techniques:
            cap_dir = _capsules_dir / tech
            print(f"   Processing: {tech}")
            n = index_capsule(tech, cap_dir, collection, embedder)
            if n <= 0:
                raise ValueError("A selected source produced no searchable text; active index retained")
            print(f"      ✓ Indexed {n} chunks")
            stats["capsules"] += 1
            stats["total_chunks"] += n

    return stats


def _source_snapshot(kb_path: Path):
    import capsule_registry
    files = []
    for directory, suffixes in (("papers", {".pdf", ".bib"}), ("books", {".pdf", ".bib"}),
                                ("logbooks", {".txt", ".md"})):
        files.extend(p for p in (kb_path / directory).glob("*") if p.is_file() and p.suffix.lower() in suffixes)
    files.extend(capsule_registry.CAPSULES_DIR.rglob("*.md"))
    return {str(path.resolve()): file_hash(path) for path in sorted(files)}


def _build_stable_sources(kb_path, collection, embedder):
    before = _source_snapshot(kb_path)
    stats = _populate_documents(kb_path, collection, embedder)
    if before != _source_snapshot(kb_path):
        raise ValueError("Knowledge sources changed during indexing; active index retained")
    stats["source_manifest_sha256"] = hashlib.sha256(json.dumps(before, sort_keys=True).encode()).hexdigest()
    return stats


def index_documents(kb_path: Path, collection_name: str = "hedm_knowledge"):
    if chromadb is None or SentenceTransformer is None or PyPDF2 is None:
        raise RuntimeError("Missing dependencies: chromadb sentence-transformers PyPDF2")
    from knowledge_base.lifecycle import load_embedder, publish_generation
    offline = _apply_offline_hf_env()
    embedder, identity = load_embedder(SentenceTransformer, EMBED_MODEL, offline=offline)
    chroma_path = kb_path / "chroma_db"
    chroma_path.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(chroma_path))
    record = publish_generation(client, kb_path, collection_name,
        lambda collection: _build_stable_sources(kb_path, collection, embedder), identity)
    print(f"Published {record['chunks']} chunks in immutable generation {record['collection_name']}")
    return record


def test_query(kb_path: Path, query: str = "How does MIDAS index grains?"):
    if chromadb is None or SentenceTransformer is None:
        return
    print()
    print(f"\U0001f50d Test query: '{query}'")
    from knowledge_base.lifecycle import active_collection, load_embedder
    embedder, identity = load_embedder(SentenceTransformer, EMBED_MODEL,
                                      offline=_apply_offline_hf_env())
    client = chromadb.PersistentClient(path=str(kb_path / "chroma_db"))
    try:
        collection, _ = active_collection(client, kb_path, embedding=identity)
        emb = embed_query(embedder, query)
        results = collection.query(query_embeddings=[emb], n_results=3)
        for i, (doc, meta, dist) in enumerate(zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        ), 1):
            print()
            sim = max(0.0, 1 - dist)
            print(f"{i}. {meta.get('citation', meta['source'])}")
            page = meta.get("page", 0)
            if page:
                print(f"   p.{page} — similarity {sim:.0%}")
            else:
                print(f"   similarity {sim:.0%}")
            print(f"   {doc[:220]}...")
    except Exception as e:
        print(f"❌ Query error: {e}")


def main():
    kb_path = Path(__file__).parent
    print("=" * 60)
    print("HEDM Knowledge Base Indexer (citation-aware)")
    print("=" * 60)

    papers = len(list((kb_path / "papers").glob("*.pdf"))) if (kb_path / "papers").exists() else 0
    logbooks = len(list((kb_path / "logbooks").glob("*.txt")) + list((kb_path / "logbooks").glob("*.md"))) if (kb_path / "logbooks").exists() else 0
    books = len(list((kb_path / "books").glob("*.pdf"))) if (kb_path / "books").exists() else 0
    capsules_dir = kb_path / "capsules"
    capsules = len([p for p in capsules_dir.iterdir()
                    if p.is_dir() and (p / "README.md").is_file() and (p / "ENVELOPE.md").is_file()]) \
        if capsules_dir.exists() else 0
    total = papers + logbooks + books + capsules

    if total == 0:
        print("⚠️  No documents to index.")
        print(f"   Add to: {kb_path}/papers, {kb_path}/logbooks, {kb_path}/books, {kb_path}/capsules")
        return

    print(f"Found {papers} papers, {logbooks} logbooks, {books} books, {capsules} capsules")
    index_documents(kb_path)
    test_query(kb_path)


if __name__ == "__main__":
    main()
