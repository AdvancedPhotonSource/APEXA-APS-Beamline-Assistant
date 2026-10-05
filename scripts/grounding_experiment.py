#!/usr/bin/env python3
"""Grounding failure in chained tool calls: a controlled experiment.

Motivating observation (ALCF Sophia, 2026-08-19). ``Llama-4-Scout-17B-16E`` emitted a
valid ``tool_call``, correctly consumed a ``{"role":"tool"}`` result, and then invoked
the dependent tool with a tool name it had **invented** rather than the one the first
tool returned. It did not garble the name; it produced a plausible substitute. That is
a *grounding* failure -- generating from prior instead of attending to context -- and
it is invisible to any benchmark that scores single tool calls or grades transcripts.

This script turns that anecdote into a measurement.

The task
--------
Two tools. ``find_analysis_tool(task)`` returns the exact name of the tool to run;
``run_analysis(tool_name)`` returns a number only for the correct name. The model must
copy a string out of a tool result into the arguments of the next call. Nothing else.

The critical design choice is that **the returned name is drawn at random per trial**
from a pool of equally plausible names. A model cannot succeed from its prior, from
memorisation, or from guessing the "obvious" MIDAS tool: the only path to the answer
is attending to the tool result. Fabrication is therefore unambiguous -- any other
name in ``run_analysis`` is a name the model made up.

Hypotheses
----------
H1 (distance)     fabrication rises with token distance between the tool result and
                  the dependent call.
H2 (distractors)  fabrication rises with the number of plausible tool names in context.
H3 (intervention) shrinking the tool surface -- APEXA's progressive disclosure, 81
                  schemas down to ~11 -- reduces fabrication.

H3 is the one that matters for the paper: it is an intervention with a measured
effect, not just an observation, and the mechanism already exists
(``apexa_toolsurface.py``) but has never been evaluated.

Usage
-----
    python scripts/grounding_experiment.py --self-test          # offline, no network
    python scripts/grounding_experiment.py --preset alcf-sophia \\
        --models openai/gpt-oss-120b meta-llama/Llama-4-Scout-17B-16E-Instruct \\
        --trials 20
    python scripts/grounding_experiment.py --preset argo-proxy \\
        --models argo:claude-opus-5 --trials 20 --out results/grounding

Results are appended as JSONL (one row per trial) plus a printed summary with
Wilson confidence intervals. The gateway exposes no seed, so every condition is
reported across N trials rather than as a point estimate.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# ── the candidate pool ───────────────────────────────────────────────────────
# Plausible, MIDAS-flavoured, and mutually interchangeable: no name is a better a
# priori answer than any other, so a correct call can only come from the tool result.
CANDIDATE_TOOLS: List[str] = [
    "midas_autocal_ceria", "midas_autocal_lab6", "midas_geom_refine",
    "midas_ring_thresh", "midas_integrate_series", "midas_integrate_caked",
    "midas_peaksearch_ff", "midas_peaksearch_nf", "midas_index_grains",
    "midas_refine_strain", "midas_grain_centroids", "midas_stress_tensor",
    "midas_mask_builder", "midas_dark_subtract", "midas_zarr_convert",
    "midas_pf_invert", "midas_odf_fit", "midas_pdf_transform",
    "midas_dfxm_forward", "midas_slip_analysis", "midas_lattice_fit",
    "midas_detector_tilt", "midas_wedge_solve", "midas_omega_window",
    "midas_spot_consolidate", "midas_layer_merge", "midas_hkl_generate",
    "midas_calibrant_screen", "midas_beam_center", "midas_distortion_map",
]

# Filler that is topical but carries no tool names, so distance can be varied
# without also varying distractor count (H1 and H2 stay orthogonal).
_FILLER = (
    "Prior run notes: the detector was operated at 63 keV with a sample-to-detector "
    "distance near 900 mm. Frames were collected in a continuous rotation series with "
    "0.25 degree steps. Ambient temperature was stable to within 0.4 K across the "
    "acquisition. The beam-defining slits were unchanged from the preceding alignment. "
)


@dataclass
class Trial:
    model: str
    preset: str
    transport: str                  # structured | text
    condition: str
    distance_tokens: int
    n_distractors: int
    surface: str
    trial: int
    target_tool: str
    called_tool: Optional[str] = None
    outcome: str = "error"          # grounded | fabricated | no_chain | error
    detail: str = ""
    elapsed_s: float = 0.0


def _tool_schemas(names: List[str]) -> List[Dict[str, Any]]:
    """`find_analysis_tool` + `run_analysis` + N distractor schemas.

    Distractors are real schemas, not just strings in prose: they occupy the same
    channel as the answer, which is what makes H2 a test of tool-surface size rather
    than of prompt length.
    """
    tools = [
        {"type": "function", "function": {
            "name": "find_analysis_tool",
            "description": "Look up which analysis tool handles a task. Returns the "
                           "exact tool name that must be passed to run_analysis.",
            "parameters": {"type": "object",
                           "properties": {"task": {"type": "string"}},
                           "required": ["task"]}}},
        {"type": "function", "function": {
            "name": "run_analysis",
            "description": "Run a named analysis tool and return its numeric result. "
                           "tool_name MUST be the exact string returned by "
                           "find_analysis_tool.",
            "parameters": {"type": "object",
                           "properties": {"tool_name": {"type": "string"}},
                           "required": ["tool_name"]}}},
    ]
    for n in names:
        tools.append({"type": "function", "function": {
            "name": n,
            "description": f"MIDAS analysis routine {n.replace('midas_', '').replace('_', ' ')}.",
            "parameters": {"type": "object",
                           "properties": {"param_file": {"type": "string"}}}}})
    return tools


def _assistant_turn(msg) -> Dict[str, Any]:
    return {"role": "assistant", "content": msg.content or "",
            "tool_calls": [{"id": tc.id, "type": "function",
                            "function": {"name": tc.function.name,
                                         "arguments": tc.function.arguments}}
                           for tc in (msg.tool_calls or [])]}


def run_trial(client, model: str, target: str, distractors: List[str],
              distance_tokens: int, max_turns: int = 6) -> tuple[str, Optional[str], str]:
    """One trial. Returns (outcome, called_tool, detail)."""
    pad = ""
    if distance_tokens:
        # ~4 chars/token is close enough for a controlled sweep.
        reps = max(1, (distance_tokens * 4) // len(_FILLER))
        pad = "\n\n" + (_FILLER * reps)

    msgs: List[Dict[str, Any]] = [
        {"role": "system",
         "content": "You are a beamline analysis assistant. To run an analysis you must "
                    "FIRST call find_analysis_tool to obtain the exact tool name, THEN "
                    "call run_analysis with that exact name. Never guess the name."},
        {"role": "user",
         "content": "Run the detector calibration analysis and report the numeric result."},
    ]
    tools = _tool_schemas(distractors)
    saw_find = False

    for _ in range(max_turns):
        r = client.chat.completions.create(model=model, messages=msgs,
                                           tools=tools, tool_choice="auto")
        m = r.choices[0].message
        calls = m.tool_calls or []
        if not calls:
            return ("no_chain", None,
                    f"stopped after {'find' if saw_find else 'no'} call: "
                    f"{(m.content or '')[:70]!r}")
        msgs.append(_assistant_turn(m))
        for tc in calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            if name == "find_analysis_tool":
                saw_find = True
                # The pad rides on the tool RESULT, so it sits between the answer and
                # the model's next decision -- exactly the distance H1 is about.
                out = {"tool_name": target, "notes": pad} if pad else {"tool_name": target}
                msgs.append({"role": "tool", "tool_call_id": tc.id,
                             "content": json.dumps(out)})
            elif name == "run_analysis":
                got = str(args.get("tool_name", "")).strip()
                if got == target:
                    return "grounded", got, "ok"
                return "fabricated", got, f"expected {target!r}, called {got!r}"
            else:
                # Called a distractor directly, skipping the lookup: still ungrounded.
                if not saw_find:
                    return "fabricated", name, f"invoked {name!r} without looking it up"
                msgs.append({"role": "tool", "tool_call_id": tc.id,
                             "content": json.dumps({"error": "call run_analysis instead"})})
    return "no_chain", None, "did not converge"


# ── TEXT TRANSPORT (APEXA's legacy `argo` mode) ──────────────────────────────
# In APEXA's `argo` path the gateway flattens every message to {role, content:str},
# so tools cannot be sent structurally. Tools are instead DESCRIBED in the system
# prompt in the TOOL_CALL:/ARGUMENTS: format, the model emits calls as TEXT, APEXA
# parses that text, and the tool RESULT is fed back as a PLAIN-TEXT turn. This is
# the fabrication-prone path the paper compares against the structured transport.
#
# PARSER ROUTE (documented per the task): we IMPORT AgentRunner and reuse its REAL
# `_parse_text_tool_calls` — the exact method the legacy text path runs in
# production (Format A: `TOOL_CALL:\s*([A-Za-z_]\w*)` + `ARGUMENTS:` + a
# brace-balanced JSON scan via `_extract_balanced_json`; plus Format B key:value
# and Format C <tool_use>). Importing the provider stack does NO network at import
# time, so offline --self-test still works. We construct one AgentRunner with a
# no-op execute fn (Format-A parsing never touches self._execute). Reusing the
# genuine parser — rather than a re-implementation — is what makes this a faithful
# APEXA-transport comparison and avoids inflating the fabrication rate with a
# weaker parser.
_text_parser = None


def _parse_tool_calls_text(text: str) -> List[tuple]:
    """(name, args_dict) list from model text, via APEXA's real Format-A parser."""
    global _text_parser
    if _text_parser is None:
        from apexa_agents import AgentRunner
        _text_parser = AgentRunner(lambda *a, **k: None)
    return [(c.name, c.arguments) for c in _text_parser._parse_text_tool_calls(text)]


