import json
from types import SimpleNamespace

import pytest

from knowledge_base.lifecycle import (
    ProvenanceCollection, active_collection, embedding_identity,
    load_embedder, publish_generation,
)
from knowledge_base import hybrid_retrieval as hybrid


class Collection:
    def __init__(self, name, metadata=None):
        self.name = self.id = name
        self.metadata = metadata or {}
        self.docs = {}
        self.reads = 0

    def add(self, documents, embeddings, metadatas, ids):
        for key, doc, meta in zip(ids, documents, metadatas):
            self.docs[key] = (doc, meta)

    def count(self):
        return len(self.docs)

    def modify(self, metadata):
        self.metadata = metadata

    def get(self, where=None, include=None):
        self.reads += 1
        rows = [(key, doc, meta) for key, (doc, meta) in self.docs.items()
                if not where or all(meta.get(k) == v for k, v in where.items())]
        return dict(ids=[r[0] for r in rows], documents=[r[1] for r in rows], metadatas=[r[2] for r in rows])

    def query(self, query_embeddings, n_results, where=None):
        # Do not count dense backend reads as sparse corpus loads.
        reads = self.reads
        result = self.get(where)
        self.reads = reads
        return {**{k: [v[:n_results]] for k, v in result.items()},
                "distances": [[0.1] * min(n_results, len(result['ids']))]}


class Client:
    def __init__(self):
        self.collections = {}
    def create_collection(self, name, metadata):
        self.collections[name] = Collection(name, metadata)
        return self.collections[name]
    def get_collection(self, name):
        return self.collections[name]
    def delete_collection(self, name):
        del self.collections[name]


def populate(collection, word='threshold'):
    collection.add(documents=[word, 'unrelated beam', 'different content'],
        embeddings=[[1.0]] * 3, metadatas=[{'type': 'capsule', 'source': f'tech/file{i}.md'} for i in range(3)],
        ids=['a', 'b', 'c'])
    return {'total_chunks': 3}


def test_failed_build_does_not_replace_or_delete_live_generation(tmp_path):
    client = Client()
    old = publish_generation(client, tmp_path, 'hedm_knowledge', populate, {'model': 'pinned'})
    def fail(collection):
        populate(collection)
        raise RuntimeError('interrupted before completion')
    with pytest.raises(RuntimeError):
        publish_generation(client, tmp_path, 'hedm_knowledge', fail, {'model': 'pinned'})
    collection, record = active_collection(client, tmp_path, embedding={'model': 'pinned'})
    assert collection.name == old['collection_name']
    assert record['verified_generation']
    assert len(client.collections) == 1
    with pytest.raises(ValueError, match='differs'):
        active_collection(client, tmp_path, embedding={'model': 'different'})


def test_publish_keeps_old_reader_and_invalidates_sparse_generation(tmp_path):
    hybrid.clear_sparse_cache()
    client = Client()
    one = publish_generation(client, tmp_path, 'hedm_knowledge', populate, {})
    old = client.get_collection(one['collection_name'])
    hybrid.hybrid_search(old, [1], 'threshold')
    hybrid.hybrid_search(old, [1], 'threshold')
    assert old.reads == 1
    two = publish_generation(client, tmp_path, 'hedm_knowledge', lambda c: populate(c, 'newflag'), {})
    new, _ = active_collection(client, tmp_path)
    assert new.name == two['collection_name'] != old.name
    result = hybrid.hybrid_search(new, [1], 'newflag')
    assert new.reads == 1
    assert any(r['document'] == 'newflag' for r in result)
    assert old.count() == 3


def test_unversioned_collections_are_never_sparse_cached():
    collection = Collection('legacy')
    populate(ProvenanceCollection(collection))
    hybrid.hybrid_search(collection, [1], 'threshold')
    hybrid.hybrid_search(collection, [1], 'threshold')
    assert collection.reads == 2


