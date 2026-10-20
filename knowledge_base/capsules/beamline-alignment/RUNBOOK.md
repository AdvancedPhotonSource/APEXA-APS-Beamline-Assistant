# Per-beamline runbook (VOLATILE)

> This file holds beamline-specific PV names, motor-calibration scales, and
> endpoint IDs. Values change between beamtimes. **Re-verify at the start of
> every beamtime** — do not treat anything here as durable truth.

## How to read this

Each beamline section lists exactly the parameters that `apexa_alignment_server`
needs to resolve a task spec to concrete PV reads/writes. Missing values → the
alignment task refuses with `nothing_was_run`, which is correct — guessing a PV
name causes a motor to drive the wrong device.

Fill in once per beamtime, commit as a `WIP` so the DB-recorded warm-start
priors tie to this runbook version.

---

## 1-ID-E (home beamline)

- **Status:** PV names SKELETON — fill from `caget -l '1ide:*' | head`
- **Last verified:** _unset_

### EPICS prefixes

| Family | Prefix | Example |
|---|---|---|
| Sample stage motors | _TBD_ (`1ide:` guess) | `1ide:samX`, `1ide:samY`, `1ide:samZ` |
| Downstream slits | _TBD_ | `1ide:ds_slit_h_cen`, `1ide:ds_slit_v_cen` |
| Scalers (IC/diode) | _TBD_ | `1ide:scaler1` |
| Area detector (PVA) | _TBD_ | `1ide:pixirad:image` |

### Scaler channel assignments

| Scalar | Channel | PV | Role |
|---|---|---|---|
| IC1 | S2 | _TBD_ | monitor (upstream) |
| IC2 | S3 | _TBD_ | monitor (sample position) |
| IC3_HRM | S4 | _TBD_ | HRM feedback |
| IC4_foil_I0 | S5 | _TBD_ | reference (I0) |
| IC5_foil_I1 | S6 | _TBD_ | reference (I1) |
| Diode (hotspot finder) | _TBD_ | _TBD_ | 1D-scan target |

### Detector px → mm scales

| Detector | Pixel size (mm/px) | Sample distance (m) | Notes |
|---|---|---|---|
| pixirad | _TBD_ | _TBD_ | SAXS beamstop centering |
| varex 2923 | 0.1500 | _TBD_ | FF-HEDM |
| ge | 0.2000 | _TBD_ | legacy FF |

### Motor soft limits (verify at beamtime start)

| Motor | Low | High | Units |
|---|---|---|---|
| samX | _TBD_ | _TBD_ | mm |
| samY | _TBD_ | _TBD_ | mm |
| samZ | _TBD_ | _TBD_ | mm |

---

## 6-ID-C (skeleton)

_Not configured. Clone the 1-ID-E section as a starting template._

---

## 20-ID (skeleton)

_Not configured. Clone the 1-ID-E section as a starting template._

---

## Shared conventions (every beamline)

- **Angles** in degrees unless the motor description says otherwise.
- **Positions** in mm unless the motor description says otherwise.
- **Pixel column numbering**: 0 at the leftmost detector edge, column index
  INCREASES rightward (looking downstream).
- **Pixel row numbering**: 0 at the top edge, row index INCREASES downward.
- **Rotation sign**: clockwise viewed from upstream = positive theta.
- **Sample X sign**: inboard = negative, outboard = positive.
- If any of the above is wrong at your beamline, document the sign flip HERE,
  not in Python.

---

## Change log

| Date | By | What changed |
|---|---|---|
| 2026-10-20 | Pawan | Initial skeleton (needs PV fill at 1-ID-E first beamtime) |
