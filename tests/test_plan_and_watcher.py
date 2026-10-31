"""Tests for the reviewable analysis-plan schema + acquisition watcher.

Covers the pieces that make the plan/watcher layer *in sync* with the FF-HEDM
graph + idempotency work:

  1. APEXAPlan round-trips through YAML and validate() is correct (energy is
     optional when it comes from a parameter_file).
  2. to_integrate_series_kwargs() drops unset fields (so tool defaults apply).
  3. The watcher groups frames by stem and excludes darks.
  4. is_complete() honours an explicit expected_count and the quiet-period rule.
  5. THE COHERENCE GUARD: the watcher refuses to auto-fire midas_integrate_series
     for HEDM/calibration techniques (those need the gated graph, not a blind
     integrate call) and only auto-executes the integration family.

Run: python -m pytest tests/test_plan_and_watcher.py  (or run the file directly)
"""
import os
import sys
import tempfile
import time
from types import SimpleNamespace

import pytest
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from apexa_plan import (APEXAPlan, Beam, Calibration, DataSpec, DarkSpec, Grid,
                        Sample)
import apexa_acquisition_watcher as watcher


def test_plan_roundtrip_and_kwargs():
    plan = APEXAPlan(
        technique="waxs",
        sample=Sample(name="JL_0Nb", expected_phases=["FCC"]),
        beam=Beam(energy_keV=61.332),
        calibration=Calibration(calibrant="CeO2", parameter_file="ceria.txt"),
        data=DataSpec(image_dir="/scratch/JL_0Nb", pattern="JL_0Nb_*.h5",
                      stem="JL_0Nb", data_location="exchange/data"),
        dark=DarkSpec(source="file", kind="after", location="exchange/data"),
        grid=Grid(q_min=0.5, q_max=12.0, n_channels=2500),
    )
    plan.assume("dark.kind", "after", "beamline default trailing dark")
    text = plan.to_yaml()
    back = APEXAPlan.from_dict(yaml.safe_load(text))
    assert back.technique == "waxs"
    assert back.sample.name == "JL_0Nb"
    assert back.grid.n_channels == 2500
    assert back.assumptions and back.assumptions[0].field == "dark.kind"
    # kwargs projection drops None; keeps set grid + dark fields
    kw = plan.to_integrate_series_kwargs()
    assert kw["parameter_file"] == "ceria.txt"
    assert kw["q_min"] == 0.5 and kw["n_channels"] == 2500
    assert "r_min" not in kw  # unset -> dropped so the tool default applies
    print("test_plan_roundtrip_and_kwargs OK")


def test_validate_energy_optional_with_param_file():
    # integration with a param file: energy lives in the params -> no energy needed
    p = APEXAPlan(technique="integration",
                  calibration=Calibration(parameter_file="p.txt"),
                  data=DataSpec(stem="s"))
    assert p.validate() == [], p.validate()
    # bare calibration with no param file + no energy -> flagged
    q = APEXAPlan(technique="calibration", calibration=Calibration(calibrant="CeO2"))
    assert any("energy" in i for i in q.validate())
    # unknown technique flagged
    r = APEXAPlan(technique="bogus")
    assert any("unknown technique" in i for i in r.validate())
    print("test_validate_energy_optional_with_param_file OK")


def test_stem_grouping_and_dark_exclusion():
    files = [
        "/d/JL_0Nb_00001.vrx.h5", "/d/JL_0Nb_00002.vrx.h5", "/d/JL_0Nb_00003.vrx.h5",
        "/d/JL_5Nb_00001.vrx.h5",
        "/d/dark_00001.vrx.h5",          # excluded
    ]
    groups = watcher.group_by_stem(files, exclude_substring="dark")
    assert set(groups) == {"JL_0Nb", "JL_5Nb"}, groups
    assert len(groups["JL_0Nb"]) == 3
    assert all("dark" not in f for g in groups.values() for f in g)
    print("test_stem_grouping_and_dark_exclusion OK")


def test_is_complete_count_and_quiet():
    paths = ["a", "b", "c"]
    done, why = watcher.is_complete(paths, expected_count=3)
    assert not done and "vanished" in why
    wait, why = watcher.is_complete(paths, expected_count=5)
    assert not wait and "3/5" in why
    # quiet-period path needs real files with mtimes
    with tempfile.TemporaryDirectory() as td:
        fp = os.path.join(td, "f.h5")
        with open(fp, "w") as fh:
            fh.write("data")
        old = time.time() - 120
        os.utime(fp, (old, old))
        done, why = watcher.is_complete([fp], expected_count=None,
                                        quiet_seconds=30.0)
        assert done and "no new frames" in why
    print("test_is_complete_count_and_quiet OK")