def test_provenance_distinguishes_history_and_same_named_sources():
    collection = Collection('test')
    wrapper = ProvenanceCollection(collection)
    wrapper.add(documents=['memory', 'procedure'], embeddings=[[1], [2]],
        metadatas=[{'source': 'a/LAB_NOTEBOOK.md', 'type': 'capsule'},
                   {'source': 'b/README.md', 'type': 'capsule'}], ids=['duplicate', 'duplicate'])
    assert collection.count() == 2
    metadata = [meta for _, meta in collection.docs.values()]
    assert metadata[0]['knowledge_role'] == 'historical_observation'
    assert metadata[1]['knowledge_role'] == 'procedure'
    assert all(len(meta['content_sha256']) == 64 for meta in metadata)


def test_remote_code_requires_separate_pin_and_offline_is_not_dropped(monkeypatch):
    monkeypatch.setenv('APEXA_NETWORK', 'data')
    monkeypatch.setenv('APEXA_EMBED_REVISION', 'a' * 40)
    monkeypatch.delenv('APEXA_EMBED_TRUST_REMOTE_CODE', raising=False)
    kwargs = {}
    def factory(name, **kw):
        kwargs.update(kw)
        return SimpleNamespace()
    load_embedder(factory, 'nomic-ai/nomic-embed-text-v1.5', offline=False)
    assert kwargs['local_files_only'] is True  # deployment policy wins over caller
    assert kwargs['trust_remote_code'] is False
    assert kwargs['revision'] == 'a' * 40
    assert not {'model_kwargs', 'processor_kwargs', 'tokenizer_kwargs'} & kwargs.keys()
    monkeypatch.setenv('APEXA_EMBED_TRUST_REMOTE_CODE', '1')
    monkeypatch.delenv('APEXA_EMBED_CODE_REVISION', raising=False)
    with pytest.raises(ValueError, match='CODE_REVISION'):
        embedding_identity('nomic-ai/nomic-embed-text-v1.5')


