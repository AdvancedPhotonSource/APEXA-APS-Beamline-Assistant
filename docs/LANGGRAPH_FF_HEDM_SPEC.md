# LangGraph FF-HEDM Workflow Graph — Design Spec

> Historical FF-only design. The current shared runtime is documented in [SCIENTIFIC_WORKFLOWS.md](SCIENTIFIC_WORKFLOWS.md). Ordinary chat no longer resumes legacy FF gates; existing checkpoints are preserved for inspected migration.

**Status:** proposal for review (no code written yet)
**Author:** design draft for Pawan Tripathi
**Scope:** Tier-1 harness change — replace the open-ended agent loop for FF-HEDM
setup (calibrate → in-plane tx → reconstruct) with a stateful, checkpointed
LangGraph state machine that has human-in-the-loop gates at the handbook's
decision points.
**Reference:** MIDAS `FF_HEDM_Handbook.md`; APEXA `.agents/skills/midas-ff-hedm/SKILL.md`;
memory `project_au_inplane_calibration`.

---

## 1. Why — what actually went wrong

The beamline transcript showed three failures, and all three trace to **one root
cause**: a deterministic 12-gate laboratory procedure is being run as an
open-ended LLM agent loop.

| Observed failure | Root cause |
|---|---|
| `midas_auto_calibrate` fired **3×** on the same CeO2 file | No memory of "calibration already done for this input" — each loop iteration re-decides from scratch. No idempotency key. |
| No prompt when `cali/` held several calibrant files; no folders proposed | Per-agent prompts **forbid** asking (`CalibrationAgent` system prompt: "no confirmations needed / do NOT ask"). The loop cannot pause for a human. |
| Steps ran out of order; no dark-verify / ring-overlay / strain gate | The handbook's ordering + gates live only as prose in a skill file. Nothing **enforces** them at runtime. |

The Argo Gateway constraint makes this worse, not better: Argo strips native
`tool_calls`, so today the model plans the *sequence* in free text and we regex it
out. Free-text planning is exactly where "no logical sequence" comes from.

**The fix is to move sequencing out of the LLM and into code.** A LangGraph graph
encodes the handbook's ordering as edges, its decision points as interrupt nodes,
and its "already did this" checks as state — while still calling the *same* MCP
tools APEXA already has. The LLM's job shrinks to what it's good at: reading a
param file, judging a residual, extracting an energy. The graph owns the plan.

---

## 2. What LangGraph buys us (and what it doesn't)

**Buys:**
- **Deterministic ordering** — calibrate cannot run after reconstruct; reconstruct
  cannot start until calibration state is populated. Edges, not prompts.
- **Human-in-the-loop** — `interrupt()` pauses the graph, surfaces a question, and
  resumes exactly where it stopped when the user answers. This is the "it should
  have asked for the calibration files" fix, first-class.
- **Checkpoint / resume** — graph state is persisted per thread. A killed CLI (or
  the Ctrl-C abort we just shipped) resumes mid-workflow instead of restarting.
- **Idempotency** — "already calibrated `X` → skip" is a state lookup + a
  conditional edge, not an LLM judgment call.
- **Observability** — every node transition is a checkpoint; the run is a replayable
  trace, not a scroll of free text.

**Doesn't buy / non-goals:**
- Not a replacement for the orchestrator's general Q&A / one-off tool calls. The
  graph handles *structured FF-HEDM workflows only*; everything else stays on the
  existing `process()` path.
- Not a rewrite of the MCP servers. Tools are unchanged; nodes call them via the
  existing `execute_tool_call`.
- Not dependent on native tool-calling. Nodes are plain Python; the LLM is invoked
  through the existing `ArgoProvider` for *extraction/judgment only*, never for tool
  dispatch.

---

## 3. Where it plugs in

Today (`apexa_agents.py`):

```
run_query()  →  OrchestratorAgent.process()
                   ├─ self._mode == "single"  → _process_single_loop()
                   └─ else                     → keyword-routed specialist agents
```

Proposed — a **third mode**, selected the same way the single-loop mode already is
(env flag + a routing predicate):

```
run_query()  →  OrchestratorAgent.process()
                   ├─ _mode == "graph" AND _is_ff_hedm_workflow(query, state)
                   │        → FFHEDMWorkflow.step(query)        # NEW
                   ├─ _mode == "single"  → _process_single_loop()
                   └─ else                → keyword-routed specialists
```

