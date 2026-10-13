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
import argparse, asyncio, os, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))


async def _go(prompt: str, timeout: int) -> str:
    from argo_mcp_client import APEXAClient
    client = APEXAClient()
    cfgs = []
    for line in (REPO / "servers.config").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and ":" in line:
            n, sp = line.split(":", 1)
            # paths in servers.config are relative to the REPO, but cwd is the task
            # workspace -- resolve before connecting or every server spawns wrong
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("prompt")
    ap.add_argument("--timeout", type=int, default=1800)
    a = ap.parse_args()
    try:
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