@pytest.fixture
def embedding_loader_fixture(tmp_path, monkeypatch):
    """Real installed ST constructors, synthetic manifests, no Hub/model downloads."""
    import importlib
    import huggingface_hub
    from sentence_transformers import SentenceTransformer
    from transformers import AutoConfig, AutoModel, AutoProcessor, BertConfig, BertModel, BertTokenizerFast
    original_loaders = (AutoConfig.from_pretrained, AutoModel.from_pretrained, AutoProcessor.from_pretrained)
    monkeypatch.setenv('APEXA_NETWORK', 'data')
    monkeypatch.setenv('APEXA_EMBED_REVISION', 'a' * 40)
    monkeypatch.setenv('APEXA_EMBED_TRUST_REMOTE_CODE', '1')
    monkeypatch.setenv('APEXA_EMBED_CODE_REVISION', 'b' * 40)
    monkeypatch.setenv('HF_HUB_OFFLINE', '1')
    monkeypatch.setenv('TRANSFORMERS_OFFLINE', '1')
    files = {
        'modules.json': [
            {'idx': 0, 'name': '0', 'path': '', 'type': 'sentence_transformers.models.Transformer'},
            {'idx': 1, 'name': '1', 'path': '1_Pooling', 'type': 'sentence_transformers.models.Pooling'},
            {'idx': 2, 'name': '2', 'path': '2_Normalize', 'type': 'sentence_transformers.models.Normalize'}],
        'config_sentence_transformers.json': {'prompts': {'query': 'search_query: '},
            'default_prompt_name': 'query', 'truncate_dim': 3, 'similarity_fn_name': 'dot'},
        'sentence_bert_config.json': {'max_seq_length': 8192, 'do_lower_case': False},
        '1_Pooling/config.json': {'word_embedding_dimension': 4, 'pooling_mode_cls_token': False,
            'pooling_mode_mean_tokens': True, 'pooling_mode_max_tokens': False},
        'config.json': {'model_type': 'bert', 'hidden_size': 4, 'auto_map': {
            'AutoConfig': 'separate/code-repo--configuration.CustomConfig',
            'AutoModel': 'separate/code-repo--modeling.CustomModel'}},
        'tokenizer_config.json': {'tokenizer_class': 'BertTokenizer', 'model_max_length': 8192},
    }
    def write():
        for name, value in files.items():
            path = tmp_path / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value))
    write()
    calls = {'download': [], 'config': [], 'model': [], 'processor': []}
    def staged_file(repo_id, filename, **kwargs):
        calls['download'].append((repo_id, filename, dict(kwargs)))
        assert kwargs['revision'] == 'a' * 40 and kwargs['local_files_only'] is True
        path = tmp_path / kwargs.get('subfolder', '') / filename
        assert path.exists(), f'Unexpected asset request: {path.name}'
        return str(path)
    monkeypatch.setattr(huggingface_hub, 'hf_hub_download', staged_file)
    file_io = importlib.import_module('sentence_transformers.util.file_io')
    monkeypatch.setattr(file_io, 'hf_hub_download', staged_file)
    base = importlib.import_module('sentence_transformers.base.model')
    transformer = importlib.import_module('sentence_transformers.base.modules.transformer')
    peft = importlib.import_module('transformers.utils.peft_utils')
    monkeypatch.setattr(base, 'get_device_name', lambda: 'cpu')
    monkeypatch.setattr(peft, 'find_adapter_config_file', lambda *a, **k: None)
    monkeypatch.setattr(transformer, 'find_adapter_config_file', lambda *a, **k: None)
    def unsafe_path(*args, **kwargs):
        pytest.fail('Generic ST module loader discards the code pin and must not run')
    monkeypatch.setattr(SentenceTransformer, '_load_module_class_from_ref', unsafe_path)
    config = BertConfig(vocab_size=8, hidden_size=4, num_hidden_layers=1, num_attention_heads=1,
                        intermediate_size=8, max_position_embeddings=16)
    def config_loader(name, **kwargs):
        calls['config'].append(dict(kwargs))
        return config
    def model_loader(name, **kwargs):
        calls['model'].append(dict(kwargs))
        # Random tiny in-memory weights only; no forward/embedding is evaluated.
        return BertModel(kwargs['config'])
    vocab = tmp_path / 'vocab.txt'
    vocab.write_text('[PAD]\n[UNK]\n[CLS]\n[SEP]\n[MASK]\na\nb\nc\n')
    def processor_loader(name, **kwargs):
        calls['processor'].append(dict(kwargs))
        return BertTokenizerFast(vocab_file=str(vocab), model_max_length=kwargs['model_max_length'])
    monkeypatch.setattr(AutoConfig, 'from_pretrained', config_loader)
    monkeypatch.setattr(AutoModel, 'from_pretrained', model_loader)
    monkeypatch.setattr(AutoProcessor, 'from_pretrained', processor_loader)
    return SimpleNamespace(factory=SentenceTransformer, files=files, write=write, calls=calls,
                           peft=peft, path=tmp_path, original_loaders=original_loaders)


def test_qualified_real_constructor_path_preserves_pins_and_saved_pipeline(embedding_loader_fixture):
    fixture = embedding_loader_fixture
    embedder, identity = load_embedder(fixture.factory, 'fixture/embedding', offline=False)
    assert len(fixture.calls['config']) == 2
    assert len(fixture.calls['model']) == len(fixture.calls['processor']) == 1
    for kwargs in fixture.calls['config'] + fixture.calls['model']:
        assert kwargs['revision'] == 'a' * 40
        assert kwargs['code_revision'] == 'b' * 40
        assert kwargs['local_files_only'] is True and kwargs['trust_remote_code'] is True
    processor = fixture.calls['processor'][0]
    assert processor['revision'] == 'a' * 40 and processor['code_revision'] == 'b' * 40
    assert processor['trust_remote_code'] is False and processor['local_files_only'] is True
    assert processor['config'] is fixture.calls['model'][0]['config']
    assert processor['model_max_length'] == embedder[0].processor.model_max_length == 8192
    assert embedder[1].pooling_mode == 'mean'
    assert type(embedder[2]).__name__ == 'Normalize'
    assert embedder.prompts['query'] == 'search_query: '
    assert embedder.default_prompt_name == 'query' and embedder.truncate_dim == 3
    assert embedder.similarity_fn_name == 'dot'
    assert identity['loader'] == 'apexa-pinned-text-pipeline-v1'
    assert identity['processor_trust_remote_code'] is False
    assert set(identity['config_sha256']) == set(fixture.files)
    assert all(len(digest) == 64 for digest in identity['config_sha256'].values())


