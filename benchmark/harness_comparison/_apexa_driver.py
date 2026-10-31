#!/usr/bin/env python3
"""Run one APEXA query in this process and print the answer. One run, then exit.

A subprocess, not an in-process call, so the APEXA arms get exactly what the other
harnesses get: a fresh interpreter, a clean cwd, and an environment set per-arm. In
process, 86 sequential runs would share lru_caches (capsule registry, parameter
facts) and a global chdir -- the ablated arm could inherit state the enforced arm
warmed, which is the one thing a controlled comparison cannot allow.

    python _apexa_driver.py --timeout 1800 "<prompt>"

stdout is the answer; diagnostics go to stderr so the caller can parse cleanly.
"""
from __future__ import annotations
import argparse, asyncio, json, os, sys
from pathlib import Path

REPO = Path(os.environ.get("APEXA_BENCH_REPO", Path(__file__).resolve().parents[2])).resolve()
sys.path.insert(0, str(REPO))


ANALYSIS_SERVERS = {"core": "beamline_core_server.py", "midas": "midas_comprehensive_server.py"}


def server_configs(repo: Path = REPO) -> list[dict]:
    """Rotation task surface only; never load motor/alignment/control servers."""
    configs = {}
    for line in (repo / "servers.config").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        name, relative = (part.strip() for part in line.split(":", 1))
        if name not in ANALYSIS_SERVERS:
            continue
        script = (repo / relative).resolve()
        expected = (repo / ANALYSIS_SERVERS[name]).resolve()
        if script != expected or not script.is_relative_to(repo.resolve()) or not script.is_file():
            raise RuntimeError(f"Invalid benchmark analysis-server path for {name}; expected {ANALYSIS_SERVERS[name]}")
        if name in configs:
            raise RuntimeError(f"Duplicate analysis server: {name}")
        configs[name] = {"name": name, "script_path": str(script)}
    missing = set(ANALYSIS_SERVERS) - configs.keys()
    if missing:
        raise RuntimeError(f"Missing benchmark analysis servers: {sorted(missing)}")
    return [configs[name] for name in ANALYSIS_SERVERS]


async def _go(prompt: str, timeout: int) -> str:
    knowledge_preflight()
    from argo_mcp_client import APEXAClient
    client = APEXAClient()
    cfgs = server_configs()
    try:
        await client.connect_to_multiple_servers(cfgs)
        connected = set(getattr(client, "sessions", {}) or {})
        if connected != set(ANALYSIS_SERVERS):
            raise RuntimeError(f"Benchmark requires exactly {sorted(ANALYSIS_SERVERS)}; connected {sorted(connected)}")
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


def knowledge_preflight() -> dict:
    from apexa_knowledge_policy import current_policy, approved_file_bytes, KnowledgePolicyError
    policy = current_policy()
    if policy is None:
        raise KnowledgePolicyError("Benchmark driver requires a hash-pinned reviewed knowledge manifest")
    for relative in policy["files"]:
        approved_file_bytes(REPO / relative, policy=policy, root=REPO)
    return {"manifest_sha256": policy["manifest_sha256"], "approved_files": len(policy["files"]),
            "retrieval_enabled": bool(policy.get("retrieval")), "review_is_not_leakage_proof": True}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("prompt", nargs="?")
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--preflight", action="store_true", help="Validate server configuration without imports, MCP startup or model calls")
    a = ap.parse_args()
    try:
        if a.preflight:
            print(json.dumps({"servers": server_configs(), "knowledge": knowledge_preflight(),
                              "model_calls": 0, "mcp_processes_started": 0}))
            return 0
        if not a.prompt or a.timeout <= 0:
            ap.error("a prompt and positive timeout are required for a model run")
        print(asyncio.run(_go(a.prompt, a.timeout)))
        return 0
    except asyncio.TimeoutError:
        print(f"APEXA timed out after {a.timeout}s", file=sys.stderr)
        return 124
    except Exception as e:
        # MCP teardown after a failed connect raises a noisy anyio cancel-scope
        # chain that buries the real cause; first line only.
        first = (str(e).strip().splitlines() or [type(e).__name__])[0]
        print(f"{type(e).__name__}: {first[:300]}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
