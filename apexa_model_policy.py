"""Model provenance policy: no Chinese-origin models anywhere in APEXA.

A standing requirement, enforced in code rather than left to whoever picks a
model id. It covers every way a model reaches APEXA -- an Argo/ALCF/custom/
Ollama LLM, the RAG embedder, and any future re-ranker -- because a policy that
only guards the picker is bypassed by one environment variable.

Enforced at the chokepoints, not at each place a model id can be set (there are
six: env, saved session, profile, the `model` command, ...):

    apexa_agents.select_provider()          every LLM call is built here
    argo_mcp_client `model <id>`            early, readable refusal
    knowledge_base/index_knowledge          the embedder

**What this can and cannot catch.** It matches on the model id -- organisation
and model-family names. That reliably catches the families themselves and
anything that keeps the name in its id (e.g. `DeepSeek-R1-Distill-Llama-70B`,
which is distilled from DeepSeek and is refused). It cannot detect a fine-tune
that was renamed so its id no longer mentions its base model. Provenance of a
model whose base is not stated in its id has to be verified at the source --
which is why ALCF ids of unstated lineage are listed for review in
`UNVERIFIED`, not silently trusted.

Leaf module: stdlib only, no repo imports, so every server and the indexer can
import it without pulling anything else in.
"""
from __future__ import annotations

import re
from typing import Dict, Optional, Tuple

__all__ = ["disallowed_reason", "assert_allowed", "ModelPolicyError",
           "BLOCKED_FAMILIES", "UNVERIFIED"]

#: (pattern, developer). Patterns are matched case-insensitively against the
#: whole model id, organisation prefix included. Each is anchored on a word or
#: path boundary where the bare string would be too generic to match safely
#: ("yi", "glm", "seed", "step", "spark" all occur in unrelated ids).
BLOCKED_FAMILIES: Tuple[Tuple[str, str], ...] = (
    (r"qwen|qwq|qvq",                         "Alibaba (Qwen)"),
    (r"deepseek",                             "DeepSeek"),
    (r"(^|[/_-])baai([/_-]|$)|(^|[/_-])bge([/_-]|$)|aquila",
                                              "BAAI, Beijing (BGE / Aquila)"),
    (r"chatglm|(^|[/_-])glm[-_]?\d|thudm|zhipu|codegeex|cogvlm|cogview",
                                              "Zhipu AI / Tsinghua (GLM)"),
    (r"(^|/)01-ai(/|$)|(^|/)yi[-_]\d",        "01.AI (Yi)"),
    (r"baichuan",                             "Baichuan"),
    (r"internlm|internvl|shanghai[-_]?ai[-_]?lab",
                                              "Shanghai AI Lab (InternLM)"),
    (r"minicpm|openbmb",                      "OpenBMB / ModelBest (MiniCPM)"),
    (r"(^|[/_-])kimi([/_-]|$)|moonshot",      "Moonshot AI (Kimi)"),
    (r"minimax",                              "MiniMax"),
    (r"hunyuan",                              "Tencent (Hunyuan)"),
    (r"(^|[/_-])ernie([/_-]|$)",              "Baidu (ERNIE)"),
    (r"doubao|bytedance[-_/]?seed",           "ByteDance (Doubao / Seed)"),
    (r"stepfun|(^|/)step[-_]\d",              "StepFun"),
    (r"skywork",                              "Kunlun (Skywork)"),
    (r"xverse",                               "XVERSE"),
    (r"telechat",                             "China Telecom (TeleChat)"),
    (r"pangu",                                "Huawei (PanGu)"),
    (r"inclusionai",                          "Ant Group (Ling / Ring)"),
    (r"xiaomimimo|(^|/)mimo[-_]",             "Xiaomi (MiMo)"),
)
_COMPILED = [(re.compile(p, re.I), who) for p, who in BLOCKED_FAMILIES]

#: Offered today, provenance not stated in the id. Not blocked -- blocking on
#: suspicion would remove working models on a guess -- but NOT treated as
#: verified either. Each needs its base model confirmed with the provider.
UNVERIFIED: Dict[str, str] = {
    "inkling-bf16": "ALCF Minerva; base model not stated in the id or ALCF docs.",
    "arcee-ai/Trinity-Large-Thinking-W4A16":
        "Arcee AI (US). Earlier Arcee releases were Qwen fine-tunes; confirm "
        "Trinity's base before relying on it.",
}


class ModelPolicyError(ValueError):
    """Raised when a disallowed model is selected. Never a silent fallback."""


def disallowed_reason(model_id: str) -> Optional[str]:
    """Why `model_id` is not allowed in APEXA, or None if it is."""
    m = (model_id or "").strip()
    if not m:
        return None
    for rx, who in _COMPILED:
        if rx.search(m):
            return (f"'{m}' is a {who} model. APEXA does not use Chinese-origin "
                    f"models (LLMs, embedders or re-rankers). Choose another model.")
    return None


def assert_allowed(model_id: str, *, role: str = "model") -> None:
    """Raise ModelPolicyError if `model_id` is disallowed."""
    why = disallowed_reason(model_id)
    if why:
        raise ModelPolicyError(f"{role} refused: {why}")
