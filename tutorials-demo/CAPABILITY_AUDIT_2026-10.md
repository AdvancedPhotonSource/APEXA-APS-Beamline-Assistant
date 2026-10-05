# APEXA capability audit — October 2026

Baseline for the next deck. What exists **now**, and what is new since the last two
decks: `APEXA_APS_Presentation` (April) and `APEXA_Capsule_Update` (18 Aug).
53 commits since the capsule deck.

---

## 1. The figure you asked for: traditional MIDAS, per technique

This does not need to be drawn by hand. The vendored capsules **are** the
traditional workflow — synced from `MIDAS/manuals/`, pinned to commit `227d87c2`,
0 drift. Every number below is read out of them by `capsule_registry`, so the
figure regenerates when MIDAS changes.

| technique | ordered steps | hard rules | traps | halt conditions |
|---|---|---|---|---|
| ff-hedm | **14** | 17 | 59 | 15 |
| pf-hedm | **13** | 12 | 18 | 9 |
| tomo | 10 | 5 | 5 | 5 |
| xrd-ct | 9 | 8 | 7 | 8 |
| defect | 8 | 14 | 0 | 0 |
| pdf | 8 | 19 | 17 | 0 |
| solve-cell | 8 | 0 | 0 | 0 |
| calibrate-integrate | 7 | 15 | 28 | 0 |
| nf-hedm | 7 | 25 | 44 | 11 |
| dct-tt | 6 | 8 | 9 | 8 |
| dfxm | 6 | **33** | 42 | 12 |
| **total** | **96** | **156** | **229** | **68** |

**The argument the figure should make.** A single FF-HEDM reconstruction is 14
ordered steps. Order matters — ω sign before scan definition, calibrate before
zip, RingThresh measured *on that zarr* and never copied from a template. Around
those 14 steps sit 59 documented traps, each one written down because it had
already produced a confidently wrong answer.

Worked example for a slide — FF-HEDM's actual order:

```
0  verify install        5  calibrate + ring overlay     9  run the pipeline
1  survey the folder     6  zip the sweep only          9b  refine tx, Wedge, re-run
2  ω sign (par field 9)  7  measure RingThresh          10  read + report
3  scan def + dark       8  build the parameter file
3b ImTransOpt            8b check RhoD + ring count
4  energy, then distance
```

Note 3b, 8b, 9b: steps that exist only because skipping them fails *silently*.

**The contrast slide.** Traditionally a scientist holds all 96 steps and 229
traps, or re-reads the manual each time. APEXA recognises the technique from the
raw data, loads that capsule's methodology, enforces the hard rules before
dispatch, and verifies the artifacts afterwards. The handbook is the source of
truth in both cases — the difference is who has to remember it.

---

## 1b. For THIS audience: forward simulation and DFXM

Read this before writing the slides — the honest picture is split, and an audience
coming for DFXM will be misled by a tool list alone.

### What is real

| tool | status | what it does |
|---|---|---|
| `run_forward_simulation` | **real** | diffraction forward-simulated from a known microstructure (grains file + param file); the mature forward-simulation path |
| `compute_pair_distribution` (midas-pdf) | real | I(Q) → G(r) |
| `analyze_grain_defects` (midas-defect) | real | dislocation rods / asterism / polytype from FF diffuse scattering |
| `fit_grain_odf` (midas-grain-odf) | real | per-grain ODF |

### What is a forward model only

| tool | mode | note |
|---|---|---|
| `simulate_dfxm_image` (midas-dfxm 0.1.0) | **synthetic** | uniform-strain field on a grid, rendered through the resolution function + objective optics. Real strain/orientation-field ingestion is **deferred upstream**. |
| `simulate_2d_diffraction` (midas-2d) | synthetic | |
| `design_xaf_experiment` (midas-xaf) | synthetic | anvil-cell design |
| `invert_pf_grain_odf`, `invert_pink_beam` | deferred | capability present, real-data I/O pending |

