from types import SimpleNamespace

import pytest


def test_pdf_extraction_failure_is_not_published_as_empty_page(monkeypatch, tmp_path):
    from knowledge_base import index_knowledge as index
    pdf = tmp_path / 'paper.pdf'
    pdf.write_bytes(b'placeholder')
    class BadPage:
        def extract_text(self):
            raise RuntimeError('corrupt page')
    monkeypatch.setattr(index, 'PyPDF2', SimpleNamespace(PdfReader=lambda stream: SimpleNamespace(pages=[BadPage()])))
    monkeypatch.setattr(index, '_citation_metadata', lambda *args: {})
    with pytest.raises(RuntimeError, match='corrupt page'):
        index.index_paper(pdf, None, None)
    with pytest.raises(RuntimeError, match='corrupt page'):
        index.index_book(pdf, None, None)


def test_capsule_discovery_failure_aborts_build(monkeypatch, tmp_path):
    from knowledge_base import index_knowledge as index
    import capsule_registry
    def fail():
        raise RuntimeError('discovery failed')
    monkeypatch.setattr(capsule_registry, 'available_techniques', fail)
    with pytest.raises(RuntimeError, match='Capsule discovery failed'):
        index._populate_documents(tmp_path, None, None)


def test_source_mutation_before_publish_is_refused(monkeypatch, tmp_path):
    from knowledge_base import index_knowledge as index
    import capsule_registry
    capsule = tmp_path / 'capsules'
    capsule.mkdir()
    source = capsule / 'README.md'
    source.write_text('original')
    monkeypatch.setattr(capsule_registry, 'CAPSULES_DIR', capsule)
    def build(*args):
        source.write_text('changed')
        return {'total_chunks': 1}
    monkeypatch.setattr(index, '_populate_documents', build)
    with pytest.raises(ValueError, match='changed during indexing'):
        index._build_stable_sources(tmp_path, None, None)
