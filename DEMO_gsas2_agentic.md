# Live demo: autonomous Rietveld refinement from APEXA

Driving `gsas2_server.py` in a screen share. Everything below is
copy-paste into the APEXA chat, in order. Total runtime ~4 min of
compute; budget 15 min with talking.

The sample is PbSO4 from the GSAS-II tutorial suite — chosen because
the audience already knows it, so nothing has to be explained about the
material and attention stays on what the agent decides.

**`fmthint: "GSAS"` is mandatory.** `.XRA` is a GSAS legacy format; without
the hint it goes to the text reader and dies on `could not convert
string 'CPD' to float64`.

---

## Act 1 — refine one pattern, and read the decision trace

Paste into APEXA:

```
Refine this powder pattern and show me the full decision trace.

data_file:    /Users/b324240/Git/Agentic-GSAS-II/tutorials/PbSO4_independent/PBSO4.XRA
cif_files:    ["/Users/b324240/Git/Agentic-GSAS-II/tutorials/PbSO4_independent/PbSO4.cif"]
instprm_file: /Users/b324240/Git/Agentic-GSAS-II/tutorials/PbSO4_independent/INST_XRY.PRM
output_dir:   /tmp/apexa_demo/pbso4
fmthint:      GSAS
two_theta_limits: [12.0, 150.0]
max_steps:    12
return_trace: true
```

`max_steps: 12` keeps it near three minutes and stops before the tail,
where the driver re-picks the instrument group to no effect. Drop the cap
to 100 if you want the full 19 steps and have the time.

### What appears, and what to say about it

Expect `Rwp` around 11%, `GOF` about 2.3, stopping on `idle`.

| Step | Category | What to point out |
|---|---|---|
| 1 | `hstrain` | Reaches the lattice through hydrostatic strain. `ComputeWorstFit` never offers the cell terms A0–A5, so strain is the only route — worth saying out loud to the primitive's author. |
| 2 | `instprm` | **Ranked `:0:Zero`, refined `U, V, W`.** The trace prints both. Zero stays fixed at 0 with no esd. |
| 5 | `sample_disp` | `Transparency`, 27.7% → 14.4%. The single largest gain in the run, and a sample effect rather than an instrument one. |
| 10 | `mustrain` | Physical broadening, not the phenomenological `X`/`Y`. |

The `instrument_policy` block in the response is the part to dwell on:

```json
"instrument_policy": {
  "refined":  ["U", "V", "W"],
  "withheld": {
    "Zero": "Toby §12.2: the zero correction does not properly apply ...",
    "X":    "prefer crystallite size and microstrain ...",
    "Y":    "...",
    "Z":    "...non-zero on no instrument he is aware of",
    "SH/L": "...negligible for area-detector data"
  }
}
```

The tool reports what it declined to refine **and cites the reason**. A
refinement that silently omits a parameter cannot be distinguished from
one that never considered it.

### The cell is not in the summary

Say this before anyone asks. The summary reports the phase's
*unstrained* cell — the input value, whether or not anything moved. The
refined cell lives in the strain-folded listing line:

```
grep 'resulting cell parameters' /tmp/apexa_demo/pbso4/hist.lst
```

Expect `a=8.48 b=5.398 c=6.95991(11)`. Only `c` carries an esd, because
`D33` was the strain term picked — `a` and `b` were never moved. The
parenthesised uncertainty is how you tell an axis that was refined from
one that was merely reported.

---

## Act 2 — retrieve a structure without being given one

This is the part that is hard to do by hand. Fast, no refinement.

```
Propose candidate structures for this pattern from the COD.
I am not giving you a starting CIF.

data_file:    /Users/b324240/Git/Agentic-GSAS-II/tutorials/PbSO4_independent/PBSO4.XRA
instprm_file: /Users/b324240/Git/Agentic-GSAS-II/tutorials/PbSO4_independent/INST_XRY.PRM
fmthint:      GSAS
text:         anglesite
top_n:        5
```

Expect the top three to be COD 9000652, 9015524 and 9004484, all
`P b n m`, M20 between 9.9 and 12.5 — the right space group for
anglesite, reached from the pattern and a mineral name.

Each candidate is ranked by de Wolff M20 computed against peaks found in
the pattern itself, with no reference cell involved. The point: M20
predicts which retrieved structures are the wrong phase *before* any
refinement is run, so a bad candidate can be flagged rather than fitted.

M20 in the low teens here is worth mentioning honestly rather than
glossing — on the 240-pattern mineral benchmark that band still carries
real risk, and the recommendation in that case is to widen the search,
not to refine with more confidence.

---

## Act 3 (optional) — ask what it thinks of a finished refinement

```
Assess the refinement in /tmp/apexa_demo/pbso4
```

Returns the trust verdict: every reason the result should be inspected,
separately from the residual. Good place to close, because the argument
of the whole project is that a good residual is not the same as a
correct answer.

---

## If APEXA is unavailable

The same run, directly, with no MCP layer:

```bash
cd /Users/b324240/Git/Agentic-GSAS-II
/Users/b324240/miniconda3/envs/GSASII/bin/python - <<'PY'
import sys; sys.path.insert(0, '.')
sys.path.insert(0, '/Users/b324240/miniconda3/envs/GSASII/GSAS-II')
from dataclasses import replace
from agentic_gsas2 import AgentConfig
from agentic_gsas2.orchestrator import refine_one
cfg = AgentConfig()
cfg = replace(cfg, worstfit=replace(cfg.worstfit, max_steps=12))
D = 'tutorials/PbSO4_independent'
s = refine_one(data_path=f'{D}/PBSO4.XRA', cif_paths=[f'{D}/PbSO4.cif'],
               output_dir='/tmp/apexa_demo/pbso4',
               instprm_path=f'{D}/INST_XRY.PRM', config=cfg,
               limits=(12.0, 150.0), fmthint='GSAS')
print('Rwp %.3f%%  GOF %.3f  stop %s' % (s['Rwp'], s['GOF'], s['stop_reason']))
for i, h in enumerate(s.get('history') or [], 1):
    did = ', '.join(h.get('refined') or [])
    note = '   ranked %s' % h['param'] if h.get('ranked_only') else ''
    print('%2d  %-12s %-22s Rwp %7.3f -> %7.3f%s'
          % (i, h['category'], did[:22],
             h['Rwp_before'] or 0, h['Rwp_after'] or 0, note))
PY
```

There is also a notebook covering the same ground, which survives being
re-run cell by cell if the conversation goes that way:
`Agentic-GSAS-II/tutorials-demo/notebooks/01_quickstart.ipynb`.

---

## Two things to be straight about if asked

**Time-of-flight is currently broken.** The joint TOF+CW benchmark
diverged and was killed. A TOF instrument file has no `U`, `V`, `W` at
all — its profile is `sig-0/1/2`, `alpha`, `beta-*` — and narrowing the
instrument set to the Caglioti terms left the TOF bank with nothing
refinable. The rule is fixed in `experiment.py`; the joint path still
needs a per-histogram context, since one context cannot describe a TOF
bank and a CW bank at once.

**Sequential refinement does not follow thermal expansion.** Across 17
temperatures only `D33` is ever picked for the CuCr2O4 phase and `D11`
never is, so `a` has no route and returns its input value exactly, while
`c` moves without tracking temperature. Rwp climbs from 24% to 70%,
which is the run telling you it has failed.