Selection rules:
- **Flag:** `APEXA_WORKFLOW_MODE=graph` (default `off` during soak). Coexists with
  `APEXA_AGENT_MODE=single`.
- **Entry predicate `_is_ff_hedm_workflow()`** — true when the user asks to
  calibrate/reconstruct FF-HEDM *and* no graph is mid-flight, OR when a graph is
  already paused on an interrupt (any input resumes it — see §6).
- Everything else falls through to the current paths untouched. This is additive;
  nothing is deleted until the graph soaks at the beamline (same discipline as the
  single-loop cutover in memory `project_single_loop_agent`).

`run_query` and all three UIs (CLI, web, desktop) are **unchanged** — they already
call `process()` and render its returned string. An interrupt is just a returned
string that happens to be a question (§6).

---

## 4. Typed state

State is a `TypedDict` (LangGraph's native state type; Pydantic optional). It is the
single source of truth the handbook's gates read and write — this is what today's
loop lacks.

```python
class FFHEDMState(TypedDict, total=False):
    # ---- provenance / discovery ----
    working_dir: str                 # experiment root the user pointed at
    calibrant_files: list[str]       # discovered candidates in cali/
    chosen_calibrant: str            # user- or auto-selected (interrupt gate 1)
    sample_dirs: list[str]           # real sample folders (NOT Au_FF_box — see memory)
    inplane_dir: str                 # Au single-crystal scan for tx calibration

    # ---- geometry (populated by calibration, consumed by recon) ----
    energy_kev: float                # from INSTRUMENT metadata, not filename (gate 4)
    wavelength_ang: float
    lsd_um: float                    # from calibrant fit, not DetZ (gate 5)
    beam_center: tuple[float, float]
    tilts: dict                      # tx, ty, tz  (tx from in-plane step, not powder)
    omega_sign: int                  # +1 / -1, aero→negate else ask (gate 1)
    skip_frame: int                  # GE far-field only (gate 3)

    # ---- output discipline ----
    output_dirs: dict[str, str]      # {calib, inplane, recon} — proposed then confirmed
    param_files: dict[str, str]      # per-step Parameters.txt actually used

    # ---- per-step status + idempotency ----
    done: dict[str, dict]            # step -> {input_hash, output_path, ts, status}
    #   done["calibrate"] = {"input_hash": "...", "output": "refined_..._CeO2.txt",
    #                        "residual_px": 0.31, "status": "ok"}

    # ---- gate results (for audit + resume) ----
    gate_log: list[dict]             # each: {gate, decision, by: user|auto, note}

    # ---- HITL plumbing ----
    pending_question: str            # set when a node calls interrupt()
    thread_id: str                   # == APEXA session name (ties to §9)
```

Two design rules on the state:
1. **`done[step].input_hash`** is the idempotency key — a stable hash of
   (tool, resolved input file(s), key params). A node consults it before running;
   the duplicate-calibration bug becomes structurally impossible (§8).
2. **Geometry is written once by the step that owns it** and read by everything
   downstream. `lsd_um` comes from the calibrant node; `tilts["tx"]` comes from the
   in-plane node; reconstruct only *reads*. No re-derivation, no drift.

---

## 5. The graph — nodes, edges, gates

Nodes are of three kinds: **action** (calls an MCP tool), **gate** (calls
`interrupt()` for a human decision), and **judgment** (calls the LLM to
read/classify, no side effects).

```
                       ┌──────────────┐
             START ───►│  discover     │  scan working_dir: cali/, samples,
                       │  (judgment)   │  Au_FF_box, param files → populate state
                       └──────┬────────┘
                              ▼
                    ┌───────────────────┐   GATE 1 (interrupt):
                    │ select_calibrant  │   >1 calibrant in cali/? ask which.
                    │      (gate)       │   1 file → auto, log, no prompt.
                    └─────────┬─────────┘
                              ▼
                    ┌───────────────────┐   GATE 2 (interrupt):
                    │ propose_folders   │   propose {calib,inplane,recon} output
                    │      (gate)       │   dirs → user confirms / edits / accepts.
                    └─────────┬─────────┘
                              ▼
                    ┌───────────────────┐   GATE 3+4 (judgment→interrupt if unsure):
                    │ resolve_geometry  │   ω-sign (aero→-1 else ASK), energy from
                    │  (judgment/gate)  │   INSTRUMENT not filename, SkipFrame if GE.
                    └─────────┬─────────┘
                              ▼
                  ┌─────────────────────┐   idempotency: input_hash in done? →
                  │  calibrate           │   skip to verify. else midas_auto_calibrate
                  │  (action, guarded)   │   into output_dirs["calib"].
                  └──────────┬───────────┘
                             ▼
                   ┌───────────────────┐   GATE 5 (gate): residual > threshold or
                   │ verify_calibration│   saturation? show lineout S/N, STOP-and-ask.
                   │      (gate)       │   (memory project_calibrant_saturation)
                   └─────────┬─────────┘
                             ▼
                  ┌─────────────────────┐   in-plane tx from Au single-crystal scan
                  │  inplane_tx          │   (memory project_au_inplane_calibration).
                  │  (action, guarded)   │   writes tilts["tx"].
                  └──────────┬───────────┘
                             ▼
                   ┌───────────────────┐   GATE 6 (gate): MANDATORY ring overlay.
                   │  ring_overlay      │   render + ask "do rings sit on the data?"
                   │      (gate)        │   no → back to resolve_geometry.
                   └─────────┬─────────┘
                             ▼
                  ┌─────────────────────┐   idempotency-guarded.
                  │  reconstruct         │   run_ff_hedm_full_workflow into
                  │  (action, guarded)   │   output_dirs["recon"].
                  └──────────┬───────────┘
                             ▼
                   ┌───────────────────┐   GATE 7 (gate): strain sanity ≤100 µε,
                   │  verify_grains     │   nMatches knee, grain count. read
                   │      (gate)        │   IndexBest.bin. flag→ask before "done".
                   └─────────┬─────────┘
                             ▼
                            END  → summary written to APEXA_ffhedm_workflow.json
```

### Handbook 12-gate → node mapping

| # | Handbook gate | Where enforced |
|---|---|---|
| 1 | ω-sign (aero→negate, else stop-and-ask) | `resolve_geometry` (judgment; interrupt if not aero) |
| 2 | Dark selection + **non-zero verify** | `calibrate` / `reconstruct` guards; verified before use |
| 3 | SkipFrame (GE far-field only) | `resolve_geometry` (detector-type judgment) |
| 4 | Energy from instrument, not filename | `resolve_geometry` (reads INSTRUMENT metadata) |
| 5 | Lsd from calibrant, not DetZ | `calibrate` writes `lsd_um` from fit output |
| 6 | Ring assignment + **mandatory overlay** | `ring_overlay` gate (cannot skip) |
| 7 | Best-vs-last iterate | loop edge `ring_overlay → resolve_geometry` on reject |
| 8 | Strain gate ≤100 µε | `verify_grains` gate |
| 9 | RingThresh knee | `verify_grains` (nMatches distribution) |
| 10 | Generous search bounds | `reconstruct` param assembly (defaults widened) |
| 11 | Refiner ≥0.5.7 | `reconstruct` precondition (validate_midas_installation) |
| 12 | Delete results before re-run | `reconstruct` guard (clears stale output on confirmed re-run) |

Multiple-calibrant / multiple-energy / multiple-distance ambiguity all resolve at
**gate 1** and **`resolve_geometry`** via `interrupt()` — the exact "should have
asked" behavior the user called out.

---

## 6. Human-in-the-loop across all three UIs

This is the crux, and it's clean because all UIs share `process()` returning a
string.

**Pause.** A gate node calls LangGraph's `interrupt({...})`. The graph checkpoints
and unwinds. `FFHEDMWorkflow.step()` catches the interrupt, stores
`state.pending_question` + `thread_id`, and **returns the question text** as the
normal assistant response. From the UI's perspective it's an ordinary answer:

```
APEXA(expt_2026)> calibrate and reconstruct the FF-HEDM data in /data/1id/nov26
APEXA: I found 3 calibrants in cali/:
         1) CeO2_att0_1.ge5   2) CeO2_att6_1.ge5   3) LaB6_att3_1.ge5
       Which should I calibrate against? (or 'all' to calibrate each)
```

**Resume.** On the next turn, `_is_ff_hedm_workflow()` sees a paused graph for this
thread and routes the input straight back in as
`graph.invoke(Command(resume=user_input), config={thread_id})`. The graph continues
from the checkpoint — the calibrant node receives "2", writes
`chosen_calibrant`, and flows on.

- **CLI:** natural — the REPL loop already prints the return and reads the next line.
  Works with the Ctrl-C abort we shipped: abort kills the busy server, the graph
  checkpoint survives, `session resume` re-enters mid-workflow.
- **Web / desktop:** identical — WebSocket sends the question, user's next message
  resumes. `on_tool_result` streaming still fires from action nodes.

**Thread identity = session name.** `thread_id = active_session` ties graph
checkpoints to APEXA sessions (§9), so `session switch` swaps workflows too.

---

## 7. Node implementation pattern

Every action node is a thin wrapper over the **existing** dispatch — no new tool
plumbing, no native tool_calls:

```python
async def _node_calibrate(state: FFHEDMState) -> FFHEDMState:
    step, tool = "calibrate", "midas_auto_calibrate"
    args = {"data_file": state["chosen_calibrant"],
            "output_dir": state["output_dirs"]["calib"], ...}

    key = _input_hash(tool, args)                        # §8 idempotency
    prior = state.get("done", {}).get(step)
    if prior and prior["input_hash"] == key and _exists(prior["output"]):
        state["gate_log"].append({"gate": step, "decision": "skip-cached"})
        return state                                     # <-- duplicate-calibration fix

    result = await client.execute_tool_call(tool, args)  # existing O(1) dispatch
    geo = await _extract_geometry(result, provider)      # LLM judgment: parse fit output
    state["lsd_um"], state["beam_center"], state["tilts"] = geo.lsd, geo.bc, geo.tilts
    state["done"][step] = {"input_hash": key, "output": geo.param_file,
                           "residual_px": geo.residual, "status": "ok"}
    return state
```

- **Action nodes** call `client.execute_tool_call(...)` — the same path the
  orchestrator uses, so registry/routing/reconnect all still apply.
- **Judgment nodes** call `provider.chat(...)` (the existing `ArgoProvider`) with a
  tiny extraction prompt and, where structure matters, parse into a dataclass.
  (Optional hardening: Instructor/Pydantic-AI for typed extraction — deferred; not
  required for v1.)
- **Gate nodes** call `interrupt(payload)` and read the resumed value. No LLM.

The LLM never chooses the next tool. That's the whole point.

---

## 8. Idempotency / dedup guard

The single highest-value fix, usable even *before* the full graph lands.

```python
def _input_hash(tool: str, args: dict) -> str:
    # stable across runs: tool + resolved absolute input paths + salient params
    salient = {k: args[k] for k in _SALIENT_KEYS.get(tool, args) if k in args}
    for k, v in salient.items():
        if _looks_like_path(v):
            salient[k] = os.path.realpath(v)
    return hashlib.sha256(json.dumps([tool, salient], sort_keys=True).encode()).hexdigest()[:16]
```

Before any action node runs, compare `_input_hash` to `state.done[step].input_hash`
and confirm the recorded output still exists on disk. Match → skip with a logged
`skip-cached`. This makes "calibrate the same CeO2 three times" impossible by
construction.

**Standalone value:** this same guard can wrap `midas_auto_calibrate`,
`run_ff_hedm_full_workflow`, `run_nf_hedm_reconstruction`, and
`midas_integrate_series` at the server layer *independently* of LangGraph — a cheap
early win if we want to de-risk before the graph.

---

## 9. Checkpointing & session integration

- **Checkpointer:** `langgraph.checkpoint.sqlite.SqliteSaver` at
  `~/.apexa/ffhedm_graph.sqlite` (mirrors `~/.apexa/timing.jsonl`). `MemorySaver`
  for tests.
- **Thread = session.** `thread_id = active_session`. A workflow paused at gate 2
  persists across CLI restart; `session resume` re-enters at gate 2. This composes
  with the abort/reconnect work already on `main`.
- **Final artifact:** on END, write `APEXA_ffhedm_workflow.json` in `working_dir`
  (mirrors `APEXA_integration_series.json`) — the citable record: chosen inputs,
  geometry, output dirs, gate decisions, grain summary. Agents cite only this.

---

## 10. Dependencies & packaging

Add to `pyproject.toml` (`uv sync`):

```toml
"langgraph>=0.2.0",
"langgraph-checkpoint-sqlite>=1.0.0",
```

- **No `langchain-*` model packages needed.** Nodes call `ArgoProvider` directly, so
  we do *not* pull LangChain's LLM abstractions or fight the Argo `{"response": ...}`
  format inside LangChain. LangGraph is used purely as the state-machine + HITL +
  checkpoint engine.
- Pure-Python, cross-platform — no C/CUDA, so no Windows carve-out (unlike
  `midas-suite`).
- ~2–3 transitive deps; small.

---

## 11. File plan

| File | Change |
|---|---|
| `apexa_ffhedm_graph.py` | **NEW** — `FFHEDMState`, node functions, graph builder, `FFHEDMWorkflow` (wraps compiled graph + checkpointer + interrupt/resume). ~400–500 LOC. |
| `apexa_agents.py` | `OrchestratorAgent.__init__`: read `APEXA_WORKFLOW_MODE`, lazily build `FFHEDMWorkflow`. `process()`: add the `_mode=="graph"` + `_is_ff_hedm_workflow()` branch (§3). `_is_ff_hedm_workflow()` predicate + paused-thread check. ~40 LOC, additive. |
| `midas_comprehensive_server.py` | Optional early win: `_input_hash` idempotency guard on the 4 heavy tools (§8). Independent of the graph. |
| `pyproject.toml` | 2 deps (§10). |
| `docs/LANGGRAPH_FF_HEDM_SPEC.md` | this file. |
| `.agents/skills/midas-ff-hedm/SKILL.md` | note that graph mode enforces the 12 gates automatically. |
| `docs/COMPUTE_DISPATCH.md` | note: reconstruct node routes through `_pick_compute_target` unchanged. |

Nothing is deleted. Legacy + single-loop paths remain the default.

---

## 12. Rollout & evaluation

1. **Phase 0 — idempotency guard** (§8) at the server layer. Ships the
   duplicate-calibration fix immediately, no LangGraph. Low risk.
2. **Phase 1 — graph behind `APEXA_WORKFLOW_MODE=graph`**, CLI only, `MemorySaver`.
   Dogfood on synthetic (`midas-pipeline simulate`) end-to-end.
3. **Phase 2 — SqliteSaver + session threading + web/desktop.** HITL over WebSocket.
4. **Phase 3 — beamline soak.** Real `cali/` with multiple calibrants; confirm gates
   fire. Only after soak, consider making graph the default for FF-HEDM.
5. **Evals** — replay the failing transcript as a fixture; assert: exactly one
   calibrate per input, a calibrant-selection interrupt when >1 file, folders
   proposed before any write, ring-overlay gate reached before reconstruct. Wire to
   the existing `interaction_logger` JSONL; optional Inspect/LangSmith later.

---

## 13. Risks & open questions

**Risks**
- *LangGraph learning-curve / API churn.* Mitigate: pin a minor, keep node logic in
  our own functions (portable if we ever swap the engine).
- *Two ways to run FF-HEDM* (graph vs legacy) during soak. Mitigate: flag-gated,
  single entry predicate, delete legacy path only post-soak.
- *Interrupt UX in web/desktop* — need to confirm a paused graph renders as a normal
  assistant turn and the next message resumes (should, since it's just a string).

**Open questions for you**
1. **Phase 0 first?** Ship the idempotency guard on the 4 heavy tools now
   (immediate duplicate-calibration fix), then build the graph — or go straight to
   the graph?
2. **Scope of v1 graph** — FF only (calibrate→inplane→recon), or include the
   integration series (`midas_integrate_series`) as a parallel graph from day one?
3. **Auto vs always-ask on single-calibrant** — when `cali/` has exactly one file,
   auto-select silently (log only), or still confirm once? (Spec currently:
   auto-select, log, no prompt.)
4. **Default calibrant handling for "all"** — if the user says "calibrate all",
   fan out one calibrate per file into separate `output_dirs`? (Spec assumes yes.)
