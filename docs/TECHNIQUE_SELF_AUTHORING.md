# Technique self-authoring — recognize data → read manual → **author a skill** → drive the analysis

> **Status:** design doc / proposal (no code yet). Extends the shipped technique-learning
> engine (recognize → recall → drive, commit `a39390c`) with the **generative** step:
> APEXA authoring a new technique doc-set from a manual, gated by a linter, delivered
> for human review. Origin: Hemant's suggestion to make APEXA do for the *analysis
> agent* what [`AdvancedPhotonSource/beamreport`](https://github.com/AdvancedPhotonSource/beamreport)
> does for *reports* — a self-describing, contract-enforced, generic-not-per-technique
> mechanism.

---

## 0. The key realization: APEXA capsules **are** beamreport doc-sets

`beamreport` is not an agent. It is a **contract + linter**: `DOCS_SPEC.md` mandates a
per-technique doc-set — `README.md` spine (scope gate, install gate, halt conditions,
hard rules + trap table, ordered steps with a "why" column, index), phase files,
`DIAGNOSIS.md` (symptom-keyed, each entry able to *exonerate* as well as implicate),
`ENVELOPE.md` (every limit sorted Fixed / Configured / Intrinsic, each with value +
unit + provenance + owner), a lab notebook, and a runbook. `beamreport-doc-lint`
**checks the contract, not the content** ("a silent degradation in a report generator
is worse than a crash").

Those are **exactly the files `capsule_registry.py` already parses.** MIDAS authors its
`manuals/<technique>/` capsules to `beamreport/DOCS_SPEC.md`; APEXA vendors them via
`scripts/sync_midas_capsules.py` and drives methodology from them. **APEXA is already
the *consumer* side of Hemant's contract.** What he is asking for is the **generative**
side: an agent that, seeing unfamiliar data, *authors* a doc-set to that spec and runs it.

---

## 1. The four verbs vs. what exists today

| Verb (Hemant) | Status | Mechanism |
|---|---|---|
| **See the data** | ✅ built | `_infer_technique` / `recommend_workflow` / `inspect_dataset_file` recognize the technique from raw data; emit `capsule_technique` |
| **Read manuals** | ✅ built | capsules + `capsule_registry.py` + technique-scoped RAG (`query_hedm_knowledge`) |
| **Create skills** | 🔲 **new** | APEXA can *consume* a doc-set but not yet *author* one from a manual |
| **Modify architecture** | ⚠️ **reframe** | the safe version already exists — see §2 |

The gap is narrower than it sounds. The only genuinely new build is **create skills**.

---

## 2. The load-bearing principle: self-**extension**, not self-**modification**

"Modify architecture to perform the analysis" has a dangerous reading (an agent
rewriting its own Python and hot-loading it into a live beamline process) and a safe
reading — and **the safe one is already how APEXA works**:

- `capsule_registry.py` **auto-discovers** any directory carrying the doc-set markers
  (`README.md` + `ENVELOPE.md`) — zero code change to add a technique.
- `handbook_guardrails.load_param_facts(pipeline)` **loads parameter facts** from a
  capsule's `PARAMETERS.md` — a new technique inherits the generic µm-floor / bound-pair
  / relational lint for free.
- `knowledge_base/index_knowledge.py` (`index_capsule`) and
  `apexa_toolsurface.py` (`list_techniques` / `learn_technique` / `open_phase`) pick a
  new capsule up automatically.

So the architecture "adapts" **because it is data-driven, not because code was
rewritten.** A new technique = drop in a new doc-set + re-index. That is beamreport's
philosophy ("the contract and the linter, not the content") and APEXA's own
anti-fabrication ethos in one move.

> **The line we hold:** APEXA generates **declarative artifacts** (doc-sets, `SKILL.md`,
> config). It never generates executable code that runs unreviewed on a beamline. A
> technique that needs a *new tool* (a MIDAS executable APEXA does not wrap) produces a
> **flagged stub for a human**, never an auto-executed generated tool (§5).

---

## 3. The new piece: the technique-onboarding loop

Given unfamiliar data, APEXA would:

1. **Recognize the unknown.** The classifier returns `capsule_technique = None` *with*
   candidate signals, and APEXA says "I have no capsule for this; the data looks like
   *X*" — instead of silently defaulting to FF. (Today `_infer_technique` returns `None`
   on no-match; the change is to surface it as an *onboarding trigger*, not a fallback.)
2. **Locate the manual.** From MIDAS `manuals/` (the same public source the auto-sync CI
   already clones — `.github/workflows/midas-capsule-sync.yml`) or from a manual the user
   supplies. Never the public internet at runtime (offline-clean).
3. **Draft the doc-set.** The LLM authors the six artifacts to `DOCS_SPEC.md` —
   `README.md` spine (scope gate, install gate, halt conditions, hard rules + trap
   table, ordered steps + "why"), phase files, symptom-keyed `DIAGNOSIS.md`, tiered
   `ENVELOPE.md`, lab notebook, runbook — scaffolded by `beamreport-doc-lint --init`.
4. **Lint = the enforced gate.** Run `beamreport-doc-lint` **and** APEXA's own
   `handbook_guardrails` structural check. A doc-set that fails the contract **never
   enters the engine.** This *is* beamreport's "silent degradation worse than a crash,"
   reused verbatim as the safety mechanism for generated skills. Pass/fail here is a
   real, reportable metric (§6).
5. **Emit the skill.** Write `.agents/skills/<technique>/SKILL.md` pointing at the new
   capsule + the tools it drives, matching the existing skill format.
6. **Human-review PR.** Reuse the machinery shipped in
   `.github/workflows/midas-capsule-sync.yml`: open a PR carrying the generated doc-set +
   the lint report. A human merges before any beamline sees it. Merge → re-index → the
   generic engine drives the analysis. (Runtime never auto-adopts a generated skill.)
7. **Tool-gap flag.** If the manual references a MIDAS executable APEXA does not wrap,
   emit a *typed-tool stub + a flagged note* for human implementation. Never auto-execute.

Net: **"beamreport's authoring side, driven by an agent, gated by beamreport's linter,
delivered through APEXA's review flow."**

---

## 4. Why the gate is credible

The generation step is an LLM and therefore fallible — which is exactly why the gate is
deterministic and external:

- **`beamreport-doc-lint`** is authored by the same group that owns the spec; it is the
  authority on contract compliance, and it is independent of APEXA's generator.
- **`handbook_guardrails`** already hard-blocks must-never-violate traps before a run
  (`run_ff_hedm_full_workflow` returns `status:"error"` on any `error` trap). A generated
  capsule feeds the *same* lint the human-authored ones do — so a bad generated trap table
  is caught by the same machine that catches a bad hand-authored one.
- **The install gate** (a `DOCS_SPEC` spine requirement — "the code is the code the
  document describes") is critical for generated skills: the doc-set must pin the exact
  MIDAS version / executable it targets, or the skill claims a procedure the installed
  code cannot run. This ties directly to the blessed-runtime work
  (`/home/beams12/S1IDUSER/opt/envs/midas/bin` on copland).

Same principle as the `rm` permission gate and the scope gate: the must-never-violate
rule is **enforced, not prompt-hoped.**

---

## 5. The boundary (where we deliberately stop)

| APEXA generates | Disposition |
|---|---|
| A technique doc-set (`README`/phases/`DIAGNOSIS`/`ENVELOPE`/notebook/runbook) | Lint → PR → human merge → generic engine runs it |
| A `SKILL.md` for the technique | Same PR, same review |
| Capsule-sourced guardrail rows (a cited `DIAGNOSIS`/trap entry) | Same PR; feeds `handbook_guardrails` generically |
| A **new typed tool** (wrapping a MIDAS exe APEXA lacks) | **Stub + flagged note only** — human implements + reviews; never hot-loaded |
| Executable analysis code run on a beamline | **Never** generated-and-run unreviewed |

The value is concentrated in the first three rows, which are declarative and fully
covered by the lint contract. The last two are the danger zone and stay human-owned.

---

## 6. Open questions to resolve before building

1. **`beamreport-doc-lint` offline-safety.** Is it pip-installable and does it run with
   no public-internet access (internal tier / CI)? `beamreport` is ANL, pre-release. If
   it is not offline-clean, either vendor the linter or reimplement the structural checks
   inside `handbook_guardrails` (they are structural — "has all parts, diagnosis entries
   well-formed, spine carries scope/install/halt"). Must not reintroduce the offline-hang
   class (see `docs/OFFLINE_DEPLOYMENT.md`).
2. **Generation transport.** The authoring LLM call goes through argo-proxy
   (`APEXA_LLM_MODE=proxy`), internal tier, no public net.
3. **Where generated capsules live vs. vendored ones.** Vendored MIDAS capsules are
   overwritten by `sync_midas_capsules.py`. APEXA-authored capsules for techniques MIDAS
   does *not* ship a manual for need a separate namespace (e.g.
   `knowledge_base/capsules_authored/`) so a re-sync never clobbers them and their
   provenance ("authored by APEXA from manual X, reviewed by Y") is explicit.
4. **Registry.** `beamreport` §9 registers each instance in `REGISTRY.md`. APEXA should
   record generated doc-sets (technique, source manual, MIDAS pin, lint result, reviewer)
   analogously.

---

## 7. Phased implementation (proposed)

- **P0 — validate the gate.** Confirm `beamreport-doc-lint` runs offline (or port its
  structural checks). Deliverable: `beamreport-doc-lint <existing capsule>` passes on
  APEXA's *already-vendored* capsules — proving the contract holds before we generate to it.
- **P1 — generator (the new build).** A tool/skill that takes a manual + the recognized
  technique and drafts the doc-set (steps 3–5), gated by the linter (step 4). Test against
  a technique APEXA has a manual for but has **not** vendored — generate it, lint it,
  diff against a human-vendored version.
- **P2 — review delivery.** Wire the PR flow (step 6), reusing `midas-capsule-sync.yml`
  patterns; add the authored-capsule namespace + registry (§6.3–4).
- **P3 — onboarding trigger.** Surface `capsule_technique = None` as an onboarding
  prompt (step 1) instead of a silent FF fallback.
- **P4 — tool-gap stubs.** Emit typed-tool stubs + flags for missing executables (step 7).

---

## 8. Paper relevance (NMI / ICLR)

This is a genuine machine-intelligence claim, not an engineering convenience: **an
instrument that self-extends to a new technique by authoring a linter-enforced operating
manual, then obeying it.** It is the generative twin of Part 11's fourth signature
("the instrument internalizes its own operating manual as an executable constraint") and
directly answers the NMI generalization risk. The **lint pass/fail rate on generated
doc-sets** is a real, defensible metric. See `manuscripts/2-NMI-APEXA/STRATEGY.md`
Part 12.
