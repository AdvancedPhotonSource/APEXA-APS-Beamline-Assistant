# Gold cubes as HEDM calibration specimens

Checked November 8, 2026, following the operator's clarification that this is a
familiar calibration example. **The calibration role is supported by a published
HEDM example and the upstream MIDAS workflow.** It changes the framing of our
unresolved reconstruction, not its retained numerical results.

## Verified published example

Sharma, H., Park, J.-S., Shastri, S. and Kenesei, P. (2026).
*MIDAS: a quantitative framework for high-energy diffraction microscopy.
Part II: accuracy, robustness and best practices.* Acta Crystallographica A82,
305–320. [DOI: 10.1107/S2053273326004018](https://doi.org/10.1107/S2053273326004018).
[Open full text at OSTI](https://www.osti.gov/servlets/purl/17427253).

Section 3.2, printed page 314 (PDF page 10), states:

> An initial guess of the experimental setup was obtained using an Au-cube sample
> with 3–4 grains centered around the rotation axis.

The following sentence says that the experimental parameters were then refined
using the sample data. This is an example of gold cubes used to initialize
NF-HEDM geometry. It does not identify the selected July 2026 20-ID FF acquisition
or supply a two-grain reference for it. Full-text bytes and the inspected excerpt
are identified in `LITERATURE_CONTEXT.json`; the downloaded PDF stays in the
ignored local research directory.

## Upstream workflow

[MIDAS NF geometry workflow §7](https://github.com/marinerhemant/MIDAS/blob/13b091e2353e16b3f478db1e6fbaca368af6dd5e/manuals/nf-hedm/phase-3-geometry.md#L487)
describes refining the geometry against a known single-crystal calibrant, a gold
cube, before moving to the real sample. The separate
[acquisition-source audit](GRAIN_COUNT_PROVENANCE.md) traces the two-object
expectation for the selected campaign.

## Interpretation for this case study

Use **gold calibration/validation specimen with an unresolved FF reconstruction**.
The 13 initial and 19 updated candidates are diagnostic outputs. Their count
does not establish more physical grains or crystallographic domains. Investigate
geometry, frame/rotation conventions, extended-spot treatment and indexing
duplication before proposing specimen complexity as an explanation.

The existing raw-patch checks establish nearby detector signal, not held-out
orientation validity. An in-sample residual reduction or selecting two convenient
candidates cannot verify the expected objects. A new numerical run needs a
specific correction supported by evidence and a validation criterion independent
of obtaining the desired count. No numerical settings or frozen comparison
resources were changed by this literature check.

## Related lead, not used as full-text evidence

Park, J.-S., Sharma, H. and Kenesei, P. (2021), *Repeatability and sensitivity
characterization of the far-field high-energy diffraction microscopy instrument
at the Advanced Photon Source*, Journal of Synchrotron Radiation 28, 1786–1800,
[DOI: 10.1107/S1600577521008286](https://doi.org/10.1107/S1600577521008286).
Metadata were verified; publisher search text mentions gold-based calibration,
but the full text was not obtained. No detailed claim here relies on that snippet.