Every one of these carries `mode` and `real_data_supported` in **every payload**,
so APEXA cannot report a synthetic result as a measurement. That is a feature to
show, not a caveat to bury — it is the same honesty contract as the calibration
gate.

### Where APEXA is genuinely strong on DFXM: the methodology

The DFXM capsule is the **richest in the whole set** — more hard rules than any
other technique, and its core phase is explicitly real-data reduction:

- **33 hard rules** (next highest: nf-hedm 25), **42 traps**, **12 halt conditions**
- 6 phases: survey → configure (material, reflection, geometry, Λ, resolution) →
  **reduce raw frames to orientation/strain maps** → multi-reflection full-F
  tensor (only with ≥2 co-registered reflections) → analyse past orientation →
  report with provenance

The rules are measured, not stylistic. A few, verbatim-ish:

1. *Subtract the pedestal before the first moment* — on raw ID03 frames the
   pedestal carries 98.5 % of the signal
2. *Refraction is a gauge, not a per-pixel strain*
3. *Orientation is centroid-exact; strain has a validity boundary at ~0.3 Λ*
4. *Multi-reflection registration is the binding systematic, not photon statistics*
5. *Validate real data by injection-recovery, never round-trip* — a
   forward-then-inverse round-trip returns ~1e-16 and proves nothing

Rule 5 is worth a slide on its own for an audience that will reach for a forward
model: **the forward operator cannot validate itself.**

### The honest gap, and the roadmap line

APEXA has **no typed tool for DFXM phase-2** — the real-data reduction the capsule
describes in detail. Today an agent would drive that through `run_command` /
`run_remote_command`, which bypasses the lint gate and the output verifier.

So the accurate claim is: *APEXA carries and enforces the DFXM methodology, and
can forward-model the imaging operator; the real-data reduction path is capsule-
guided but not yet a typed, guard-railed tool.* That is a credible roadmap slide —
and it is exactly the shape of gap the calibration work just closed for
FF/calibrate-integrate, so there is a worked precedent for how it gets closed.

---

## 2. New since the capsule deck (18 Aug)

### Canonical calibration (28–30 Sep) — the largest single change
The handbook's four-stage recipe is now **executed**, not merely retrieved. It
previously lived in the capsule, was injected into context on every calibration,
and was never run: the code did a one-shot approximation with no template, no
acceptance gate and no sentinel handling.

- `_calibrate_runner.py` — the recipe as a real, testable file
- One engine decision (`auto` / `v2` / `v1` / `legacy`) replacing a four-branch
  cascade whose first branch ignored the caller's choice entirely
- Acceptance gate on held-out strain, **reported with its regime** (the 100 µε cap
  is a fraction, so a short throw reads higher — the payload carries the ring
  window and Lsd so the operator can tell which case they are in)
- Preconditions that refuse rather than produce a number: `detector_scope_gate`
  (upstream: halts 42 of 252 archive exposures, 26 of which had already produced a
  plausible-looking calibration), finite shape-matched dark, seed provenance
- `at_bounds` — which parameters finished *on* a bound, i.e. ran out of room
  rather than converged
- Output is a drop-in `refined_MIDAS_params_v2.txt`; previously a v2 run wrote a
  `calibration.json` that nothing in APEXA read

Found by running it on real 20-ID data: a pixel size wrong by 1.5× (the seeder
returned exactly `595939.5 ÷ (100/150)`), a version-dependent `RhoD` unit bug,
and the whole azimuthal distortion block railing at its bounds.

### Rules come from the manual, not from Python
`_calibration_handbook_rules()` resolves rule **numbers** against the vendored
`HARD_RULES.md` at report time. A rule edited upstream reaches APEXA through a
capsule sync. Same principle `handbook_guardrails` already applied to parameter
files, now extended to the procedural path.

### GSAS-II server (3–4 Sep) — a 4th MCP server, 5 tools
`refine_pattern`, `propose_structures`, `assess_refinement`,
`refine_series_submit`, `refinement_status`. Autonomous Rietveld on **any** powder
pattern, not just MIDAS caked output; `_trust()` grades every refinement so a
converged-but-meaningless fit is not reported as good.

