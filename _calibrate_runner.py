#!/usr/bin/env python3
"""Canonical MIDAS detector calibration — the four-stage recipe, run as a subprocess.

This is the executable form of the handbook procedure vendored at
``knowledge_base/capsules/calibrate-integrate/phase-4-calibrate.md`` (§4). It is a
real file rather than an inline ``python -c`` string for three reasons: it can be
linted and unit-tested, it can be diffed against the handbook when MIDAS moves, and
it can be shipped to a remote analysis host and run there.

Called from ``midas_comprehensive_server.midas_auto_calibrate`` under an interpreter
that has ``midas_calibrate_v2`` importable, with a CLEAN env — the pip torch stack
breaks under the C++ DYLD/LD injection ``get_midas_env()`` applies (same "regime 3"
constraint as ``_capability_runner.py``).

Two invariants worth stating up front, because both fail silently when broken:

* **ImTransOpt is applied exactly once**, at read time. ``spec_from_v1_params``
  would otherwise re-apply it from the template's ``extra``, double-flipping the
  frame; and seeding an untransformed frame while the pipeline transforms it lands
  the fit on a mirrored beam centre *with a good strain number*.
* **Capability is feature-detected, never version-pinned.** Older
  ``midas_calibrate_v2`` builds lack ``return_mask``, the four-stage ``mask=``
  argument, ``add_panel_no_expansion_constraint`` and ``phases_from_calibrants``.
  Anything unavailable that would change the answer is refused with
  ``nothing_was_run``; only the sentinel mask degrades, and then it says so.

Emits a single JSON object on stdout; all diagnostics go to stderr.
"""

import argparse
import inspect
import json
import os
import sys
import warnings

# Reserve the real stdout for our single JSON payload. midas_calibrate_v2 prints
# progress with verbose=True, which would otherwise corrupt the JSON the MCP
# server parses off stdout.
_REAL_STDOUT = sys.stdout


def _output(data):
    json.dump(data, _REAL_STDOUT, indent=2, default=str)
    _REAL_STDOUT.write("\n")
    _REAL_STDOUT.flush()


def _fail(msg, **extra):
    """A refusal. `nothing_was_run` is the contract the MCP layer keys on."""
    _output({"status": "error", "error": msg, "nothing_was_run": True, **extra})
    sys.exit(1)


def _accepts(fn, name):
    """True when `fn` takes a keyword argument `name`. The feature probe."""
    try:
        return name in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


def _parse_codes(raw):
    """'2', '1 3', '1,3' -> (2,) / (1, 3). Drops 0 (the no-op code)."""
    if not raw:
        return ()
    out = []
    for tok in str(raw).replace(",", " ").split():
        try:
            code = int(tok)
        except ValueError:
            raise ValueError(f"ImTransOpt code {tok!r} is not an integer")
        if code == 0:
            continue
        if code not in (1, 2, 3):
            raise ValueError(f"ImTransOpt code {code} outside the valid set 0-3")
        out.append(code)
    return tuple(out)


def _parse_gaps(raw):
    """'1 7 1 7 1' -> [1, 7, 1, 7, 1]; '' -> 0 (uniform, no gap)."""
    if not raw:
        return 0
    vals = [int(t) for t in str(raw).replace(",", " ").split()]
    return vals[0] if len(vals) == 1 else vals


