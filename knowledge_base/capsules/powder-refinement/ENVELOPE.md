# Powder refinement capability envelope

| Capability | Execution support | Scientific qualification |
|---|---|---|
| Known phases, independent batch | GSAS-II process jobs | Per-result evidence; provisional by default |
| Sequential transfer | New observations plus compatible state | Incompatible constraints/calibration cold-start explicitly |
| Joint instruments | Shared phase model, per-observation calibration | Requires common sample and conditions |
| Candidate mixtures | Bounded screening and full-fit finalists | Heuristic ranking; no calibrated phase posterior |
| Live tracking | Persistent fast/full policy | Sustained throughput not established by unit tests |
| Image advice | Configured structured image provider | Proposals only; matched scientific evaluation required |
| BGMN/FullProf/MAUD | Surveyed alternatives | No reproduced backend parity in this revision |

## Inputs

`data_path`, `instrument_path`, `cif_paths`; optional explicit `axis`, `units`,
`format`, `wavelength_A`, `source`, `radiation`, `geometry`, `sample_id`,
`frame_id`, `timestamp`, `conditions`, `limits`, `intensity_semantics`,
`calibration_id` and `calibration_status`.

## Outputs

Execution and numerical status, scientific status, diagnostic coverage,
per-histogram residual sums, phases, fractions and conditional uncertainties,
reference/effective cells, exact input identities, GPX/plot artifacts,
source fingerprint, attempt identity and decision history.
