"""Immutable retrieval generations and explicitly pinned embedding assets.

Publishing changes one atomic pointer, never the collection used by readers.
Old generations are retained for in-flight readers and reproducible studies.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import uuid


def embedding_identity(model: str) -> dict:
    revision = os.environ.get("APEXA_EMBED_REVISION", "").strip()
    if not revision:
        # Resolve an already staged snapshot without network access. Never
        # silently execute code from a moving remote branch.
        from huggingface_hub import try_to_load_from_cache
        cached = try_to_load_from_cache(model, "modules.json")
        if isinstance(cached, str):
            match = re.search(r"[/\\]snapshots[/\\]([0-9a-f]{40})[/\\]", cached)
            revision = match.group(1) if match else ""
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Set APEXA_EMBED_REVISION to the reviewed 40-character model commit, or stage its Hugging Face snapshot first")
    trust = os.environ.get("APEXA_EMBED_TRUST_REMOTE_CODE", "0") == "1"
    code_revision = os.environ.get("APEXA_EMBED_CODE_REVISION", "").strip()
    if trust and not re.fullmatch(r"[0-9a-f]{40}", code_revision):
        raise ValueError("Remote embedding code requires APEXA_EMBED_CODE_REVISION with its reviewed 40-character code-repository commit")
    return {"model": model, "revision": revision, "trust_remote_code": trust,
            "code_revision": code_revision if trust else ""}


def load_embedder(factory, model: str, *, offline: bool):
    from apexa_model_policy import assert_allowed
    from apexa_network import apply_offline_env
    # Caller preferences may strengthen, never weaken, the deployment policy.
    policy_offline = apply_offline_env()
    offline = bool(offline or policy_offline or os.environ.get("APEXA_OFFLINE") == "1")
    assert_allowed(model, role="embedder")
    identity = embedding_identity(model)
    kwargs = {"revision": identity["revision"],
              "trust_remote_code": identity["trust_remote_code"],
              "local_files_only": offline}
    if not identity["trust_remote_code"]:
        return factory(model, **kwargs), identity

    # ST 5.7's general saved-model loader pops model_kwargs['code_revision']
    # while resolving a module class, before nested AutoModel loading. Avoid that
    # path entirely. This adapter is qualified only for the saved text pipeline
    # below; future SDK versions need new constructor-boundary qualification.
    from importlib.metadata import version
    versions = {name: version(name) for name in ("sentence-transformers", "transformers")}
    if versions != {"sentence-transformers": "5.7.0", "transformers": "4.57.6"}:
        raise RuntimeError("Pinned remote embedding code is not qualified for this SDK version pair; "
                           "use the locked environment or a reviewed model with remote code disabled")
    from huggingface_hub import hf_hub_download
    from sentence_transformers.sentence_transformer.modules import Transformer, Pooling, Normalize
    import transformers
    from transformers import AutoConfig, PreTrainedTokenizerBase
    from transformers.utils.peft_utils import find_adapter_config_file

    hashes = {}
    def manifest(filename):
        path = Path(hf_hub_download(repo_id=model, filename=filename,
            revision=identity["revision"], local_files_only=offline))
        if path.stat().st_size > 4 * 1024 * 1024:
            raise ValueError(f"Embedding configuration is unexpectedly large: {filename}")
        raw = path.read_bytes()
        hashes[filename] = hashlib.sha256(raw).hexdigest()
        return json.loads(raw)

    rows = manifest("modules.json")
    supported = {"sentence_transformers.models.Transformer": Transformer,
                 "sentence_transformers.models.Pooling": Pooling,
                 "sentence_transformers.models.Normalize": Normalize}
    supported.update({f"{cls.__module__}.{cls.__name__}": cls for cls in (Transformer, Pooling, Normalize)})
    expected = [Transformer, Pooling] + ([Normalize] if isinstance(rows, list) and len(rows) == 3 else [])
    if not isinstance(rows, list) or len(rows) not in (2, 3):
        raise ValueError("Pinned embedding loader requires Transformer, Pooling, and optional Normalize")
    for index, (row, cls) in enumerate(zip(rows, expected, strict=True)):
        if (not isinstance(row, dict) or set(row) - {"idx", "name", "path", "type", "kwargs"}
                or supported.get(row.get("type")) is not cls or row.get("idx") != index
                or row.get("name") != str(index) or row.get("kwargs")
                or not isinstance(row.get("path"), str)
                or not re.fullmatch(r"(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-]*", row["path"])):
            raise ValueError("Unsupported embedding module, order, path or module kwargs; no model was constructed")
    def module_file(index, filename):
        return "/".join(part for part in (rows[index]["path"], filename) if part)

    saved = manifest("config_sentence_transformers.json")
    allowed = {"__version__", "prompts", "default_prompt_name", "similarity_fn_name", "truncate_dim", "model_type"}
    if not isinstance(saved, dict) or set(saved) - allowed or saved.get("model_type", "SentenceTransformer") != "SentenceTransformer":
        raise ValueError("Unsupported saved SentenceTransformer settings")
    prompts = saved.get("prompts", {})
    if (not isinstance(prompts, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in prompts.items())
            or (saved.get("default_prompt_name") is not None and saved["default_prompt_name"] not in prompts)
            or saved.get("similarity_fn_name") not in (None, "cosine", "dot", "euclidean", "manhattan")
            or (saved.get("truncate_dim") is not None and (type(saved["truncate_dim"]) is not int or saved["truncate_dim"] <= 0))):
        raise ValueError("Invalid saved embedding prompts, similarity or truncation settings")

    transformer_config = manifest(module_file(0, "sentence_bert_config.json"))
    if (not isinstance(transformer_config, dict) or set(transformer_config) - {"max_seq_length", "do_lower_case"}
            or ("max_seq_length" in transformer_config and (type(transformer_config["max_seq_length"]) is not int or transformer_config["max_seq_length"] <= 0))
            or ("do_lower_case" in transformer_config and type(transformer_config["do_lower_case"]) is not bool)):
        raise ValueError("Unsupported Transformer settings; alternate model/tokenizer paths and custom loading kwargs are not qualified")
    pooling_config = manifest(module_file(1, "config.json"))
    modes = {"cls", "max", "mean", "mean_sqrt_len_tokens", "weightedmean", "lasttoken"}
    legacy_modes = {"pooling_mode_cls_token", "pooling_mode_max_tokens", "pooling_mode_mean_tokens",
                    "pooling_mode_mean_sqrt_len_tokens", "pooling_mode_weightedmean_tokens", "pooling_mode_lasttoken"}
    if not isinstance(pooling_config, dict) or set(pooling_config) - (legacy_modes | {"word_embedding_dimension", "embedding_dimension", "pooling_mode", "include_prompt"}):
        raise ValueError("Unsupported Pooling settings")
    dimensions = [pooling_config[k] for k in ("word_embedding_dimension", "embedding_dimension") if k in pooling_config]
    selected_modes = pooling_config.get("pooling_mode", "mean")
    if isinstance(selected_modes, str):
        selected_modes = [selected_modes]
    if (len(dimensions) != 1 or type(dimensions[0]) is not int or dimensions[0] <= 0
            or not isinstance(selected_modes, list) or not selected_modes or any(mode not in modes for mode in selected_modes)
            or any(type(pooling_config[k]) is not bool for k in (legacy_modes | {"include_prompt"}) if k in pooling_config)
            or ("pooling_mode" in pooling_config and legacy_modes.intersection(pooling_config))):
        raise ValueError("Invalid or ambiguous Pooling settings")

    # Config/model code can be pinned in a different repository. Processor code
    # stays disabled: the preloaded pinned config avoids AutoProcessor trying an
    # unpinned dynamic AutoConfig, and the tokenizer assets use the model commit.
    model_config = manifest(module_file(0, "config.json"))
    tokenizer_config = manifest(module_file(0, "tokenizer_config.json"))
    for config in (model_config, tokenizer_config):
        if (not isinstance(config, dict) or config.get("processor_class")
                or not isinstance(config.get("auto_map", {}), dict)
                or {"AutoProcessor", "AutoTokenizer"}.intersection(config.get("auto_map", {}))):
            raise ValueError("Custom embedding processor/tokenizer code is not qualified")
    tokenizer_name = tokenizer_config.get("tokenizer_class")
    tokenizer_class = getattr(transformers, tokenizer_name, None) if isinstance(tokenizer_name, str) else None
    if not isinstance(tokenizer_class, type) or not issubclass(tokenizer_class, PreTrainedTokenizerBase):
        raise ValueError("A saved built-in tokenizer class is required")
    if find_adapter_config_file(model, subfolder=rows[0]["path"],
            revision=identity["revision"], local_files_only=offline):
        raise ValueError("PEFT adapter embedding pipelines are not qualified; their base-model revision is separate")

    pinned = {"code_revision": identity["code_revision"], "revision": identity["revision"],
              "local_files_only": offline}
    config = AutoConfig.from_pretrained(model, subfolder=rows[0]["path"], trust_remote_code=True, **pinned)
    transformer = Transformer.load(model, subfolder=rows[0]["path"], **kwargs,
        model_kwargs=dict(pinned), config_kwargs=dict(pinned),
        processor_kwargs={**pinned, "config": config, "trust_remote_code": False})
    if not isinstance(transformer.processor, PreTrainedTokenizerBase):
        raise ValueError("Only a built-in text tokenizer is qualified for this embedding pipeline")
    modules = [transformer, Pooling.load(model, subfolder=rows[1]["path"],
        revision=identity["revision"], local_files_only=offline)]
    if len(rows) == 3:
        modules.append(Normalize())
    pipeline = {key: saved[key] for key in ("prompts", "default_prompt_name", "similarity_fn_name", "truncate_dim") if key in saved}
    # No model path: ST does not enter its unsafe module-class resolution path.
    embedder = factory(modules=modules, local_files_only=offline, trust_remote_code=False, **pipeline)
    identity = {**identity, "loader": "apexa-pinned-text-pipeline-v1", "sdk_versions": versions,
                "config_sha256": hashes, "processor_trust_remote_code": False}
    return embedder, identity


def _pointer(kb_path: Path, name: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{3,80}", name):
        raise ValueError("Invalid collection name")
    return Path(kb_path) / "data" / f"{name}-active.json"


def active_collection(client, kb_path: Path, name="hedm_knowledge", embedding=None):
    pointer = _pointer(kb_path, name)
    if not pointer.exists():
        # Existing collections remain readable, but are never eligible for the
        # immutable sparse cache and carry an explicit provenance limitation.
        return client.get_collection(name=name), {"verified_generation": False,
            "corpus_revision": None, "embedding_verified": False}
    record = json.loads(pointer.read_text())
    if record.get("schema_version") != 1:
        raise ValueError("Unsupported retrieval generation manifest")
    if embedding is not None and record.get("embedding") != embedding:
        raise ValueError("Query embedding identity differs from the published index; configure the recorded model/code revisions or rebuild")
    collection = client.get_collection(name=record["collection_name"])
    meta = collection.metadata or {}
    if not meta.get("apexa_immutable") or meta.get("corpus_revision") != record["corpus_revision"]:
        raise ValueError("Retrieval generation metadata does not match its manifest")
    return collection, {**record, "verified_generation": True,
                        "embedding_verified": embedding is not None}


def source_role(metadata: dict) -> str:
    if metadata.get("type") == "logbook" or "LAB_NOTEBOOK" in metadata.get("source", ""):
        return "historical_observation"
    if metadata.get("type") == "capsule":
        return "procedure"
    return "reference_material"


class ProvenanceCollection:
    """Attach stable content/source IDs without changing public chunk schemas."""
    def __init__(self, collection):
        self.collection = collection

    def add(self, *, documents, embeddings, metadatas, ids):
        enriched = []
        stable_ids = []
        for document, metadata, legacy_id in zip(documents, metadatas, ids, strict=True):
            source_id = hashlib.sha256((metadata.get("type", "") + ":" + metadata.get("source", "")).encode()).hexdigest()
            content_id = hashlib.sha256(document.encode()).hexdigest()
            enriched.append({**metadata, "source_id": source_id,
                "content_sha256": content_id, "knowledge_role": source_role(metadata),
                "trust_level": "retrieved_content", "legacy_chunk_id": legacy_id})
            stable_ids.append(hashlib.sha256((source_id + ":" + legacy_id).encode()).hexdigest())
        return self.collection.add(documents=documents, embeddings=embeddings,
                                   metadatas=enriched, ids=stable_ids)


def publish_generation(client, kb_path: Path, name: str, build, embedding: dict):
    """Build to completion, validate, then atomically publish. Failures keep old data."""
    pointer = _pointer(kb_path, name)
    generation = uuid.uuid4().hex
    collection_name = f"{name}-{generation}"
    collection = client.create_collection(name=collection_name,
        metadata={"hnsw:space": "cosine", "apexa_immutable": False})
    published = False
    try:
        stats = build(ProvenanceCollection(collection))
        count = collection.count()
        if count <= 0 or count != stats["total_chunks"]:
            raise ValueError("Refusing to publish an empty or incomplete retrieval generation")
        # This generation is never mutated after publication. Its ID namespaces
        # sparse cache entries; chunk SHA-256 values bind result text to evidence.
        revision = hashlib.sha256(json.dumps({"generation": generation,
            "embedding": embedding, "count": count}, sort_keys=True).encode()).hexdigest()
        collection.modify(metadata={"apexa_immutable": True,
                                    "corpus_revision": revision})
        record = {"schema_version": 1, "collection_name": collection_name,
                  "corpus_revision": revision, "embedding": embedding,
                  "chunks": count, "stats": stats}
        pointer.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=pointer.name + ".", dir=pointer.parent)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(record, stream, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, pointer)
            published = True
        finally:
            Path(temporary).unlink(missing_ok=True)
        return record
    finally:
        if not published:
            client.delete_collection(name=collection_name)
