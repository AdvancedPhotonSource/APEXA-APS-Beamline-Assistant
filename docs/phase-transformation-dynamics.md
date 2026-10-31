# Phase-transformation dynamics from diffraction series

This workflow is for ordered 2D-detector line scans or time series that are
integrated into one 1D powder pattern per frame. It is intended to expose when
diffraction features appear, disappear, split or shift, then test candidate phase
models on selected observations. It is not an FF-HEDM grain-reconstruction plan.

For the `tkliu_oct26` dataset inspected in the APEXA session, the directory
listing showed a CeO₂ calibrant HDF5 file, Pilatus TIFF line scans in `EXP001`–
`EXP025`, 750 TIFFs in the inspected `EXP001` scan, and TIFFs in a separate
`Background` directory. Folder names encode sample and acquisition settings.
These observations came from directory listings; raw-frame values, TIFF metadata,
and the correspondence between background and sample files still need to be
checked on the host that can read `/gdata/dm/20ID/HT_HEDM/2026-3/tkliu_oct26/`.

## 1. Establish acquisition order and physical coordinates

1. Confirm the scan is complete and collect the acquisition record that maps each
   frame to time, translation position, or another changing condition. Preserve
   frame identity through integration.
2. Validate the CeO₂ calibration against its fit residuals and confirm that the
   parameter file is a complete MIDAS integration parameter set. Inspect the
   radial and azimuth ranges, masks, detector orientation, wavelength, and units;
   do not infer these from a filename or copy a detector radius from a calibration
   summary without checking the MIDAS parameter convention.
3. Inspect representative sample and background frames, including dimensions,
   detector counts, exposure, attenuation and acquisition metadata. A folder named
   `Background` does not by itself establish that its images are dark-current
   frames or that their attenuation matches the sample.

Directory scans are sorted by numeric filename suffix, with a stable filename
tie-break. If filename order is not acquisition order, supply an explicit ordered
`images` list; an axis mapping supplies coordinates but does not reorder rows. A
CSV keyed by `filename` can map exact file names; `sequence_index` or
`pattern_index` can map zero-based keys when the CSV has `0`, otherwise
one-based keys. Use `frame_index` for numeric frame suffixes. Never label frame
number as seconds or millimetres unless the acquisition record supports that
conversion.

## 2. Integrate a representative subset

Use `midas_integrate_series` once for many separate frames. For TIFF inputs, set
`pattern="*.tiff"` (or the actual extension) and first use an evenly spaced
`max_files=3` pilot. Set `result_folder` to an analysis output directory. Choose
`dark_source="none"` only when intentionally integrating without a dark; otherwise
identify a valid dark/background file and its relationship to sample exposure and
attenuation before passing `dark_file` or a narrowly scoped `dark_dir` and
`dark_pattern`. The current sample and background names do not show an obvious
frame-pair convention, so automatic nearest-frame matching is not justified until
that relationship is confirmed.

Check the integration manifest for per-file success, the selected dark and whether
it was applied, output grid, and errors. If multiple TIFF backgrounds are confirmed
to share detector settings, exposure and attenuation, use
`combine_detector_backgrounds` with `method="mean"` or `"median"` and an explicit
derived output path. It validates image shape and writes a TIFF plus provenance
manifest; it does not decide whether subtraction is appropriate. The directory
listing showed att4 and att5 backgrounds; do not use those for att3 samples unless
the acquisition record validates the correction. Compare the pilot lineouts for peak
alignment, intensity scale and clipping before scaling to a full experiment.
Keep exposure, attenuation, polarization, masks, background correction and radial
grid consistent or record the changes so intensity trends are not mistaken for
phase-fraction changes.

## 3. Visualize the full lineout series

Use `plot_lineout_series_contour` on the resulting `.xye` directory. The tool
returns a downsampled Plotly heatmap in the APEXA canvas, a PNG, and an optional
standalone HTML. Pass `axis_csv` with a verified time or position map to use a
physical vertical axis; otherwise the plot is labelled with frame number or
pattern index. Patterns with incompatible x grids are interpolated only over
their measured overlap; unmeasured edges remain blank.

Example axis map:

```csv
filename,position_mm
frame_0001.xye,0.00
frame_0002.xye,0.02
```

Inspect peak onset, disappearance, splitting, broadening, and shifts. Treat this
map as qualitative until calibration, background, count normalization and scan
metadata are checked.

## 4. Track diffraction features

Run `track_diffraction_peak_series` on the same lineout series and use the same
axis map. It selects local maxima by prominence, fits local Gaussian profiles,
and links nearby candidates across frames. Review the trajectory plot and CSV;
adjust the prominence threshold and link radius after inspecting a pilot rather
than treating defaults as scientifically tuned. The tracks are unassigned
features: overlap, texture, background, peak splitting and changing intensity can
break or merge tracks. Fit covariance does not include calibration or model
systematics.

## 5. Test phase models on selected patterns

Use composition, temperature/processing history and verified peak positions to
assemble candidate CIFs. Inspect alternatives and unexplained reflections. For a
selected set or a full series, `refine_series_submit` can run independent fits or
sequential warm-started fits in GSAS-II. Supply the measured instrument file and
candidate CIFs. Pass `axis_values` in exactly the same order as `data_files`, plus
`axis_units="s"` or `"mm"`; the refinement canvas then plots modeled phase
fractions against that scan coordinate. Without these values it uses job index.
Poll the returned job with `refinement_status`.

Modeled fractions are conditional on the phases and corrections in the model.
They do not account automatically for amorphous material or missing phases.
Quantitative claims need validated phase coverage, instrument/background model,
texture and absorption treatment, uncertainty, and comparison to reference
measurements. Rwp alone is not phase identification or a trust verdict.

## Capability boundary

APEXA's registered MIDAS path covers calibration and series integration; the
series contour and peak-trajectory tools provide the missing dynamics views; and
the Agentic-GSAS-II service supports sequential refinement with phase-fraction
plots. These are software capabilities, not evidence that the remote data path,
MIDAS interpreter, GSAS-II runtime, calibration or correction model has been
validated for a particular experiment. See the [diffraction software
survey](diffraction-software-survey.md) for pyFAI, MAUD, TOPAS and other engine
boundaries.
