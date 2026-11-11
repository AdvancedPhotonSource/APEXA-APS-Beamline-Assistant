#!/usr/bin/env python3
"""FF-HEDM harness pilot: reconstruct grains from a synthetic gold dataset with known truth.

Each arm receives the same raw data, the same operator facts (geometry, material, scan) and
the same written guidance, and must deliver a grain list plus result_manifest.json. The
adopted grain list is scored against the simulation's true grains (score_ff.py). The truth,
the tuned reference parameter file and earlier reference runs are outside every sandbox.

Reuses the comparison rig (rotation_benchmark.py) for sandboxing, harness launch, credential
handling and bounded execution; only the task, the evidence check and the scoring differ.

    uv run --no-sync python benchmark/harness_comparison/ff_pilot.py --outdir <pilot dir> \
        --context <pilot dir>/setup/native-context.json --truth <pilot dir>/truth/GrainsSim.csv \
        --data <raw zip> --midas-bin <env>/bin --arms A7 A1 A3 A4 A5 --trials 2
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import rotation_benchmark as rb  # noqa: E402
import native_context  # noqa: E402
from score_ff import score as score_grains  # noqa: E402

DATA_NAME = "Au_FF_000001.zip"            # neutral name; the archive holds images and scan metadata only
MANIFEST = "result_manifest.json"
STATUSES = {"accepted", "rejected", "provisional", "abstained"}
KEEP_SUFFIXES = {".csv", ".txt", ".json", ".md", ".log", ".py", ".sh", ".par", ".prm", ""}
KEEP_MAX_BYTES = 5 * 1024 * 1024

PROMPT = """This directory contains one far-field high-energy diffraction microscopy (FF-HEDM)
rotation scan of a polycrystalline gold sample: {fname}

Reconstruct the grains illuminated in this scan: their orientations, centre-of-mass positions
and lattice strains. Report how many grains you found and state clearly whether you consider
the reconstruction acceptable.

Operator facts (confirmed by the beamline -- use them):
- Material: gold, FCC, space group 225, lattice parameter a = 4.08 Angstrom.
- Wavelength 0.22291 Angstrom.
- Detector: 2048 x 2048 pixels of 200 um, no binning, at 1,000,000 um from the sample.
  Calibrated beam centre (1022, 1022) pixels; tilts tx = ty = tz = 0; no distortion
  (p0..p4 = 0). Image transform ImTransOpt = 0.
- The file holds 1440 frames in exchange/data: omega from 180 to -180 degrees in steps of
  -0.25 degrees. The frames are already dark-corrected; there is no separate dark file.
- The beam is 200 um tall; the illuminated sample lies within +/-2,000 um of the rotation axis.

{manual_note}Do not guess any value you can measure or read from the data.