def test_count_waits_for_last_write(tmp_path):
    frame = tmp_path / "scan_000001.h5"
    frame.write_bytes(b"detector data")
    os.utime(frame, (100, 100))
    assert not watcher.is_complete([frame], expected_count=1, now=110)[0]
    assert watcher.is_complete([frame], expected_count=1, now=131)[0]
    frame.write_bytes(b"")
    assert not watcher.is_complete([frame], expected_count=1, now=time.time()+100)[0]
    assert not watcher.is_complete([], now=200)[0]
    assert not watcher.is_complete([frame, frame], expected_count=1, now=200)[0]


def _watch_args(tmp_path):
    return SimpleNamespace(
        directory=str(tmp_path), pattern="*.h5", exclude_substring="dark",
        expected_count=1, quiet_seconds=0, technique="waxs", instrument="",
        energy=None, param_file="p.txt", calibrant="", data_location="",
        dark_source="none", dark_kind="after", dark_location="",
        result_folder=str(tmp_path / "results"), execute=True,
    )


def test_quiet_file_execution_is_review_only(tmp_path, monkeypatch):
    (tmp_path / "scan_000001.h5").write_bytes(b"data")
    monkeypatch.setattr(watcher, "execute_plan", lambda plan: pytest.fail("quiet files cannot authorize execution"))
    fired = watcher.scan_once(_watch_args(tmp_path), set())
    assert len(fired) == 1
    plan = APEXAPlan.from_yaml(str(tmp_path / "APEXA_plan_scan.yaml"))
    assert plan.perform_execution is False


def test_scan_isolates_directories_and_exact_inputs(tmp_path, monkeypatch):
    for folder in ("first", "second"):
        d = tmp_path / folder
        d.mkdir()
        (d / "scan_000001.h5").write_bytes(b"data")
        (d / "scan_notes.txt").write_text("not detector data")
    (tmp_path / "directory.h5").mkdir()
    monkeypatch.setattr(watcher, "execute_plan", lambda plan: pytest.fail("review only"))
    fired = watcher.scan_once(_watch_args(tmp_path), set())
    calls = [APEXAPlan.from_yaml(str(tmp_path / folder / "APEXA_plan_scan.yaml"))
             for folder in ("first", "second")]
    assert len(fired) == len(calls) == 2
    assert len({p.result_folder for p in calls}) == 2
    for plan in calls:
        kw = plan.to_integrate_series_kwargs()
        assert kw["images"] == [str(tmp_path / plan.data.image_dir / "scan_000001.h5")]
        back = APEXAPlan.from_dict(yaml.safe_load(plan.to_yaml()))
        assert back.data.images == plan.data.images


@pytest.mark.parametrize("flag,value", [("--expected-count", "0"),
    ("--interval", "0"), ("--interval", "nan"),
    ("--quiet-seconds", "-1"), ("--quiet-seconds", "inf")])
def test_watcher_rejects_invalid_timing(tmp_path, flag, value):
    with pytest.raises(SystemExit) as exc:
        watcher.main([str(tmp_path), flag, value, "--once"])
    assert exc.value.code == 2


def test_watcher_refuses_hedm_autoexecute():
    """The coherence guard: HEDM/calibration must NOT auto-fire integrate."""
    for tech in ("ff-hedm", "nf-hedm", "pf-hedm", "calibration"):
        plan = APEXAPlan(technique=tech,
                         calibration=Calibration(parameter_file="p.txt"),
                         data=DataSpec(stem="s"))
        try:
            watcher.execute_plan(plan)
            assert False, f"execute_plan should refuse technique {tech}"
        except ValueError as e:
            assert "gated" in str(e) or "auto-executable" in str(e)
    # integration family is allowed through the guard (import happens lazily)
    for tech in ("waxs", "saxs", "integration"):
        assert tech in watcher._AUTO_EXECUTABLE
    print("test_watcher_refuses_hedm_autoexecute OK")


if __name__ == "__main__":
    test_plan_roundtrip_and_kwargs()
    test_validate_energy_optional_with_param_file()
    test_stem_grouping_and_dark_exclusion()
    test_is_complete_count_and_quiet()
    test_watcher_refuses_hedm_autoexecute()
    print("\nall plan/watcher tests passed")
