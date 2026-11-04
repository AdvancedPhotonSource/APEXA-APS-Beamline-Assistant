#!/usr/bin/env python3
"""Qualify ALCF Inference Service models for APEXA — run this on the beamline server.

Answers one question per model: **can this model actually drive APEXA?**

ALCF's capability flags say what a model is *supposed* to do; this measures what it
*does*. That distinction matters twice over here: Minerva's models (``inkling-bf16``,
``nemotron-3-ultra``) carry no ``T`` flag but ALCF never states tool calling is
unsupported there — unlike Metis, where they do — so it is genuinely unknown until
probed. And a ``T`` flag only promises the model can emit a tool call; it says
nothing about whether it can chain them, which is what APEXA needs.

Four probes, in increasing order of what APEXA demands:

  1. reachable    the endpoint answers at all
  2. tool_call    turn 1 returns a structured tool_call (not prose about calling one)
  3. multi-turn   a ``{"role":"tool", "tool_call_id":...}`` result is accepted and used
  4. **chaining** the model calls tool A, reads A's RESULT, and uses it to choose the
                  arguments for tool B, then reports a value only reachable that way

Probe 4 is the discriminator. It mirrors APEXA's actual loop — `search_tools` →
`load_tools` → the real tool — and it is where models with a ``T`` flag commonly
fall over. A model that passes 1-3 but fails 4 will handle "list this directory" and
fail an FF-HEDM workflow.

Prerequisites (on the beamline server, ANL network):

    pip install openai globus_sdk
    # ALCF's helper — see docs.alcf.anl.gov/services/inference-endpoints
    python inference_auth_token.py authenticate

Then:

    python scripts/alcf_qualify_models.py                  # all candidates
    python scripts/alcf_qualify_models.py --preset alcf-minerva     # just inkling etc.
    python scripts/alcf_qualify_models.py --models openai/gpt-oss-120b -v

Non-always-hot models can take 10-15 minutes to cold start; --timeout defaults to
600 s for that reason. Exit code 0 if at least one model fully qualifies.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from openai import OpenAI
except ImportError:
    sys.exit("openai SDK not installed.  pip install openai")

from apexa_llm_endpoints import ALCF_CANDIDATES, PRESETS, EndpointRejected

# ── Probe 2/3 fixture: one tool whose answer cannot be guessed ───────────────
COUNT_TOOL = [{
    "type": "function",
    "function": {
        "name": "get_tool_count",
        "description": "Return how many analysis tools a beamline instrument has registered.",
        "parameters": {
            "type": "object",
            "properties": {"instrument": {"type": "string"}},
            "required": ["instrument"],
        },
    },
}]
SENTINEL = 61

# ── Probe 4 fixture: two tools that must be CHAINED ──────────────────────────
# Mirrors APEXA's real pattern: discover a tool name, then call that tool. The
# final number is reachable only by feeding tool A's output into tool B.
CHAIN_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "find_analysis_tool",
            "description": "Look up which analysis tool handles a given task. "
                           "Returns the exact tool name to use.",
            "parameters": {
                "type": "object",
                "properties": {"task": {"type": "string"}},
                "required": ["task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_analysis",
            "description": "Run a named analysis tool and return its numeric result. "
                           "The tool_name must come from find_analysis_tool.",
            "parameters": {
                "type": "object",
                "properties": {"tool_name": {"type": "string"}},
                "required": ["tool_name"],
            },
        },
    },
]
CHAIN_TOOL_NAME = "midas_auto_calibrate"     # what find_analysis_tool returns
CHAIN_RESULT = 907.42                        # what run_analysis returns for it


def _assistant_turn(msg):
    return {
        "role": "assistant",
        "content": msg.content or "",
        "tool_calls": [{
            "id": tc.id, "type": "function",
            "function": {"name": tc.function.name, "arguments": tc.function.arguments},
        } for tc in (msg.tool_calls or [])],
    }


def probe_multiturn(client, model, verbose=False):
    """Probes 2 + 3: emit a tool_call, accept a tool result, use it."""
    msgs = [
        {"role": "system", "content": "You are a terse assistant. Use the provided tools."},
        {"role": "user", "content": "How many analysis tools does APEXA have registered? Use the tool."},
    ]
    r1 = client.chat.completions.create(model=model, messages=msgs,
                                        tools=COUNT_TOOL, tool_choice="auto")
    m = r1.choices[0].message
    if not (m.tool_calls or []):
        return False, False, f"no tool_call in turn 1 (said: {(m.content or '')[:70]!r})"
    call = m.tool_calls[0]
    if not call.id:
        return True, False, "tool_call carries no id — results cannot be correlated"

    msgs += [_assistant_turn(m),
             {"role": "tool", "tool_call_id": call.id,
              "content": json.dumps({"tool_count": SENTINEL})}]
    r2 = client.chat.completions.create(model=model, messages=msgs, tools=COUNT_TOOL)
    final = (r2.choices[0].message.content or "").strip()
    if verbose:
        print(f"      multiturn final: {final[:110]!r}")
    if not final:
        return True, False, "empty content after tool result"
    if str(SENTINEL) not in final:
        return True, False, f"ignored the tool result: {final[:70]!r}"
    return True, True, "ok"


def probe_chaining(client, model, verbose=False):
    """Probe 4: use tool A's RESULT to choose tool B's arguments. The APEXA pattern."""
    msgs = [
        {"role": "system",
         "content": "You are a beamline assistant. Use tools. To run an analysis you must "
                    "FIRST find the correct tool name, THEN run it."},
        {"role": "user",
         "content": "Run the detector calibration analysis and tell me the numeric result."},
    ]
    saw_find = saw_run = False
    for _ in range(6):
        r = client.chat.completions.create(model=model, messages=msgs,
                                           tools=CHAIN_TOOLS, tool_choice="auto")
        m = r.choices[0].message
        calls = m.tool_calls or []
        if not calls:
            final = (m.content or "").strip()
            if verbose:
                print(f"      chain final: {final[:110]!r}")
            if not (saw_find and saw_run):
                return False, "stopped before chaining both tools"
            if str(CHAIN_RESULT) in final or "907" in final:
                return True, "ok"
            return False, f"chained but lost the value: {final[:70]!r}"

        msgs.append(_assistant_turn(m))
        for tc in calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            if name == "find_analysis_tool":
                saw_find = True
                out = {"tool_name": CHAIN_TOOL_NAME}
            elif name == "run_analysis":
                got = str(args.get("tool_name", ""))
                if CHAIN_TOOL_NAME not in got:
                    # Called tool B without using tool A's answer — the exact failure
                    # that breaks APEXA workflows.
                    return False, f"run_analysis called with {got!r}, not the name tool A returned"
                saw_run = True
                out = {"result": CHAIN_RESULT}
            else:
                out = {"error": f"unknown tool {name}"}
            msgs.append({"role": "tool", "tool_call_id": tc.id,
                         "content": json.dumps(out)})
    return False, "did not converge within 6 turns"


def qualify(client, model, verbose=False):
    row = {"model": model, "reachable": False, "tool_call": False,
           "multiturn": False, "chaining": False, "latency_s": None, "detail": ""}
    t0 = time.monotonic()
    try:
        tc_ok, mt_ok, detail = probe_multiturn(client, model, verbose)
        row.update(reachable=True, tool_call=tc_ok, multiturn=mt_ok, detail=detail)
    except Exception as e:
        row["detail"] = f"{type(e).__name__}: {str(e)[:110]}"
        row["latency_s"] = round(time.monotonic() - t0, 1)
        return row
    if mt_ok:
        try:
            ch_ok, ch_detail = probe_chaining(client, model, verbose)
            row["chaining"] = ch_ok
            if not ch_ok:
                row["detail"] = ch_detail
        except Exception as e:
            row["detail"] = f"chain {type(e).__name__}: {str(e)[:100]}"
    row["latency_s"] = round(time.monotonic() - t0, 1)
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--preset", default=None,
                    help="Restrict to one preset (alcf-sophia / alcf-minerva).")
    ap.add_argument("--models", nargs="+", default=None,
                    help="Explicit model ids (overrides the candidate table).")
    ap.add_argument("--api-key", default=None, help="Bearer token; else resolved per preset.")
    ap.add_argument("--timeout", type=float, default=600.0,
                    help="Per-request timeout. Non-always-hot models cold start in 10-15 min.")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    cands = list(ALCF_CANDIDATES)
    if args.preset:
        cands = [c for c in cands if c["preset"] == args.preset]
    if args.models:
        base = args.preset or "alcf-sophia"
        _known = {c["model"]: c for c in ALCF_CANDIDATES}
        cands = [{"preset": _known.get(m, {}).get("preset", base), "model": m,
                  "flags": _known.get(m, {}).get("flags", "?"),
                  "note": _known.get(m, {}).get("note", "")} for m in args.models]
    if not cands:
        print("No candidates selected.")
        return 2

    # One client per preset (they're different clusters / base URLs).
    clients: dict[str, object] = {}
    for preset in sorted({c["preset"] for c in cands}):
        ep = PRESETS[preset]()
        try:
            key = args.api_key or ep.resolve_key(os.environ.get("ANL_USERNAME", ""))
        except EndpointRejected as e:
            print(f"  {preset}: credential unavailable — {e}")
            return 2
        clients[preset] = OpenAI(base_url=ep.base_url, api_key=key,
                                 timeout=args.timeout, max_retries=0)
        print(f"{preset:<14} {ep.base_url}")
    print(f"\nProbing {len(cands)} model(s). Cold (non-H) models may take 10-15 min each.\n")

    rows = []
    for c in cands:
        print(f"── {c['model']}   [{c['flags']}]  {c['note']}")
        row = qualify(clients[c["preset"]], c["model"], args.verbose)
        row["flags"] = c["flags"]
        row["preset"] = c["preset"]
        rows.append(row)
        marks = "".join("✓" if row[k] else "·"
                        for k in ("reachable", "tool_call", "multiturn", "chaining"))
        verdict = "QUALIFIED" if row["chaining"] else "unusable"
        print(f"    {marks}  {verdict}  ({row['latency_s']}s)"
              f"{'  — ' + row['detail'] if row['detail'] and not row['chaining'] else ''}\n")

    print("=" * 96)
    print(f"{'model':<44}{'flags':<10}{'rch':<5}{'tool':<6}{'multi':<7}{'chain':<7}{'sec':<7}")
    print("-" * 96)
    for r in sorted(rows, key=lambda r: (not r["chaining"], r["model"])):
        y = lambda b: " ✓  " if b else " ·  "          # noqa: E731
        print(f"{r['model']:<44}{r['flags']:<10}{y(r['reachable']):<5}{y(r['tool_call']):<6}"
              f"{y(r['multiturn']):<7}{y(r['chaining']):<7}{str(r['latency_s']):<7}")
    print("=" * 96)

    good = [r for r in rows if r["chaining"]]
    if good:
        fastest = min(good, key=lambda r: r["latency_s"] or 1e9)
        print(f"\n{len(good)}/{len(rows)} models can drive APEXA.")
        print(f"Fastest qualified: {fastest['model']} ({fastest['latency_s']}s)")
        print("\nTo use one:")
        print(f"  APEXA_LLM_MODE=native")
        print(f"  APEXA_LLM_PRESET={good[0]['preset']}")
        print(f"  ARGO_MODEL={good[0]['model']}")
        from apexa_llm_endpoints import _default_token_cmd
        print(f'  APEXA_LLM_TOKEN_CMD="{_default_token_cmd()}"')
        print("\nNext: run benchmark/eval_harness.py against a qualified model to compare "
              "task accuracy with your existing Argo numbers — qualification only proves "
              "the loop works, not that the science is right.")
        return 0

    print("\nNo model completed the chaining probe. APEXA cannot run on these endpoints.")
    print("Models that passed multi-turn but failed chaining will handle single-tool "
          "requests and fail real workflows — do not deploy them.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