def _text_preamble(distractors: List[str]) -> str:
    """Task-scoped mirror of APEXA's `_TOOL_PREAMBLE` format block: describe the
    available tools in prose and quote the exact TOOL_CALL:/ARGUMENTS: format the
    model must emit (no structured tools are sent)."""
    lines = "\n".join(f"- {n}(param_file): MIDAS analysis routine." for n in distractors)
    return (
        "You do NOT have structured tool calling. To call a tool you MUST emit it as\n"
        "TEXT in EXACTLY this format (APEXA's TOOL_CALL: protocol):\n\n"
        "TOOL_CALL: tool_name\n"
        'ARGUMENTS: {"param": "value"}\n\n'
        "Available tools:\n"
        "- find_analysis_tool(task): returns the EXACT tool name to pass to run_analysis.\n"
        "- run_analysis(tool_name): runs a named analysis tool and returns a number.\n"
        "  tool_name MUST be the exact string returned by find_analysis_tool.\n"
        f"{lines}\n\n"
        "Emit ONE TOOL_CALL at a time and wait for its [Tool Result] before the next."
    )


def run_trial_text(client, model: str, target: str, distractors: List[str],
                   distance_tokens: int, max_turns: int = 6
                   ) -> tuple[str, Optional[str], str]:
    """One trial over APEXA's TEXT transport. Mirrors run_trial's outcome
    semantics (grounded | fabricated | no_chain | error) but sends NO tools=/
    tool_choice, parses tool calls from model TEXT with APEXA's real parser, and
    feeds tool results back as PLAIN-TEXT turns."""
    pad = ""
    if distance_tokens:
        reps = max(1, (distance_tokens * 4) // len(_FILLER))
        pad = "\n\n" + (_FILLER * reps)

    msgs: List[Dict[str, Any]] = [
        {"role": "system",
         "content": "You are a beamline analysis assistant. To run an analysis you must "
                    "FIRST call find_analysis_tool to obtain the exact tool name, THEN "
                    "call run_analysis with that exact name. Never guess the name.\n\n"
                    + _text_preamble(distractors)},
        {"role": "user",
         "content": "Run the detector calibration analysis and report the numeric result."},
    ]
    saw_find = False
    no_call_streak = 0

    for _ in range(max_turns):
        # The whole point: NO tools=, NO tool_choice — the backend can't carry
        # structured calls, so the model must use the text protocol.
        r = client.chat.completions.create(model=model, messages=msgs)
        content = r.choices[0].message.content or ""
        msgs.append({"role": "assistant", "content": content})
        calls = _parse_tool_calls_text(content)
        if not calls:
            no_call_streak += 1
            if no_call_streak >= 2:
                return ("no_chain", None,
                        f"no parseable TOOL_CALL for 2 turns; last: {content[:70]!r}")
            # Give the drifting model one more turn to emit the format (neutral
            # nudge — never reveals the target name).
            msgs.append({"role": "user",
                         "content": "You have not emitted a TOOL_CALL yet. Respond using "
                                    "the required TOOL_CALL:/ARGUMENTS: format."})
            continue
        no_call_streak = 0
        for name, args in calls:
            if not isinstance(args, dict):
                args = {}
            if name == "find_analysis_tool":
                saw_find = True
                out = {"tool_name": target, "notes": pad} if pad else {"tool_name": target}
                # Plain-text result turn — NOT a structured {"role":"tool"} message.
                msgs.append({"role": "user",
                             "content": f"[Tool Result for find_analysis_tool]: {json.dumps(out)}"})
            elif name == "run_analysis":
                got = str(args.get("tool_name", "")).strip()
                if got == target:
                    return "grounded", got, "ok"
                return "fabricated", got, f"expected {target!r}, called {got!r}"
            else:
                if not saw_find:
                    return "fabricated", name, f"invoked {name!r} without looking it up"
                msgs.append({"role": "user",
                             "content": f"[Tool Result for {name}]: "
                                        + json.dumps({"error": "call run_analysis instead"})})
    return "no_chain", None, "did not converge"


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval -- behaves at 0/N and N/N, unlike the normal approx."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


# Conditions. `full` vs `disclosed` is H3: APEXA's progressive disclosure takes the
# surface from ~81 schemas to ~11, so `disclosed` is that intervention.
CONDITIONS = [
    # name            distance   distractors  surface
    ("baseline",             0,      8,  "disclosed"),
    ("distance_2k",       2000,      8,  "disclosed"),
    ("distance_8k",       8000,      8,  "disclosed"),
    ("distractors_28",       0,     28,  "full"),
    ("distance_8k_full",  8000,     28,  "full"),
]


def summarise(rows: List[Trial]) -> None:
    print("\n" + "=" * 104)
    print(f"{'model':<42}{'condition':<17}{'ok':>4}{'fab':>5}{'none':>6}{'ERR':>5}"
          f"{'n':>4}   {'fabrication rate [95% CI]':<26}")
    print("-" * 104)
    for model in dict.fromkeys(r.model for r in rows):
        for cond, *_ in CONDITIONS:
            sub = [r for r in rows if r.model == model and r.condition == cond]
            if not sub:
                continue
            g  = sum(r.outcome == "grounded" for r in sub)
            f  = sum(r.outcome == "fabricated" for r in sub)
            n0 = sum(r.outcome == "no_chain" for r in sub)
            er = sum(r.outcome == "error" for r in sub)
            # Errors are transport failures (502/408 from the gateway), not model
            # behaviour. They must be EXCLUDED from the denominator and shown
            # separately: a cell where every trial errored is not a 0% fabrication
            # rate, it is no measurement at all. Reporting those identically is the
            # exact failure this paper is about.
            n = g + f + n0
            if n == 0:
                print(f"{model:<42}{cond:<17}{g:>4}{f:>5}{n0:>6}{er:>5}{n:>4}   "
                      f"{'NO DATA - all trials errored':<26}")
                continue
            lo, hi = wilson(f, n)
            flag = "  <-- high error rate" if er > n else ""
            print(f"{model:<42}{cond:<17}{g:>4}{f:>5}{n0:>6}{er:>5}{n:>4}   "
                  f"{f/n:>5.2f} [{lo:.2f},{hi:.2f}]{flag}")
    print("=" * 104)
    tot_err = sum(r.outcome == "error" for r in rows)
    if tot_err:
        print(f"NOTE: {tot_err}/{len(rows)} trials errored (gateway 502/408, cold start). "
              f"Excluded from rates; re-run those cells when the model is warm.")
    print("H1 distance: compare baseline -> distance_2k -> distance_8k")
    print("H2 distractors: compare baseline (8) -> distractors_28")
    print("H3 intervention: compare distance_8k_full -> distance_8k (disclosed surface)")


def _cell(rows: List[dict]) -> str:
    """Fabrication-rate cell for a set of trial dicts, with the SAME error-exclusion
    and Wilson-CI rules as summarise: errors excluded from the denominator, an
    all-errored cell rendered as NO DATA (never 0%), missing cell as a dash."""
    if not rows:
        return f"{'—':>22}"
    g  = sum(r["outcome"] == "grounded" for r in rows)
    f  = sum(r["outcome"] == "fabricated" for r in rows)
    n0 = sum(r["outcome"] == "no_chain" for r in rows)
    er = sum(r["outcome"] == "error" for r in rows)
    n = g + f + n0
    if n == 0:
        return f"{'NO DATA (all err)':>22}"
    lo, hi = wilson(f, n)
    flag = "!" if er > n else " "
    return f"{f/n:>5.2f} [{lo:.2f},{hi:.2f}] n={n:<2}{flag}"


def compare_transports(paths: List[str]) -> int:
    """Offline: load result JSONL(s) and print the consolidated structured-vs-text
    comparison, one line per (model, condition)."""
    rows: List[dict] = []
    for p in paths:
        fp = Path(p)
        if not fp.exists():
            print(f"missing: {p}")
            return 2
        with open(fp) as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    if not rows:
        print("no rows loaded")
        return 2

    transports = [t for t in ("structured", "text")
                  if any(r.get("transport") == t for r in rows)]
    models = list(dict.fromkeys(r["model"] for r in rows))
    cond_order = [c[0] for c in CONDITIONS]
    conds_present = [c for c in cond_order
                     if any(r["condition"] == c for r in rows)]

    print("\n" + "=" * 108)
    print("CONSOLIDATED TRANSPORT ABLATION — fabrication rate [95% Wilson CI] "
          "(errors excluded from denominator)")
    print("-" * 108)
    hdr = f"{'model':<26}{'condition':<18}"
    for t in transports:
        hdr += f"{t:>24}"
    print(hdr)
    print("-" * 108)
    for model in models:
        for cond in conds_present:
            line = f"{model:<26}{cond:<18}"
            for t in transports:
                sub = [r for r in rows if r["model"] == model
                       and r["condition"] == cond and r.get("transport") == t]
                line += f"{_cell(sub):>24}"
            print(line)
    print("=" * 108)
    tot_err = sum(r["outcome"] == "error" for r in rows)
    if tot_err:
        print(f"NOTE: {tot_err}/{len(rows)} trials errored (excluded from rates); "
              f"'!' marks a cell with more errors than measured trials — warm re-run.")
    print("thesis: if text fab-rate > structured fab-rate on the SAME model+backend, "
          "fabrication is a property of the transport, not the model.")
    return 0


def self_test() -> int:
    """Offline check of the machinery: a scripted client, no network."""
    class _FakeFn:
        def __init__(s, n, a): s.name, s.arguments = n, a
    class _FakeTC:
        def __init__(s, n, a): s.id, s.type, s.function = "c1", "function", _FakeFn(n, a)
    class _Msg:
        def __init__(s, tcs, c=""): s.tool_calls, s.content = tcs, c
    class _Choice:
        def __init__(s, m): s.message = m
    class _Resp:
        def __init__(s, m): s.choices = [_Choice(m)]

    class _Client:
        """Copies the name back correctly, or fabricates, per script."""
        def __init__(s, mode): s.mode, s.n = mode, 0
        class _C:
            def __init__(s, o): s.o = o
            @property
            def completions(s): return s
            def create(s, model, messages, tools, tool_choice=None):
                s.o.n += 1
                if s.o.n == 1:
                    return _Resp(_Msg([_FakeTC("find_analysis_tool", '{"task":"calib"}')]))
                got = [m for m in messages if m.get("role") == "tool"][-1]["content"]
                real = json.loads(got)["tool_name"]
                use = real if s.o.mode == "grounded" else "midas_detector_calibration"
                return _Resp(_Msg([_FakeTC("run_analysis",
                                           json.dumps({"tool_name": use}))]))
        @property
        def chat(self): return _Client._C(self)

    ok = True
    for mode, want in (("grounded", "grounded"), ("fabricate", "fabricated")):
        got, called, detail = run_trial(_Client(mode), "m", "midas_ring_thresh",
                                        CANDIDATE_TOOLS[:8], 0)
        status = "PASS" if got == want else "FAIL"
        ok &= got == want
        print(f"  {status}  structured {mode:<10} -> outcome={got} called={called}")

    # ── TEXT transport: fake client returns TEXT in the TOOL_CALL:/ARGUMENTS:
    # format; prove APEXA's real parser classifies grounded vs fabricated. No
    # tools=/tool_choice is passed (signature is create(model, messages)).
    class _TextClient:
        def __init__(s, mode): s.mode, s.n = mode, 0
        class _C:
            def __init__(s, o): s.o = o
            @property
            def completions(s): return s
            def create(s, model, messages):
                s.o.n += 1
                if s.o.n == 1:
                    return _Resp(_Msg(None,
                        'TOOL_CALL: find_analysis_tool\nARGUMENTS: {"task": "calib"}'))
                # Read the plain-text tool-result turn fed back by run_trial_text.
                res = [m for m in messages
                       if "[Tool Result for find_analysis_tool]" in (m.get("content") or "")][-1]
                c = res["content"]
                real = json.loads(c[c.index("{"):])["tool_name"]
                use = real if s.o.mode == "grounded" else "midas_detector_calibration"
                return _Resp(_Msg(None,
                    f'TOOL_CALL: run_analysis\nARGUMENTS: {json.dumps({"tool_name": use})}'))
        @property
        def chat(self): return _TextClient._C(self)

    for mode, want in (("grounded", "grounded"), ("fabricate", "fabricated")):
        got, called, detail = run_trial_text(_TextClient(mode), "m", "midas_ring_thresh",
                                             CANDIDATE_TOOLS[:8], 0)
        status = "PASS" if got == want else "FAIL"
        ok &= got == want
        print(f"  {status}  text       {mode:<10} -> outcome={got} called={called}")

    lo, hi = wilson(0, 20)
    print(f"  {'PASS' if hi < 0.2 else 'FAIL'}  wilson(0/20) = [{lo:.3f}, {hi:.3f}]")
    print(f"  PASS  pool={len(CANDIDATE_TOOLS)} candidates, {len(CONDITIONS)} conditions")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true", help="offline machinery check")
    ap.add_argument("--preset", default="alcf-sophia")
    ap.add_argument("--models", nargs="+", default=["openai/gpt-oss-120b"])
    ap.add_argument("--trials", type=int, default=20)
    ap.add_argument("--conditions", nargs="+", default=None)
    ap.add_argument("--transport", choices=["structured", "text"], default="structured",
                    help="structured = real OpenAI tool_calls (default, existing "
                         "behavior); text = APEXA legacy `argo` TOOL_CALL: text protocol")
    ap.add_argument("--out", default="benchmark/results/grounding")
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--seed", type=int, default=0, help="seeds TARGET SELECTION only")
    ap.add_argument("--compare", nargs="+", default=None, metavar="JSONL",
                    help="offline: load these result JSONL files and print the "
                         "consolidated structured-vs-text comparison, then exit")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    if args.compare:
        return compare_transports(args.compare)

    from openai import OpenAI
    from apexa_llm_endpoints import PRESETS, EndpointRejected

    ep = PRESETS[args.preset]()
    try:
        key = ep.resolve_key(os.environ.get("ANL_USERNAME", ""))
    except EndpointRejected as e:
        print(f"credential unavailable: {e}")
        return 2
    client = OpenAI(base_url=ep.base_url, api_key=key,
                    timeout=args.timeout, max_retries=0)

    conds = [c for c in CONDITIONS if not args.conditions or c[0] in args.conditions]
    outdir = Path(args.out); outdir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path = outdir / f"grounding_{args.preset}_{args.transport}_{stamp}.jsonl"
    trial_fn = run_trial_text if args.transport == "text" else run_trial

    rng = random.Random(args.seed)
    rows: List[Trial] = []
    print(f"{ep.name} -> {ep.base_url}  [transport={args.transport}]")
    print(f"{len(args.models)} model(s) x {len(conds)} condition(s) x {args.trials} trials\n")

    with open(path, "w") as fh:
        for model in args.models:
            for cond, dist, ndist, surface in conds:
                print(f"── {model}  [{cond}]", flush=True)
                for i in range(args.trials):
                    # Fresh target per trial: defeats priors and memorisation.
                    target = rng.choice(CANDIDATE_TOOLS)
                    pool = [t for t in CANDIDATE_TOOLS if t != target]
                    distractors = [target] + rng.sample(pool, max(0, ndist - 1))
                    rng.shuffle(distractors)
                    t0 = time.monotonic()
                    try:
                        outcome, called, detail = trial_fn(
                            client, model, target, distractors, dist)
                    except Exception as e:
                        outcome, called, detail = "error", None, f"{type(e).__name__}: {e}"[:150]
                    row = Trial(model=model, preset=args.preset, transport=args.transport,
                                condition=cond,
                                distance_tokens=dist, n_distractors=ndist,
                                surface=surface, trial=i, target_tool=target,
                                called_tool=called, outcome=outcome, detail=detail,
                                elapsed_s=round(time.monotonic() - t0, 2))
                    rows.append(row)
                    fh.write(json.dumps(asdict(row)) + "\n"); fh.flush()
                    print({"grounded": "·", "fabricated": "F",
                           "no_chain": "-", "error": "!"}.get(outcome, "?"),
                          end="", flush=True)
                print()

    summarise(rows)
    print(f"\nrows -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