def test_installed_auto_resolvers_receive_pins_and_processor_keeps_code_disabled(embedding_loader_fixture, monkeypatch):
    import importlib
    from transformers import AutoTokenizer, BertConfig, BertModel
    fixture = embedding_loader_fixture
    config_loader, model_loader, processor_loader = fixture.original_loaders
    resolved = []
    config = BertConfig()
    config.auto_map = fixture.files['config.json']['auto_map']
    class FixtureConfig(BertConfig):
        @classmethod
        def from_pretrained(cls, *args, **kwargs):
            return config
    class FixtureModel(BertModel):
        @classmethod
        def from_pretrained(cls, *args, **kwargs):
            return 'model-boundary'
    def dynamic(cls):
        def intercept(reference, path, **kwargs):
            resolved.append((reference, kwargs))
            return cls
        return intercept
    monkeypatch.setattr(importlib.import_module('transformers.models.auto.configuration_auto'),
                        'get_class_from_dynamic_module', dynamic(FixtureConfig))
    monkeypatch.setattr(importlib.import_module('transformers.models.auto.auto_factory'),
                        'get_class_from_dynamic_module', dynamic(FixtureModel))
    pins = {'revision': 'a' * 40, 'code_revision': 'b' * 40, 'local_files_only': True}
    assert config_loader(str(fixture.path), trust_remote_code=True, **pins) is config
    assert model_loader(str(fixture.path), config=config, trust_remote_code=True, **pins) == 'model-boundary'
    assert len(resolved) == 2
    for reference, kwargs in resolved:
        assert reference.startswith('separate/code-repo--')
        assert kwargs['code_revision'] == 'b' * 40 and kwargs['revision'] == 'a' * 40
        assert kwargs['local_files_only'] is True
    tokenizer_calls = []
    def tokenizer(*args, **kwargs):
        tokenizer_calls.append(kwargs)
        return 'tokenizer-boundary'
    monkeypatch.setattr(AutoTokenizer, 'from_pretrained', tokenizer)
    def forbidden(*args, **kwargs):
        pytest.fail('The processor must not load custom code')
    monkeypatch.setattr(importlib.import_module('transformers.models.auto.processing_auto'),
                        'get_class_from_dynamic_module', forbidden)
    assert processor_loader(str(fixture.path), config=config, trust_remote_code=False, **pins) == 'tokenizer-boundary'
    assert tokenizer_calls[0]['trust_remote_code'] is False
    assert tokenizer_calls[0]['revision'] == 'a' * 40 and tokenizer_calls[0]['code_revision'] == 'b' * 40


@pytest.mark.parametrize('mutation,match', [
    ('custom_module', 'Unsupported embedding module'),
    ('alternate_tokenizer', 'Unsupported Transformer settings'),
    ('custom_processor', 'Custom embedding processor'),
    ('unknown_tokenizer', 'A saved built-in tokenizer'),
    ('unknown_saved_setting', 'Unsupported saved SentenceTransformer'),
    ('invalid_pooling', 'Invalid or ambiguous Pooling'),
    ('adapter', 'PEFT adapter'),
])
def test_unqualified_pipeline_is_rejected_before_model_code(embedding_loader_fixture, monkeypatch, mutation, match):
    fixture = embedding_loader_fixture
    if mutation == 'custom_module': fixture.files['modules.json'][0]['type'] = 'repo_model.CustomModule'
    elif mutation == 'alternate_tokenizer': fixture.files['sentence_bert_config.json']['tokenizer_name_or_path'] = 'other/repo'
    elif mutation == 'custom_processor': fixture.files['tokenizer_config.json']['auto_map'] = {'AutoTokenizer': ['custom.Tokenizer', None]}
    elif mutation == 'unknown_tokenizer': fixture.files['tokenizer_config.json']['tokenizer_class'] = 'CustomRemoteTokenizer'
    elif mutation == 'unknown_saved_setting': fixture.files['config_sentence_transformers.json']['custom_option'] = True
    elif mutation == 'invalid_pooling': fixture.files['1_Pooling/config.json']['pooling_mode'] = 'cls'
    elif mutation == 'adapter': monkeypatch.setattr(fixture.peft, 'find_adapter_config_file', lambda *a, **k: '/fixture/adapter_config.json')
    fixture.write()
    with pytest.raises(ValueError, match=match):
        load_embedder(fixture.factory, 'fixture/embedding', offline=True)
    assert not fixture.calls['model'] and not fixture.calls['config'] and not fixture.calls['processor']


