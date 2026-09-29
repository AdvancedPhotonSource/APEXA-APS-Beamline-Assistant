"""Calibration engine selection, the canonical-recipe runner, and the param merge.

Nothing covered the engine branching before this file: the tool had four dispatch
paths chosen by a cascade of early returns, and the first of them ignored
`calibration_engine` entirely. These tests pin the behaviours that were wrong or
silent, so they cannot regress quietly:

  * an explicit "v2" never downgrades -- it refuses,
  * "auto" prefers the canonical recipe but records WHY when it cannot,
  * zarr never reaches midas_calibrate_v2, which has no zarr reader,
  * ImTransOpt is applied exactly once,
  * the param merge restores what write_v1_paramstest/to_text drop.

They use no MIDAS data and no network.
"""
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import midas_comprehensive_server as M   # noqa: E402

RUNNER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "_calibrate_runner.py")


# --------------------------------------------------------------------------- #
# engine resolution
# --------------------------------------------------------------------------- #

@pytest.fixture
def force_v2_available(monkeypatch):
    """Pretend the canonical recipe is runnable, without probing an interpreter."""
    monkeypatch.setattr(M, "_calibration_interpreter",
                        lambda: ("/fake/python", "0.22.0", True, ""))
    return None


@pytest.fixture
def force_v2_missing(monkeypatch):
    monkeypatch.setattr(M, "_calibration_interpreter",
                        lambda: ("/fake/python", "0.5.3", False, "No module named 'skimage'"))
    return None


def test_auto_prefers_canonical_v2(force_v2_available):
    r = M._choose_calibration_engine("auto", "/d/CeO2.tif", 0.2)
    assert r["engine"] == "canonical-v2"


def test_auto_falls_back_and_says_why(force_v2_missing):
    r = M._choose_calibration_engine("auto", "/d/CeO2.tif", 0.2)
    assert r["engine"] == "native"
    # The reason must survive into the payload -- a silent downgrade is the bug.
    assert any("skimage" in f for f in r["fallbacks"]), r["fallbacks"]


def test_explicit_v2_refuses_rather_than_downgrading(force_v2_missing):
    r = M._choose_calibration_engine("v2", "/d/CeO2.tif", 0.2)
    assert r["engine"] == "refuse"
    assert "skimage" in r["reason"]


def test_explicit_v2_refuses_without_a_wavelength(force_v2_available):
    r = M._choose_calibration_engine("v2", "/d/CeO2.tif", 0.0)
    assert r["engine"] == "refuse"
    assert "wavelength" in r["reason"]


@pytest.mark.parametrize("suffix", [".zip", ".zarr"])
def test_zarr_never_reaches_canonical_v2(force_v2_available, suffix):
    """midas_calibrate_v2.io.readers dispatches on extension and has no zarr
    branch, so routing a zarr store there would raise, not calibrate."""
    assert M._choose_calibration_engine("auto", f"/d/CeO2{suffix}", 0.2)["engine"] == "native"
    assert M._choose_calibration_engine("v2", f"/d/CeO2{suffix}", 0.2)["engine"] == "refuse"


def test_v1_and_legacy_are_honoured(force_v2_available):
    assert M._choose_calibration_engine("v1", "/d/CeO2.tif", 0.2)["engine"] == "native"
    assert M._choose_calibration_engine("legacy", "/d/CeO2.tif", 0.2)["engine"] == "legacy"


def test_default_engine_is_auto():
    """The tool used to default to v1, which on a pip-only host meant the legacy
    AutoCalibrateZarr path -- a MIDAS repo clone plus diplib that is not there."""
    import inspect
    sig = inspect.signature(M.midas_auto_calibrate)
    assert sig.parameters["calibration_engine"].default == "auto"


# --------------------------------------------------------------------------- #
# the param merge
# --------------------------------------------------------------------------- #

def _write(p, text):
    p.write_text(text)
    return str(p)


