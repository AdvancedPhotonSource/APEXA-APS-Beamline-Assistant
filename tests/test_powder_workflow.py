import asyncio
import base64
import hashlib
import json
from types import SimpleNamespace

import pytest

from apexa_plan import APEXAPlan, PowderRefinement
from apexa_powder import advise
import gsas2_server as gsas


def test_powder_plan_roundtrip_preserves_per_observation_bindings():
    observations = [{"data_path": "lab.xye", "instrument_path": "lab.prm", "cif_paths": ["phase.cif"]},
                    {"data_path": "beamline.xye", "instrument_path": "synchrotron.prm", "cif_paths": ["phase.cif"]}]
    plan = APEXAPlan(technique="powder-refinement", refinement=PowderRefinement(observations=observations))
    restored = APEXAPlan.from_dict(plan.to_dict())
    assert restored.validate() == []
    assert restored.to_powder_refine_kwargs()["observations"] == observations


def test_powder_plan_rejects_missing_instrument():
    plan = APEXAPlan(technique="powder-refinement", refinement=PowderRefinement(observations=[{"data_path": "a"}]))
    assert plan.validate()


def test_trust_does_not_infer_qualification_from_no_warnings():
    result = {"Rwp": 2., "history": [{"accepted": True}], "scientific_status": "provisional"}
    assert gsas._trust(result)["trustworthy"] is False


def test_advisor_sends_image_bytes_with_evidence_identity(tmp_path):
    path = tmp_path / "panel.png"
    data = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j8ioAAAAASUVORK5CYII=")
    path.write_bytes(data)
    bundle = {"observation_id": "observed", "snapshot_sha256": "snapshot",
              "panels": [{"id": "peak_1", "image_path": str(path), "image_sha256": hashlib.sha256(data).hexdigest()}]}
    class Provider:
        async def chat(self, messages, **kwargs):
            self.messages = messages
            return SimpleNamespace(content=json.dumps({"proposals": [], "abstention_reason": "ambiguous peak"}))
    provider = Provider()
    result = asyncio.run(advise(provider, bundle))
    content = provider.messages[1]["content"]
    image = next(block for block in content if block["type"] == "image_url")
    assert base64.b64decode(image["image_url"]["url"].split(",",1)[1]) == data
    assert result["image_count"] == 1
    path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="contents changed"):
        asyncio.run(advise(provider, bundle))


def test_advisor_rejects_string_only_transport():
    provider = type("ArgoProvider", (), {})()
    with pytest.raises(ValueError, match="structured"):
        asyncio.run(advise(provider, {"panels": []}))


def test_unconfigured_vision_is_explicit(monkeypatch, tmp_path):
    for key in ("APEXA_POWDER_VISION_MODEL", "ARGO_USER", "ANL_USERNAME"):
        monkeypatch.delenv(key, raising=False)
    result = json.loads(asyncio.run(gsas.powder_advise(str(tmp_path / "missing.json"), str(tmp_path))))
    assert result["status"] == "unavailable"


def test_individual_live_job_status(monkeypatch, tmp_path):
    import apexa_powder
    job_id = "a" * 24
    monkeypatch.setattr(gsas, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(apexa_powder, "status", lambda *args: {"jobs": [{"id": job_id, "state": "queued"}]})
    result = json.loads(asyncio.run(gsas.refinement_status(job_id)))
    assert result["status"] == "queued"
    assert result["jobs"][0]["id"] == job_id


def test_powder_report_preserves_provisional_science():
    from apexa_report import build_report, render_html
    report = build_report({"tool": "refinement_status", "status": "complete", "jobs": [
        {"id": "pattern1", "state": "completed", "result": {"Rwp": 2.1, "scientific_status": "provisional",
         "evidence_coverage": {"calibration": {"status": "unavailable"}}, "weight_fractions": {"A": .7, "B": .3}}}]}, with_plates=False)
    assert report.verdict == "look"
    text = render_html(report)
    assert "calibration" in text and "70.00%" in text and "provisional" in text


def test_watcher_dispatches_powder_and_recognizes_submission(monkeypatch):
    import apexa_acquisition_watcher as watcher
    calls = []
    async def submit(**kwargs):
        calls.append(kwargs)
        return json.dumps({"status": "submitted", "job_id": "gsas2-test", "job_ids": ["enginejob"]})
    monkeypatch.setattr(gsas, "powder_refine_submit", submit)
    plan = APEXAPlan(technique="powder-refinement", refinement=PowderRefinement(observations=[
        {"data_path": "scan.xye", "instrument_path": "inst.prm", "cif_paths": ["phase.cif"]}]))
    result = watcher.execute_plan(plan)
    assert calls[0]["observations"][0]["data_path"] == "scan.xye"
    assert watcher._accepted_powder_submission(result)
    assert not watcher._successful_result(result)
    assert not watcher._accepted_powder_submission({"status": "submitted"})
