import asyncio
import json

import pytest
import gsas2_server as gsas
import midas_comprehensive_server as midas
from apexa_result_integrity import output_snapshot, changed_outputs


@pytest.mark.parametrize("results, expected", [
    ([{"Rwp": 4}], "complete"),
    ([{"error": "bad data"}], "error"),
    ([{"Rwp": 4}, {"error": "bad data"}], "partial"),
])
def test_series_completion_reports_pattern_failures(monkeypatch, tmp_path, results, expected):
    monkeypatch.setattr(gsas, "JOBS_DIR", tmp_path)
    job = tmp_path / "gsas2-0123456789"
    job.mkdir()
    (job / "progress.json").write_text(json.dumps({"done": len(results), "total": len(results), "results": results}))
    (job / "done").write_text("ok")
    payload = json.loads(asyncio.run(gsas.refinement_status(job.name)))
    assert payload["status"] == expected


def test_worker_startup_error_is_not_permanent_running(monkeypatch, tmp_path):
    monkeypatch.setattr(gsas, "JOBS_DIR", tmp_path)
    job = tmp_path / "gsas2-0123456789"
    job.mkdir()
    (job / "error.json").write_text(json.dumps({"error": "missing GSASII"}))
    payload = json.loads(asyncio.run(gsas.refinement_status(job.name)))
    assert payload["status"] == "error"


def test_freshness_requires_nonempty_changed_outputs(tmp_path):
    old = tmp_path / "old.csv"
    old.write_text("1,2\n")
    before = output_snapshot([old])
    fresh = tmp_path / "fresh.csv"
    fresh.write_text("3,4\n")
    empty = tmp_path / "empty.csv"
    empty.touch()
    assert changed_outputs([old, fresh, empty], before) == [fresh]


@pytest.mark.parametrize("write_fresh", [False, True])
def test_pf_requires_fresh_grain_data(monkeypatch, tmp_path, write_fresh):
    import shutil
    import subprocess
    monkeypatch.setattr(shutil, "which", lambda _name, path=None: "/fixture/midas-pipeline")
    param = tmp_path / "params.txt"
    param.write_text("SpaceGroup 225\nnScans 2\n")
    output = tmp_path / "out"
    layer = output / "LayerNr_1"
    layer.mkdir(parents=True)
    grains = layer / "Grains.csv"
    grains.write_text("% old result\n1,2,3\n")

    def fake_run(*args, **kwargs):
        if write_fresh:
            grains.write_text("% new result\nID,X,Y\n1,2,3\n2,3,4\n")
        return subprocess.CompletedProcess([], 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    payload = json.loads(asyncio.run(midas.run_pf_hedm_workflow(str(output), str(param), n_scans=2)))
    assert payload["status"] == ("success" if write_fresh else "warning")
    assert payload["total_grains"] == (2 if write_fresh else 0)