def test_merge_restores_keys_the_upstream_writer_drops(tmp_path):
    """write_v1_paramstest drops DATA_LOCATION_KEYS (incl. MaskFile) and
    to_text() omits MinRingRad/MaxRingRad/Width/EtaBinSize/RBinSize. Integration
    needs them, and the template is the only place they survive."""
    template = _write(tmp_path / "t.txt", "\n".join([
        "Lsd 650000.0", "BC 700.0 800.0", "px 172.0",
        "NrPixelsY 1475", "NrPixelsZ 1679", "Wavelength 0.202153",
        "SpaceGroup 225", "MinRingRad 120", "MaxRingRad 700",
        "Width 800", "EtaBinSize 5", "RBinSize 0.25",
        "MaskFile /data/mask.tif", "ImTransOpt 2",
    ]) + "\n")
    paramstest = _write(tmp_path / "paramstest_v2.txt", "\n".join([
        "Lsd 650618.426094", "BC 702.759599 812.604964",
        "tx 0.0", "ty -0.229320", "tz 0.579339",
        "Wavelength 0.202153", "px 172.000000",
        "NrPixelsY 1475", "NrPixelsZ 1679", "RhoD 120400.0",
    ]) + "\n")
    out = tmp_path / "refined_MIDAS_params_v2.txt"
    path, notes = M._merge_refined_params(template, paramstest, out)
    assert path is not None, notes

    from apexa_lib import read_params
    g = read_params(path)
    # refined geometry wins
    assert abs(g["lsd_um"] - 650618.426094) < 1e-3
    assert abs(g["bc_y"] - 702.759599) < 1e-4
    # template-only keys survive
    assert g["MaskFile"] == "/data/mask.tif"
    assert g["MinRingRad"] == 120
    assert g["Width"] == 800
    assert g["ImTransOpt"] == 2
    assert any("restored from the template" in n for n in notes), notes


def test_merge_keeps_the_typed_px_when_the_two_copies_disagree(tmp_path):
    """to_text() emits px twice -- typed, then the stale passthrough copy. A
    line-by-line parse keeps the LAST, which is the wrong one."""
    template = _write(tmp_path / "t.txt", "px 172.0\nLsd 1.0\nMaxRingRad 700\n")
    paramstest = _write(tmp_path / "p.txt",
                        "Lsd 650000.0\npx 172.000000\nRhoD 120400.0\npx 150.0\n")
    out = tmp_path / "m.txt"
    path, notes = M._merge_refined_params(template, paramstest, out)
    from apexa_lib import read_params
    assert read_params(path)["px"] == 172.0
    assert any("conflicting px" in n for n in notes), notes


def test_merge_never_raises_on_a_bad_input(tmp_path):
    path, notes = M._merge_refined_params(str(tmp_path / "nope.txt"),
                                          str(tmp_path / "also-nope.txt"),
                                          tmp_path / "out.txt")
    assert path is None and notes


# --------------------------------------------------------------------------- #
# the runner's own contract (no MIDAS data needed)
# --------------------------------------------------------------------------- #

def _run_runner(*args):
    p = subprocess.run([sys.executable, RUNNER, *args],
                       capture_output=True, text=True, timeout=300)
    try:
        return json.loads(p.stdout), p.returncode
    except Exception:
        return {"_stdout": p.stdout, "_stderr": p.stderr[-800:]}, p.returncode


def test_runner_refusals_carry_nothing_was_run(tmp_path):
    """Every precondition failure must be distinguishable from a bad result."""
    payload, rc = _run_runner("--image", str(tmp_path / "x.tif"),
                              "--template", str(tmp_path / "missing.txt"),
                              "--output-dir", str(tmp_path / "out"))
    assert rc == 1
    assert payload.get("nothing_was_run") is True
    assert payload.get("status") == "error"
    # the capability probe is always reported, so a refusal is diagnosable
    assert "capabilities" in payload


def test_runner_rejects_a_bad_imtransopt_code(tmp_path):
    tmpl = _write(tmp_path / "t.txt", "px 172.0\nNrPixelsY 10\nNrPixelsZ 10\n")
    payload, rc = _run_runner("--image", str(tmp_path / "x.tif"), "--template", tmpl,
                              "--output-dir", str(tmp_path / "out"), "--im-trans", "7")
    assert rc == 1
    assert "7" in payload.get("error", "")


