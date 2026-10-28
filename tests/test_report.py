"""Run reports: identity, logbook, and the report itself.

The report exists because a run's most decision-relevant fact was being recorded
and never shown — `at_bounds` was on disk for every calibration while 23 of 35
accepted ones sat on a search bound unnoticed. So the tests that matter most
here are the ones asserting that a bound-railed run cannot render as a clean
pass, and that nothing in the output reaches for the network.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import apexa_experiment as X
import apexa_logbook as L
import apexa_report as R


# ── experiment identity ───────────────────────────────────────────────────────
def _esaf(tmp_path: Path, payload: dict, name="esaf.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(payload))
    return p


def test_nested_json_export_is_flattened(tmp_path):
    p = _esaf(tmp_path, {"esaf": {"esafId": 123456, "experimentTitle": "HT-HEDM"},
                         "beamline": "20-ID-E"})
    r = X.load_esaf(p)
    assert r.esaf_id == "123456" and r.title == "HT-HEDM" and r.beamline == "20-ID-E"


def test_csv_key_value_and_header_row_shapes(tmp_path):
    kv = tmp_path / "a.csv"
    kv.write_text("ESAF Id,123456\nExperiment Title,HT-HEDM\nBeamline,20-ID-E\n")
    hr = tmp_path / "b.csv"
    hr.write_text("ESAF Id,Experiment Title,Beamline,PI\n123456,HT-HEDM,20-ID-E,P. T\n")
    for p in (kv, hr):
        r = X.load_esaf(p)
        assert (r.esaf_id, r.title, r.beamline) == ("123456", "HT-HEDM", "20-ID-E")


def test_label_value_text_export(tmp_path):
    p = tmp_path / "esaf.txt"
    p.write_text("ESAF Id: 123456\nExperiment Title: HT-HEDM\n"
                 "Beamline(s): 20-ID-E\nExperimenters: A. One; B. Two\n")
    r = X.load_esaf(p)
    assert r.esaf_id == "123456" and r.experimenters == ["A. One", "B. Two"]


def test_people_lists_in_every_shape():
    assert X._as_people([{"firstName": "Pawan", "lastName": "Tripathi"}]) == ["Pawan Tripathi"]
    assert X._as_people("A. One; B. Two") == ["A. One", "B. Two"]
    assert X._as_people([{"name": "J. Park"}]) == ["J. Park"]
    # "Last, First" is one person, not two
    assert X._as_people("Tripathi, Pawan") == ["Tripathi, Pawan"]


def test_pi_is_not_listed_twice_in_a_different_word_order():
    r = X.ESAF(pi="Tripathi, Pawan", experimenters=["Pawan Tripathi", "J. Park"])
    people = dict(r.header_pairs()).get("Experimenters", "")
    assert "Tripathi" not in people and "J. Park" in people


def test_two_different_people_are_not_merged():
    """Guessing that two people are one is the worse error."""
    r = X.ESAF(pi="J. Park", experimenters=["Jaeyoung Park"])
    assert "Jaeyoung Park" in dict(r.header_pairs()).get("Experimenters", "")


def test_esaf_is_found_by_walking_up_from_a_deep_run_dir(tmp_path):
    _esaf(tmp_path, {"esafId": 1})
    deep = tmp_path / "analysis" / "Ceria_000268" / "out"
    deep.mkdir(parents=True)
    assert X.find_esaf_file(deep).name == "esaf.json"


def test_an_empty_esaf_is_treated_as_absent(tmp_path):
    _esaf(tmp_path, {"unrelated": "value"})
    assert X.FileESAFSource().get(tmp_path) is None


def test_a_malformed_esaf_does_not_take_the_report_down(tmp_path):
    (tmp_path / "esaf.json").write_text("{ not json")
    assert X.FileESAFSource().get(tmp_path) is None


def test_filename_carries_the_experiment_and_never_invents(tmp_path):
    r = X.ESAF(esaf_id="123456")
    name = X.report_filename(r, sample="Ceria", stage="calibration", scans="000268")
    assert name.startswith("123456_Ceria_calibration_000268_")
    assert "None" not in X.report_filename(None, stage="calibration")


def test_filename_is_filesystem_safe():
    name = X.report_filename(X.ESAF(esaf_id="12/34"), sample="a b:c*d")
    assert not set(name) & set('/\\:*?"<>| ')


def test_a_report_never_overwrites_an_earlier_one(tmp_path):
    (tmp_path / "r.html").write_text("first")
    assert X.unique_path(tmp_path, "r").name == "r-2.html"
    (tmp_path / "r-2.html").write_text("second")
    assert X.unique_path(tmp_path, "r").name == "r-3.html"
    assert (tmp_path / "r.html").read_text() == "first"


# ── logbook ───────────────────────────────────────────────────────────────────
LOG = ("2026-09-26 09:10  Aligned detector, CeO2 in.\n"
       "2026-09-26 14:32  tz railing at the bound.\n"
       "  widened tol_tilts_deg to 10.\n"
       "2026-09-26 15:40  Both converged.\n")


def test_entries_split_on_timestamps_and_keep_continuations(tmp_path):
    p = tmp_path / "l.md"; p.write_text(LOG)
    r = L.FileLogbookSource(str(p)).read(n_entries=10)
    assert r.ok and len(r.entries) == 3
    assert "widened tol_tilts_deg" in r.entries[1].text   # continuation stayed put
    assert r.entries[1].timestamp == "2026-09-26 14:32"


def test_n_entries_returns_the_newest_not_the_oldest(tmp_path):
    p = tmp_path / "l.md"; p.write_text(LOG)
    r = L.FileLogbookSource(str(p)).read(n_entries=1)
    assert "Both converged" in r.entries[0].text


def test_a_long_logbook_is_tailed_not_truncated_from_the_top(tmp_path):
    """remote_read_text is `head -c`, which would show the stalest page. A
    logbook read from the front is worse than no logbook: it looks current."""
    p = tmp_path / "big.md"
    p.write_text("".join(f"2026-09-26 00:{i:02d}  entry {i}\n" for i in range(4000)))
    r = L.FileLogbookSource(str(p), tail_bytes=2000).read(n_entries=1)
    assert r.truncated is True
    assert "entry 3999" in r.entries[0].text


def test_the_partial_first_entry_of_a_tail_is_dropped(tmp_path):
    p = tmp_path / "big.md"
    p.write_text("".join(f"2026-09-26 00:{i:02d}  entry {i}\n" for i in range(4000)))
    r = L.FileLogbookSource(str(p), tail_bytes=2000).read(n_entries=0)
    # a fragment would carry the previous entry's timestamp onto the wrong text
    assert all(e.timestamp for e in r.entries)


def test_markdown_heading_logbooks_parse(tmp_path):
    p = tmp_path / "l.md"
    p.write_text("## Monday\nset up\n\n## Tuesday\nran calibration\n")
    r = L.FileLogbookSource(str(p)).read()
    assert len(r.entries) == 2 and "Tuesday" in r.entries[-1].text


def test_an_unparseable_logbook_is_still_returned_verbatim(tmp_path):
    p = tmp_path / "l.txt"; p.write_text("just one blob of prose with no structure")
    r = L.FileLogbookSource(str(p)).read()
    assert len(r.entries) == 1 and "blob" in r.entries[0].text


def test_a_missing_logbook_reports_an_error_rather_than_raising(tmp_path):
    r = L.FileLogbookSource(str(tmp_path / "nope.md")).read()
    assert not r.ok and r.entries == [] and "FileNotFound" in r.error


@pytest.mark.parametrize("spec,kind,host", [
    ("/data/logbook.md", "file", ""),
    ("copland:/data/logbook.md", "file", "copland"),
    ("https://example.org/doc", "web", None),
    (r"C:\logs\logbook.md", "file", ""),       # a drive letter is not a host
])
def test_spec_resolution(spec, kind, host, monkeypatch):
    monkeypatch.delenv("APEXA_LOGBOOK_HOST", raising=False)
    src = L.resolve_logbook_source(spec)
    assert src.kind == kind and getattr(src, "host", None) == host


def test_no_spec_and_no_env_means_no_source(monkeypatch):
    monkeypatch.delenv("APEXA_LOGBOOK", raising=False)
    assert L.resolve_logbook_source("") is None


def test_the_web_backend_refuses_below_the_web_tier(monkeypatch):
    monkeypatch.setenv("APEXA_NETWORK", "internal")
    r = L.WebLogbookSource("https://example.org/doc").read()
    assert not r.ok and "APEXA_NETWORK=web" in r.error


# ── the report ────────────────────────────────────────────────────────────────
RAILED = {
    "tool": "midas_auto_calibrate", "status": "success", "engine": "canonical-v2",
    "calibrated_parameters": {"lsd": 895439.0, "bc_x": 1440.2, "bc_y": 1467.5,
                              "ty": 0.31, "tz": -3.0},
    "convergence_metrics": {
        "stage4_strain_uE_test": 162.0, "strain_gate_ue": 100.0,
        "at_bounds": [{"parameter": "tz", "value": -3.0, "bounds": [-3.0, 3.0]},
                      # real names and bounds from the 20-ID manifests
                      {"parameter": "iso_R4", "value": -0.01, "bounds": [-0.01, 0.01]}]},
}
CLEAN = {
    "tool": "midas_auto_calibrate", "status": "success",
    "calibrated_parameters": {"lsd": 895439.0, "tz": -3.5178},
    "convergence_metrics": {"stage4_strain_uE_test": 4.864, "strain_gate_ue": 100.0,
                            "at_bounds": []},
}


def test_a_bound_railed_run_cannot_render_as_a_clean_pass():
    """The whole point. status says success and the gate is irrelevant to the
    question; 66% of accepted calibrations looked exactly like this."""
    spec = R.build_report(RAILED, with_plates=False)
    assert spec.verdict == "look"
    assert "bound" in spec.verdict_note


def test_a_genuinely_clean_run_still_passes():
    assert R.build_report(CLEAN, with_plates=False).verdict == "pass"


def test_the_railed_parameter_decides_which_lever_is_offered():
    """At 20-ID a railed tz was read as a distortion problem and the
    full -> radial -> none ladder was tried three times. None can move a tilt."""
    labels = " ".join(a.label for a in R.build_report(RAILED, with_plates=False).actions)
    instr = " ".join(a.instruction for a in R.build_report(RAILED, with_plates=False).actions)
    assert "tilt" in labels.lower() and "tol_tilts_deg" in instr
    assert "refine_distortion" in instr          # a_2 railed too


def test_a_parameter_inside_its_bounds_is_not_counted_as_railed():
    m = json.loads(json.dumps(RAILED))
    m["convergence_metrics"]["at_bounds"].append(
        {"parameter": "ty", "value": 0.31, "bounds": [-3.0, 3.0]})
    spec = R.build_report(m, with_plates=False)
    assert "2 parameter" in spec.verdict_note          # not 3
    tilt = [a for a in spec.actions if "tilt" in a.label.lower()][0]
    assert "ty" not in tilt.label


def test_railed_detection_matches_between_plate_and_finding():
    mid = {"parameter": "x", "value": 0.0, "bounds": [-1.0, 1.0]}
    edge = {"parameter": "y", "value": -1.0, "bounds": [-1.0, 1.0]}
    degenerate = {"parameter": "z", "value": 1.0, "bounds": [1.0, 1.0]}
    assert not R._railed(mid) and R._railed(edge) and not R._railed(degenerate)


def test_a_dark_that_did_nothing_is_surfaced():
    m = {"tool": "midas_integrate_series", "status": "success", "succeeded": 43,
         "failed": 0, "dark_dataset": "exchange/dark", "dark_first_frame_mean": 0.0,
         "dark_applied": False}
    spec = R.build_report(m, with_plates=False)
    assert spec.verdict == "look"
    assert any("no-op" in f.title.lower() for f in spec.findings)
    assert any("dark_file" in a.instruction for a in spec.actions)


def test_an_unknown_tool_still_produces_a_report():
    spec = R.build_report({"tool": "something_new", "status": "success"},
                          with_plates=False)
    assert spec.verdict == "pass" and spec.findings


def test_an_empty_manifest_does_not_crash():
    assert R.build_report({}, with_plates=False) is not None


# ── rendering ─────────────────────────────────────────────────────────────────
def _html(**kw) -> str:
    return R.render_html(R.build_report(RAILED, **kw))


def test_the_page_reaches_for_nothing_external():
    """copland has no public internet. A CDN reference is a blank box there,
    and the file on disk looks perfectly valid."""
    h = _html(with_plates=True)
    assert not re.search(r"https?://", h)
    assert "<img" in h and h.count("data:image/png;base64,") >= 1


def test_plates_are_embedded_not_linked():
    h = _html(with_plates=True)
    assert 'src="data:image/png;base64,' in h
    assert not re.search(r'<img[^>]+src="(?!data:)', h)


def test_both_themes_are_defined_including_the_unstamped_default():
    h = _html(with_plates=False)
    assert "prefers-color-scheme:dark" in h
    assert ':root:not([data-theme="light"])' in h      # system-default dark
    assert ':root[data-theme="dark"]' in h
    assert "background:var(--paper)" in h              # explicit body ground


def test_the_verdict_is_visible_in_the_markup():
    assert "LOOK" in _html(with_plates=False)


def test_actions_are_copyable_in_a_plain_file():
    h = _html(with_plates=False)
    assert 'class="copy"' in h and "data-cmd=" in h
    assert "execCommand" in h, "file:// pages need the clipboard fallback"


def test_user_content_is_escaped():
    m = dict(CLEAN)
    m["engine"] = '<script>alert(1)</script>'
    h = R.render_html(R.build_report(m, with_plates=False))
    assert "<script>alert(1)</script>" not in h
    assert "&lt;script&gt;" in h


def test_logbook_entries_appear_verbatim_and_a_summary_is_labelled():
    lb = {"entries": [{"text": "tz railing at the bound", "timestamp": "14:32"}],
          "source": "/l.md", "summary": "things went fine"}
    h = R.render_html(R.build_report(RAILED, logbook=lb, with_plates=False))
    assert "tz railing at the bound" in h
    assert "generated" in h, "a summary must be marked as generated, not implied"


def test_a_logbook_that_could_not_be_read_says_so():
    h = R.render_html(R.build_report(
        RAILED, logbook={"entries": [], "error": "host unreachable"}, with_plates=False))
    assert "host unreachable" in h


def test_the_rail_plate_renders_and_is_a_real_png():
    import base64
    p = R.plate_parameter_rail(RAILED["convergence_metrics"]["at_bounds"])
    assert p is not None and p.key == "parameter_rail"
    assert base64.b64decode(p.png_b64)[:8] == b"\x89PNG\r\n\x1a\n"


def test_no_bounds_means_no_rail_plate():
    assert R.plate_parameter_rail([]) is None


def test_the_detector_plate_degrades_instead_of_raising():
    assert R.plate_detector("/nonexistent/frame.h5") is None


def test_the_tier_gate_fails_closed_if_it_cannot_answer(monkeypatch):
    """A previous version swallowed an ImportError around this gate and went
    to the network anyway — on an air-gapped host that is a hang, not an error."""
    import apexa_network as net
    monkeypatch.setattr(net, "has_web", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    r = L.WebLogbookSource("https://example.org/doc").read()
    assert not r.ok and "refusing to reach the network" in r.error


# ── a phase at +-180 deg is the seam of an angle, not a bound ────────────────
def test_a_phase_at_180_is_not_railed():
    """On the real 20-ID series this distinction moved the headline from 66% of
    accepted calibrations 'railed' to 31%: 12 of 23 had nothing on a bound but a
    harmonic phase at +-180, which is one point on a circle."""
    assert not R._railed({"parameter": "phi4", "value": -179.99999, "bounds": [-180.0, 180.0]})
    assert not R._railed({"parameter": "phi1", "value": 180.0, "bounds": [-180.0, 180.0]})
    assert R._railed({"parameter": "iso_R4", "value": -0.01, "bounds": [-0.01, 0.01]})


def test_a_phase_only_run_renders_pass_not_look():
    m = {"tool": "midas_auto_calibrate", "status": "success",
         "convergence_metrics": {"stage4_strain_uE_test": 5.5, "at_bounds": [
             {"parameter": "phi1", "value": 180.0, "bounds": [-180.0, 180.0]},
             {"parameter": "phi5", "value": -179.9999, "bounds": [-180.0, 180.0]}]}}
    spec = R.build_report(m, with_plates=False)
    assert spec.verdict == "pass"
    assert not any("refine_distortion" in a.instruction for a in spec.actions)


def test_the_distortion_lever_fires_on_real_manifest_names():
    """The pattern used to expect p\\d+/a_\\d+, so on a real calibration -- whose
    terms are iso_R4, a1, ... -- the distortion advice never appeared."""
    for name, bounds, v in (("iso_R6", [-0.01, 0.01], 0.01), ("a1", [0.0, 0.5], 0.5)):
        m = {"tool": "midas_auto_calibrate", "status": "success",
             "convergence_metrics": {"stage4_strain_uE_test": 5.0, "at_bounds": [
                 {"parameter": name, "value": v, "bounds": bounds}]}}
        acts = R.build_report(m, with_plates=False).actions
        assert any("refine_distortion" in a.instruction for a in acts), name
