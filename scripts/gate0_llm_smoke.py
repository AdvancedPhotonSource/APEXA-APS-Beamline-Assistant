#!/usr/bin/env python3
"""Gate 0 — prove an endpoint supports MULTI-TURN tool calling before APEXA depends on it.

This is the load-bearing assumption of the architecture refactor: that we can send a
tool RESULT back to the model as a structured ``{"role":"tool","tool_call_id":...}``
message and have the conversation continue. Argo's own ``/chat/`` templates only
document the single-turn case, which is exactly why APEXA fell back to a text
``TOOL_CALL:`` protocol and grew a cluster of regex anti-fabrication guards.

If this script prints PASS for a model, that model can drive the structured loop and
those guards become unnecessary. If it prints FAIL for every model, STOP — the
refactor's foundation is gone.

Prerequisites: ANL internal network or VPN on an Argonne-managed machine, and
ANL_USERNAME set. Nothing to install or run -- Argo serves the OpenAI surface
itself (the argo-proxy sidecar this script was written for has been retired).

    python scripts/gate0_llm_smoke.py                          # Argo native
    python scripts/gate0_llm_smoke.py --models claudeopus55 gpt56sol
    python scripts/gate0_llm_smoke.py --preset alcf-sophia     # an ALCF cluster

Exit code is 0 only if every tested model passes.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

try:
    from openai import OpenAI
except ImportError:
    sys.exit("openai SDK not installed.  pip install openai   (or: uv run --with openai <this script>)")

# One trivial, unmistakable tool. The correct final answer is only reachable by
# actually reading the tool result — the model cannot know 61 from its own weights.
TOOLS = [{
    "type": "function",
    "function": {
        "name": "get_tool_count",
        "description": "Return how many analysis tools a given beamline instrument has registered.",
        "parameters": {
            "type": "object",
            "properties": {
                "instrument": {"type": "string", "description": "Instrument name, e.g. 'APEXA'"},
            },
            "required": ["instrument"],
        },
    },
}]

SENTINEL = 61          # the value the tool returns
DEFAULT_MODELS = ["claudeopus5", "gpt56sol", "gemini35flash"]


# Send the SAME output limit APEXA does. Without one, Argo defaults to the model
# maximum, and for Claude the Anthropic SDK behind Argo refuses any non-streaming
# call whose max_tokens implies more than 10 minutes (cutoff 21,333 tokens,
# measured to the token on 26 Oct 2026). That made this script report every
# Claude model as unusable on the native endpoint when APEXA itself was fine.
try:
    import sys as _sys, os as _os
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from apexa_provider_openai import DEFAULT_MAX_TOKENS as _MAX_TOKENS
except Exception:
    _MAX_TOKENS = 16000


def probe(client: OpenAI, model: str, verbose: bool = False) -> tuple[bool, str]:
    """Run a two-turn tool exchange. Returns (passed, detail)."""
    messages = [
        {"role": "system", "content": "You are a terse assistant. Use the provided tools when relevant."},
        {"role": "user", "content": "How many analysis tools does the APEXA instrument have registered? Use the tool."},
    ]

    # ---- Turn 1: expect the model to emit a structured tool_call --------------
    try:
        r1 = client.chat.completions.create(
            model=model, messages=messages, tools=TOOLS, tool_choice="auto",
            max_completion_tokens=_MAX_TOKENS,
        )
    except Exception as e:
        return False, f"turn-1 request failed: {type(e).__name__}: {e}"

    msg = r1.choices[0].message
    calls = msg.tool_calls or []
    if not calls:
        return False, f"turn 1 returned no tool_calls (content={(msg.content or '')[:120]!r})"

    call = calls[0]
    if verbose:
        print(f"    turn1 tool_call: id={call.id!r} name={call.function.name!r} "
              f"args={call.function.arguments!r}")
    if not call.id:
        return False, "tool_call has no id — cannot correlate a tool result to it"

    # ---- Turn 2: feed the result back as a structured tool message -----------
    # This is THE thing being tested. APEXA cannot do this today.
    messages.append({
        "role": "assistant",
        "content": msg.content or "",
        "tool_calls": [{
            "id": call.id,
            "type": "function",
            "function": {"name": call.function.name, "arguments": call.function.arguments},
        }],
    })
    messages.append({
        "role": "tool",
        "tool_call_id": call.id,
        "content": json.dumps({"instrument": "APEXA", "tool_count": SENTINEL}),
    })

    try:
        r2 = client.chat.completions.create(model=model, messages=messages, tools=TOOLS,
                                            max_completion_tokens=_MAX_TOKENS)
    except Exception as e:
        return False, f"turn-2 (tool result) rejected: {type(e).__name__}: {e}"

    final = (r2.choices[0].message.content or "").strip()
    if verbose:
        print(f"    turn2 final: {final[:160]!r}")
    if not final:
        return False, "turn 2 returned empty content (the old Argo empty-completion failure)"
    if str(SENTINEL) not in final:
        return False, f"turn 2 did not use the tool result (expected {SENTINEL}): {final[:120]!r}"

    return True, f"multi-turn OK — final answer cites {SENTINEL}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--preset", default=None,
                    help="APEXA endpoint preset (argo, alcf-sophia, openai, …). "
                         "Supplies base-url and credential automatically.")
    ap.add_argument("--base-url", default=None)
    ap.add_argument("--api-key", default=None,
                    help="ANL username for Argo, or a bearer token / API key")
    ap.add_argument("--models", nargs="+", default=None)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    # A preset fills in whatever wasn't given explicitly. This is how you qualify a
    # NON-Argo endpoint (e.g. ALCF Sophia) with the same harness: tool-calling
    # quality varies a lot across open models, and only an actual two-turn exchange
    # settles whether one can drive APEXA.
    if args.preset:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        try:
            from apexa_llm_endpoints import PRESETS, EndpointRejected
        except ImportError as e:
            return int(bool(sys.stderr.write(f"cannot load presets: {e}\n"))) or 2
        cls = PRESETS.get(args.preset.strip().lower())
        if cls is None:
            print(f"Unknown preset {args.preset!r}. Choose: {', '.join(sorted(PRESETS))}")
            return 2
        ep = cls()
        if not ep.tool_calling:
            print(f"Preset {ep.name!r} does not support tool calling: {ep.notes}")
            print("APEXA cannot run on it. Nothing to test.")
            return 1
        # Precedence: explicit --base-url > APEXA_LLM_BASE_URL > preset default.
        # (Instantiating the class directly bypasses active_endpoint()'s env
        # override, so apply it here or a configured port is silently ignored.)
        args.base_url = (args.base_url
                         or (os.environ.get("APEXA_LLM_BASE_URL") or "").strip()
                         or ep.base_url)
        if not args.api_key:
            try:
                args.api_key = ep.resolve_key(os.environ.get("ANL_USERNAME", ""))
            except EndpointRejected as e:
                return int(bool(sys.stderr.write(f"credential unavailable: {e}\n"))) or 2
        if args.models is None and ep.name.startswith("alcf"):
            # ALCF T-flagged (tool-calling) models, per docs.alcf.anl.gov.
            args.models = ["meta-llama/Llama-3.3-70B-Instruct", "openai/gpt-oss-120b"]

    args.base_url = args.base_url or os.environ.get("APEXA_LLM_BASE_URL",
                                                    "https://apps.inside.anl.gov/argoapi/v1")
    args.api_key = args.api_key or os.environ.get("APEXA_LLM_API_KEY") \
        or os.environ.get("ANL_USERNAME", "")
    args.models = args.models or DEFAULT_MODELS

    if not args.api_key:
        return int(bool(sys.stderr.write(
            "No credential. Set ANL_USERNAME / APEXA_LLM_API_KEY, pass --api-key, "
            "or use --preset.\n"))) or 2

    _label = (args.preset or "endpoint")
    _shown = args.api_key if len(args.api_key) < 40 else args.api_key[:8] + "…(token)"
    print(f"endpoint ({_label}) : {args.base_url}")
    print(f"credential          : {_shown}")
    print(f"models              : {', '.join(args.models)}\n")

    try:
        client = OpenAI(base_url=args.base_url, api_key=args.api_key)
        served = {m.id for m in client.models.list().data}
        print(f"reachable — {len(served)} models served\n")
    except Exception as e:
        print(f"FAIL: cannot reach {args.base_url}\n  {type(e).__name__}: {e}\n")
        print("Are you on the ANL network/VPN, and is ANL_USERNAME correct?")
        return 2

    results: list[tuple[str, bool, str]] = []
    # Resolve ids the way OpenAICompatProvider does, so this tests what APEXA will
    # actually send. An exact-match check (written for argo-proxy's id format)
    # reported every model "not served" against Argo native, whose listing uses
    # display names: `claudeopus55` is listed as `Claude Opus 5.5`.
    import re as _re
    def _norm(n: str) -> str:
        return _re.sub(r"[^a-z0-9]", "", (n or "").lower()).removeprefix("argo")
    by_norm = {_norm(x): x for x in served}
    for model in args.models:
        print(f"── {model}")
        sent = model if (not served or model in served) else by_norm.get(_norm(model))
        if sent is None:
            near = [x for x in served if _norm(model)[:6] in _norm(x)][:4]
            print(f"    SKIP: not served. similar: {near or 'none'}\n")
            results.append((model, False, "not served by this endpoint"))
            continue
        if sent != model:
            print(f"    (listed as {sent!r})")
        ok, detail = probe(client, sent, verbose=args.verbose)
        print(f"    {'PASS' if ok else 'FAIL'}: {detail}\n")
        results.append((model, ok, detail))

    print("=" * 64)
    for model, ok, detail in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {model:<16} {detail}")
    print("=" * 64)

    passed = [m for m, ok, _ in results if ok]
    if len(passed) == len(results):
        print("\nGate 0 PASSED — structured multi-turn tool calling works.")
        print("The refactor's foundation holds; APEXA can drop the text TOOL_CALL: protocol.")
        return 0
    if passed:
        print(f"\nGate 0 PARTIAL — works on: {', '.join(passed)}")
        print("Proceed, but pin APEXA's default model to a passing one.")
        return 1
    print("\nGate 0 FAILED — no model completed a multi-turn tool exchange.")
    print("STOP: do not migrate. Fall back to extending ArgoProvider directly.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
