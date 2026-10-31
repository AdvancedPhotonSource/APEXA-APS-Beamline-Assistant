# Phase 2 — Context-conditioned priors

The implemented diode runtime searches its latest 500 persisted runtime runs
for an accepted fit with the exact instrument configuration hash (including its
revision), recipe/source identity, and mode. Its recommended center must lie
inside the new bounds. The selected run ID is recorded in `prior` and used only
as the Gaussian fit's initial guess. It changes neither the grid nor any safety
constraint. Without a match, fitting starts at the current sampled maximum.

Simulation never seeds shadow, replay never seeds simulation, and changing optics
configuration/revision invalidates a prior. A prior is not motion authorization
or evidence that hardware is safe or aligned.

`recommend_initial` and `query_alignment_history` expose separate legacy history.
They can inform discussion but do not meet this runtime's revision/configuration
contract and are not imported automatically. Do not copy their ranges into
physical limits or infer a qualified optimum from historical means. There is no
implemented `allow_cold_start` or random-start Bayesian optimizer, and no measured
sample-budget reduction from these priors.

Proceed to [phase-3-execute.md](phase-3-execute.md) with an explicit plan.
