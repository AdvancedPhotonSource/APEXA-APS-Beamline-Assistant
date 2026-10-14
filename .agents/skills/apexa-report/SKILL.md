---
name: apexa-report
description: Write a self-contained HTML report for a finished run, or read the experiment logbook. Use when the user asks to see how a run went, wants a report/summary of a calibration or integration, wants something to share or show a collaborator, or asks what happened earlier in the beamtime.
compatibility: No MIDAS required. matplotlib/numpy are in the APEXA base venv; pypdf and python-docx are only needed for ESAF/logbook files in those formats.
metadata:
  author: pawan-tripathi
  version: "1.0"
---

## Reporting in APEXA

**One tool: `generate_run_report(run_dir=...)`.** It reads whatever manifest the
run left behind, writes one self-contained `.html` beside it, and returns the
path plus the spec.

```
generate_run_report(
    run_dir  = "<the directory holding APEXA_calibration.json / APEXA_integration_series.json>",
    logbook  = "copland:/gdata/.../logbook.md",   # optional
)
```

**Cite the returned `report` path. Never construct one** — the filename is built
from the ESAF and timestamped, and an existing report is never overwritten.

### What it is for

Not "a plot of the results". The report's job is to show **what the acceptance
number does not cover.**

A calibration that stopped on a search bound reports the same residual as one
that converged — so `status: success` cannot distinguish them. In the 20-ID
rotation series **23 of the 35 calibrations that passed the gate (66%) had a
parameter pinned to a bound**, and the information was on disk the whole time in
`at_bounds`. The report draws each refined parameter as its position inside its
own interval, so a railed one is visibly at the wall.

A report whose verdict is **LOOK** means exactly that: the gate passed, and
something it does not measure is wrong. Say so to the user; do not round it up
to "the calibration succeeded".

### The actions are the point

Each finding carries the lever for that specific failure, and **which lever
depends on which parameter railed**. Getting this wrong wastes runs: at 20-ID a
railed `tz` was read as a distortion problem and the `full → radial → none`
ladder was tried three times, none of which can move a tilt bound.

| railed | lever |
|---|---|
| `tx`/`ty`/`tz` | `tol_tilts_deg=10` |
| `p*`, `a_*`, `phi_*` | `refine_distortion="radial"`, then `"none"` |
| `Lsd` | larger `lsd_tol_um` — and check the wavelength; they are degenerate (rule 9) |

When the user agrees to one, run it — the instruction in the report is literal.

### Experiment identity comes from the ESAF

Export the ESAF to a file beside the data (`esaf.json`, `.csv`, `.txt`, `.pdf`,
`.docx`). It is searched for at and above `run_dir`, and supplies the header
(number, title, PI, experimenters, beamline) and the filename:

```
123456_Ceria_calibration_000269_20261014-1346.html
```

**With no ESAF, ask.** Do not infer the experiment or the sample from a path and
present it as fact — the same rule as energy. Unknown fields are omitted.

### The logbook

`read_logbook(path=..., host=...)` reads the running log, over SSH when it lives
on another machine — the usual case, since APEXA runs where the compute is and
the logbook lives wherever the team put it.

```
read_logbook(path="copland:/gdata/.../logbook.md", n_entries=10)
```

It returns the **most recent** entries (the file is tailed, not read from the
top — a logbook's first page is its stalest). Entries come back verbatim with
timestamps.

**Quote them; do not paraphrase them as your own observation.** If you add a
summary, say it is a summary. If the logbook is unreachable, say so — never
reconstruct what it probably said.

### Offline

The report embeds every figure as base64 and references nothing external,
because the beamline host has no public internet and a CDN reference renders as
a blank page there with a perfectly valid-looking file on disk.
