# Deploying and maintaining native semantic retrieval

Retrieval requires three matching components: source documents, a pinned
embedding model, and an index built with that model. A copied Chroma database
alone is not a working deployment. On November 7, 2026, Copland had a legacy
768-dimensional index but no model cache, model revision or verified generation.
The loader correctly refused to guess the embedding identity.

## Pinned Nomic configuration

The native loader supports this saved Transformer + mean-Pooling pipeline with
sentence-transformers **5.7.0** and transformers **4.57.6**. Its 8192-token limit
is retained. The following exact cached snapshots passed a live CPU model-load
and finite 768-dimensional embedding check on November 7:

```sh
export APEXA_EMBED_MODEL=nomic-ai/nomic-embed-text-v1.5
export APEXA_EMBED_REVISION=e9b6763023c676ca8431644204f50c2b100d9aab
export APEXA_EMBED_TRUST_REMOTE_CODE=1
export APEXA_EMBED_CODE_REVISION=7710840340a098cfb869c4f65e87cf2b1b70caca
export HF_HUB_CACHE=/path/to/pinned-assets/hub
export HF_MODULES_CACHE=/path/to/writable-runtime/hf_modules
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
```

Stage the model snapshot under
`hub/models--nomic-ai--nomic-embed-text-v1.5/snapshots/<model revision>/` and the
code snapshot under `hub/models--nomic-ai--nomic-bert-2048/snapshots/<code revision>/`.
For this Nomic code revision, also include each repository's `refs/main` file,
containing exactly its pinned model/code commit. The upstream weight loader
internally resolves that alias even when its caller supplies a revision. Freeze
and hash those alias files with the assets and keep loading offline; they must
not point at a different or moving revision. The first isolated Copland attempt
caught missing aliases before creating an index.
Keep an inventory of file SHA-256 values and verify it after transfer. The model
weights are approximately 547 MB. The hub assets can be read-only; the runtime
module cache must be writable. Apply the same environment at index and query time.
These settings select APEXA's existing loader, without a substitute retriever.

## Build and check

Use the locked APEXA environment from the deployed source directory:

```sh
python knowledge_base/index_knowledge.py
python scripts/check_knowledge.py --output /path/to/new-health-receipt.json
```

The indexer builds a separate immutable collection, verifies nonempty output
and stable sources, then atomically publishes
`knowledge_base/data/hedm_knowledge-active.json`. Failure retains the previous
active generation. Keep old generations needed by live sessions or frozen studies.
Do not relabel a legacy index as compatible with a newly selected embedding model.

The health command runs real native retrieval queries for powder calibration,
FF-HEDM and NF-HEDM. It requires nonempty technique-matched excerpts, valid content
hashes, verified model/index identity and unchanged source fingerprints. It exits
nonzero on failure and makes no LLM calls. This checks retrieval availability and
provenance; it is not a benchmark of scientific answer quality.

## Routine maintenance

Run the health command after deployment, after rebuilding, and before a beamtime
that depends on semantic retrieval. Preserve its JSON receipt with the deployment
commit, model/code pins and active-generation record.

Rebuild when source documents, embedding identity, chunking/indexer behavior or
source location changes. Keep the exact dependency lock with the deployment.
Qualify a dependency or embedding upgrade separately before using it with an
existing index. A stale source fingerprint, missing assets or empty query result
is a failed health check, not a reason to continue with an unannounced fallback.

For an experiment, freeze the verified assets, generation and environment before
collecting outcomes. Give each trial a private writable Chroma copy and module
cache while retaining the same source/model identities. Changing retrieval
mid-cohort requires a new recorded configuration; do not pool the outcomes.

The November 7 study repair is staged under
`/home/beams/S20HEDM/apexa_eval/knowledge-maintenance/20261107/`. Live production
processes remain untouched while the isolated deployment is qualified.
