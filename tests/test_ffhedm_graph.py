"""Eval fixture for the FF-HEDM workflow graph (docs/LANGGRAPH_FF_HEDM_SPEC.md §12).

Replays the failing beamline scenario against a FAKE tool executor (no MIDAS, no
beamline data) and asserts the harness now does what the open-ended loop did not:

  1. asks which calibrant when cali/ holds more than one file (HITL gate),
  2. proposes output folders and waits for confirmation before any write,
  3. calibrates EXACTLY ONCE per chosen input (the duplicate-calibration bug),
  4. reaches the mandatory ring-overlay gate before reconstruction,
  5. enforces order: no reconstruct call before a calibrate call.

Run: python -m pytest tests/test_ffhedm_graph.py  (or: python tests/test_ffhedm_graph.py)
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from apexa_ffhedm_graph import (FFHEDMWorkflow, LANGGRAPH_AVAILABLE,
                                _classify_entries, extract_working_dir)


class FakeTools:
    """Records every tool call and returns plausible MIDAS-shaped payloads."""

    def __init__(self, layout):
        self.layout = layout
        self.calls = []

    async def __call__(self, tool, args):
        self.calls.append((tool, dict(args)))
        if tool == "list_directory":
            path = args.get("path", "")
            names = self.layout.get(os.path.basename(path.rstrip("/")) or path, [])
            if path in self.layout:
                names = self.layout[path]
            return {"status": "ok", "entries": names}
        if tool == "midas_auto_calibrate":
            out = args.get("output_dir", "/tmp/calib")
            os.makedirs(out, exist_ok=True)
            p = os.path.join(out, "refined_MIDAS_params_CeO2.txt")
            with open(p, "w") as f:
                f.write("Lsd 1000000\nBC 1024 1024\n")
            return {"status": "ok", "refined_param_file": p, "Lsd": 1000000.0,
                    "BC": [1024.0, 1024.0], "residual_px": 0.31, "energy_kev": 61.332}
        if tool == "run_ff_hedm_full_workflow":
            out = args.get("result_folder", "/tmp/recon")
            os.makedirs(out, exist_ok=True)
            return {"status": "ok", "result_folder": out, "n_grains": 47}
        if tool == "write_file":
            return {"status": "ok"}
        return {"status": "ok"}

    def count(self, tool):
        return sum(1 for t, _ in self.calls if t == tool)

    def index_of(self, tool):
        for i, (t, _) in enumerate(self.calls):
            if t == tool:
                return i
        return -1


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print("  ok -", msg)


def test_pure_helpers():
    print("helpers:")
    c = _classify_entries(["CeO2_att6.ge5", "LaB6_att3.ge5", "Au_FF_box_att6",
                           "LSHR5_FF_boxBeam_att6", "notes.txt"])
    _assert(len(c["calibrants"]) == 2, "two powder calibrants classified")
    _assert(c["inplane"] == ["Au_FF_box_att6"], "Au_FF flagged in-plane, not sample")
    _assert("LSHR5_FF_boxBeam_att6" in c["samples"], "real sample folder classified")
    _assert(extract_working_dir("calibrate /data/1id/nov26 please") == "/data/1id/nov26",
            "working dir parsed from free text")


def test_multi_calibrant_flow(tmpbase):
    print("multi-calibrant HITL flow:")
    wd = os.path.join(tmpbase, "expt")
    layout = {
        wd: ["cali", "Au_FF_box_att6", "LSHR5_FF_boxBeam_att6"],
        os.path.join(wd, "cali"): ["CeO2_att0.ge5", "CeO2_att6.ge5", "LaB6_att3.ge5"],
    }
    tools = FakeTools(layout)
    wf = FFHEDMWorkflow(tools)
    tid = "expt-multi"

    # 1) start → must interrupt asking WHICH calibrant (3 in cali/)
    q1 = asyncio.run(wf.astep(f"run FF-HEDM in {wd}", provider=None, session=tid))
    _assert("Which should I calibrate" in q1 or "calibrant files" in q1,
            "gate fires: asks which calibrant when >1 present")
    _assert(wf.is_active(tid), "thread is paused after first gate")
    _assert(tools.count("midas_auto_calibrate") == 0, "no calibration before selection")

    # 2) choose calibrant #1 → next gate proposes folders
    q2 = asyncio.run(wf.astep("1", provider=None, session=tid))
    _assert("output folders" in q2 or "APEXA_calib" in q2, "gate fires: proposes folders")
    _assert(tools.count("midas_auto_calibrate") == 0, "still no calibration before folder confirm")

    # 3) accept folders → omega-sign gate (non-aero → asks)
    q3 = asyncio.run(wf.astep("yes", provider=None, session=tid))
    _assert("omega" in q3.lower(), "gate fires: asks omega sign (non-aero)")

    # 4) answer omega → calibrate runs, then verify passes (residual 0.31) → in-plane →
    #    ring-overlay gate must appear BEFORE any reconstruction
    q4 = asyncio.run(wf.astep("-1", provider=None, session=tid))
    _assert("rings sit on" in q4 or "ring" in q4.lower(), "gate fires: mandatory ring overlay")
    _assert(wf.pending_gate(tid) == "ring_overlay", "pending_gate reports the paused gate name")
    _assert(tools.count("run_ff_hedm_full_workflow") == 0, "no reconstruct before overlay gate")
    _assert(tools.count("midas_auto_calibrate") >= 1, "calibration ran after gates")

    # 5) confirm overlay → reconstruct → finish
    final = asyncio.run(wf.astep("yes", provider=None, session=tid))
    _assert(not wf.is_active(tid), "workflow complete, thread no longer paused")
    summ = json.loads(final)
    _assert(summ["workflow"] == "ff_hedm", "final summary is the citable artifact")

    # ── the headline assertions ──────────────────────────────────────────
    _assert(tools.count("midas_auto_calibrate") == 2,
            "calibrate called exactly twice: 1 powder + 1 in-plane tx (NOT 3x duplicate)")
    _assert(tools.count("run_ff_hedm_full_workflow") == 1, "reconstruct ran exactly once")
    ci = tools.index_of("midas_auto_calibrate")
    ri = tools.index_of("run_ff_hedm_full_workflow")
    _assert(ci < ri, "ordering enforced: calibrate precedes reconstruct")
    # powder calibrate and in-plane tx used SEPARATE output dirs (folder discipline)
    calib_dirs = [a.get("output_dir") for t, a in tools.calls if t == "midas_auto_calibrate"]
    _assert(len(set(calib_dirs)) == 2, "powder + in-plane tx wrote to separate folders")


def test_single_calibrant_autoselect(tmpbase):
    print("single-calibrant auto-select (no prompt):")
    wd = os.path.join(tmpbase, "expt2")
    layout = {wd: ["cali", "S1_FF_boxBeam"], os.path.join(wd, "cali"): ["CeO2_att6.ge5"]}
    tools = FakeTools(layout)
    wf = FFHEDMWorkflow(tools)
    tid = "expt-single"
    q1 = asyncio.run(wf.astep(f"run FF-HEDM in {wd}", provider=None, session=tid))
    # exactly one calibrant → skip selection gate, go straight to folder proposal
    _assert("output folders" in q1 or "APEXA_calib" in q1,
            "single calibrant auto-selected (no which-calibrant prompt)")


def test_durable_resume_across_restart(tmpbase):
    """Phase 2: a workflow paused on a gate survives a 'restart'.

    Simulates a process restart by building a SECOND FFHEDMWorkflow against the same
    on-disk sqlite checkpoint + state sidecar (fresh FakeTools, fresh event loop) and
    asserting it (a) reports is_active/pending_gate synchronously from the persisted
    sidecar before touching the DB, and (b) resumes the durable checkpoint to finish.
    """
    print("durable resume across restart (AsyncSqliteSaver):")
    try:
        import aiosqlite  # noqa: F401
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver  # noqa: F401
    except Exception as e:
        print(f"  SKIP - async sqlite checkpoint unavailable ({e})")
        return

    wd = os.path.join(tmpbase, "expt3")
    layout = {wd: ["cali", "S1_FF_boxBeam"], os.path.join(wd, "cali"): ["CeO2_att6.ge5"]}
    db = os.path.join(tmpbase, "graph.sqlite")
    state = os.path.join(tmpbase, "graph.state.json")
    tid = "expt-durable"

    # ── "process 1": start the run, pause on the first gate, then close ──────
    async def proc1():
        wf1 = FFHEDMWorkflow(FakeTools(layout), db_path=db, state_path=state)
        q1 = await wf1.astep(f"run FF-HEDM in {wd}", provider=None, session=tid)
        _assert("output folders" in q1 or "APEXA_calib" in q1,
                "proc1 pauses on propose-folders gate (single calibrant auto-selected)")
        _assert(wf1.is_active(tid), "proc1: thread active")
        _assert(wf1.pending_gate(tid) == "propose_folders", "proc1: pending_gate == propose_folders")
        await wf1.aclose()
    asyncio.run(proc1())

    # disk state must reflect the pause even with NO live workflow object
    _assert(os.path.exists(db), "durable sqlite checkpoint written to disk")
    _assert(os.path.exists(state), "state sidecar written to disk")

    # ── "process 2": fresh instance, same paths → sees the pause synchronously ─
    wf2 = FFHEDMWorkflow(FakeTools(layout), db_path=db, state_path=state)
    _assert(wf2.is_active(tid), "RESTART: new instance reports paused from persisted sidecar (sync, no DB open)")
    _assert(wf2.pending_gate(tid) == "propose_folders", "RESTART: pending_gate restored")

    async def proc2():
        # resume the durable checkpoint through to completion
        q2 = await wf2.astep("yes", provider=None, session=tid)       # accept folders → omega gate
        _assert("omega" in q2.lower(), "RESTART: resumed graph advances to omega gate")
        q3 = await wf2.astep("-1", provider=None, session=tid)        # omega → calibrate/verify → ring gate
        _assert("ring" in q3.lower(), "RESTART: reaches ring-overlay gate after resume")
        final = await wf2.astep("yes", provider=None, session=tid)    # overlay → reconstruct → done
        _assert(not wf2.is_active(tid), "RESTART: workflow completes; no longer paused")
        _assert(json.loads(final)["workflow"] == "ff_hedm", "RESTART: final summary emitted")
        await wf2.aclose()
    asyncio.run(proc2())


def main():
    if not LANGGRAPH_AVAILABLE:
        print("SKIP: langgraph not installed")
        return
    import tempfile
    test_pure_helpers()
    with tempfile.TemporaryDirectory() as d:
        test_multi_calibrant_flow(d)
    with tempfile.TemporaryDirectory() as d:
        test_single_calibrant_autoselect(d)
    with tempfile.TemporaryDirectory() as d:
        test_durable_resume_across_restart(d)
    print("\nALL FF-HEDM GRAPH TESTS PASSED")


if __name__ == "__main__":
    main()


def test_live_instances_refresh_and_merge_shared_pause_index(tmp_path):
    if not LANGGRAPH_AVAILABLE:
        return
    state = str(tmp_path / 'shared.state.json')
    async def unused(*args):
        raise AssertionError('no analysis expected')
    first = FFHEDMWorkflow(unused, state_path=state)
    second = FFHEDMWorkflow(unused, state_path=state)
    first._gen['cli'] = 1
    first._paused['cli'] = True
    first._gate['cli'] = 'ring_overlay'
    first._save_state('cli')
    assert second.is_active('cli')
    assert second.pending_gate('cli') == 'ring_overlay'
    second._gen['web'] = 1
    second._paused['web'] = True
    second._gate['web'] = 'select_calibrant'
    second._save_state('web')
    # A stale object updates only its own session.
    first._paused['cli'] = False
    first._gate['cli'] = None
    first._save_state('cli')
    assert second.is_active('web')
    assert not second.is_active('cli')
    assert set(json.loads((tmp_path / 'shared.state.json').read_text())) == {'cli', 'web'}


def test_unavailable_or_interrupted_graph_never_restarts_tools(tmp_path):
    if not LANGGRAPH_AVAILABLE:
        return
    import pytest
    state = tmp_path / 'shared.state.json'
    async def unused(*args):
        raise AssertionError('no analysis expected')
    wf = FFHEDMWorkflow(unused, state_path=str(state))
    state.write_text('{broken')
    assert wf.is_active('scan')
    with pytest.raises(RuntimeError, match='checkpoint index'):
        asyncio.run(wf.astep('yes', None, 'scan'))
    state.write_text(json.dumps({'scan': {'gen': 1, 'paused': True, 'gate': 'ring_overlay'}}))
    with pytest.raises(RuntimeError, match='checkpoint is unavailable'):
        asyncio.run(wf.astep('yes', None, 'scan'))
    state.write_text(json.dumps({'scan': {'gen': 1, 'paused': True, 'inflight': True}}))
    with pytest.raises(RuntimeError, match='step was interrupted'):
        asyncio.run(wf.astep('yes', None, 'scan'))
    assert 'External jobs were not stopped' in asyncio.run(wf.astep('abandon workflow', None, 'scan'))
    assert not wf.is_active('scan')
