# Calibration contracts

When an application has a supplied structured calibration specification, pass
`calibration_contract` to `APEXAClient.run_query`. Its value is the path-argument
dictionary accepted by `load_calibration_contract`: the JSON path, original image
and phase template. The client activates it before the first model call and records
that activation. Invalid input stops the query before model execution. For example:

```python
await client.run_query("Review the calibration and save a final decision.",
    calibration_contract={
        "contract_path": "task.json", "image_file": "acquisition.h5",
        "template_param_file": "effective_template.txt",
        "candidate_parameters_file": "candidate.params",
        "candidate_run_file": "candidate_run.json",
        "candidate_execution_file": "execution_evidence.json",
    })
```

The model can also call the loader explicitly when the application did not supply
the structured specification. Merely mentioning a task file in prose does not
guarantee that the model will activate its contract.
An existing handoff also needs its parameter file, run record and stage execution
record. The loader checks these files without writing beside detector data.

The contract contains `schema_version: 1`, `facts`, `requested_model`, `pipeline`,
`distance_policy_fraction` and `zero_tolerance`. Facts name pixel pitch in µm,
nominal distance in mm, energy in keV, cubic lattice in Å, space group, detector
dataset, contiguous frame indices, mean/median reduction and MIDAS transform codes.
This first version supports one cubic phase and no separate dark. Unsupported
phase, frame or dark conventions are refused rather than inferred.

The client owns the contract for one query. It binds `midas_auto_calibrate` inputs
and records the model's original proposal plus any corrections. The native runner
records the flags after the Stage 2 thaw and the fitted restricted coefficients;
the returned artifact, effective inputs and gate are saved in a new execution
receipt. Finalization checks the original pinned handoff or an intact live receipt,
records real repair provenance and preserves the proposed decision alongside the
system-verified fields. A conditional release uses scientific status `provisional`.
Independent reasons to hold remain allowed.

When there is an original handoff, finalization requires its decision separately
from the adopted-result decision. A repair cannot retroactively make a faulty
handoff releasable. For conditional releases, the authoritative summary, check
descriptions and scope limits come from the adopted artifact and its recorded gate.
The original agent summary, reasons, limits and other proposed fields are retained
separately for audit. This does not certify the agent's free-form chat response.

These checks establish consistency with declared facts and the recorded native
execution. They do not establish the physical truth of the declaration, absolute
geometry, or the validity of unconstrained prose claims. A nominal distance is a
policy reference, not an independently calibrated truth. The initial handoff has
supplied-evidence provenance. Copying/editing its files does not create a verified
repair. Arbitrary shell code can bypass native finalization, so external trace and
artifact audits remain necessary. The client registry is a workflow boundary, not
an OS security boundary. No contract is inferred from free-form user prose.