### Open models on ANL hardware (ALCF)
11 candidate models across two clusters, per-user Globus identity, no code change
(ALCF is OpenAI-compatible). `model <id>` switches **cluster, credential and base
URL** together. Demonstrated end to end: `google/gemma-4-31B-it` driving tool
calls, progressive disclosure and a calibration.

### Offline / air-gapped deployment
Network tiers (`data` / `internal` / `web`, default `internal`), BM25 vendored
in-tree so the base install fetches nothing, tiktoken tier-gated, HF forced
offline. Driven by a real failure: APEXA hung at startup on a HuggingFace HEAD
request, inside a `try/except` — because a network hang is not an exception.

### Remote execution
Per-tool SSH routing for data-dependent MIDAS tools, with a fail-closed locality
decision. Twice fixed to stop APEXA SSHing to the host it is already running on.

### Benchmarks (27 Aug, for ICLR)
Harness-comparison and grounding harnesses. Grounding result: frontier models
fabricate at the floor (0%); the guardrails recover smaller models.

---

## 2b. Worked examples for the deck

Three. Each is **traditional vs APEXA on the same job**, which is the slide shape
that lands. Example A is real — the numbers are from a 20-ID beamtime on
2026-09-29/30, not a mock-up.

### A. Calibration — the one with real numbers

**Traditional.** Seven ordered steps (`calibrate-integrate`), 15 hard rules,
28 traps. In practice: write a parameter file by hand, pick `ImTransOpt`, run the
CLI, read a strain number, decide whether to believe it.

**What actually happened at 20-ID.** The pixel size was entered as 100 µm
(the filename reads `..._100x100_...`, which is the *beam* size; the VarexD is
150 µm). The run produced:

```
Lsd 913.2 mm   ty −3.16°   tz −10.05°   held-out strain 3057 µε
p3 −180   p6 −180   p12 −180   p14 +180   p5 0.01      ← all on their bounds
```

A number was produced. Nothing errored. Re-run with 150 µm:

```
seed 893.9 mm (vs nominal 900)   Lsd 895.4 mm   ty −0.36°  tz −3.44°   314 µε
```

The seeder's two answers differ by exactly `100/150` — the pixel-size ratio. That
is how a wrong input looks when nothing validates it.

**What APEXA now does with the same call.** Returns, per run:

- `px_um_source` — the provenance of the pixel size, labelled `VERIFY THIS` when
  it is only a shape heuristic
- `lsd` — the seeder's distance, the recorded one, and which was used
- `at_bounds` — the parameters that finished **on** a bound, i.e. ran out of room
  rather than converged; that is how the ±180 railing above becomes visible
  instead of needing someone to read the parameter file by hand
- `gate` — held-out vs full strain **and** the ring-radius window and Lsd, so the
  100 µε cap can be read against the geometry (it is a fraction, so a short throw
  reads higher)
- `handbook_rules` — the rules this run touched, text pulled live from the
  vendored manual

and it **refuses** rather than returning a number when: the rings do not reach the
detector (`detector_scope_gate` halts 42 of 252 archive exposures upstream, 26 of
which had already produced a plausible-looking calibration), the dark is
non-finite or format-mismatched, or the gate fails.

Verified locally end to end: `canonical-v2:four_stage (0.23.0)`, held-out
**80.4 µε**, gate passed, all five capabilities active.

### B. FF-HEDM reconstruction

**Traditional — the real order**, from the capsule (14 steps):

```
0  verify install        5  calibrate + ring overlay     9  run the pipeline
1  survey the folder     6  zip the sweep only          9b  refine tx, Wedge, re-run
2  ω sign (par field 9)  7  measure RingThresh on       10  read + report
3  scan def + dark          THAT zarr
3b ImTransOpt            8  build the parameter file
4  energy, then distance 8b check RhoD + ring count
```

