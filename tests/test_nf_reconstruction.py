"""Regression test for run_nf_hedm_reconstruction command-building + output parse.

Does NOT run the heavy NF pipeline. It monkeypatches subprocess.run to capture
the argv the tool builds and writes a synthetic consolidated H5 with the expected
dataset shapes during the fake run, exercising counts and output freshness.

Guards the specific robustness fixes:
  1. new flags (--dtype/--refine/--skip-validation/--install-dir) are forwarded,
  2. inline "# ..." comments in the param file are stripped into a sanitized copy
     (they otherwise crash the run's final consolidation stage),
  3. voxel/grain counts are read from *_consolidated.h5 (not the old, never-
     matching **/Grains.mic glob that always yielded 0),
  4. the mic map is discovered via *.mic (not "Grains.mic").

Run: python -m pytest tests/test_nf_reconstruction.py
"""
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import midas_comprehensive_server as midas


class _FakeCompleted:
    def __init__(self):
        self.returncode = 0
        self.stdout = "NF pipeline complete.\n"
        self.stderr = ""


def _make_consolidated_h5(path):
    """Write a minimal but real-shaped consolidated H5 (voxels + grains)."""
    import h5py
    import numpy as np
    with h5py.File(path, "w") as f:
        f.create_dataset("voxels/position", data=np.zeros((3120, 2)))
        f.create_dataset("voxels/euler_angles", data=np.zeros((3120, 3)))
        f.create_dataset("grains/grain_id", data=np.array([1], dtype="int32"))


def _run_tool(**kwargs):
    return asyncio.run(midas.run_nf_hedm_reconstruction(**kwargs))


def test_nf_argbuild_sanitize_and_counts(monkeypatch):
    captured = {}

    def fake_run(cmd, *a, **kw):
        captured["cmd"] = cmd
        _make_consolidated_h5(os.path.join(rf, "Au_txt_Reconstructed_consolidated.h5"))
        with open(os.path.join(rf, "Au_txt_Reconstructed.mic"), "w") as fh:
            fh.write("%header\n0 1 0.0 -20 -48 1 -1 4.8 0.7 2.1 0.11 1\n")
        return _FakeCompleted()

    monkeypatch.setattr(subprocess, "run", fake_run)
    # midas-nf-pipeline must appear "installed" for the tool to proceed.
    # accepts path= : production resolves MIDAS CLIs through midas_search_path()
    monkeypatch.setattr(shutil, "which",
                        lambda _n, path=None: "/usr/bin/midas-nf-pipeline")

    with tempfile.TemporaryDirectory() as d:
        # Param file WITH an inline comment (the crash trigger).
        param = os.path.join(d, "ps.txt")
        with open(param, "w") as fh:
            fh.write("# full-line comment kept\n")
            fh.write("GridSize 2.5 # Voxel grid spacing (microns)\n")
            fh.write("MicFileText Au_txt_Reconstructed.mic\n")
            fh.write("SpaceGroup 225\n")

        rf = os.path.join(d, "out")
        os.makedirs(rf)
        out = _run_tool(
            param_file=param, result_folder=rf,
            n_cpus=8, device="cpu", dtype="fp64", refine="nm-batched",
            skip_validation=True, install_dir=d, min_confidence=0.7)

        # Read the sanitized param while the temp dir still exists.
        param_arg = captured["cmd"][2]  # [bin, "run", <param>, ...]
        cleaned = open(param_arg).read() if os.path.exists(param_arg) else ""

    res = json.loads(out)
    cmd = captured["cmd"]
    cmd_str = " ".join(cmd)

    # 1. new flags forwarded
    assert "--dtype" in cmd and "fp64" in cmd, cmd_str
    assert "--refine" in cmd and "nm-batched" in cmd, cmd_str
    assert "--skip-validation" in cmd, cmd_str
    assert "--install-dir" in cmd, cmd_str
    assert "--min-confidence" in cmd and "0.7" in cmd, cmd_str

    # 2. sanitized param used (not the commented original)
    assert param_arg.endswith(".apexa_clean.txt"), param_arg
    assert "# Voxel grid spacing" not in cleaned  # inline comment stripped
    assert "GridSize 2.5" in cleaned
    assert "# full-line comment kept" in cleaned  # full-line comment preserved
    assert res.get("param_sanitized")

    # 3 + 4. counts read from consolidated H5; mic discovered via *.mic
    assert res["status"] == "success", res
    assert res["total_voxels"] == 3120, res
    assert res["total_grains"] == 1, res
    assert res["consolidated_h5"].endswith("_consolidated.h5"), res
    assert any(m.endswith("Au_txt_Reconstructed.mic") for m in res["mic_files"]), res


def test_nf_no_sanitize_when_no_inline_comments(monkeypatch):
    """A clean param file is passed through unchanged (no .apexa_clean copy)."""
    captured = {}
    def fake_run(cmd, *args, **kwargs):
        captured["cmd"] = cmd
        return _FakeCompleted()
    monkeypatch.setattr(subprocess, "run", fake_run)
    # accepts path= : production resolves MIDAS CLIs through midas_search_path()
    monkeypatch.setattr(shutil, "which",
                        lambda _n, path=None: "/usr/bin/midas-nf-pipeline")

    with tempfile.TemporaryDirectory() as d:
        param = os.path.join(d, "ps.txt")
        with open(param, "w") as fh:
            fh.write("GridSize 2.5\nMicFileText m\nSpaceGroup 225\n")
        rf = os.path.join(d, "out")
        os.makedirs(rf)
        out = _run_tool(param_file=param, result_folder=rf, device="cpu")

    res = json.loads(out)
    assert captured["cmd"][2] == param, "clean param should pass through unchanged"
    assert res["status"] == "warning"  # no output was produced by the fake run
    assert not res.get("param_sanitized")
    # dtype=auto and empty refine must NOT be forwarded
    assert "--dtype" not in captured["cmd"]
    assert "--refine" not in captured["cmd"]


def test_nf_old_voxels_and_fresh_unrelated_csv_are_not_success(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", lambda _n, path=None: "/usr/bin/midas-nf-pipeline")
    param = tmp_path / "ps.txt"
    param.write_text("GridSize 2.5\nMicFileText m\nSpaceGroup 225\n")
    output = tmp_path / "out"
    output.mkdir()
    stale = output / "old_consolidated.h5"
    _make_consolidated_h5(stale)

    def fake_run(*args, **kwargs):
        (output / "GrainsLayer1.csv").write_text("1,2,3\n")
        return _FakeCompleted()

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = json.loads(_run_tool(param_file=str(param), result_folder=str(output), device="cpu", force=True))
    assert result["status"] == "warning"
    assert result["total_voxels"] == 0
    assert str(stale) in result["stale_outputs_on_disk"]


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
