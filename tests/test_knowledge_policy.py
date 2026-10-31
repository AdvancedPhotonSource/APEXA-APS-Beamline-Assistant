"""Study knowledge scope uses synthetic files, no models or index assets."""
import hashlib
import json

import pytest

import apexa_knowledge_policy as policy


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture(autouse=True)
def isolated_policy(tmp_path, monkeypatch):
    monkeypatch.delenv(policy.MANIFEST_ENV, raising=False)
    monkeypatch.delenv(policy.DIGEST_ENV, raising=False)
    monkeypatch.setattr(policy, "REPO", tmp_path)


def activate(tmp_path, monkeypatch, files=None, retrieval=None):
    record = {"schema_version": 1, "study_id": "synthetic-test", "reviewed_by": "test-fixture",
              "review_note": "Synthetic data only; not a reviewed production study.", "files": files or {}}
    if retrieval is not None:
        record["retrieval"] = retrieval
    path = tmp_path / "study.json"
    path.write_text(json.dumps(record))
    monkeypatch.setenv(policy.MANIFEST_ENV, str(path))
    monkeypatch.setenv(policy.DIGEST_ENV, sha(path.read_bytes()))
    return path


def test_normal_operation_unchanged(tmp_path):
    path = tmp_path / "README.md"
    path.write_text("ordinary knowledge")
    assert policy.current_policy() is None
    assert policy.check_knowledge_file(path)
    assert policy.read_knowledge_text(path) == "ordinary knowledge"
    assert policy.retrieval_scope() is None
    policy.verify_retrieval_hit("ordinary", {})


def test_approved_bytes_only_and_changed_file_refuses(tmp_path, monkeypatch):
    allowed, hidden = tmp_path / "README.md", tmp_path / "LAB_NOTEBOOK.md"
    allowed.write_text("reviewed procedure")
    hidden.write_text("held-out answer")
    activate(tmp_path, monkeypatch, {"README.md": sha(allowed.read_bytes())})
    assert policy.read_knowledge_text(allowed) == "reviewed procedure"
    assert policy.read_knowledge_text(hidden) == ""
    assert not policy.check_knowledge_file(hidden)
    allowed.write_text("now contains an answer")
    with pytest.raises(policy.KnowledgePolicyError, match="changed"):
        policy.read_knowledge_text(allowed)


def test_manifest_pin_and_both_environment_variables_required(tmp_path, monkeypatch):
    path = activate(tmp_path, monkeypatch)
    path.write_text(path.read_text() + "\n")
    with pytest.raises(policy.KnowledgePolicyError, match="SHA-256"):
        policy.current_policy()
    monkeypatch.delenv(policy.DIGEST_ENV)
    with pytest.raises(policy.KnowledgePolicyError, match="both"):
        policy.current_policy()


@pytest.mark.parametrize("name", ["../answer.md", "/answer.md", "a/../answer.md", "a//answer.md", "a\\answer.md"])
def test_manifest_paths_cannot_escape_or_alias(tmp_path, monkeypatch, name):
    activate(tmp_path, monkeypatch, {name: "a" * 64})
    with pytest.raises(policy.KnowledgePolicyError):
        policy.current_policy()


def test_duplicate_manifest_json_keys_refused(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"schema_version":1,"schema_version":1}')
    with pytest.raises(policy.KnowledgePolicyError):
        policy.load_manifest(path)


def test_symlink_cannot_authorize_unreviewed_target(tmp_path, monkeypatch):
    hidden = tmp_path / "hidden.md"
    hidden.write_text("held-out answer")
    alias = tmp_path / "reviewed.md"
    alias.symlink_to(hidden)
    activate(tmp_path, monkeypatch, {"reviewed.md": sha(hidden.read_bytes())})
    assert policy.read_knowledge_text(alias) == ""


def test_warm_capsule_and_skill_caches_cannot_bypass_study_policy(tmp_path, monkeypatch):
    import capsule_registry as capsules
    import skill_registry as skills
    cap = tmp_path / "capsules" / "test-tech"
    cap.mkdir(parents=True)
    for name, text in (("README.md", "procedure"), ("ENVELOPE.md", "envelope"), ("HARD_RULES.md", "1. Hidden answer")):
        (cap / name).write_text(text)
    skill = tmp_path / "skills" / "test-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("secret procedure")
    monkeypatch.setattr(capsules, "CAPSULES_DIR", cap.parent)
    monkeypatch.setattr(skills, "SKILLS_DIR", skill.parent)
    capsules.available_techniques.cache_clear()
    capsules._read.cache_clear()
    skills.load_skill_text.cache_clear()
    try:
        assert capsules.spine("test-tech") == "procedure"
        assert skills.load_skill_text("test-skill") == "secret procedure"
        activate(tmp_path, monkeypatch)
        assert capsules.spine("test-tech") == ""
        assert capsules.hard_rules("test-tech") == []
        assert skills.load_skill_text("test-skill") == ""
        activate(tmp_path, monkeypatch, {"capsules/test-tech/README.md": sha((cap / "README.md").read_bytes())})
        assert capsules.spine("test-tech") == "procedure"
        assert skills.load_skill_text("test-skill") == ""
    finally:
        capsules.available_techniques.cache_clear()
        capsules._read.cache_clear()
        skills.load_skill_text.cache_clear()


def test_study_without_retrieval_scope_refuses_before_loading_a_model(tmp_path, monkeypatch):
    activate(tmp_path, monkeypatch)
    with pytest.raises(policy.KnowledgePolicyError, match="not approved"):
        policy.retrieval_scope()


def test_retrieval_requires_exact_generation_source_and_content(tmp_path, monkeypatch):
    document = "reviewed procedure"
    retrieval = {"collection_name": "hedm_knowledge-frozen", "corpus_revision": "c" * 64,
                 "embedding": {"model": "synthetic", "revision": "e" * 40},
                 "sources": [{"source_id": "a" * 64, "file_sha256": "b" * 64}]}
    activate(tmp_path, monkeypatch, retrieval=retrieval)
    scope = policy.retrieval_scope()
    assert scope["where"] == {"$and": [{"source_id": "a" * 64}, {"file_hash": "b" * 64}]}
    provenance = {**retrieval, "verified_generation": True, "embedding_verified": True}
    policy.verify_retrieval_provenance(provenance)
    with pytest.raises(policy.KnowledgePolicyError, match="differs"):
        policy.verify_retrieval_provenance({**provenance, "corpus_revision": "d" * 64})
    metadata = {"source_id": "a" * 64, "file_hash": "b" * 64, "content_sha256": sha(document.encode())}
    policy.verify_retrieval_hit(document, metadata)
    with pytest.raises(policy.KnowledgePolicyError, match="unapproved"):
        policy.verify_retrieval_hit(document, {**metadata, "source_id": "x" * 64})
    with pytest.raises(policy.KnowledgePolicyError, match="SHA-256"):
        policy.verify_retrieval_hit("changed answer", metadata)
