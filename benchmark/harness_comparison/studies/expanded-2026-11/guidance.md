Shared workflow guidance for this calibration task

1. Inspect the provided metadata, installed MIDAS version and relevant upstream
   documentation before selecting a route. Use documented public commands and
   inspect actual Python signatures or returned fields before using them; do not
   guess internal helper names. For visibly tilted rings or a poor initial fit,
   consult the documented tilted-detector workflow. Decide from this exposure,
   without assuming an angle or using another acquisition's result.
2. Honor the operator's exact energy, pixel size, transform and mean-of-20 frame
   selection. Keep the image orientation and invalid-pixel mask consistent through
   fitting and validation. Check the calibrant and lattice convention in the
   supplied metadata/documentation; identify any unresolved ambiguity explicitly.
   A filename distance is a nominal hint, not the refined answer.
3. Validate the geometry actually selected for delivery, on the requested image,
   using the documented numerical ring-position checks, coverage and masks. Check
   the geometry's units, finiteness and consistency with the saved artifact.
   Distinguish optimizer convergence from scientific validation. Preserve
   PASS/WARN/FAIL and describe the limitations of the check; a PASS is not proof
   of absolute accuracy. Do not lower thresholds to obtain acceptance.
4. This task has a 3,600-second wall limit and at most 120 model requests. Record
   the start time. Aim to finish scientific work by 3,000 seconds and reserve
   approximately twelve model requests for writing and checking the final decision.
   Avoid starting work that cannot finish inside the remaining budget. If an API
   call fails, inspect its documented interface before retrying. Use a documented
   fallback if essential validation fails and time permits.
5. Once essential validation passes and no material contradiction remains, save
   the adopted result and finish. Integration, repeated fits, deliberately wrong
   controls and extra diagnostic plots are optional unless needed to resolve a
   specific scientific concern. Do not continue optional work after completion.
   If essential checks fail or cannot finish, report rejected, provisional or
   abstained with the unresolved reason. A complete non-accepted decision is useful
   but does not count as an accepted calibration.
6. Before ending, write result_manifest.json as specified in the task. Hash the
   exact final artifact bytes; verify that its relative path exists and that the
   manifest, geometry and final prose agree. Report distance, beam centre, both
   tilts, units, decision and remaining uncertainty. Do not describe a seed, an
   individual frame or an earlier passing candidate as the final mean-of-20 result.

You are authorized to complete the analysis in the trial workspace. Use the
provided facts and resources to resolve routine choices. If essential information
is genuinely unavailable, record the limitation and an honest final decision.

The python executable on PATH has MIDAS 0.26.0 installed. Run locally on CPU, using at most four threads. No remote computation is needed.
