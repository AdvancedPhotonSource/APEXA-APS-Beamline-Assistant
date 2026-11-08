# What “two grains” refers to in MIDAS

Checked November 7–8, 2026, after the fresh FF runs and candidate audit. The
operator recalled the count from MIDAS and clarified that gold cubes are a
familiar calibration example. The [literature check](LITERATURE_CONTEXT.md)
confirms that role. The exact count for this acquisition still requires
acquisition-specific evidence.

Sources were read from the unchanged upstream MIDAS commit
`13b091e2353e16b3f478db1e6fbaca368af6dd5e`, also used for the comparison's upstream
resource snapshot. No experimental input, reconstruction or frozen context was changed.

| Source | Dataset and actual statement | What it supports |
| --- | --- | --- |
| [NF notebook §7d, line 456](https://github.com/marinerhemant/MIDAS/blob/13b091e2353e16b3f478db1e6fbaca368af6dd5e/manuals/nf-hedm/LAB_NOTEBOOK.md#L456) | `nfdev_jul26` gold: two absorbers, one near the axis and one at 496.8 ± 0.7 µm; shadow widths 40.6 and 44.0 µm. The text distinguishes absorbers from single crystals. | A historical physical-object hypothesis for this campaign, based on reported absorption measurements. |
| [NF notebook §8g, line 1195](https://github.com/marinerhemant/MIDAS/blob/13b091e2353e16b3f478db1e6fbaca368af6dd5e/manuals/nf-hedm/LAB_NOTEBOOK.md#L1195) | Later NF reconstruction locates the second cube at (−279.2, −416.7) µm, radius 501.6 µm, with 446 voxels; the near-axis cube has 661 voxels. | The documentation reports a second modality supporting the two-object interpretation. These raw NF measurements have not been revalidated in this fresh FF study. |
| [FF runbook R2e, line 147](https://github.com/marinerhemant/MIDAS/blob/13b091e2353e16b3f478db1e6fbaca368af6dd5e/manuals/ff-hedm/RUNBOOK.md#L147) | Same campaign's older Au reconstruction: “5 grains at confidence 1.000,” described as “the two gold cubes.” | The upstream record itself distinguishes two physical cubes from the number of FF reconstruction rows. Neither five nor two is a fresh reconstruction target. |
| [FF notebook §4a, line 538](https://github.com/marinerhemant/MIDAS/blob/13b091e2353e16b3f478db1e6fbaca368af6dd5e/manuals/ff-hedm/LAB_NOTEBOOK.md#L538) | `Au3_cubes_ff_000008`: “2 grains — a parent and its Σ3 twin.” The notebook introduction assigns this to the 1-ID GE5 dataset at 95 keV. | A different acquisition, detector and energy. Its two-orientation result cannot be transferred to the selected 20-ID Varex scan 001181 at 63.314 keV. |

The selected scan is explicitly identified in FF notebook §9a, line 2069. The
same notebook also retracts a cherry-picked FF/NF radius match (§9h): a candidate
cluster spanned 472–539 µm. Do not pick a convenient fresh candidate to recreate
the published NF coordinate or call that an independent cross-check.

The interpretation is **a gold calibration/validation specimen with documented
support for two spatial objects and an unresolved FF reconstruction**. Treat
the excess candidates as a calibration/reconstruction issue to investigate;
the output does not establish additional physical grains or domains. The fresh
13/19-candidate outputs and all 152 raw-patch checks remain unchanged. The source
audit explains the expectation but does not independently validate the old
reconstructions or justify filtering to two candidates.