def test_remote_loading_refuses_unqualified_sdk_before_asset_or_model_access(monkeypatch):
    import importlib.metadata
    monkeypatch.setenv('APEXA_NETWORK', 'data')
    monkeypatch.setenv('APEXA_EMBED_REVISION', 'a' * 40)
    monkeypatch.setenv('APEXA_EMBED_TRUST_REMOTE_CODE', '1')
    monkeypatch.setenv('APEXA_EMBED_CODE_REVISION', 'b' * 40)
    monkeypatch.setattr(importlib.metadata, 'version', lambda name: 'unknown')
    def forbidden(*args, **kwargs):
        pytest.fail('Must refuse before constructing an embedder')
    with pytest.raises(RuntimeError, match='not qualified'):
        load_embedder(forbidden, 'fixture/embedding', offline=True)


def test_remote_code_disabled_path_accepts_real_constructor_signature(monkeypatch):
    import importlib
    from sentence_transformers import SentenceTransformer
    monkeypatch.setenv('APEXA_NETWORK', 'data')
    monkeypatch.setenv('APEXA_EMBED_REVISION', 'a' * 40)
    monkeypatch.setenv('APEXA_EMBED_TRUST_REMOTE_CODE', '0')
    monkeypatch.setattr(importlib.import_module('sentence_transformers.base.model'), 'get_device_name', lambda: 'cpu')
    class ReachedLoader(Exception):
        pass
    def intercept(self, model_name_or_path, **kwargs):
        assert model_name_or_path == 'fixture/builtin'
        assert kwargs['revision'] == 'a' * 40
        assert kwargs['trust_remote_code'] is False and kwargs['local_files_only'] is True
        raise ReachedLoader
    monkeypatch.setattr(SentenceTransformer, '_load_modules', intercept)
    with pytest.raises(ReachedLoader):
        load_embedder(SentenceTransformer, 'fixture/builtin', offline=False)


def test_moving_revision_is_refused(monkeypatch):
    monkeypatch.setenv('APEXA_EMBED_REVISION', 'main')
    with pytest.raises(ValueError, match='40-character'):
        embedding_identity('nomic-ai/nomic-embed-text-v1.5')


def test_bm25_inverted_postings_preserve_formula():
    import numpy as np
    corpus = [['a', 'a', 'b'], ['b', 'c'], ['c', 'd', 'd'], ['e']]
    index = hybrid.BM25Okapi(corpus)
    expected = np.zeros(4)
    lengths = np.asarray([len(doc) for doc in corpus])
    for word in ['a', 'b', 'd', 'absent']:
        freq = np.asarray([doc.count(word) for doc in corpus])
        expected += index.idf.get(word, 0) * freq * (index.k1 + 1) / (
            freq + index.k1 * (1 - index.b + index.b * lengths / index.avgdl))
    np.testing.assert_allclose(index.get_scores(['a', 'b', 'd', 'absent']), expected)


def test_real_chroma_generation_publication_and_query(tmp_path, monkeypatch):
    monkeypatch.setenv('ANONYMIZED_TELEMETRY', 'False')
    monkeypatch.setenv('CHROMA_TELEMETRY_ENABLED', 'False')
    import chromadb
    client = chromadb.PersistentClient(path=str(tmp_path / 'chroma'))
    record = publish_generation(client, tmp_path, 'hedm_knowledge', populate, {'model': 'synthetic'})
    collection, provenance = active_collection(client, tmp_path, embedding={'model': 'synthetic'})
    assert collection.count() == 3 and provenance['verified_generation']
    result = collection.query(query_embeddings=[[1.0]], n_results=1)
    assert result['documents'][0]
    assert provenance['collection_name'] == record['collection_name']
