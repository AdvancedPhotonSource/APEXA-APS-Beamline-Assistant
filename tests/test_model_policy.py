"""No Chinese-origin models anywhere in APEXA — LLMs, embedders, re-rankers.

These test the rule in both directions, because a provenance filter fails two
ways: letting a disallowed model through, and blocking an allowed one. The
second is not harmless -- a false positive on, say, `gemma` would quietly
remove a working model from a beamline.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import apexa_model_policy as MP


@pytest.mark.parametrize("model", [
    "Qwen/Qwen2.5-72B-Instruct", "Qwen/QwQ-32B", "qwen3-coder",
    "deepseek-ai/DeepSeek-V3", "deepseek-r1",
    # distilled FROM a blocked family: name still carries it, still blocked
    "deepseek-ai/DeepSeek-R1-Distill-Llama-70B",
    "BAAI/bge-m3", "BAAI/bge-reranker-v2-m3", "BAAI/Aquila-7B",
    "THUDM/glm-4-9b-chat", "THUDM/chatglm3-6b", "zai-org/GLM-4.5",
    "01-ai/Yi-34B", "baichuan-inc/Baichuan2-13B",
    "internlm/internlm2-chat-20b", "OpenGVLab/InternVL2-8B",
    "openbmb/MiniCPM-V-2_6", "moonshotai/Kimi-K2-Instruct",
    "MiniMaxAI/MiniMax-M1-80k", "tencent/Hunyuan-A13B-Instruct",
    "baidu/ERNIE-4.5-21B", "stepfun-ai/step-3", "Skywork/Skywork-13B",
    "inclusionAI/Ling-lite", "XiaomiMiMo/MiMo-7B",
])
def test_chinese_origin_models_are_refused(model):
    assert MP.disallowed_reason(model), f"{model} should be refused"
    with pytest.raises(MP.ModelPolicyError):
        MP.assert_allowed(model)


@pytest.mark.parametrize("model", [
    # every model APEXA offers today
    "claudeopus55", "claudeopus5", "claudesonnet55", "claudehaiku55",
    "gpt56sol", "gpt56terra", "gpt55", "gpt54", "gpt4o",
    "gemini35flash", "gemini31flashlite",
    "openai/gpt-oss-120b", "openai/gpt-oss-20b",
    "google/gemma-4-31B-it", "google/gemma-3-27b-it",
    "meta-llama/Llama-3.3-70B-Instruct", "meta-llama/Llama-4-Scout-17B-16E-Instruct",
    "nvidia/nemotron-3-super-120b", "nemotron-3-ultra",
    "inkling-bf16", "arcee-ai/Trinity-Large-Thinking-W4A16",
    # embedders
    "nomic-ai/nomic-embed-text-v1.5", "nvidia/llama-embed-nemotron-8b",
    # near-misses for the boundary-anchored patterns
    "mistralai/Mistral-7B", "microsoft/phi-4", "ibm-granite/granite-3.1",
    "allenai/OLMo-2", "seed-model-x", "stepwise-tuner", "glmnet-fit",
])
def test_allowed_models_are_not_falsely_refused(model):
    assert MP.disallowed_reason(model) is None, f"{model} wrongly refused"


def test_empty_id_is_not_a_violation():
    assert MP.disallowed_reason("") is None and MP.disallowed_reason(None) is None


def test_every_offered_alcf_model_passes():
    """If ALCF ever adds a blocked model to the curated table, this fails."""
    from apexa_llm_endpoints import ALCF_CANDIDATES
    bad = [c["model"] for c in ALCF_CANDIDATES if MP.disallowed_reason(c["model"])]
    assert not bad, f"blocked models offered in ALCF_CANDIDATES: {bad}"


def test_every_offered_argo_model_passes():
    import re, argo_mcp_client as A
    src = Path(A.__file__).read_text()
    ids = re.findall(r'^\s+"([a-z0-9]+)":\s+"', src, re.M)
    assert ids, "could not find the model table"
    bad = [m for m in ids if MP.disallowed_reason(m)]
    assert not bad, f"blocked models in the Argo table: {bad}"


def test_select_provider_refuses_before_choosing_a_transport():
    """The chokepoint: every LLM call is built here."""
    from apexa_agents import select_provider
    with pytest.raises(MP.ModelPolicyError):
        select_provider("someone", "deepseek-r1")


def test_the_indexer_refuses_a_blocked_embedder(monkeypatch):
    import importlib
    monkeypatch.setenv("APEXA_EMBED_MODEL", "BAAI/bge-m3")
    import knowledge_base.index_knowledge as IK
    IK = importlib.reload(IK)
    with pytest.raises(MP.ModelPolicyError):
        IK._load_embedder()
    monkeypatch.delenv("APEXA_EMBED_MODEL")
    importlib.reload(IK)


def test_no_blocked_model_is_pre_wired_in_the_embedder_table():
    import knowledge_base.index_knowledge as IK
    bad = [m for m in IK.EMBED_PREFIXES if MP.disallowed_reason(m)]
    assert not bad, f"blocked embedders pre-wired: {bad}"


def test_unverified_models_are_tracked_not_silently_trusted():
    """Name matching cannot see a renamed fine-tune's base model. Ids whose
    lineage is not stated must stay on the review list until confirmed."""
    assert "inkling-bf16" in MP.UNVERIFIED