def build_parser():
    p = argparse.ArgumentParser(
        prog="_calibrate_runner.py",
        description="Canonical four-stage MIDAS calibration (calibrate-integrate §4).",
    )
    p.add_argument("--image", required=True, help="calibrant frame")
    p.add_argument("--template", required=True,
                   help="v1 CalibrationParams .txt — detector size, px, lattice, thresholds")
    p.add_argument("--dark", default="", help="dark frame (optional)")
    p.add_argument("--output-dir", required=True)

    # Orientation. Applied at READ time only — see the module docstring.
    p.add_argument("--im-trans", default="",
                   help="MIDAS ImTransOpt codes, e.g. '2' or '1 3'")

    # Seeding / ring window. The recipe overwrites these on the template.
    p.add_argument("--wavelength", type=float, default=0.0,
                   help="Angstrom; overrides the template when > 0")
    p.add_argument("--px-um", type=float, default=0.0,
                   help="pixel size in microns; overrides the template when > 0")
    p.add_argument("--calibrant", action="append", default=[],
                   help="repeatable; FIRST entry seeds (handbook §4b)")
    p.add_argument("--min-ring-rad-px", type=float, default=0.0)
    p.add_argument("--max-ring-rad-px", type=float, default=0.0)
    p.add_argument("--min-ring-separation", type=float, default=0.0,
                   help="px; only meaningful with two calibrants")
    p.add_argument("--expected-lsd-um", type=float, default=0.0,
                   help="recorded sample-to-detector distance (um). make_seed has no "
                        "distance hint, so without this a ring-pattern alias can seed "
                        "a false basin hundreds of mm away.")
    p.add_argument("--lsd-tol-um", type=float, default=0.0,
                   help="search half-window around Lsd (um). Default 50000 when an "
                        "expected distance is given, else the template's tolLsd.")
    p.add_argument("--trust-seed-lsd", action="store_true",
                   help="take the seeder's distance even when it disagrees with "
                        "--expected-lsd-um")

    # Image reading.
    p.add_argument("--data-loc", default="exchange/data", help="HDF5 dataset path")
    p.add_argument("--skip-frame", type=int, default=0)
    p.add_argument("--frame-reduce", default="median", choices=("mean", "median"),
                   help="multi-frame collapse; median is right for a calibrant")

    # Tiled detectors. Absent -> single panel, which skips panels entirely.
    p.add_argument("--panel-ny", type=int, default=0)
    p.add_argument("--panel-nz", type=int, default=0)
    p.add_argument("--panel-sy", type=int, default=0)
    p.add_argument("--panel-sz", type=int, default=0)
    p.add_argument("--panel-gaps-y", default="")
    p.add_argument("--panel-gaps-z", default="")

    # Pipeline knobs.
    p.add_argument("--n-iter-stage1", type=int, default=2)
    p.add_argument("--n-iter-stage2", type=int, default=3)
    p.add_argument("--device", default="cpu")

    # Gates.
    p.add_argument("--strain-gate-ue", type=float, default=100.0,
                   help="held-out strain cap in microstrain (handbook §4)")
    p.add_argument("--ignore-gate", action="store_true",
                   help="report the gate verdict but do not fail on it")
    p.add_argument("--skip-scope-gate", action="store_true",
                   help="skip detector_scope_gate (are the rings even on the detector)")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)

    # Everything a package prints goes to stderr from here on.
    sys.stdout = sys.stderr

    # Argument validation first. It must not depend on the environment -- a bad
    # ImTransOpt code is wrong regardless of which packages are importable.
    try:
        im_trans = _parse_codes(args.im_trans)
    except ValueError as e:
        _fail(str(e))

    try:
        import numpy as np
    except Exception as e:
        _fail(f"numpy unavailable in this interpreter: {e}")

    # ---- imports + capability probe -------------------------------------------------
    try:
        from midas_calibrate.params import CalibrationParams
        from midas_calibrate_v2.io.readers import read_image
        from midas_calibrate_v2.seed.auto_seed import make_seed
        from midas_calibrate_v2.compat.from_v1 import (
            spec_from_v1_params, add_panel_parameters)
        from midas_calibrate_v2.compat.to_v1 import write_v1_paramstest
        from midas_calibrate_v2.forward.panels import PanelLayout
        from midas_calibrate_v2.pipelines.four_stage import autocalibrate_four_stage
    except Exception as e:
        _fail(f"midas_calibrate_v2 is not importable in {sys.executable}: {e}",
              interpreter=sys.executable,
              fix="Point APEXA_MIDAS_BIN at the MIDAS pip environment "
                  "(the one where `python -c 'import midas_calibrate_v2'` succeeds).")

    try:
        import importlib.metadata as _md
        v2_version = _md.version("midas-calibrate-v2")
    except Exception:
        v2_version = "unknown"

    def _optional(module, name):
        try:
            mod = __import__(module, fromlist=[name])
            return getattr(mod, name, None)
        except Exception:
            return None

    add_no_expansion = _optional(
        "midas_calibrate_v2.compat.from_v1", "add_panel_no_expansion_constraint")
    phases_from_calibrants = _optional(
        "midas_calibrate_v2.seed.calibrant", "phases_from_calibrants")
    detector_scope_gate = _optional(
        "midas_calibrate_v2.pipelines.diagnostics", "detector_scope_gate")

    # make_seed imports scikit-image at call time. midas-calibrate-v2 only declared
    # it as a hard requirement from 0.22.0 ("scikit-image is a HARD requirement, not
    # an extra"); older builds import it undeclared, so a clean install of one fails
    # inside make_seed with what looks like a data error. Check it up front.
    missing_deps = []
    for _mod, _pkg in (("skimage", "scikit-image"), ("tifffile", "tifffile")):
        try:
            __import__(_mod)
        except Exception:
            missing_deps.append(_pkg)

    can_return_mask = _accepts(read_image, "return_mask")
    can_pass_mask = _accepts(autocalibrate_four_stage, "mask")
    can_frame_reduce = _accepts(read_image, "frame_reduce")

    caps = {
        "midas_calibrate_v2": v2_version,
        "read_image.return_mask": can_return_mask,
        "four_stage.mask": can_pass_mask,
        "add_panel_no_expansion_constraint": add_no_expansion is not None,
        "phases_from_calibrants": phases_from_calibrants is not None,
        "detector_scope_gate": detector_scope_gate is not None,
    }
    notes = []

    if missing_deps:
        _fail(
            "the canonical recipe cannot run: "
            + ", ".join(missing_deps) + " missing from this interpreter. "
            "make_seed imports scikit-image at call time; midas-calibrate-v2 only "
            f"declared it as a hard requirement from 0.22.0, and this is {v2_version}, "
            "so the package installed cleanly without it.",
            interpreter=sys.executable, capabilities=caps,
            missing_dependencies=missing_deps,
            fix="Run under the MIDAS pip environment (set APEXA_MIDAS_BIN), where "
                "midas-calibrate-v2 >= 0.22.0 pulls scikit-image in as a declared "
                "dependency.")

    # ---- the template ---------------------------------------------------------------
    if not os.path.exists(args.template):
        _fail(f"template parameter file not found: {args.template}", capabilities=caps)
    try:
        v1 = CalibrationParams.from_file(args.template)
    except Exception as e:
        _fail(f"could not parse the template parameter file: {e}",
              template=args.template, capabilities=caps)

    if args.wavelength > 0:
        v1.Wavelength = float(args.wavelength)
    if args.px_um > 0:
        v1.pxY = v1.pxZ = float(args.px_um)
    if v1.pxZ <= 0 < v1.pxY:
        v1.pxZ = v1.pxY

    # Two calibrants need an upstream symbol that older builds lack. Refuse rather
    # than quietly calibrating against the first phase only.
    calibrants = [c for c in (args.calibrant or []) if c]
    if len(calibrants) > 1:
        if phases_from_calibrants is None:
            _fail(
                f"two calibrants were requested ({', '.join(calibrants)}) but "
                f"midas_calibrate_v2 {v2_version} has no phases_from_calibrants; "
                "calibrating against the first phase alone would silently discard "
                "the second.",
                capabilities=caps,
                fix="Upgrade midas-calibrate-v2, or pass a single calibrant.")
        v1.Phases = phases_from_calibrants(calibrants)
        if args.min_ring_separation > 0:
            v1.MinRingSeparation = float(args.min_ring_separation)
        notes.append(f"two calibrants declared, {calibrants[0]} seeds (handbook §4b)")
    seed_calibrant = calibrants[0] if calibrants else "CeO2"

    # ---- read the frame; apply the orientation ONCE ---------------------------------
    read_kw = {"data_loc": args.data_loc, "skip_frame": args.skip_frame,
               "im_trans": im_trans}
    if can_frame_reduce:
        read_kw["frame_reduce"] = args.frame_reduce

    bad_mask = None
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            if can_return_mask:
                img, bad_mask = read_image(args.image, return_mask=True, **read_kw)
            else:
                img = read_image(args.image, **read_kw)
                notes.append(
                    f"midas_calibrate_v2 {v2_version} has no read_image(return_mask=); "
                    "sentinels handled by the handbook's img[img<0]=0 fallback only — "
                    "an unsigned high sentinel (EIGER 2**32-1) would NOT be caught.")
            for w in caught:
                if "Sentinel" in type(w.message).__name__ or "sentinel" in str(w.message):
                    notes.append(f"read_image: {w.message}")
    except Exception as e:
        _fail(f"could not read the calibrant frame: {e}",
              image=args.image, data_loc=args.data_loc, capabilities=caps)

    img = np.asarray(img, dtype=float)
    img[img < 0] = 0.0   # belt and braces for a low-sentinel file (handbook §4)

    dark = None
    if args.dark:
        try:
            dark = read_image(args.dark, **read_kw)
            dark = np.asarray(dark, dtype=float)
        except Exception as e:
            _fail(f"could not read the dark frame: {e}", dark=args.dark, capabilities=caps)
        if not np.isfinite(dark).all():
            _fail("the dark frame contains NaN or inf — one non-finite pixel poisons "
                  "the whole subtracted frame and the fit will not fail, it will "
                  "return a wrong geometry.",
                  dark=args.dark, capabilities=caps,
                  fix="Regenerate the dark, or run without one.")
        if dark.shape != img.shape:
            _fail(f"dark shape {dark.shape} != image shape {img.shape}",
                  capabilities=caps)

    ny, nz = int(img.shape[1]), int(img.shape[0])
    if v1.NrPixelsY <= 0 or v1.NrPixelsZ <= 0:
        v1.NrPixelsY, v1.NrPixelsZ = ny, nz
    elif (v1.NrPixelsY, v1.NrPixelsZ) != (ny, nz):
        notes.append(
            f"template says {v1.NrPixelsY}x{v1.NrPixelsZ}, frame is {ny}x{nz} "
            f"after ImTransOpt {list(im_trans) or 'none'}; using the frame")
        v1.NrPixelsY, v1.NrPixelsZ = ny, nz

    # ---- scope gate: are the rings even on this detector? ---------------------------
    scope = None
    if detector_scope_gate is not None and not args.skip_scope_gate:
        try:
            lsd_for_gate = float(v1.Lsd) if v1.Lsd > 0 else 0.0
            if lsd_for_gate > 0 and v1.Wavelength > 0 and v1.pxY > 0:
                g = detector_scope_gate(
                    wavelength_A=float(v1.Wavelength), Lsd_um=lsd_for_gate,
                    pxY_um=float(v1.pxY), NrPixelsY=v1.NrPixelsY,
                    NrPixelsZ=v1.NrPixelsZ)
                scope = {"severity": getattr(g, "severity", None),
                         "message": getattr(g, "message", str(g))}
                if str(scope["severity"]).lower() in ("halt", "error", "fail"):
                    _fail(
                        "detector_scope_gate halted this frame: "
                        f"{scope['message']}. The fitter does not fail when the "
                        "expected rings fall outside the detector — it succeeds, and "
                        "the result looks plausible in every downstream diagnostic.",
                        scope_gate=scope, capabilities=caps,
                        fix="Check the energy/wavelength and the recorded distance, "
                            "or pass --skip-scope-gate if you know better.")
        except Exception as e:
            notes.append(f"detector_scope_gate could not run: {e}")

    # ---- seed, AFTER cleaning -------------------------------------------------------
    seed_kw = {}
    if _accepts(make_seed, "use_diplib"):
        # Older builds default this True. Upstream flipped it to False after diplib
        # segfaulted on one platform and hung a Windows kernel at import; scipy's
        # median filter is the safe path and is what 0.22.0 uses.
        seed_kw["use_diplib"] = False
    try:
        seed = make_seed(img, wavelength_A=float(v1.Wavelength),
                         px_um=float(v1.pxY), calibrant=seed_calibrant, **seed_kw)
    except Exception as e:
        _fail(f"make_seed failed: {e}",
              calibrant=seed_calibrant, wavelength_A=float(v1.Wavelength),
              px_um=float(v1.pxY), capabilities=caps,
              fix="Common causes, in archive order: the frame is a dark/shutter-closed "
                  "exposure; the dark is wrong for this frame; sentinels are still in "
                  "the frame; the wavelength is wrong.")

    seed_info = {
        "BC_y": float(seed.BC_y), "BC_z": float(seed.BC_z),
        "Lsd_um": float(seed.Lsd_um),
        "rms_px": float(getattr(seed, "rms_px", float("nan"))),
        "n_measured": int(getattr(seed, "n_measured", 0)),
        "threshold_rung": int(getattr(seed, "threshold_rung", 0)),
        "calibrant": getattr(seed, "calibrant_name", seed_calibrant),
        "method": "make_seed",
        "notes": getattr(seed, "notes", ""),
    }
    if seed_info["threshold_rung"] > 0:
        notes.append(
            f"make_seed relaxed its threshold to rung {seed_info['threshold_rung']} — "
            "the strict setting found nothing. Relaxed far enough, the arc finder "
            "fits noise; treat this geometry as provisional and check a ring overlay.")

    # ---- the recipe: start from scratch, never from an existing block ---------------
    # BC always comes from the seed -- it genuinely has to be found. Lsd is
    # different: it is a RECORDED instrument setting, and make_seed has no distance
    # hint, so it infers the distance from ring radii alone. When the ring pattern
    # aliases, the seeder lands in a false basin and the fit happily refines inside
    # it: measured at 20-ID, a 900 mm setup seeded 595 mm and refined to 1617 ue,
    # while the same frame given the distance reached 894 mm. Hard rule 9's lever is
    # exactly this -- tie Lsd to a recorded distance.
    v1.BC_y, v1.BC_z = seed.BC_y, seed.BC_z
    lsd_info = {"seed_lsd_um": float(seed.Lsd_um),
                "expected_lsd_um": float(args.expected_lsd_um) or None,
                "source": "seed"}
    if args.expected_lsd_um > 0:
        _rel = abs(float(seed.Lsd_um) - args.expected_lsd_um) / args.expected_lsd_um
        lsd_info["relative_disagreement"] = round(_rel, 4)
        if _rel > 0.10 and not args.trust_seed_lsd:
            v1.Lsd = float(args.expected_lsd_um)
            lsd_info["source"] = "expected (seed rejected)"
            notes.append(
                f"the seeder returned Lsd {seed.Lsd_um/1000:.1f} mm against a recorded "
                f"{args.expected_lsd_um/1000:.1f} mm ({_rel*100:.0f}% off) — a "
                "ring-pattern alias, not a measurement. Pinned to the recorded "
                "distance and bounded; pass trust_seed_lsd to override.")
        else:
            v1.Lsd = float(seed.Lsd_um)
            lsd_info["source"] = ("seed (agrees with expected)" if _rel <= 0.10
                                  else "seed (forced by trust_seed_lsd)")
    else:
        v1.Lsd = float(seed.Lsd_um)
        notes.append("no recorded distance supplied, so Lsd rests entirely on the "
                     "ring-pattern seed — check it against the setup.")

    # Bound the search. spec_from_v1_params turns tolLsd into the Lsd bound.
    _lsd_tol = (args.lsd_tol_um if args.lsd_tol_um > 0
                else (50000.0 if args.expected_lsd_um > 0 else float(v1.tolLsd or 0)))
    if _lsd_tol > 0:
        v1.tolLsd = _lsd_tol
        lsd_info["tol_um"] = _lsd_tol

    v1.tx = v1.ty = v1.tz = 0.0
    for n in [f"p{i}" for i in range(15)]:
        setattr(v1, n, 0.0)
    if args.min_ring_rad_px > 0:
        v1.MinRingRad = float(args.min_ring_rad_px)
    if args.max_ring_rad_px > 0:
        v1.MaxRingRad = float(args.max_ring_rad_px)
    if v1.MaxRingRad <= 0:
        # validate() rejects 0; default to the largest circle inscribed in the frame.
        v1.MaxRingRad = float(min(v1.NrPixelsY, v1.NrPixelsZ) * 0.5)
        notes.append(f"MaxRingRad defaulted to {v1.MaxRingRad:.0f} px (half the short axis)")
    if v1.MinRingRad <= 0:
        # validate() does NOT check this one, contrary to the handbook comment.
        v1.MinRingRad = 120.0
        notes.append("MinRingRad defaulted to 120 px")

    # RhoD, in MICRONS (hard rule 12). Do NOT leave this to spec_from_v1_params:
    # the derivation is version-dependent and silently wrong on older builds --
    # 0.5.3 does `rho_d = v1.MaxRingRad` (PIXELS), 0.22.0 does
    # `v1.MaxRingRad * 0.5 * (pxY + pxZ)` (microns). The distortion polynomial lives
    # in rho = R_um / RhoD, so a pixel-valued RhoD rescales every p0..p14 by the
    # pixel size and downstream integration then applies a wrong distortion -- with
    # no error and a perfectly good strain number. Measured on a 172 um Pilatus:
    # RhoD written as 700 instead of 120400.
    _rho_expected = float(v1.MaxRingRad) * 0.5 * (float(v1.pxY) + float(v1.pxZ))
    if v1.RhoD <= 0:
        v1.RhoD = _rho_expected
        notes.append(f"RhoD set to {v1.RhoD:.1f} um "
                     f"(MaxRingRad {v1.MaxRingRad:.0f} px x px {v1.pxY:.1f} um); "
                     "not left to the version-dependent upstream derivation")
    elif v1.RhoD < 0.5 * _rho_expected:
        notes.append(
            f"template RhoD {v1.RhoD:g} is far below the {_rho_expected:.0f} um implied "
            f"by MaxRingRad x px -- it looks like PIXELS, not microns (hard rule 12). "
            "Left as given; the distortion block may be mis-scaled.")

    try:
        v1.validate()
    except Exception as e:
        _fail(f"the parameter set is invalid after seeding: {e}",
              capabilities=caps, seed=seed_info)

    # ---- spec; clear im_trans so the pipeline does not re-apply it ------------------
    spec = spec_from_v1_params(v1)
    respec = getattr(spec, "im_trans", ())
    if im_trans and tuple(respec or ()) :
        notes.append(
            f"template ImTransOpt {list(respec)} cleared on the spec — the transform "
            f"{list(im_trans)} was already applied at read time; applying it twice "
            "would flip the frame back.")
    try:
        spec.im_trans = ()
    except Exception:
        pass

    # ---- panels (tiled detectors only) ----------------------------------------------
    layout = None
    n_panels = 0
    if args.panel_ny > 0 and args.panel_nz > 0:
        n_panels = args.panel_ny * args.panel_nz
        if add_no_expansion is None:
            _fail(
                f"a tiled layout ({args.panel_ny}x{args.panel_nz} = {n_panels} panels) "
                f"was requested but midas_calibrate_v2 {v2_version} has no "
                "add_panel_no_expansion_constraint. Without that gauge the panel field "
                "has a free expansion mode that mimics an Lsd error and rails panels "
                "(hard rule 7).",
                capabilities=caps,
                fix="Upgrade midas-calibrate-v2, or calibrate this detector as a "
                    "single panel.")
        add_panel_parameters(spec, n_panels=n_panels, tol_shift_px=2.0,
                             tol_rot_deg=0.0, enable_lsd=False, enable_p2=False)
        add_no_expansion(spec)
        layout = PanelLayout.regular(
            args.panel_ny, args.panel_nz, args.panel_sy, args.panel_sz,
            gap_y=_parse_gaps(args.panel_gaps_y),
            gap_z=_parse_gaps(args.panel_gaps_z))
        notes.append(f"tiled: {n_panels} panels, in-plane shift only (hard rule 4)")

    # ---- run -------------------------------------------------------------------------
    run_kw = dict(spec=spec, panel_layout=layout, dark=dark,
                  stage1_lsd_tol_um=(_lsd_tol if _lsd_tol > 0 else None),
                  n_iter_stage1=args.n_iter_stage1,
                  n_iter_stage2=args.n_iter_stage2,
                  common_kwargs=dict(drop_gap_fits=True),
                  device=args.device, verbose=True)
    if can_pass_mask and bad_mask is not None:
        run_kw["mask"] = bad_mask
    elif bad_mask is not None:
        notes.append(
            f"midas_calibrate_v2 {v2_version} four-stage takes no mask= argument; "
            "sentinels are zeroed in the image but not excluded from the fit.")

    try:
        res = autocalibrate_four_stage(v1, img, **run_kw)
    except Exception as e:
        _fail(f"autocalibrate_four_stage failed: {e}",
              capabilities=caps, seed=seed_info, notes=notes)

    # ---- export: v1 format, which is what everything downstream reads ---------------
    os.makedirs(args.output_dir, exist_ok=True)
    paramstest = os.path.join(args.output_dir, "paramstest_v2.txt")
    sidecar = None
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            sidecar = write_v1_paramstest(res.stage2.unpacked, v1, paramstest)
            for w in caught:
                notes.append(f"write_v1_paramstest: {w.message}")
    except Exception as e:
        _fail(f"the fit converged but write_v1_paramstest failed: {e}",
              capabilities=caps, seed=seed_info, notes=notes)

    def _f(x):
        try:
            return float(x)
        except Exception:
            return None

    strain_test = _f(getattr(res, "stage4_strain_uE_test", None))
    strain_full = _f(getattr(res, "stage4_strain_uE", None))
    gap = (abs(strain_test - strain_full)
           if strain_test is not None and strain_full is not None else None)

    gate = {
        "threshold_uE": args.strain_gate_ue,
        "stage4_strain_uE_test": strain_test,
        "stage4_strain_uE_full": strain_full,
        "held_out_vs_full_gap_uE": gap,
        "stage4_strain_uE_test_med": _f(getattr(res, "stage4_strain_uE_test_med", None)),
        "stage3_test_rms_um": _f(getattr(res, "stage3_test_rms_um", None)),
        # Regime, so a failure can be read against the geometry rather than in the
        # abstract: the cap is a fraction |1 - R_obs/R_pred|, so strain = dpixel/R_ring
        # and a short throw structurally reads higher (RUNBOOK, amended 2026-09-08).
        "ring_radius_window_px": [_f(v1.MinRingRad), _f(v1.MaxRingRad)],
        "lsd_um": _f(res.stage2.unpacked.get("Lsd")) if hasattr(
            res.stage2.unpacked, "get") else None,
    }
    gate["passed"] = (strain_test is not None and strain_test < args.strain_gate_ue)

    payload = {
        "status": "success",
        "engine": f"canonical-v2:four_stage ({v2_version})",
        "recipe": "calibrate-integrate phase-4-calibrate.md §4",
        "capabilities": caps,
        "image": os.path.abspath(args.image),
        "dark": os.path.abspath(args.dark) if args.dark else None,
        "template": os.path.abspath(args.template),
        "output_dir": os.path.abspath(args.output_dir),
        "paramstest_file": paramstest,
        "panelshifts_file": str(sidecar) if sidecar else None,
        "im_trans_applied": list(im_trans),
        "n_panels": n_panels,
        "calibrants": calibrants or [seed_calibrant],
        "seed": seed_info,
        "lsd": lsd_info,
        "scope_gate": scope,
        "gate": gate,
        "notes": notes,
    }

    if not gate["passed"] and not args.ignore_gate:
        payload["status"] = "error"
        payload["nothing_was_run"] = False   # it ran; the result is not trustworthy
        payload["error"] = (
            f"held-out strain {strain_test} µε is not below the {args.strain_gate_ue} µε "
            "gate — a calibrant refining worse than the gate is not a calibration "
            "(handbook §4, halt H3)."
            if strain_test is not None else
            "the pipeline returned no held-out strain, so the gate cannot be evaluated.")
        payload["fix"] = (
            "Before calling this a bad calibration, LOOK: the cap is a fraction, so a "
            "short sample-to-detector distance reads a higher microstrain for the same "
            "real precision. Check the ring overlay and the ring_radius_window_px/lsd_um "
            "in `gate`. Pass ignore_calibration_gate=True to accept it deliberately, or "
            "raise strain_gate_uE for this geometry.")
        _output(payload)
        sys.exit(2)

    _output(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
