# Repair of benchmark startup, with a new matched cohort

The first 16-attempt batch is retained unchanged. All eight APEXA attempts exited
before any model request because a benchmark-only timeout override addressed an
absent `apexa_provider_openai.AsyncOpenAI` attribute. Production imports that SDK
class locally inside its constructor. The override was added after qualification;
the admission check failed to require the same executable hashes. These are
experiment infrastructure defects, not measured failures of APEXA's reasoning.

Revision 2 configures the actual SDK export, tests the real production constructor
without network access, and requires fresh full native qualifications with the
exact executable and configuration hashes. Those hashes must still match at
freeze and before each wave. A startup failure with zero model requests stops
further admission after the active pair; it cannot consume the remaining schedule.

Repeat the complete 16-attempt matched schedule in `revisions/r2`, with fresh
sessions. Retain all earlier Pi results separately; do not substitute its successful
attempts into the new cohort. Reuse the four qualified numerical fixtures, source
snapshot, retrieval index and software environments. Do not repeat the numerical
fits or change the common task prompt, scientific scoring rules, feedback report,
model, or budgets in response to the earlier outcomes.

The earlier Pi cohort includes answers returned as prose JSON instead of saved
manifests, malformed tool-call output, and a repair attempt requiring provenance
review. These are retained and audited; revision 2 does not save manifests on
the agents' behalf or relax the requirement for a genuine, verified computation.

The earlier native qualification passed before the faulty override, so its success
does not qualify revision 2. No new scientific attempt starts until the corrected
driver has passed the full session test under its final frozen configuration.
