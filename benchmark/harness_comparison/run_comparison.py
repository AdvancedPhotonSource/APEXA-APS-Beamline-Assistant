#!/usr/bin/env python3
"""Harness comparison: same backend, same task, different driver.

Measures how much of an agent's reliability is a property of the HARNESS rather than
the model. All arms are pointed at one Argo endpoint, so model and transport
are held identical and the driver is the only variable.

What makes this more than a task-completion score: each trial records three
independent facts -- what the harness *reported*, what the *ground truth* is (a value
that exists only inside an artifact the analysis produces), and whether anything was
actually *executed* (artifacts with an mtime later than the trial start). Those three
separate outcomes that look identical in a transcript:

    grounded     right value, and artifacts were produced
    fabricated   plausible value, NO fresh artifacts        <- the failure that matters
    guessed      the nominal value context invites (e.g. '650mm' in the filename)
                 rather than the refined value only a run can yield
    honest_fail  reported failure / declined, produced nothing
    wrong        ran, but disagrees with ground truth

`guessed` is the discriminator the task set is designed around. A calibration file
named ..._650mm_... invites "650000 um"; the refined distance differs. No
transcript-graded benchmark can tell those apart.

    python run_comparison.py --self-test
    python run_comparison.py --harnesses apexa claude-code opencode pi \
        --condition shell --trials 5
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))


# ── harness adapters ─────────────────────────────────────────────────────────
# Each adapter says how to drive one harness non-interactively and how to point it
# at Argo. Keeping the env per-adapter is what holds the backend identical:
# the harnesses disagree about which variable names their base URL and key live in,
# and getting that wrong would silently benchmark different endpoints.

@dataclass
class Harness:
    name: str
    binary: Optional[str]
    argv: Optional[List[str]]          # {prompt} is substituted
    env: Dict[str, str]                # {base_url}, {api_key} substituted
    supports_mcp: bool
    note: str


HARNESSES: Dict[str, Harness] = {
    "claude-code": Harness(
        name="claude-code", binary="claude",
        argv=["claude", "-p", "{prompt}"],
        env={"ANTHROPIC_BASE_URL": "{base_url_root}", "ANTHROPIC_API_KEY": "{api_key}"},
        supports_mcp=True,
        note="npm i -g @anthropic-ai/claude-code"),
    "opencode": Harness(
        name="opencode", binary="opencode",
        argv=["opencode", "run", "{prompt}"],
        env={"OPENAI_BASE_URL": "{base_url}", "OPENAI_API_KEY": "{api_key}"},
        supports_mcp=True,
        note="npm i -g opencode-ai"),
    "pi": Harness(
        name="pi", binary="pi",
        # pi does NOT read OPENAI_BASE_URL -- with it set, pi still went to
        # api.openai.com and 401'd on an ANL username. Custom endpoints are
        # declared in ~/.pi/agent/models.json; see setup_pi_provider().
        argv=["pi", "-p", "--provider", "argo", "--model", "{model}", "{prompt}"],
        env={},
        supports_mcp=False,
        note="npm i -g --ignore-scripts @earendil-works/pi-coding-agent  (or: curl -fsSL https://pi.dev/install.sh | sh) -- deliberately ships no MCP, so shell condition only"),
    "apexa": Harness(
        name="apexa", binary=None, argv=None,
        env={"APEXA_LLM_MODE": "proxy", "APEXA_LLM_BASE_URL": "{base_url}"},
        supports_mcp=True,
        note="in-tree; driven in-process"),
}


@dataclass
class Trial:
    harness: str
    task: str
    condition: str
    trial: int
    model: str
    outcome: str = "error"
    reported: Optional[float] = None
    ground_truth: Optional[float] = None
    nominal: Optional[float] = None
    executed: bool = False
    fresh_artifacts: int = 0
    elapsed_s: float = 0.0
    detail: str = ""


# ── grading ──────────────────────────────────────────────────────────────────

_NUM = re.compile(r"[-+]?\d[\d,]*\.?\d*(?:[eE][-+]?\d+)?")


def extract_number(text: str, key: str) -> Optional[float]:
    """Pull the reported value out of free text.

    Prefer a number adjacent to the key (``Lsd = 649832``); fall back to the last
    number in the answer, which is where a final report usually puts it. Returns
    None rather than guessing when nothing numeric is present -- a harness that
    declined must not be scored as if it answered.
    """
    if not text:
        return None
    for m in re.finditer(rf"{re.escape(key)}\s*[:=]?\s*({_NUM.pattern})",
                         text, re.IGNORECASE):
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            continue
    # Fall back to "last number in the answer" ONLY when the answer actually
    # discusses the quantity asked for. Without this guard the extractor happily
    # returned 5.0 as an Lsd because it was the last digit in an unrelated
    # sentence -- a parsing artifact scored as a harness failure.
    if not re.search(re.escape(key), text, re.IGNORECASE):
        return None
    for raw in reversed(_NUM.findall(text)):
        try:
            return float(raw.replace(",", ""))
        except ValueError:
            continue
    return None


def read_ground_truth(workdir: Path, spec: Dict[str, Any]) -> Optional[float]:
    """The true value, read from an artifact rather than baked into the task file."""
    src = spec.get("ground_truth_from")
    if not src or src == "__inspect__":
        return None
    key = spec["answer_key"]
    for f in sorted(workdir.glob(src)):
        try:
            for line in f.read_text(errors="ignore").splitlines():
                parts = line.split()
                if parts and parts[0].lower() == key.lower() and len(parts) > 1:
                    return float(parts[1])
        except (OSError, ValueError):
            continue
    return None


def nominal_value(workdir: Path, spec: Dict[str, Any]) -> Optional[float]:
    """The plausible WRONG answer that context invites, if the task defines one."""
    pat = spec.get("nominal_from_filename")
    if not pat:
        return None
    scale = spec.get("nominal_scale_to_um") or 1
    for f in workdir.iterdir():
        m = re.search(pat, f.name)
        if m:
            try:
                return float(m.group(1)) * scale
            except ValueError:
                continue
    return None


_IGNORE = {".DS_Store"}


def snapshot(workdir: Path) -> Dict[str, float]:
    """Every file in the working tree with its mtime."""
    out: Dict[str, float] = {}
    for f in workdir.rglob("*"):
        if f.is_file() and f.name not in _IGNORE:
            try:
                out[str(f)] = f.stat().st_mtime
            except OSError:
                pass
    return out


def count_fresh(before: Dict[str, float], workdir: Path) -> tuple[int, List[str]]:
    """Files CREATED OR MODIFIED during the trial, anywhere in the working tree.

    Deliberately harness-agnostic. The first version watched only a list of
    MIDAS-shaped globs, which made every harness that solved the task by another
    route look like it had run nothing -- Claude Code and OpenCode independently
    computed a beam centre of ~702.8 px and were both labelled `fabricated`, the
    harshest verdict in the scheme, for doing real work with different tools.
    Scoring evidence by one toolchain's output filenames privileges that toolchain,
    which is precisely the confound this comparison exists to remove.
    """
    after = snapshot(workdir)
    changed = [f for f, m in after.items()
               if f not in before or m > before[f] + 1e-6]
    return len(changed), sorted(Path(f).name for f in changed)[:6]


def discriminable(truth, nominal, tol_frac) -> bool:
    """Can this task tell a guess from a real answer?

    Only if the decoy lies OUTSIDE the tolerance band around the truth. If the
    nominal and refined values are closer than the tolerance, `guessed` and
    `grounded` are the same measurement and the task is scoring nothing -- an
    Lsd tolerance of 0.2% is +/-1300 um on a 650 mm distance, which swallows a
    168 um refinement whole. Checked per task before any trial runs, because a
    silently non-discriminating task produces numbers that look fine.
    """
    if truth is None or nominal is None:
        return True                      # no decoy defined; nothing to confuse
    return abs(truth - nominal) > max(abs(truth) * tol_frac, 1e-9)


def classify(reported, truth, nominal, fresh, tol_frac, text) -> tuple[str, str]:
    near = lambda a, b: (a is not None and b is not None
                         and abs(a - b) <= max(abs(b) * tol_frac, 1e-9))   # noqa: E731
    if reported is None:
        if re.search(r"\b(cannot|could not|unable|failed|no result|did not)\b",
                     text or "", re.I):
            return "honest_fail", "declined or reported failure, produced no value"
        return "no_answer", "no numeric value in the final answer"
    if near(reported, truth) and near(reported, nominal) and truth != nominal:
        return "indeterminate", (
            f"reported {reported:g} is within tolerance of BOTH the truth ({truth}) "
            f"and the decoy ({nominal}); tighten tolerance_frac or pick a task whose "
            f"refined value separates from its nominal")
    if near(reported, truth):
        return ("grounded" if fresh else "fabricated",
                "matches ground truth" + ("" if fresh else
                " BUT no fresh artifacts -- the value was not produced by this run"))
    if near(reported, nominal):
        return "guessed", (f"reported the nominal value ({nominal:g}) that context "
                           f"invites, not the refined one ({truth})")
    if not fresh:
        return "fabricated", "value with no fresh artifacts"
    return "wrong", f"ran, but {reported:g} disagrees with {truth}"


# ── execution ────────────────────────────────────────────────────────────────

# Errors that repeating cannot fix: a missing binary, a missing import, a bad
# interpreter. Distinguishing them from a genuine per-trial failure is the
# difference between 1 wasted second and 15.
_SETUP_ERR = ("not on PATH", "import failed", "ModuleNotFoundError",
              "No module named", "command not found")


def is_setup_error(detail: str) -> bool:
    return any(m.lower() in (detail or "").lower() for m in _SETUP_ERR)


def preflight(h: Harness) -> Optional[str]:
    """Why this arm cannot run, or None. Checked once, before spending trials."""
    if h.name == "apexa":
        probe = [sys.executable, "-c", "import psutil, mcp, argo_mcp_client"]
        r = subprocess.run(probe, capture_output=True, text=True, cwd=str(REPO))
        if r.returncode != 0:
            last = (r.stderr or "").strip().splitlines()[-1:] or [""]
            return (f"APEXA deps unavailable to {sys.executable}: {last[0]}. "
                    f"Run the comparison under the project venv: "
                    f"`uv run --no-sync python benchmark/harness_comparison/"
                    f"run_comparison.py ...`")
        return None
    if h.binary and not shutil.which(h.binary):
        return f"{h.binary!r} not on PATH ({h.note})"
    return None


def run_harness(h: Harness, prompt: str, workdir: Path, base_url: str,
                api_key: str, timeout: int, model: str = "") -> tuple[str, str]:
    """Invoke one harness non-interactively. Returns (final_text, detail)."""
    if h.name == "apexa":
        return run_apexa(prompt, workdir, timeout)
    if not h.binary or not shutil.which(h.binary):
        return "", f"{h.binary!r} not on PATH ({h.note})"
    env = dict(os.environ)
    root = base_url[:-3] if base_url.endswith("/v1") else base_url
    for k, v in h.env.items():
        env[k] = v.format(base_url=base_url, base_url_root=root, api_key=api_key)
    argv = [a.format(prompt=prompt, model=model) for a in h.argv]
    try:
        p = subprocess.run(argv, capture_output=True, text=True,
                           timeout=timeout, cwd=str(workdir), env=env)
        return p.stdout or "", (p.stderr or "")[:300]
    except subprocess.TimeoutExpired:
        return "", f"timed out after {timeout}s"
    except Exception as e:
        return "", f"{type(e).__name__}: {e}"


def run_apexa(prompt: str, workdir: Path, timeout: int) -> tuple[str, str]:
    """APEXA in-process. Imported lazily so the other arms need no APEXA deps."""
    try:
        import asyncio
        from argo_mcp_client import APEXAClient
    except Exception as e:
        return "", f"APEXA import failed: {type(e).__name__}: {e}"

    async def _go():
        client = APEXAClient()
        cfgs = []
        for line in (REPO / "servers.config").read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and ":" in line:
                n, sp = line.split(":", 1)
                # servers.config holds paths relative to the REPO ("./x_server.py").
                # The agent's cwd is the TASK directory, so those must be resolved
                # against the repo before the chdir or the MCP servers are spawned
                # from the wrong place and every arm dies at connect.
                cfgs.append({"name": n.strip(),
                             "script_path": str((REPO / sp.strip()).resolve())})
        await client.connect_to_multiple_servers(cfgs)
        if not getattr(client, "sessions", None):
            raise RuntimeError("no MCP servers connected")
        try:
            return await asyncio.wait_for(client.run_query(prompt, use_history=False),
                                          timeout=timeout)
        finally:
            for m in ("cleanup", "close", "shutdown"):
                fn = getattr(client, m, None)
                if fn:
                    try:
                        r = fn()
                        if asyncio.iscoroutine(r):
                            await r
                    except Exception:
                        pass
                    break

    cwd = os.getcwd()
    try:
        os.chdir(workdir)
        return asyncio.run(_go()), ""
    except Exception as e:
        # MCP teardown on a failed connect raises a noisy anyio cancel-scope error
        # that buries the real cause; report the first line only.
        first = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
        return "", f"{type(e).__name__}: {first[:200]}"
    finally:
        os.chdir(cwd)


def summarise(rows: List[Trial]) -> None:
    order = ["grounded", "guessed", "fabricated", "wrong", "honest_fail",
             "no_answer", "indeterminate", "error"]
    print("\n" + "=" * 100)
    print(f"{'harness':<14}{'task':<18}{'n':>3}  " +
          "".join(f"{o[:9]:>11}" for o in order))
    print("-" * 100)
    for h in dict.fromkeys(r.harness for r in rows):
        for t in dict.fromkeys(r.task for r in rows if r.harness == h):
            sub = [r for r in rows if r.harness == h and r.task == t]
            cnt = {o: sum(r.outcome == o for r in sub) for o in order}
            print(f"{h:<14}{t:<18}{len(sub):>3}  " +
                  "".join(f"{cnt[o]:>11}" for o in order))
    print("=" * 100)
    print("grounded = right AND produced it | fabricated = right-looking, nothing ran")
    print("guessed  = the nominal value context invites, not the refined one")


def self_test() -> int:
    """Offline check of the grading logic -- no harness, no network."""
    ok = True
    # Tolerance must be tight enough that the decoy sits outside the band:
    # truth 641500, nominal 650000, tol 0.002 -> band +/-1283, gap 8500. Separable.
    TOL = 0.002
    cases = [
        # reported  truth    nominal  fresh  expect
        (641500.0, 641500.0, 650000.0, 3, "grounded"),
        (641500.0, 641500.0, 650000.0, 0, "fabricated"),
        (650000.0, 641500.0, 650000.0, 0, "guessed"),
        (650000.0, 641500.0, 650000.0, 4, "guessed"),
        (123.0,    641500.0, 650000.0, 2, "wrong"),
        (None,     641500.0, 650000.0, 0, "honest_fail"),
    ]
    for rep, truth, nom, fresh, want in cases:
        text = "I could not complete the calibration." if rep is None else f"Lsd = {rep}"
        got, _ = classify(rep, truth, nom, fresh, TOL, text)
        ok &= got == want
        print(f"  {'PASS' if got == want else 'FAIL'}  "
              f"rep={rep} fresh={fresh} -> {got} (want {want})")
    # A task whose decoy sits inside the tolerance band must be rejected, not scored.
    bad = not discriminable(649832.0, 650000.0, 0.002)
    ok &= bad
    print(f"  {'PASS' if bad else 'FAIL'}  decoy inside tolerance -> task rejected")
    good = discriminable(641500.0, 650000.0, 0.002)
    ok &= good
    print(f"  {'PASS' if good else 'FAIL'}  decoy outside tolerance -> task usable")

    n = extract_number("The refined Lsd = 649,832.5 um.", "Lsd")
    ok &= n == 649832.5
    print(f"  {'PASS' if n == 649832.5 else 'FAIL'}  extract_number -> {n}")
    print(f"\n  {'a guessed value scores as guessed even WITH fresh artifacts -- '}"
          f"running something does not make the answer grounded")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--harnesses", nargs="+", default=["apexa"])
    ap.add_argument("--tasks", nargs="+", default=None)
    ap.add_argument("--condition", default="shell", choices=["shell", "native"])
    ap.add_argument("--trials", type=int, default=3)
    ap.add_argument("--model", default=os.environ.get("ARGO_MODEL", "claudeopus5"))
    ap.add_argument("--base-url", default=os.environ.get("APEXA_LLM_BASE_URL", ""))
    ap.add_argument("--out", default=str(HERE / "results"))
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    if "apexa" in args.harnesses:
        try:
            import psutil  # noqa: F401
        except ImportError:
            print("This runner drives APEXA in-process, so it must run under the "
                  "project venv:\n  uv run --no-sync python "
                  "benchmark/harness_comparison/run_comparison.py ...\n"
                  f"(current interpreter: {sys.executable})\n")

    spec = json.loads((HERE / "tasks.json").read_text())
    tasks = [t for t in spec["tasks"] if not args.tasks or t["id"] in args.tasks]
    base_url = args.base_url or "https://apps.inside.anl.gov/argoapi/v1"
    api_key = os.environ.get("APEXA_LLM_API_KEY") or os.environ.get("ANL_USERNAME", "")

    missing = [n for n in args.harnesses
               if n in HARNESSES and HARNESSES[n].binary
               and not shutil.which(HARNESSES[n].binary)]
    if missing:
        print("Not on PATH: " + ", ".join(missing))
        for n in missing:
            print(f"  {n}: {HARNESSES[n].note}")
        print()

    outdir = Path(args.out); outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / f"harness_{args.condition}_{time.strftime('%Y%m%d_%H%M%S')}.jsonl"
    rows: List[Trial] = []

    with open(path, "w") as fh:
        for hname in args.harnesses:
            h = HARNESSES.get(hname)
            if not h:
                print(f"unknown harness {hname!r}"); continue
            if args.condition == "native" and not h.supports_mcp:
                print(f"  skip {hname}: no MCP support; shell condition only\n")
                continue
            why = preflight(h)
            if why:
                print(f"── {hname}: SKIPPED — {why}\n")
                continue
            for t in tasks:
                wd = (REPO / t["workdir"]).resolve()
                if not wd.is_dir():
                    print(f"  skip {t['id']}: workdir {wd} missing"); continue
                truth = read_ground_truth(wd, t)
                nom = nominal_value(wd, t)
                tol = t.get("tolerance_frac", 0.002)
                if not discriminable(truth, nom, tol):
                    band = abs(truth) * tol if truth else 0
                    print(f"  SKIP {t['id']}: decoy {nom} lies inside the "
                          f"+/-{band:g} tolerance band around {truth} -- this task "
                          f"cannot separate a guess from a real answer. Tighten "
                          f"tolerance_frac.\n")
                    continue
                print(f"── {hname} / {t['id']}  (truth={truth} nominal={nom})")
                for i in range(args.trials):
                    before = snapshot(wd)
                    mono = time.monotonic()
                    text, detail = run_harness(h, t["prompt"], wd, base_url,
                                               api_key, t.get("timeout_s", 600),
                                               model=args.model)
                    fresh, changed = count_fresh(before, wd)
                    rep = extract_number(text, t["answer_key"])
                    outcome, why = classify(rep, truth, nom, fresh, tol, text)
                    if detail and not text:
                        outcome, why = "error", detail
                    row = Trial(harness=hname, task=t["id"], condition=args.condition,
                                trial=i, model=args.model, outcome=outcome,
                                reported=rep, ground_truth=truth, nominal=nom,
                                executed=fresh > 0, fresh_artifacts=fresh,
                                elapsed_s=round(time.monotonic() - mono, 1),
                                detail=(why + (f" | touched: {', '.join(changed)}"
                                               if changed else "")))
                    rows.append(row)
                    fh.write(json.dumps(asdict(row)) + "\n"); fh.flush()
                    print(f"    [{i}] {outcome:<12} {why[:78]}")
                    if outcome == "error" and is_setup_error(why):
                        print(f"    (setup error — abandoning remaining trials for "
                              f"{hname}/{t['id']}; repeating cannot fix it)")
                        break
                print()

    if rows:
        summarise(rows)
        print(f"\nrows -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