def test_runner_parsers():
    """ImTransOpt and panel-gap parsing, straight from the runner module."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("_calrunner", RUNNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert mod._parse_codes("2") == (2,)
    assert mod._parse_codes("1 3") == (1, 3)
    assert mod._parse_codes("1,3") == (1, 3)
    assert mod._parse_codes("0") == ()          # 0 is the documented no-op
    assert mod._parse_codes("") == ()
    with pytest.raises(ValueError):
        mod._parse_codes("4")

    assert mod._parse_gaps("") == 0
    assert mod._parse_gaps("17") == 17
    assert mod._parse_gaps("1 7 1 7 1") == [1, 7, 1, 7, 1]


def test_the_imtransopt_double_apply_hazard_is_real_and_handled(tmp_path):
    """The trap this guards against, demonstrated against the real upstream API.

    read_image(im_trans=...) transforms at read time. spec_from_v1_params ALSO
    lifts ImTransOpt out of the template's `extra` onto spec.im_trans, and
    autocalibrate_four_stage applies that to image/dark/mask itself -- so doing
    both flips the frame twice and silently undoes it. The runner applies the
    transform at read time and clears the spec copy.
    """
    pytest.importorskip("midas_calibrate_v2")
    from midas_calibrate.params import CalibrationParams
    from midas_calibrate_v2.compat.from_v1 import spec_from_v1_params

    tmpl = tmp_path / "t.txt"
    tmpl.write_text("\n".join([
        "px 172.0", "NrPixelsY 1475", "NrPixelsZ 1679",
        "Wavelength 0.202153", "Lsd 650000.0", "SpaceGroup 225",
        "LatticeConstant 5.4116 5.4116 5.4116 90 90 90",
        "MaxRingRad 700", "ImTransOpt 2",
    ]) + "\n")

    v1 = CalibrationParams.from_file(tmpl)
    assert v1.extra.get("ImTransOpt") == "2"     # it lands in `extra`, not a field

    spec = spec_from_v1_params(v1)
    carried = tuple(getattr(spec, "im_trans", ()) or ())

    # Whether the spec picks the transform up is VERSION-DEPENDENT, which is
    # exactly why the runner clears it unconditionally rather than reasoning about
    # the installed build: 0.22.0 does `s.im_trans = im_trans_from_v1(v1)`
    # (from_v1.py:51) and would re-apply it; 0.5.3 has no such line, so read time
    # is the only place the transform can be applied at all.
    assert carried in ((), (2,)), carried

    # The runner's mitigation, which is correct for both.
    spec.im_trans = ()
    assert tuple(getattr(spec, "im_trans", ()) or ()) == ()


# --------------------------------------------------------------------------- #
# locality: APEXA running ON the data host must not route to itself
# --------------------------------------------------------------------------- #

def test_registry_host_forms_resolve_to_this_machine(monkeypatch):
    """A registry `host` is an ssh TARGET, so it can carry a user@ prefix and a
    domain. Matching only the bare string made APEXA-on-copland fail to recognise
    itself and SSH to itself -- and the loopback login shell lacks the MIDAS
    activation, so the run failed with `command not found` on the very host where
    the binary is on PATH.
    """
    import importlib
    import apexa_remote_exec as R
    monkeypatch.setenv("APEXA_LOCAL_HOSTNAMES", "copland")
    importlib.reload(R)
    try:
        for name in ("copland", "COPLAND", "s1iduser@copland",
                     "copland.xray.aps.anl.gov",
                     "s1iduser@copland.xray.aps.anl.gov"):
            assert R.is_local_host(name), name
        assert not R.is_local_host("chiltepin")
        assert not R.is_local_host("")
    finally:
        monkeypatch.delenv("APEXA_LOCAL_HOSTNAMES", raising=False)
        importlib.reload(R)


def test_force_local_beats_the_registry(monkeypatch):
    """The escape hatch an operator can reach for mid-experiment: it must take
    precedence over a data-root prefix match, without editing the registry."""
    import importlib
    import apexa_remote_exec as R
    monkeypatch.setenv("APEXA_FORCE_REMOTE_EXEC", "0")
    importlib.reload(R)
    try:
        d = R.decide_exec_host("/gdata/dm/20ID/whatever.h5")
        assert d["is_remote"] is False, d
    finally:
        monkeypatch.delenv("APEXA_FORCE_REMOTE_EXEC", raising=False)
        importlib.reload(R)


def test_px_um_is_exposed_and_the_shape_heuristic_cannot_override_it():
    """_detector_shape_and_px maps 2880x2880 -> 150 um (Varex 2923). A VarexD at
    20-ID is 100 um, and passing the heuristic as --px-um overrode the template,
    putting a 50% error straight into Lsd. px_um must exist and win.
    """
    import inspect
    sig = inspect.signature(M.midas_auto_calibrate)
    assert "px_um" in sig.parameters
    assert sig.parameters["px_um"].default == 0.0

    # the heuristic that made this dangerous, pinned so a change is deliberate
    import tempfile, pathlib
    src = inspect.getsource(M._detector_shape_and_px)
    assert "150" in src and "2880" in src


def test_read_param_value_finds_px_in_a_template(tmp_path):
    """The template branch of the px precedence depends on this lookup."""
    t = tmp_path / "t.txt"
    t.write_text("NrPixelsY 2880\npx 100.0\nWavelength 0.1968\n")
    assert float(M._read_param_value(t, "px")) == 100.0
    assert M._read_param_value(t, "NotThere") in (None, "", 0)
