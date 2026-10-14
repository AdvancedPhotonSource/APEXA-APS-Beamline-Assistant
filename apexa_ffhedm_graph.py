"""APEXA FF-HEDM workflow graph (Phase 1 — APEXA_WORKFLOW_MODE=graph).

A stateful, checkpointed LangGraph state machine that runs the FF-HEDM setup
procedure — discover → select calibrant → propose folders → resolve geometry →
calibrate → verify → in-plane tx → ring overlay → reconstruct → verify grains —
as *deterministic Python control flow* with *human-in-the-loop gates* at the
handbook's decision points.

Why this exists (see docs/LANGGRAPH_FF_HEDM_SPEC.md): the open-ended agent loop
re-decided the plan from free text every iteration, which produced duplicate
calibration, no "ask which calibrant" prompt, and out-of-order steps. Moving the
*sequence* into a graph (edges, not prompts) fixes that structurally; the LLM is
used only inside judgment nodes for extraction, never to choose the next tool.

Design:
  * Dependency-injected: the graph is handed ``execute_tool_fn`` (the same async
    ``APEXAClient.execute_tool_call`` the orchestrator uses — O(1) registry
    dispatch, reconnect-aware) and a ``provider`` (ArgoProvider) for judgment.
    This also makes the whole graph unit-testable with a fake tool executor.
  * Gates call LangGraph ``interrupt()``. ``FFHEDMWorkflow.astep()`` turns an
    interrupt into a returned question string; the user's next turn resumes the
    graph via ``Command(resume=...)``. All three UIs (CLI/web/desktop) just print
    the string and read the next line — no UI changes.
  * ``thread_id`` == APEXA session name → checkpoints tie to sessions and survive
    the Ctrl-C abort / restart.

Phase 2 adds *durability*: the checkpointer is ``AsyncSqliteSaver`` at
``~/.apexa/ffhedm_graph.sqlite`` (mirrors ``~/.apexa/timing.jsonl``), so a workflow
paused at gate 2 survives a full CLI restart — the next input resumes it. The
sync ``is_active``/``pending_gate`` accessors the orchestrator calls before each
turn are backed by a tiny persisted sidecar (``ffhedm_graph.state.json``) rather
than the async store, so they stay correct across restart without touching the
event-loop-bound sqlite connection. ``MemorySaver`` (no sidecar, no file I/O) is
used for tests and whenever durability is unavailable/disabled.

This module degrades safely: if langgraph is not installed, ``LANGGRAPH_AVAILABLE``
is False and the orchestrator falls back to its normal path.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from apexa_sessions import file_lock
from typing import Any, Awaitable, Callable, Dict, List, Optional, TypedDict

try:
    from langgraph.graph import StateGraph, START, END
    from langgraph.types import interrupt, Command
    from langgraph.checkpoint.memory import MemorySaver
    LANGGRAPH_AVAILABLE = True
except Exception:  # pragma: no cover - import guard
    LANGGRAPH_AVAILABLE = False
    StateGraph = START = END = interrupt = Command = MemorySaver = None  # type: ignore

import re

ExecuteToolFn = Callable[[str, Dict[str, Any]], Awaitable[Any]]


# --------------------------------------------------------------------------- #
# State
# --------------------------------------------------------------------------- #
class FFHEDMState(TypedDict, total=False):
    # provenance / discovery
    working_dir: str
    raw_query: str
    calibrant_files: List[str]
    chosen_calibrant: str
    sample_dirs: List[str]
    inplane_dir: str

    # geometry (written by calibration/in-plane, read by reconstruct)
    energy_kev: float
    lsd_um: float
    beam_center: List[float]
    tilts: Dict[str, float]
    omega_sign: int

    # output discipline
    output_dirs: Dict[str, str]

    # per-step status + idempotency
    done: Dict[str, Dict[str, Any]]

    # audit
    gate_log: List[Dict[str, Any]]
    errors: List[str]

    # terminal
    summary: str


# --------------------------------------------------------------------------- #
# small helpers (pure, testable)
# --------------------------------------------------------------------------- #
_PATH_RE = re.compile(r"(/[^\s'\"]+|~[^\s'\"]*)")

# Calibrant name signatures (powder detector calibration).
_POWDER_HINTS = ("ceo2", "cerium", "lab6", "lanthanum", " si ", "silicon", "al2o3")
# Au single-crystal FF scan == in-plane (tx) calibration, NOT a sample pilot.
# (memory project_au_inplane_calibration)
_INPLANE_HINTS = ("au_ff", "au-ff", "aussc", "gold_ff")


def extract_working_dir(query: str) -> str:
    """Best-effort absolute/`~` path out of a free-text request. '' if none."""
    m = _PATH_RE.search(query or "")
    return m.group(1) if m else ""


def _classify_entries(entries: List[str]) -> Dict[str, List[str]]:
    """Heuristic split of a directory listing into calibrant / in-plane / sample.

    Deliberately name-based and conservative — the authoritative classification
    (reading param-file keys) happens in the MIDAS server's recommend_workflow;
    here we only need enough to drive the gates and let the human correct us.
    """
    calibrants, inplane, samples = [], [], []
    for e in entries:
        low = f" {e.lower()} "
        if any(h in low for h in _INPLANE_HINTS):
            inplane.append(e)
        elif any(h in low for h in _POWDER_HINTS):
            calibrants.append(e)
        elif "_ff" in low or "ff_" in low or "boxbeam" in low:
            samples.append(e)
    return {"calibrants": calibrants, "inplane": inplane, "samples": samples}


def _as_dict(result: Any) -> Dict[str, Any]:
    """Coerce a tool result (JSON string or dict) into a dict; {} on failure."""
    if isinstance(result, dict):
        return result
    if isinstance(result, str):
        try:
            v = json.loads(result)
            return v if isinstance(v, dict) else {"raw": result}
        except Exception:
            return {"raw": result}
    return {}


def _listing_names(result: Any) -> List[str]:
    """Pull entry names out of a list_directory result across plausible shapes."""
    d = _as_dict(result)
    for key in ("entries", "files", "items", "contents", "listing"):
        v = d.get(key)
        if isinstance(v, list):
            out = []
            for it in v:
                if isinstance(it, str):
                    out.append(it)
                elif isinstance(it, dict):
                    out.append(it.get("name") or it.get("path") or "")
            return [x for x in out if x]
    return []


# --------------------------------------------------------------------------- #
# The workflow
# --------------------------------------------------------------------------- #
class FFHEDMWorkflow:
    """Compiled FF-HEDM graph + checkpointer + interrupt/resume plumbing.

    Public surface used by the orchestrator:
      * ``is_active(thread_id)``  -> True if that thread is paused mid-workflow.
      * ``await astep(query, provider, thread_id)`` -> assistant text (a gate
        question, or the final summary).
    """

    def __init__(self, execute_tool_fn: ExecuteToolFn, checkpointer=None, *,
                 db_path: Optional[str] = None, state_path: Optional[str] = None):
        if not LANGGRAPH_AVAILABLE:
            raise RuntimeError("langgraph is not installed; graph mode unavailable")
        self._execute = execute_tool_fn
        self._provider = None  # set per-step; judgment nodes read self._provider
        self._db_path = db_path
        self._state_path = state_path
        self._conn = None          # aiosqlite connection (durable mode only)
        self._async_saver = False  # True when using AsyncSqliteSaver (no sync API)
        # Per-session bookkeeping — persisted to `state_path` so the sync accessors
        # (is_active / pending_gate) the orchestrator calls each turn stay correct
        # across a restart without touching the loop-bound sqlite connection.
        #   _gen:   generation counter. A fresh start (or a cancel) bumps it so a
        #           completed/abandoned run's checkpoint is orphaned and the next
        #           run gets a clean thread_id instead of resuming stale `done`.
        #   _paused: session -> True iff its current run is waiting on a gate.
        #   _gate:  session -> the pending gate name (for UI signalling).
        self._gen: Dict[str, int] = {}
        self._paused: Dict[str, bool] = {}
        self._gate: Dict[str, Optional[str]] = {}
        self._inflight: Dict[str, bool] = {}
        self._state_error = None
        self._load_state()
        # Eager, synchronous checkpointer when a checkpointer is supplied (tests) or
        # no durable db_path was requested. Durable AsyncSqliteSaver is built lazily
        # in _ensure_app() on the running event loop (aiosqlite binds to its loop).
        if checkpointer is not None or db_path is None:
            self._checkpointer = checkpointer or MemorySaver()
            self._app = self._build().compile(checkpointer=self._checkpointer)
        else:
            self._checkpointer = None
            self._app = None

    # ---- durable checkpointer / state persistence ----------------------- #
    async def _ensure_app(self) -> None:
        """Lazily build the durable graph on the running loop; fall back to memory."""
        if self._app is not None:
            return
        try:
            import aiosqlite
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
            os.makedirs(os.path.dirname(os.path.abspath(self._db_path)), exist_ok=True)
            self._conn = await aiosqlite.connect(self._db_path)
            saver = AsyncSqliteSaver(self._conn)
            await saver.setup()
            self._checkpointer = saver
            self._async_saver = True
        except Exception as e:  # pragma: no cover - environment dependent
            print(f"⚠ FF-HEDM graph: durable checkpoint unavailable ({e}); "
                  "using in-memory (no cross-restart resume this run)", file=sys.stderr)
            self._checkpointer = MemorySaver()
            self._async_saver = False
        self._app = self._build().compile(checkpointer=self._checkpointer)

    def _read_state(self):
        if not self._state_path or not os.path.exists(self._state_path):
            if self._state_path and self._db_path and os.path.exists(self._db_path):
                import sqlite3
                # A missing index beside existing checkpoints is lost state,
                # rather than permission to start the same workflow again.
                try:
                    with sqlite3.connect(f"{Path(self._db_path).resolve().as_uri()}?mode=ro", uri=True) as connection:
                        exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='checkpoints'").fetchone()
                        if exists and connection.execute("SELECT 1 FROM checkpoints LIMIT 1").fetchone():
                            raise RuntimeError("FF-HEDM checkpoint index is missing beside an existing database. Restore the index before resuming; no workflow was restarted.")
                except sqlite3.Error as exc:
                    raise RuntimeError(f"FF-HEDM checkpoint database cannot be read: {exc}. No workflow was restarted.") from exc
            return {}
        try:
            with open(self._state_path) as stream:
                data = json.load(stream)
            if not isinstance(data, dict) or any(not isinstance(r, dict) for r in data.values()):
                raise ValueError("expected a session map")
            return data
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"FF-HEDM checkpoint index cannot be read: {exc}. Restore the index before resuming; no workflow was restarted.") from exc

    def _load_state(self) -> None:
        if not self._state_path:
            return
        try:
            data = self._read_state()
            self._gen = {s: int(r.get("gen", 0)) for s, r in data.items()}
            self._paused = {s: bool(r.get("paused", False)) for s, r in data.items()}
            self._gate = {s: r.get("gate") for s, r in data.items()}
            self._inflight = {s: bool(r.get("inflight", False)) for s, r in data.items()}
            self._state_error = None
        except (RuntimeError, ValueError, TypeError) as exc:
            self._state_error = str(exc)

    def _save_state(self, session: str) -> None:
        if not self._state_path:
            return
        path = Path(self._state_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with file_lock(path.with_suffix(path.suffix + ".lock")):
            # Merge just this session so a different CLI/WebUI process cannot
            # erase other workflows using a stale in-memory map.
            data = self._read_state()
            data[session] = {"gen": self._gen.get(session, 0),
                             "paused": self._paused.get(session, False),
                             "gate": self._gate.get(session),
                             "inflight": self._inflight.get(session, False)}
            fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".ff-state-")
            try:
                with os.fdopen(fd, "w") as stream:
                    json.dump(data, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)

    async def aclose(self) -> None:
        """Close the durable connection (best-effort). Safe to call repeatedly."""
        if self._conn is not None:
            try:
                await self._conn.close()
            except Exception:
                pass
            self._conn = None

    # ---- orchestrator-facing API ---------------------------------------- #
    def _tid(self, session: str) -> str:
        session = session or "_ffhedm"
        return f"{session}#{self._gen.get(session, 0)}"

    def _cfg(self, session: str) -> Dict[str, Any]:
        return {"configurable": {"thread_id": self._tid(session)}}

    def is_active(self, session: str) -> bool:
        """True iff this session's current run is paused on a gate.

        Sync + cheap: reads the persisted paused-map, not the checkpointer (whose
        async store can't be queried synchronously). ``astep`` keeps the map true.
        """
        self._load_state()
        return bool(self._state_error or self._paused.get(session or "_ffhedm") or self._inflight.get(session or "_ffhedm"))

    def pending_gate(self, session: str) -> Optional[str]:
        """Name of the gate this session is paused on, or None. For UI signalling."""
        session = session or "_ffhedm"
        self._load_state()
        return self._gate.get(session) if self._paused.get(session) else None

    def cancel(self, session: str) -> None:
        """Abandon this session's in-flight workflow; next input starts fresh."""
        session = session or "_ffhedm"
        self._load_state()
        if self._state_error:
            raise RuntimeError(self._state_error)
        if self._paused.get(session) or self._inflight.get(session):
            self._inflight[session] = False
            self._gen[session] = self._gen.get(session, 0) + 1
            self._paused[session] = False
            self._gate[session] = None
            self._save_state(session)

    async def astep(self, query: str, provider, session: str) -> str:
        session = session or "_ffhedm"
        self._provider = provider
        await self._ensure_app()
        self._load_state()
        if self._state_error:
            raise RuntimeError(self._state_error)
        if query.strip().lower() in ("cancel workflow", "abandon workflow"):
            self.cancel(session)
            return "Workflow checkpoint abandoned. External jobs were not stopped; inspect them before starting another run."
        if self._inflight.get(session):
            raise RuntimeError("The previous FF-HEDM step was interrupted. Inspect existing outputs and external jobs before retrying. No tool was rerun. Use a new session or 'abandon workflow' after checking them.")
        if self._paused.get(session):
            snapshot = await self._app.aget_state(self._cfg(session))
            if not snapshot.values or not snapshot.next:
                raise RuntimeError("The saved FF-HEDM checkpoint is unavailable. Restore its database before resuming; no workflow was restarted.")
        else:
            self._gen[session] = self._gen.get(session, 0) + 1
        self._inflight[session] = True
        self._save_state(session)
        if self._paused.get(session):
            result = await self._app.ainvoke(Command(resume=query), self._cfg(session))
        else:
            init: FFHEDMState = {
                "raw_query": query,
                "working_dir": extract_working_dir(query),
                "done": {}, "gate_log": [], "errors": [],
                "output_dirs": {}, "tilts": {},
            }
            result = await self._app.ainvoke(init, self._cfg(session))
        self._inflight[session] = False
        # Reflect the new pause-state into the persisted sidecar.
        intr = result.get("__interrupt__")
        if intr:
            payload = intr[0].value if isinstance(intr, (list, tuple)) else intr
            self._paused[session] = True
            self._gate[session] = payload.get("gate") if isinstance(payload, dict) else None
        else:
            self._paused[session] = False
            self._gate[session] = None
        self._save_state(session)
        return self._render(result)

    def _render(self, result: Dict[str, Any]) -> str:
        intr = result.get("__interrupt__")
        if intr:
            payload = intr[0].value if isinstance(intr, (list, tuple)) else intr
            if isinstance(payload, dict):
                return payload.get("prompt") or json.dumps(payload, indent=2)
            return str(payload)
        return result.get("summary") or "FF-HEDM workflow finished."

    # ---- graph definition ----------------------------------------------- #
    def _build(self):
        g = StateGraph(FFHEDMState)
        g.add_node("discover", self._n_discover)
        g.add_node("select_calibrant", self._n_select_calibrant)
        g.add_node("propose_folders", self._n_propose_folders)
        g.add_node("resolve_geometry", self._n_resolve_geometry)
        g.add_node("calibrate", self._n_calibrate)
        g.add_node("verify_calibration", self._n_verify_calibration)
        g.add_node("inplane_tx", self._n_inplane_tx)
        g.add_node("ring_overlay", self._n_ring_overlay)
        g.add_node("reconstruct", self._n_reconstruct)
        g.add_node("verify_grains", self._n_verify_grains)

        g.add_edge(START, "discover")
        g.add_edge("discover", "select_calibrant")
        g.add_edge("select_calibrant", "propose_folders")
        g.add_edge("propose_folders", "resolve_geometry")
        g.add_edge("resolve_geometry", "calibrate")
        g.add_edge("calibrate", "verify_calibration")
        # verify → either loop back to geometry (rejected) or advance
        g.add_conditional_edges(
            "verify_calibration", self._route_after_verify,
            {"redo": "resolve_geometry", "ok": "inplane_tx"},
        )
        g.add_edge("inplane_tx", "ring_overlay")
        # ring overlay gate → reconstruct, or back to geometry if rings don't sit
        g.add_conditional_edges(
            "ring_overlay", self._route_after_overlay,
            {"redo": "resolve_geometry", "ok": "reconstruct"},
        )
        g.add_edge("reconstruct", "verify_grains")
        g.add_edge("verify_grains", END)
        return g

    # ---- nodes ----------------------------------------------------------- #
    async def _call(self, tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
        try:
            return _as_dict(await self._execute(tool, args))
        except Exception as e:  # a node failure must not crash the graph
            return {"status": "error", "error": f"{tool}: {e}"}

    async def _n_discover(self, state: FFHEDMState) -> FFHEDMState:
        wd = state.get("working_dir", "")
        if not wd:
            wd = interrupt({
                "gate": "working_dir",
                "prompt": ("Which experiment directory holds this FF-HEDM data? "
                           "Give the absolute path (it should contain a cali/ folder "
                           "and the sample scan folders)."),
            })
            wd = extract_working_dir(wd) or wd.strip()

        listing = _listing_names(await self._call("list_directory", {"path": wd}))
        cls = _classify_entries(listing)

        # calibrants live in cali/ if present; look there too
        cali_dir = os.path.join(wd, "cali")
        cali_listing = _listing_names(await self._call("list_directory", {"path": cali_dir}))
        cali_hits = [os.path.join("cali", e) for e in cali_listing
                     if any(h in f" {e.lower()} " for h in _POWDER_HINTS)]

        gl = list(state.get("gate_log", []))
        gl.append({"gate": "discover", "dir": wd,
                   "found": {"calibrants": cls["calibrants"] + cali_hits,
                             "inplane": cls["inplane"], "samples": cls["samples"]}})
        return {
            "working_dir": wd,
            "calibrant_files": cls["calibrants"] + cali_hits,
            "inplane_dir": (cls["inplane"][0] if cls["inplane"] else ""),
            "sample_dirs": cls["samples"],
            "gate_log": gl,
        }

    async def _n_select_calibrant(self, state: FFHEDMState) -> FFHEDMState:
        cands = state.get("calibrant_files", [])
        gl = list(state.get("gate_log", []))
        if not cands:
            ans = interrupt({
                "gate": "select_calibrant",
                "prompt": ("No powder calibrant (CeO2/LaB6/Si) auto-detected in "
                           f"{state.get('working_dir')} or its cali/ folder. "
                           "Give the calibrant image path to calibrate against."),
            })
            chosen = extract_working_dir(ans) or ans.strip()
            by = "user"
        elif len(cands) == 1:
            chosen = cands[0]
            by = "auto"  # single candidate → auto-select, logged, no prompt
        else:
            numbered = "\n".join(f"  {i+1}) {c}" for i, c in enumerate(cands))
            ans = interrupt({
                "gate": "select_calibrant",
                "prompt": (f"Found {len(cands)} calibrant files:\n{numbered}\n"
                           "Which should I calibrate against? (number, name, or 'all')"),
                "options": cands,
            })
            chosen = self._resolve_choice(ans, cands)
            by = "user"
        gl.append({"gate": "select_calibrant", "decision": chosen, "by": by})
        return {"chosen_calibrant": chosen, "gate_log": gl}

    async def _n_propose_folders(self, state: FFHEDMState) -> FFHEDMState:
        wd = state.get("working_dir", "")
        proposed = {
            "calib": os.path.join(wd, "APEXA_calib"),
            "inplane": os.path.join(wd, "APEXA_inplane_tx"),
            "recon": os.path.join(wd, "APEXA_ff_recon"),
        }
        pretty = "\n".join(f"  {k:8s} → {v}" for k, v in proposed.items())
        ans = interrupt({
            "gate": "propose_folders",
            "prompt": ("I'll create these output folders and run each step in its own "
                       f"directory:\n{pretty}\n"
                       "OK? (reply 'yes' to accept, or give a base path to use instead)"),
            "proposed": proposed,
        })
        text = (ans or "").strip().lower()
        gl = list(state.get("gate_log", []))
        if text in ("", "y", "yes", "ok", "accept", "sure"):
            dirs = proposed
            by = "accepted"
        else:
            base = extract_working_dir(ans) or ans.strip()
            dirs = {"calib": os.path.join(base, "APEXA_calib"),
                    "inplane": os.path.join(base, "APEXA_inplane_tx"),
                    "recon": os.path.join(base, "APEXA_ff_recon")}
            by = "edited"
        gl.append({"gate": "propose_folders", "decision": dirs, "by": by})
        return {"output_dirs": dirs, "gate_log": gl}

    async def _n_resolve_geometry(self, state: FFHEDMState) -> FFHEDMState:
        # ω-sign (handbook gate 1): aerotech → -1 automatically; else stop-and-ask.
        gl = list(state.get("gate_log", []))
        sign = state.get("omega_sign", 0)
        if not sign:
            q = (state.get("raw_query", "") + " " + state.get("working_dir", "")).lower()
            if "aero" in q:
                sign, by = -1, "auto-aero"
            else:
                ans = interrupt({
                    "gate": "omega_sign",
                    "prompt": ("What is the omega rotation sign convention for this scan? "
                               "(Aerotech stages → negative. Reply '-1' or '+1', or the "
                               "stage type.)"),
                })
                low = (ans or "").lower()
                sign = -1 if ("-1" in low or "aero" in low or "neg" in low) else 1
                by = "user"
            gl.append({"gate": "omega_sign", "decision": sign, "by": by})
        return {"omega_sign": sign, "gate_log": gl}

    async def _n_calibrate(self, state: FFHEDMState) -> FFHEDMState:
        done = dict(state.get("done", {}))
        # Idempotency at the graph level (belt-and-suspenders with the server guard):
        if "calibrate" in done and done["calibrate"].get("status") == "ok":
            return {}
        out = state["output_dirs"]["calib"]
        chosen = state.get("chosen_calibrant", "")
        img = chosen if os.path.isabs(chosen) else os.path.join(state["working_dir"], chosen)
        res = await self._call("midas_auto_calibrate",
                               {"image_file": img, "output_dir": out})
        status = str(res.get("status", "")).lower()
        geo = self._extract_geometry(res)
        done["calibrate"] = {"status": "ok" if status in ("ok", "success", "completed") else "error",
                             "output": res.get("refined_param_file") or out,
                             "residual_px": geo.get("residual"),
                             "cached": bool(res.get("cached"))}
        upd: FFHEDMState = {"done": done}
        if geo.get("lsd"):
            upd["lsd_um"] = geo["lsd"]
        if geo.get("bc"):
            upd["beam_center"] = geo["bc"]
        if geo.get("energy"):
            upd["energy_kev"] = geo["energy"]
        return upd

    async def _n_verify_calibration(self, state: FFHEDMState) -> FFHEDMState:
        cal = state.get("done", {}).get("calibrate", {})
        resid = cal.get("residual_px")
        gl = list(state.get("gate_log", []))
        # Stop-and-ask if calibration errored or residual is missing/high
        # (handbook gate 5; saturation guard, memory project_calibrant_saturation).
        bad = (cal.get("status") != "ok") or (resid is None) or (isinstance(resid, (int, float)) and resid > 1.0)
        if bad:
            # Surface the geometry the fit DID produce — on the pip-console engine
            # the convergence metric is often absent even when Lsd/BC are sound, so
            # a human choosing accept/redo needs the actual refined numbers, not just
            # "residual=None".
            lsd = state.get("lsd_um")
            bc = state.get("beam_center")
            energy = state.get("energy_kev")
            geo_bits = []
            if lsd is not None:
                geo_bits.append(f"Lsd={lsd} µm")
            if bc:
                geo_bits.append(f"BC={bc}")
            if energy:
                geo_bits.append(f"E={energy} keV")
            geo_line = ("  Refined geometry: " + ", ".join(geo_bits) + ".\n") if geo_bits else ""
            ans = interrupt({
                "gate": "verify_calibration",
                "prompt": (f"Calibration result looks uncertain (status={cal.get('status')}, "
                           f"residual={resid}).\n{geo_line}"
                           "Check the lineout S/N for saturation. "
                           "Reply 'accept' to proceed, or 'redo' to re-resolve geometry."),
            })
            decision = "redo" if "redo" in (ans or "").lower() else "ok"
        else:
            decision = "ok"
        gl.append({"gate": "verify_calibration", "decision": decision, "residual_px": resid})
        done = {**state.get("done", {}), "calibrate": {**cal, "verify": decision}}
        return {"gate_log": gl, "done": done}

    def _route_after_verify(self, state: FFHEDMState) -> str:
        return state.get("done", {}).get("calibrate", {}).get("verify", "ok")

    async def _n_inplane_tx(self, state: FFHEDMState) -> FFHEDMState:
        inplane = state.get("inplane_dir", "")
        gl = list(state.get("gate_log", []))
        done = dict(state.get("done", {}))
        if not inplane:
            gl.append({"gate": "inplane_tx", "decision": "skipped-none-found"})
            return {"gate_log": gl}
        # Au single-crystal scan refines in-plane tilt tx (memory project_au_inplane_calibration)
        out = state["output_dirs"]["inplane"]
        res = await self._call("midas_auto_calibrate", {
            "image_file": inplane if os.path.isabs(inplane) else os.path.join(state["working_dir"], inplane),
            "output_dir": out,
            "seed_from_params": state.get("done", {}).get("calibrate", {}).get("output", ""),
        })
        done["inplane_tx"] = {"status": str(res.get("status", "")).lower(), "output": out,
                              "cached": bool(res.get("cached"))}
        tilts = dict(state.get("tilts", {}))
        tx = self._extract_geometry(res).get("tx")
        if tx is not None:
            tilts["tx"] = tx
        gl.append({"gate": "inplane_tx", "decision": "ran", "dir": inplane})
        return {"done": done, "tilts": tilts, "gate_log": gl}

    async def _n_ring_overlay(self, state: FFHEDMState) -> FFHEDMState:
        # Handbook gate 6: MANDATORY ring overlay before reconstruction.
        gl = list(state.get("gate_log", []))
        ans = interrupt({
            "gate": "ring_overlay",
            "prompt": ("Before reconstruction: do the predicted rings sit on the measured "
                       "data in the calibration overlay? (I can render it with run_midas_viewer.) "
                       "Reply 'yes' to reconstruct, or 'redo' to re-resolve geometry."),
        })
        decision = "redo" if "redo" in (ans or "").lower() or "no" == (ans or "").strip().lower() else "ok"
        gl.append({"gate": "ring_overlay", "decision": decision})
        return {"gate_log": gl, "done": {**state.get("done", {}), "_overlay": decision}}

    def _route_after_overlay(self, state: FFHEDMState) -> str:
        return state.get("done", {}).get("_overlay", "ok")

    async def _n_reconstruct(self, state: FFHEDMState) -> FFHEDMState:
        done = dict(state.get("done", {}))
        if "reconstruct" in done and done["reconstruct"].get("status") == "ok":
            return {}
        out = state["output_dirs"]["recon"]
        param = state.get("done", {}).get("calibrate", {}).get("output", "")
        sample = state.get("sample_dirs", [""])[0] if state.get("sample_dirs") else ""
        res = await self._call("run_ff_hedm_full_workflow", {
            "result_folder": out,
            "param_file": param,
            "data_file": sample if os.path.isabs(sample) else (os.path.join(state["working_dir"], sample) if sample else ""),
        })
        status = str(res.get("status", "")).lower()
        done["reconstruct"] = {"status": "ok" if status in ("ok", "success", "completed") else "error",
                               "output": out, "grains": res.get("n_grains") or res.get("grains"),
                               "cached": bool(res.get("cached"))}
        return {"done": done}

    async def _n_verify_grains(self, state: FFHEDMState) -> FFHEDMState:
        recon = state.get("done", {}).get("reconstruct", {})
        gl = list(state.get("gate_log", []))
        gl.append({"gate": "verify_grains", "grains": recon.get("grains"),
                   "status": recon.get("status")})
        summary = self._summarize({**state, "gate_log": gl})
        # persist the citable artifact
        try:
            path = os.path.join(state.get("working_dir", "."), "APEXA_ffhedm_workflow.json")
            await self._call("write_file", {"path": path, "content": summary})
        except Exception:
            pass
        return {"gate_log": gl, "summary": summary}

    # ---- extraction / formatting helpers -------------------------------- #
    def _resolve_choice(self, ans: str, cands: List[str]) -> str:
        ans = (ans or "").strip()
        if ans.lower() == "all":
            return "all"
        if ans.isdigit():
            i = int(ans) - 1
            if 0 <= i < len(cands):
                return cands[i]
        for c in cands:
            if ans.lower() in c.lower():
                return c
        return ans

    def _extract_geometry(self, res: Dict[str, Any]) -> Dict[str, Any]:
        """Pull geometry out of a calibration result across plausible key names.

        Handles two payload shapes seen in the wild:
          * flat top-level keys (Lsd/BC/energy_kev/residual_px) — legacy / test shape;
          * the pip-console ``midas-autocalibrate`` engine, which nests the refined
            geometry under ``calibrated_parameters`` (separate ``bc_x``/``bc_y``,
            ``lsd`` in µm, ``wavelength`` in Å) and quality under
            ``convergence_metrics`` (``final_mean_strain``, ``converged``).
        Without the nested lookups a real calibration yields an empty geometry
        summary and residual=None, which makes the verify gate cry wolf every run.
        """
        g: Dict[str, Any] = {}
        cp = res.get("calibrated_parameters")
        cp = cp if isinstance(cp, dict) else {}
        cm = res.get("convergence_metrics")
        cm = cm if isinstance(cm, dict) else {}
        src = {**res, **cp}   # nested refined values take precedence over any flat echo

        for k in ("lsd", "Lsd", "lsd_um", "LsdMicrons"):
            if src.get(k) is not None:
                g["lsd"] = src[k]; break
        # beam center: a [x, y] list, or the engine's separate bc_x / bc_y floats
        for k in ("beam_center", "BC", "bc"):
            if isinstance(src.get(k), (list, tuple)) and len(src[k]) >= 2:
                g["bc"] = list(src[k][:2]); break
        else:
            if src.get("bc_x") is not None and src.get("bc_y") is not None:
                g["bc"] = [src["bc_x"], src["bc_y"]]
        # energy: direct, else derive from wavelength (Å): E[keV] = hc/λ
        for k in ("energy_kev", "energy", "Energy"):
            if src.get(k):
                g["energy"] = src[k]; break
        else:
            wl = src.get("wavelength") or src.get("wavelength_angstrom")
            try:
                if wl:
                    g["energy"] = round(12.398419843320026 / float(wl), 4)
            except (TypeError, ValueError):
                pass
        # residual / fit quality: flat keys, else the nested convergence metric
        for k in ("residual_px", "residual", "mean_residual", "rms_residual"):
            if res.get(k) is not None:
                g["residual"] = res[k]; break
        else:
            for k in ("final_mean_strain", "mean_strain", "final_residual", "residual"):
                if cm.get(k) is not None:
                    g["residual"] = cm[k]; break
        if isinstance(cm.get("converged"), bool):
            g["converged"] = cm["converged"]
        for k in ("tx", "tilt_x", "in_plane_tilt"):
            if src.get(k) is not None:
                g["tx"] = src[k]; break
        return g

    def _summarize(self, state: FFHEDMState) -> str:
        return json.dumps({
            "workflow": "ff_hedm",
            "working_dir": state.get("working_dir"),
            "chosen_calibrant": state.get("chosen_calibrant"),
            "output_dirs": state.get("output_dirs"),
            "geometry": {"lsd_um": state.get("lsd_um"), "beam_center": state.get("beam_center"),
                         "energy_kev": state.get("energy_kev"), "tilts": state.get("tilts"),
                         "omega_sign": state.get("omega_sign")},
            "steps": state.get("done"),
            "gates": state.get("gate_log"),
        }, indent=2, default=str)


# --------------------------------------------------------------------------- #
# factory
# --------------------------------------------------------------------------- #
def build_default_workflow(execute_tool_fn: ExecuteToolFn) -> "FFHEDMWorkflow":
    """FF-HEDM workflow wired for production: durable checkpoint under ~/.apexa/.

    Durability (AsyncSqliteSaver) is on by default so a workflow paused on a gate
    survives a CLI restart. Disable with ``APEXA_FFHEDM_DURABLE=0`` (→ MemorySaver,
    the Phase-1 behavior). Override the store location with ``APEXA_FFHEDM_DB``.
    """
    durable = os.environ.get("APEXA_FFHEDM_DURABLE", "1").strip().lower() not in ("0", "false", "no")
    if not durable:
        return FFHEDMWorkflow(execute_tool_fn)  # in-memory, no cross-restart resume
    db = os.environ.get("APEXA_FFHEDM_DB", "").strip() or os.path.join(
        os.path.expanduser("~"), ".apexa", "ffhedm_graph.sqlite")
    state = os.path.splitext(db)[0] + ".state.json"
    return FFHEDMWorkflow(execute_tool_fn, db_path=db, state_path=state)
