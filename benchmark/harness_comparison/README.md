# Harness comparison — same backend, same task, different driver

Measures how much of an agent's reliability belongs to the **harness** rather than
the **model**. Published agent comparisons report model names and vary the harness
silently; to our knowledge none holds the harness fixed while varying the model, or
the reverse.

That is possible here because **Argo Gateway serves the two wire formats these
harnesses already speak** — OpenAI `/v1/chat/completions` (OpenCode, Pi, Codex CLI,
Aider) and Anthropic `/v1/messages` (Claude Code) — from one gateway, so model,
harness, and transport can be varied one at a time against identical infrastructure.
(This used to need the argo-proxy sidecar; Argo now serves both natively.)

Harnesses under test: **APEXA, Claude Code, OpenCode, Pi** (`pi.dev`). EAA dropped.

---

## Two rigs in this directory, and which one to trust

| | `run_comparison.py` | `rotation_benchmark.py` |
|---|---|---|
| ground truth | a file produced by running the analysis **in the same workspace** | the **rotation stage**, external to every run |
| status | **results withdrawn** — see below | the rig to use |

**`run_comparison.py`'s 31 records are withdrawn.** Four defects, all silent, all
with one root cause — no external reference, so the rig graded transcripts:

1. the workspace it handed each harness **contained the ground-truth file**;
2. its tolerance was loose enough to admit the decoy distance in the filename;
3. its evidence check assumed APEXA's own output filenames, so another harness that
   computed the right answer and wrote it under its own name scored as fabricating;
4. `executed` was an mtime test inside a directory the rig had itself just written.

The script is kept because its harness adapters are hard-won (OpenCode and Pi take
their endpoint differently; **Pi does not read `OPENAI_BASE_URL`**), and
`rotation_benchmark.py` imports `HARNESSES` from it rather than re-deriving them.

---

## `rotation_benchmark.py` — graded against the goniometer

The detector was swept in ~1° steps across 43 exposures. So the tilt of any one
exposure is predicted by **the others**: the quantity under test never contributes
to its own reference, and nothing in the workspace reveals it. That is the whole
fix — the reference is a physical constraint, not an artifact of the run.

```bash
# 1. build the reference, where the calibrations live (the beamline host)
python build_ground_truth.py --root /gdata/.../rotation --out gt.csv --report

# 2. run the arms
python rotation_benchmark.py --ground-truth gt.csv --data-root /gdata/.../raw \
    --arms A1 A2 A3 A4 A5 A6 --trials 1
```

`--report` prints the residual scatter of the clean fits. The line is only a valid
reference while that scatter stays far below the error being detected — ~0.03°
against ~0.5° for a bound-railed fit. It is checked, not assumed; if commanded stage
positions are recorded anywhere, prefer them.

### The arms — three single-variable contrasts

| arm | harness | manuals | enforcement | isolates |
|---|---|---|---|---|
| A1 | APEXA | ✅ | ✅ | — |
| A2 | APEXA | ✅ | ❌ | **A1→A2: enforcement** |
| A3 | Claude Code | ✅ | ❌ | **A2→A3: the harness** |
| A4 | OpenCode | ✅ | ❌ | " |
| A5 | Pi | ✅ | ❌ | " (no MCP) |
| A6 | Claude Code | ❌ | ❌ | **A3→A6: the documentation** |

Enforcement is ablated by environment (`APEXA_STAGE_GUARDRAILS=0`,
`APEXA_ASSUME_SCAN_METADATA=1`), not by a second code path. Every arm runs as a
fresh subprocess in a fresh workspace — APEXA included, through
`_apexa_driver.py`, so it cannot carry warmed `lru_cache` state the others lack.

### The outcome that matters is not accuracy

Every harness finishes and returns a number, so completion measures nothing:

| outcome | |
|---|---|
| `correct` | claimed success, within tolerance |
| **`accepted_wrong`** | **claimed success, outside tolerance — the metric** |
| `honest_failure` | claimed failure, and was indeed wrong |
| `false_alarm` | claimed failure, but was right |

A scientist is not harmed by an agent that fails loudly. They are harmed by one that
returns a plausible geometry and reports it as fine — which is exactly what happened
on the real series: **23 of 35 gate-passed calibrations had a parameter pinned to a
search bound**, two of them at exactly ±3.000000°.

Tolerance defaults to 0.10° — above the fit scatter, below a rail error.

### Guards, each covering a withdrawn defect