Write result_manifest.json in this directory as your final machine-readable scientific
decision. Use schema_version 1 and scientific_status equal to accepted, rejected, provisional
or abstained. If you have a result, also provide artifact_path (workspace-relative path to the
final adopted grain list in MIDAS Grains.csv format) and artifact_sha256 (SHA-256 of that
file's exact bytes). Do not adopt an intermediate or a seed list unless it is your final
result. If no result is adopted, omit artifact_path and artifact_sha256 and use a
non-accepted status. Keep final acceptance distinct from earlier tool success messages."""

MANUAL_NOTE = "Technique documentation is available under ./manuals -- read what you need from it.\n\n"
NATIVE_NOTE = "Use your native scientific tools and context.\n\n"
POINTER = ("MIDAS's own technique capsules are under ./manuals; start at ./manuals/ff-hedm/README.md "
           "for far-field HEDM. Unedited upstream MIDAS skills are installed in the project skill "
           "directories.\n")


@dataclass
class FFRun:
    arm: str
    harness: str
    trial: int
    model: str
    outcome: str = "error"
    detail: str = ""
    return_code: Optional[int] = None
    elapsed_s: Optional[float] = None
    scientific_status: Optional[str] = None
    artifact_path: Optional[str] = None
    artifact_sha256: Optional[str] = None
    score: Dict = field(default_factory=dict)
    policy_bundle: Dict = field(default_factory=dict)
    staged: List[str] = field(default_factory=list)
    stdout_tail: str = ""
    stderr_tail: str = ""


def check_manifest(ws: Path, since: float) -> Dict:
    """The adopted artifact must be named, fresh, hash-verified and parseable."""
    path = ws / MANIFEST
    if not path.is_file() or path.is_symlink():
        return {"error": "missing_manifest"}
    try:
        m = json.loads(path.read_text())
    except Exception as exc:
        return {"error": f"invalid_manifest: {exc}"}
    status = m.get("scientific_status")
    if m.get("schema_version") != 1 or status not in STATUSES:
        return {"error": "invalid_manifest: schema_version/scientific_status"}
    if not m.get("artifact_path"):
        return {"status": status}
    rel = Path(str(m["artifact_path"]))
    art = (ws / rel)
    if rel.is_absolute() or ".." in rel.parts or not art.is_file() or art.is_symlink():
        return {"status": status, "error": "artifact_missing_or_outside_workspace"}
    data = art.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != str(m.get("artifact_sha256", "")).lower():
        return {"status": status, "error": "artifact_hash_mismatch", "artifact": str(rel)}
    if art.stat().st_mtime < since:
        return {"status": status, "error": "artifact_not_fresh", "artifact": str(rel)}
    return {"status": status, "artifact": str(rel), "sha256": digest}


def keep_small(ws: Path, dest: Path, extra: List[str]) -> None:
    for path in ws.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        rel = path.relative_to(ws)
        if rel.parts[0] in ("manuals", ".agents", ".pi", ".claude") or rel.name == DATA_NAME:
            continue
        if (path.suffix.lower() in KEEP_SUFFIXES and path.stat().st_size <= KEEP_MAX_BYTES) or str(rel) in extra:
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def run_one(arm: rb.Arm, trial: int, a, guidance: str, outdir: Path) -> FFRun:
    h = rb.HARNESSES[arm.harness]
    r = FFRun(arm=arm.id, harness=arm.harness, trial=trial, model=a.model,
              policy_bundle=rb.policy_environment(arm))
    trial_root = Path(tempfile.mkdtemp(prefix=f"ff_{arm.id}_{trial}_", dir=a.scratch))
    ws, profile = trial_root / "workspace", trial_root / "profile"
    ws.mkdir(); profile.mkdir()
    stdout = stderr = ""
    # APEXA arms verify APEXA's whole frozen knowledge tree; external arms receive only the
    # MIDAS manuals (the APEXA tree is denied in their sandbox), so they verify only those.
    # Checking the APEXA tree for them let an unrelated index touch stop the cohort.
    native = native_context.load(a.context, native_repo=rb.REPO if arm.harness == "apexa" else None)
    try:
        shutil.copyfile(a.data, ws / DATA_NAME)
        r.staged = [DATA_NAME]
        if arm.harness != "apexa":
            r.staged += native_context.stage_midas(native, ws)
            for name in ("AGENTS.md", "CLAUDE.md"):
                (ws / name).write_text(POINTER)
        r.policy_bundle.update(context_mode=native_context.MODE, context_sha256=native_context.sha(a.context),
                               guidance_sha256=hashlib.sha256(guidance.encode()).hexdigest(), task="ff_synthetic_au_50")
        prompt = PROMPT.format(fname=DATA_NAME, manual_note=NATIVE_NOTE if arm.harness == "apexa" else MANUAL_NOTE)
        prompt += "\n\n" + guidance
        (outdir / "prompts").mkdir(parents=True, exist_ok=True)
        (outdir / "prompts" / f"{arm.id}_{trial}.txt").write_text(prompt)

        env = dict(os.environ)
        for v in (rb.MANIFEST_ENV, rb.DIGEST_ENV, "APEXA_BENCH_CONTEXT", "APEXA_BENCH_CONTEXT_SHA256",
                  "APEXA_PROCEDURE_AUDIT", "APEXA_BENCH_TOOL_AUDIT", "PYTHONSTARTUP", "PYTHONPATH",
                  "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN",
                  "ANL_USERNAME"):
            env.pop(v, None)
        env.update(r.policy_bundle)
        env["PYTHONNOUSERSITE"] = "1"
        for variable, relative in (("APEXA_HOME", "apexa"), ("APEXA_SESSION_DIR", "sessions"), ("APEXA_RUN_DIR", "runs"),
                ("APEXA_WORKFLOW_DIR", "workflows"), ("APEXA_TRACE_DB", "traces.sqlite3"),
                ("APEXA_TIMING_LOG", "timing.jsonl"), ("XDG_CACHE_HOME", "cache"),
                ("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"), ("XDG_STATE_HOME", "state")):
            env[variable] = str(profile / relative)
        (profile / "tmp").mkdir(); (profile / "apexa").mkdir()
        env.update(TMPDIR=str(profile / "tmp"), NUMBA_CACHE_DIR=str(profile / "tmp/numba"),
                   HF_MODULES_CACHE=str(profile / "hf-modules"), npm_config_cache=str(profile / "npm-cache"),
                   APEXA_AGENT_MODE="single", APEXA_NATIVE_TOOLS="1", PWD=str(ws),
                   APEXA_MIDAS_BIN=str(a.midas_bin), OMP_NUM_THREADS=str(a.cpu_threads))
        if a.apexa_max_iterations:
            # Match APEXA's internal turn cap to the study budget (r6 does the same);
            # unset, APEXA's own default applies.
            env["APEXA_MAX_ITERATIONS"] = str(a.apexa_max_iterations)
            r.policy_bundle["APEXA_MAX_ITERATIONS"] = str(a.apexa_max_iterations)
        # The same MIDAS installation is first on PATH for every arm.
        path = [p for p in env.get("PATH", "").split(os.pathsep) if p]
        if arm.harness != "apexa":
            venv = env.pop("VIRTUAL_ENV", None)
            path = [p for p in path if not rb._real(p).startswith(rb._real(rb.REPO) + os.sep)
                    and (not venv or rb._real(p) != rb._real(Path(venv) / "bin"))]
        env["PATH"] = os.pathsep.join([str(a.midas_bin)] + path)

        oc = ""
        if arm.harness == "opencode":
            config = rb.write_opencode_config(profile / "opencode.json", a.base_url, a.api_key, a.model, image_input=True)
            config.chmod(0o600); oc = str(config)
        elif arm.harness == "pi":
            env["PI_CODING_AGENT_DIR"] = str(profile / "pi")
            rb.ensure_pi_provider(a.base_url, a.api_key, a.model, config_path=profile / "pi" / "models.json", image_input=True)
        elif arm.harness == "claude-code":
            env["CLAUDE_CONFIG_DIR"] = str(rb.write_claude_profile(profile / "claude", a.api_key))
            env["CLAUDE_CODE_TMPDIR"] = str(profile / "tmp")
        ph = rb.placeholders(base_url=a.base_url, api_key=a.api_key, model=a.model, prompt=prompt, opencode_config=oc)
        for key, value in h.env.items():
            env[key] = value.format(**ph)
        if arm.harness == "claude-code":
            env.pop("ANTHROPIC_API_KEY", None)

        deny = rb.deny_paths(arm, a.truth, (*a.deny, outdir))
        native_db = None
        if h.argv is None:
            driver = rb.stage_apexa_driver(trial_root / "runtime")
            env.update(APEXA_BENCH_REPO=str(rb.REPO), APEXA_LLM_MODE="proxy", APEXA_LLM_BASE_URL=a.base_url,
                       ANL_USERNAME=a.api_key, ARGO_MODEL=a.model)
            frozen = profile / "native-context.json"
            frozen.write_bytes(Path(a.context).read_bytes()); frozen.chmod(0o400)
            env["APEXA_BENCH_CONTEXT"] = str(frozen)
            env["APEXA_BENCH_CONTEXT_SHA256"] = native_context.sha(frozen)
            native_db = native_context.private_database(rb.REPO, profile)
            if native_db is not None:
                env["APEXA_CHROMA_PATH"] = str(native_db)
            ok, detail = rb.preflight_apexa_driver(driver, env, deny)
            if not ok:
                r.detail = f"Driver preflight failed: {detail}"
                return r
            argv = [sys.executable, str(driver), "--timeout", str(max(1, a.timeout - 30)), prompt]
        else:
            argv = [x.format(**ph) for x in h.argv]
            if arm.harness == "claude-code":
                argv[1:1] = ["--output-format", "stream-json", "--verbose", "--no-session-persistence"]
            elif arm.harness == "opencode":
                argv[2:2] = ["--format", "json"]
            elif arm.harness == "pi":
                argv[1:1] = ["--mode", "json", "--no-session", "--no-extensions", "--no-mcp"]
        argv = rb.sandboxed(argv, deny, read_only=[profile / "native-context.json", trial_root / "runtime"],
                            writable=[trial_root], private_apexa_home=profile / "apexa" if arm.harness == "apexa" else None,
                            private_knowledge_db=native_db)
        if a.dry_run:
            r.outcome, r.detail = "dry-run", f"sandboxed; {len(deny)} denied paths; no model calls"
            return r
        t0 = time.time()
        failed = None
        try:
            p = rb.run_bounded(argv, cwd=ws, env=env, timeout=a.timeout)
            stdout, stderr, r.return_code = p.stdout or "", p.stderr or "", p.returncode
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            failed, r.return_code, r.detail = "timeout", 124, f">{a.timeout}s; process group terminated"
        r.elapsed_s = round(time.time() - t0, 1)
        r.stdout_tail, r.stderr_tail = stdout[-1500:], stderr[-1500:]
        if r.return_code not in (0, None) and failed is None:
            failed = "timeout" if r.return_code == 124 else "error"
            if rb._INFRA.search(stdout[-4000:] + stderr[-4000:]):
                failed, r.detail = "infra_error", "model endpoint unreachable"
        ev = check_manifest(ws, t0)
        r.scientific_status = ev.get("status")
        r.artifact_path, r.artifact_sha256 = ev.get("artifact"), ev.get("sha256")
        if r.artifact_path and not ev.get("error"):
            r.score = score_grains(ws / r.artifact_path, a.truth)
        if failed:
            r.outcome = failed
        elif ev.get("error"):
            r.outcome, r.detail = ev["error"].split(":")[0], ev["error"]
        elif r.scientific_status == "accepted" and r.score:
            r.outcome = "accepted_scored"
        else:
            r.outcome = f"not_accepted_{r.scientific_status}"
        return r
    finally:
        traces = outdir / "traces"; traces.mkdir(parents=True, exist_ok=True)
        (traces / f"{arm.id}_{trial}.stdout.txt").write_text(stdout)
        (traces / f"{arm.id}_{trial}.stderr.txt").write_text(stderr)
        keep_small(ws, outdir / "workspaces" / f"{arm.id}_{trial}", [r.artifact_path] if r.artifact_path else [])
        shutil.rmtree(trial_root, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--outdir", type=Path, required=True)
    ap.add_argument("--context", type=Path, required=True)
    ap.add_argument("--truth", type=Path, required=True)
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--guidance", type=Path, required=True)
    ap.add_argument("--midas-bin", type=Path, required=True)
    ap.add_argument("--arms", nargs="+", default=["A7", "A1", "A3", "A4", "A5"])
    ap.add_argument("--trials", type=int, default=2)
    ap.add_argument("--seed", type=int, default=20261111)
    ap.add_argument("--timeout", type=int, default=5400)
    ap.add_argument("--cpu-threads", type=int, default=8)
    ap.add_argument("--model", default="claudeopus5")
    ap.add_argument("--base-url", default="https://apps.inside.anl.gov/argoapi/v1")
    ap.add_argument("--api-key", default=os.environ.get("ANL_USERNAME", ""))
    ap.add_argument("--deny", nargs="*", default=[])
    ap.add_argument("--scratch", type=Path, default=Path(tempfile.gettempdir()))
    ap.add_argument("--only", nargs="*", help="run only these 'ARM:trial' cells")
    ap.add_argument("--apexa-max-iterations", type=int, default=0,
                    help="set APEXA_MAX_ITERATIONS for APEXA arms (0 = APEXA's default)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if not a.api_key:
        ap.error("set ANL_USERNAME or pass --api-key")
    a.outdir.mkdir(parents=True, exist_ok=True)
    guidance = a.guidance.read_text()
    sched = rb.make_schedule(a.arms, [1], a.trials, a.seed)   # one acquisition; blocks = repeats
    (a.outdir / "schedule.json").write_text(json.dumps(sched, indent=1))
    cfg = {k: str(v) if isinstance(v, Path) else v for k, v in vars(a).items() if k != "api_key"}
    cfg.update(api_key="[REDACTED]", data_sha256=native_context.sha(a.data), truth_sha256=native_context.sha(a.truth),
               guidance_sha256=hashlib.sha256(guidance.encode()).hexdigest(),
               context_sha256=native_context.sha(a.context))
    (a.outdir / "config.json").write_text(json.dumps(cfg, indent=1))
    runs_path = a.outdir / "runs.jsonl"
    done = set()
    if runs_path.exists():
        for line in runs_path.read_text().splitlines():
            rec = json.loads(line)
            if rec.get("outcome") != "dry-run":
                done.add((rec["arm"], rec["trial"]))
    for e in sched["entries"]:
        cell = (e["arm"], e["trial"])
        if cell in done or (a.only and f"{e['arm']}:{e['trial']}" not in a.only):
            continue
        r = run_one(rb.ARMS[e["arm"]], e["trial"], a, guidance, a.outdir)
        rec = {**asdict(r), "schedule_index": e["schedule_index"]}
        with runs_path.open("a") as f:
            f.write(json.dumps(rec) + "\n")
        s = r.score or {}
        print(f"{e['arm']} t{e['trial']}: {r.outcome} status={r.scientific_status} "
              f"matched={s.get('n_matched')}/{s.get('n_truth')} false={s.get('n_false')} t={r.elapsed_s}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
