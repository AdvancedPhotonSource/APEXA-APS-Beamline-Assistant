"""Deployment health must reject stale, unverified and empty native retrieval."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("fault", [None, "stale", "legacy", "empty", "content"])
def test_native_health_requires_fresh_verified_query_results(tmp_path, monkeypatch, fault):
    path = Path(__file__).resolve().parents[1] / 'scripts/check_knowledge.py'
    spec = importlib.util.spec_from_file_location('health_under_test', path)
    health = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(health)
    monkeypatch.setattr(health, 'REPO', tmp_path)
    monkeypatch.setattr(health, 'weight_alias_issues', lambda: [])
    sources = {'example/procedure.md': 'f' * 64}
    pointer = tmp_path / 'knowledge_base/data/hedm_knowledge-active.json'
    pointer.parent.mkdir(parents=True)
    digest = hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest()
    pointer.write_text(json.dumps({'stats': {'source_manifest_sha256': 'old' if fault == 'stale' else digest}}))
    monkeypatch.setitem(sys.modules, 'knowledge_base.index_knowledge', SimpleNamespace(_source_snapshot=lambda _: sources))

    def query(question, *, max_results, technique):
        text = 'actual native procedure'
        hits = [{'technique': technique, 'excerpt': text,
                 'content_sha256': 'wrong' if fault == 'content' else hashlib.sha256(text.encode()).hexdigest()}]
        return json.dumps({'status': 'success', 'excerpts': [] if fault == 'empty' else hits,
            'retrieval': {'provenance': {'verified_generation': fault != 'legacy', 'embedding_verified': True}}})

    monkeypatch.setitem(sys.modules, 'apexa_knowledge_service', SimpleNamespace(query_knowledge=query))
    result = health.check()
    assert result['passed'] is (fault is None)
    assert len(result['queries']) == 3
    assert result['model_generations'] == 0


def test_nomic_health_refuses_moving_or_missing_weight_alias(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[1] / 'scripts/check_knowledge.py'
    spec = importlib.util.spec_from_file_location('alias_health', path)
    health = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(health)
    monkeypatch.setenv('APEXA_EMBED_MODEL', 'nomic-ai/nomic-embed-text-v1.5')
    monkeypatch.setenv('APEXA_EMBED_REVISION', 'a'*40)
    monkeypatch.setenv('HF_HUB_CACHE', str(tmp_path))
    assert health.weight_alias_issues()
    alias = tmp_path/'models--nomic-ai--nomic-embed-text-v1.5/refs/main'
    alias.parent.mkdir(parents=True)
    alias.write_text('b'*40)
    assert health.weight_alias_issues()
    alias.write_text('a'*40)
    assert not health.weight_alias_issues()
