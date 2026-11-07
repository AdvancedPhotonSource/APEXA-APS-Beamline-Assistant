# APEXA Knowledge Base (RAG)

Citation-aware retrieval that backs the `KnowledgeAgent` and the
`query_hedm_knowledge` MCP tool. Content is embedded with Nomic
(`nomic-ai/nomic-embed-text-v1.5`, cosine) into a local ChromaDB collection
(`hedm_knowledge`).

## What ships in git

| Path | Tracked | Notes |
|---|---|---|
| `index_knowledge.py`, `fetch_materials_from_mp.py`, `sync_from_zotero.py` | ✅ | indexer + helpers |
| `data/*.json` | ✅ | index stats, materials/params templates |
| `papers/*.bib` | ✅ | citation sidecars (the `*.pdf` are **not** shipped — copyrighted) |
| `logbooks/*.md` (the 15 open MIDAS docs) | ✅ | FF/NF handbooks + lab notebooks, `Reconstruction_Reports`, and the analysis/calibration docs — un-ignored explicitly in `.gitignore` |
| `logbooks/*.txt`, other `logbooks/*.md` | ❌ | drop-zone for **private** beamtime notes — gitignored by default |
| `papers/*.pdf`, `books/*.pdf` | ❌ | copyrighted / large |
| `chroma_db/` | ✅ | a historical prebuilt index ships in git; verify its model identity and generation before use. A database alone is insufficient. |

## Deploy and maintain retrieval

Stage pinned embedding assets and use the same model/code revisions for indexing
and querying. The deployed native loader requires explicit immutable revisions
or a staged snapshot. It does not silently download and execute an unpinned
model on the first query. Legacy indexes without verified embedding provenance
must be rebuilt under the selected configuration.

Follow [the retrieval maintenance guide](../docs/RETRIEVAL_MAINTENANCE.md), then run:

```sh
python knowledge_base/index_knowledge.py
python scripts/check_knowledge.py --output /path/to/new-health-receipt.json
```

The indexer publishes a new immutable generation through
`data/hedm_knowledge-active.json`; a failed build preserves the previous generation.
The health command exercises native powder, FF-HEDM and NF-HEDM retrieval and
checks source freshness, model/index identity and excerpt hashes. Run it after
changes and before beamtime. A changed embedding model or source corpus requires
a new index, not just a configuration edit.

## Adding open MIDAS docs

Copy the `.md` into `logbooks/`, add a matching `!knowledge_base/logbooks/<name>.md`
negation line in `.gitignore`, then re-run the indexer. Keep proprietary beamtime
notes out of git — leave them as plain `logbooks/*.md`/`*.txt` (already ignored).
