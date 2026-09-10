# PDF runbook — hosts, versions, pick-up point

> Part of the **PDF doc set**. Spine: [`README.md`](README.md).
>
> **This file is VOLATILE.** Host names, versions and install paths drift. Everything below
> is a *last-verified* record, not a promise — **re-verify live** with the install gate in
> spine §0 before relying on any of it. It is deliberately excluded from the RAG index for
> exactly this reason.

---

## Current pick-up point

**There is no campaign in progress.** This capsule was written from the `midas-pdf` package
and a synthetic probe of APEXA's tool path (`LAB_NOTEBOOK.md` §1, 2026-09-10). The next
session's job is the first real one:

1. Get a real total-scattering pattern with a **σ column** and a **known composition**.
2. Run phase 0 → phase 3 with the refinement (phase 2) actually on.
3. Record what happened in `LAB_NOTEBOOK.md` — including which beamline, since the spine
   currently asserts no beamline scope.

---

## Where it runs — last verified 2026-09-10

| Host | Interpreter | Status |
|---|---|---|
| APEXA dev laptop | `.venv` in the APEXA repo (`uv run`) | `midas_pdf` imports; all 7 CLIs on PATH |
| APS beamline host (`copland`) | `/home/beams12/S1IDUSER/opt/envs/midas/bin` (pip env, no repo clone) | the blessed MIDAS runtime; `midas-pdf` presence **not verified** — run the install gate |

APEXA invokes `midas_pdf` through `_capability_runner.py` under the `.venv` interpreter with
a clean environment (regime 3 — the pip torch stack breaks under C++ DYLD/LD injection). Do
not add `DYLD_LIBRARY_PATH`/`LD_LIBRARY_PATH` to a `midas-pdf` call.

Prefix long torch runs with `KMP_DUPLICATE_LIB_OK=TRUE` (as the package's own examples do).
Outputs go in a project/gdata directory you own — **never `/tmp`**.

---

## Version state — last verified 2026-09-10

| What | Value | How checked |
|---|---|---|
| repo source | **0.2.0** | `packages/midas_pdf/pyproject.toml` |
| installed distribution (APEXA `.venv`) | **0.1.1** | `importlib.metadata.version("midas-pdf")` |
| `midas_pdf.__version__` | **0.1.1** | attribute read |
| modules actually present | `structure`, `cif`, `rmc`, `saxs`, `model_comparison`, `ionic_form_factors`, `placzek`, `multi_phase`, `aniso_refine`, `bayesian_refine` — **all import** | import probe |
| CLI entry points on PATH | all **7** | `shutil.which` |
| APEXA tool docstring claims | "midas-pdf 0.1.0" | `midas_comprehensive_server.compute_pair_distribution` |

**The version string lags the content.** A 0.1.1-labelled install ships the 0.2.0 module set
and CLI. Three different numbers are visible (0.2.0 source / 0.1.1 dist / 0.1.0 in APEXA's
docstring) and none of them is a reliable capability gate. **Gate on the import probe**
(spine §0), never on `__version__`.

Dependency floors from `pyproject.toml`: `torch>=2.1`, `numpy>=1.22`, `midas-params>=0.9.0`
(the last for CLI argument errors and path preflight), Python `>=3.9`. Optional extras:
`dev` (pytest, matplotlib), `bayes`.

---

## Healthy ranges — what a good run looks like

Provisional; none of these is calibrated against a real APS dataset yet.

| Quantity | Healthy | Unhealthy, and what it means |
|---|---|---|
| ⟨S(Q)⟩ over the top `q_asymptote_frac` of the range, **after** phase 2 | ≈ 1 | 2.75 was measured with refinement off — scale never fitted (Notebook §1b) |
| S(Q) at high Q | oscillating about 1, decaying | a monotonic ramp ⇒ background/Compton wrong; an offset ⇒ scale wrong |
| G(r) below the shortest bond | close to the straight line −4πρ₀r | curvature or peaks there ⇒ normalization or termination ripple (rule 10) |
| first peak r | ≥ the shortest chemically possible bond for the composition | 0.72 Å was measured on synthetic Ni data (Ni–Ni is 2.49 Å) — a ripple (Notebook §1c) |
| `sigma_G` | non-zero, smooth, growing where counts are low | **all zeros** ⇒ σ never entered the chain (rule 2) |
| refinement | converges in ≲60 L-BFGS steps | non-convergence ⇒ check ρ₀, `r_min_phys`, and whether Q_max includes a noisy tail |
| CLI χ²/ndof | order 1 **when σ is real** | order 100× off is the classic fabricated-σ signature — read stderr |

---

## Re-verification checklist

Run this at session start, before quoting anything from this file:

- [ ] install gate from spine §0 — imports **and** CLI list, not `__version__`
- [ ] `midas-pdf` present on the host that owns the data, not just on the APEXA host
- [ ] `midas-integrate-v2` available (it owns pixels→I(Q) with σ, the input to everything here)
- [ ] the pattern you are about to reduce actually has a σ column, and your path keeps it
- [ ] composition and wavelength read from a file you can name, not from a filename
