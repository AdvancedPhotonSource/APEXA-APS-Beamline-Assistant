import hashlib
import json
from types import SimpleNamespace

import numpy as np

import apexa_knowledge_service as service


def policy(monkeypatch, tmp_path, retrieval=None):
    path = tmp_path / 'reviewed.json'
    record = {'schema_version': 1, 'study_id': 'fixture', 'reviewed_by': 'test',
              'review_note': 'synthetic sources only', 'files': {}}
    if retrieval is not None:
        record['retrieval'] = retrieval
    path.write_text(json.dumps(record))
    monkeypatch.setenv('APEXA_KNOWLEDGE_MANIFEST', str(path))
    monkeypatch.setenv('APEXA_KNOWLEDGE_MANIFEST_SHA256', hashlib.sha256(path.read_bytes()).hexdigest())


def test_unapproved_study_retrieval_refuses_before_model_load(monkeypatch, tmp_path):
    policy(monkeypatch, tmp_path)
    def forbidden():
        raise AssertionError('must refuse before loading any embedder')
    monkeypatch.setattr(service, 'get_knowledge_base', forbidden)
    result = json.loads(service.query_knowledge('question'))
    assert result['status'] == 'error' and 'not approved' in result['error']


def test_study_filter_composes_and_checks_returned_content(monkeypatch, tmp_path):
    from knowledge_base import lifecycle
    doc = 'synthetic procedure'
    retrieval = {'collection_name': 'frozen-fixture', 'corpus_revision': 'a' * 64,
        'embedding': {'model': 'fixture', 'revision': 'b' * 40},
        'sources': [{'source_id': 'c' * 64, 'file_sha256': 'd' * 64}]}
    policy(monkeypatch, tmp_path, retrieval)
    calls = []
    meta = {'source': 'fixture.md', 'type': 'capsule', 'source_id': 'c' * 64,
            'file_hash': 'd' * 64, 'content_sha256': hashlib.sha256(doc.encode()).hexdigest()}
    def query(**kwargs):
        calls.append(kwargs)
        return {'documents': [[doc]], 'metadatas': [[dict(meta)]], 'distances': [[0.1]]}
    kb = {'available': True, 'client': object(), 'embedder': SimpleNamespace(encode=lambda text: np.array([1.])),
          'embedding_identity': retrieval['embedding'], 'kb_path': tmp_path}
    monkeypatch.setattr(service, 'get_knowledge_base', lambda: kb)
    monkeypatch.setattr(lifecycle, 'active_collection', lambda *args, **kwargs:
        (SimpleNamespace(query=query), {**retrieval, 'verified_generation': True, 'embedding_verified': True}))
    result = json.loads(service.query_knowledge('question', source_type='capsule', technique='fixture'))
    assert result['status'] == 'success'
    assert len(calls[0]['where']['$and']) == 3
    assert result['retrieval']['study_manifest_sha256']
    meta['content_sha256'] = 'e' * 64
    result = json.loads(service.query_knowledge('question'))
    assert result['status'] == 'error' and 'SHA-256' in result['error']