Order is load-bearing. Step 7 must run on the zarr from step 6 — a `RingThresh`
copied from a template gives **0 peaks**, and the tell is that the output is
threshold-*invariant*. Steps 3b, 8b and 9b exist only because skipping them fails
silently.

**APEXA.** One call:

```
run_ff_hedm_full_workflow(result_folder=…, param_file=…, data_file=…,
                          refine_backend="c-omp", process_grains=True)
```

with three things wrapped around it:

1. **Before dispatch** — the handbook lint gate evaluates the parameter file and
   **hard-blocks** on any `error` trap (µm-vs-px on `Width`/`Margin*`, `SkipFrame`
   GE-vs-NF, dark `exchange/data`, template `RingThresh`, degenerate ω windows).
   Nothing runs. `ignore_handbook_traps=True` is the explicit override.
2. **Locality** — if the data lives on another host, the typed tool routes the run
   there over SSH and lints the *remote* parameter file first, rather than the
   agent hand-driving `ff_MIDAS.py` and bypassing every gate.
3. **After** — `verify_ff_reconstruction` reads the artifacts on disk against the
   documented silent-failure chains: `InputAll.csv` present/non-empty/not-all-zero,
   `SpotsToIndex.csv` > 0 seed rows, `IndexBest*.bin` with a solved grain
   (col 14 > 0), `Grains.csv` > 0 rows. A *present-but-degenerate* artifact is a
   `fail`; a *missing* one is `n/a`.

The slide line: **returncode 0 is not success.** A stale `Grains.csv` from
yesterday satisfies "the command exited cleanly".

### C. Forward simulation — and the trap this audience will hit

**The tool** (real, not synthetic):

```
run_forward_simulation(input_grains_file="Grains.csv",   # known orientations
                       param_file="Parameters.txt",      # experimental geometry
                       output_prefix="sim_",
                       scanning_mode=False)              # True → PF/scanning
```

Diffraction forward-simulated from a known microstructure. Its documented use is
testing reconstruction algorithms and validating experimental data.

**The closed loop, which is the interesting slide:**

```
known Grains.csv ──run_forward_simulation──▶ simulated patterns
                                                   │
                                   run_ff_hedm_full_workflow
                                                   ▼
                        recovered Grains.csv ──match_grains──▶ recovered vs truth
                                              calculate_misorientation
```

`match_grains` (Hungarian assignment) and `calculate_misorientation` close it, so
you get a per-grain orientation error against ground truth — a genuine
reconstruction-fidelity test with no beamtime.

**The trap, and it is DFXM hard rule 5:**

> *Validate real data by injection-recovery, never round-trip. A
> forward-then-inverse round-trip returns ~1e-16 and proves nothing.*

A round trip through your own forward operator tests arithmetic, not physics. The
loop above is only meaningful when the reconstruction path is independent of the
simulation path, or when you **inject** a known perturbation into real data and
measure how much comes back. Worth stating explicitly to an audience that is
coming for forward modelling — it is the single most likely way to generate a
convincing wrong result.

---

## 3. What the April APS deck now gets wrong

Worth knowing before reusing those slides.

- **"Multi-Agent Orchestration" with 5 specialist agents** is no longer the
  architecture. `APEXA_AGENT_MODE` defaults to **`single`** — one persistent
  reasoning loop with full context across turns. The keyword-routed specialists
  are legacy.
- **Tool and server counts**: now **84 tools across 4 servers** (core 11,
  midas 55, motor 13, gsas2 5).
- **Transport**: structured tool calling on every turn via an OpenAI-compatible
  endpoint. Argo now serves that natively, so the argo-proxy sidecar is optional.
- **Integrity** is enforced by an execution **ledger** over recorded tool calls,
  not by regexes over prose.

## 4. Numbers for a summary slide

- 84 tools · 4 MCP servers · 11 vendored techniques · 177 tests
- 96 ordered steps, 156 hard rules, 229 traps, 68 halt conditions — all sourced
  from MIDAS's own manuals, 0 drift against the pinned commit
- 5 interfaces: CLI, web, desktop (pywebview), Gradio, Trame
