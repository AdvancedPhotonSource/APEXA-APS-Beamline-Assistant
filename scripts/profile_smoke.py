#!/usr/bin/env python3
"""Qualify each connection profile by making it actually chain a tool call.

Why a round trip and not a ``/models`` listing
----------------------------------------------
Every APEXA capability is a tool call, so "the endpoint answered" is not the
question — "the endpoint emitted a structured tool call, took the result back,
and used it" is. Several profiles reach APEXA through a *translation shim*
(Gemini's OpenAI-compatibility layer; any institutional gateway) where exactly
that step is what breaks, while a plain chat completion looks perfect.

This repo has been bitten in both directions on published capability flags —
Metis documented tool calling it does not have, Minerva omitted a flag for tool
calling it does have (``apexa_llm_endpoints.ALCF_CANDIDATES``). So capability is
measured here, never trusted.

Usage::

    python scripts/profile_smoke.py                 # every credentialled profile
    python scripts/profile_smoke.py --profiles mag gemini-personal
    python scripts/profile_smoke.py --list

Exit code is 0 only if every tested profile passes.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:                                    # same .env contract as argo_mcp_client
    from dotenv import load_dotenv
    load_dotenv()
except Exception:                       # noqa: BLE001 - .env is optional
    pass

import apexa_profiles as PR                                    # noqa: E402
from apexa_llm_endpoints import PRESETS, EndpointRejected      # noqa: E402

async def qualify(name: str, timeout: float = 45.0) -> tuple[bool, str]:
    """Use the same native tool-result verifier as the connection dialog."""
    from apexa_connection import ConnectionConfig, verify
    prof = PR.load_profiles().get(name)
    if prof is None:
        return False, "unknown profile"
    keys = ("APEXA_LLM_PRESET", "APEXA_LLM_BASE_URL", "APEXA_LLM_MODE", "ARGO_MODEL",
            "APEXA_LLM_KEYRING_ACCOUNT", "APEXA_ACTIVE_PROFILE")
    saved = {k: os.environ.get(k) for k in keys}
    try:
        PR.apply(prof)
        ep = PRESETS[prof.preset]()
        config = ConnectionConfig(preset=prof.preset, base_url=prof.base_url or ep.base_url,
            model=prof.model or ep.default_model, username=os.environ.get("ANL_USERNAME", ""))
        result = await asyncio.wait_for(verify(config), timeout)
        return result["ok"], result.get("detail", "Native tool call and tool result verified")
    except asyncio.TimeoutError:
        return False, f"timed out after {timeout:.0f}s"
    except EndpointRejected as exc:
        return False, str(exc)
    except Exception:
        return False, "Connection verification failed; check the endpoint, model and credential configuration"
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


async def run(names: list[str]) -> int:
    worst = 0
    for name in names:
        print(f"  {name:<22} … ", end="", flush=True)
        ok, detail = await qualify(name)
        print(("PASS  " if ok else "FAIL  ") + detail)
        worst = worst or (0 if ok else 1)
    return worst


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profiles", nargs="*", default=None,
                    help="profiles to test (default: every one with a credential)")
    ap.add_argument("--list", action="store_true", help="list profiles and exit")
    args = ap.parse_args()

    profiles = PR.load_profiles()
    if args.list:
        for n, p in profiles.items():
            print(f"  {n:<22} {p.preset:<14} cred={'yes' if PR.has_credential(p) else 'no '}"
                  f"  {'EXTERNAL' if p.external else ''}")
        return 0

    names = args.profiles or [n for n, p in profiles.items() if PR.has_credential(p)]
    if not names:
        print("No profile has a credential. Store one with `profile key <preset>` "
              "in the CLI, or export APEXA_LLM_API_KEY.")
        return 1
    print(f"\nQualifying {len(names)} profile(s) — each must CHAIN a tool call.\n")
    rc = asyncio.run(run(names))
    print("\n" + ("All tested profiles chained a tool call." if rc == 0
                  else "At least one profile FAILED — do not rely on it at the beamline."))
    return rc


if __name__ == "__main__":
    sys.exit(main())