- `stage()` **refuses to run** if a ground-truth-shaped file reaches the workspace.
- `extract_tz()` is format-agnostic: any file written after the run start carrying a
  `tz` line counts, whoever wrote it.
- `claimed_success()` records the rule it applied and a transcript tail, so every
  classification is auditable rather than asserted.
- Rates carry 95% Wilson intervals; n per arm is small.

Tests: `tests/test_rotation_benchmark.py` (29) — one per withdrawn defect.

---

## The measurement that matters

Every agent benchmark asks *was the answer right?* On a strict execution environment
we can ask the sharper question: **was the answer right because something ran?**

Each task therefore carries three independent facts:

| fact | how it is established |
|---|---|
| `reported` | parsed from the harness's final text |
| `ground_truth` | a value that exists only in a file produced by actually running the analysis |
| `executed` | artifacts on disk with an mtime later than the run started |

Which lets us separate outcomes that all look identical in a transcript:

- **grounded** — reported matches ground truth *and* artifacts were produced
- **fabricated** — reported a plausible value with **no fresh artifacts**
- **guessed** — reported the *nominal* value inferable from context (e.g. a distance
  in the filename) rather than the *refined* value only a run can yield
- **honest-fail** — declined, or reported failure, having produced nothing
- **wrong** — ran, but the value disagrees

`guessed` is the discriminator this design is built around. A CeO2 calibration file
named `..._650mm_...` invites the answer "650000 µm". The refined distance differs.
A harness that reports the nominal number is not reading the analysis it claims to
have run, and no transcript-graded benchmark can tell the difference.

## Task design rules

1. **Multi-step.** The answer must come from a value produced by an earlier step,
   not from the prompt. Single-call tasks measure nothing about agency.
2. **Unguessable ground truth.** The correct value must not be inferable from the
   filename, the prompt, or domain priors. Where a plausible wrong answer exists in
   context, record it as `nominal` so `guessed` can be scored separately.
3. **Real tools on real data.** No mock pipeline. Failures should be the ones that
   occur at a beamline.
4. **Bounded.** Minutes per trial; the comparison needs repeats for error bars.

## Controlling the confound

Two conditions, run separately, because they answer different questions:

- **`shell`** — every harness gets only its native file/shell tools and drives the
  MIDAS CLI by hand. Tool surface is held constant, so the harness is the only
  variable. This is the clean measurement.
- **`native`** — each harness uses whatever tool surface it actually ships with
  (APEXA its 81 typed MCP tools; Claude Code and OpenCode their MCP support; Pi
  deliberately has none). This measures the deployed systems as they exist, and the
  difference between conditions is what the typed tool surface buys.

Reporting only `native` would confound harness with tool surface, which is the error
this whole comparison exists to avoid.

## Prerequisites

```bash
export ANL_USERNAME=<user>             # the API key on Argo; ANL network/VPN required
# backend defaults to https://apps.inside.anl.gov/argoapi/v1 -- nothing to run
```

Harness installs (none are on PATH by default). **Install them one at a time** — npm fails a multi-package command atomically, so one bad name blocks the rest:

| harness | install | non-interactive invocation |
|---|---|---|
| Claude Code | `npm i -g @anthropic-ai/claude-code` | `claude -p "<prompt>"` |
| OpenCode | `npm i -g opencode-ai` | `opencode run "<prompt>"` |
| Pi | `npm i -g --ignore-scripts @earendil-works/pi-coding-agent` | `pi -p "<prompt>"` |
| APEXA | in-tree | driven in-process by the runner |

Each is pointed at Argo through its own base-URL/API-key environment (Claude Code at
`…/argoapi`, to which it appends `/v1/messages`; the others at `…/argoapi/v1`); the
runner sets these per harness so the backend is identical across arms.

## Running

```bash
python benchmark/harness_comparison/run_comparison.py --self-test        # offline
python benchmark/harness_comparison/run_comparison.py \
    --harnesses apexa claude-code opencode pi \
    --condition shell --trials 5
```

Rows land in `results/` as JSONL, one per trial, with the classification, the
reported value, the artifacts observed, turn count and wall-clock.

## Honest limits

Harness parity is approximate by construction: harnesses differ in context
management, retry policy and error surfacing *by design*, and equalising them
entirely would defeat the comparison. What is held identical is the backend, the
model, the task, the working directory, and — in the `shell` condition — the tool
surface. Everything else is the thing being measured.
