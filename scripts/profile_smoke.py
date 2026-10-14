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

#: Deliberately a two-step chain. A model can emit one tool call by luck or by
#: mimicry; feeding the result back and requiring the number to reappear is what
#: proves the loop closes.
TOOLS = [{
    "type": "function",
    "function": {
        "name": "xray_wavelength",
        "description": "Return the x-ray wavelength in angstroms for a beam energy in keV.",
        "parameters": {
            "type": "object",
            "properties": {"energy_kev": {"type": "number", "description": "Beam energy, keV"}},
            "required": ["energy_kev"],
        },
    },
}]

ENERGY = 61.332
ANSWER = "0.2021"


async def qualify(name: str, timeout: float = 120.0) -> tuple[bool, str]:
    """Run one profile through a two-turn tool chain. Never raises."""
    profiles = PR.load_profiles()
    prof = profiles.get(name)
    if prof is None:
        return False, "unknown profile"

    saved = {k: os.environ.get(k) for k in
             ("APEXA_LLM_PRESET", "APEXA_LLM_BASE_URL", "APEXA_LLM_MODE", "ARGO_MODEL")}
    try:
        PR.apply(prof)
        import apexa_agents as A
        model = os.environ.get("ARGO_MODEL") or PRESETS[prof.preset]().default_model
        if not model:
            return False, "no model configured (set one in the profile)"

        try:
            provider = A.select_provider(os.environ.get("ANL_USERNAME", ""), model)
        except EndpointRejected as e:
            return False, f"rejected at config time: {e}"

        if not A.provider_is_structured(provider):
            return False, ("provider is not structured — would fall back to the text "
                           "TOOL_CALL protocol, which APEXA must not rely on off-site")

        messages = [{"role": "user",
                     "content": f"Use the xray_wavelength tool for a {ENERGY} keV beam. "
                                f"Call the tool; do not compute it yourself."}]
        r1 = await asyncio.wait_for(
            provider.chat(messages, temperature=0.0, tools=TOOLS), timeout)
        if not r1.tool_calls:
            return False, f"turn 1 emitted no tool call (stop_reason={r1.stop_reason})"
        call = r1.tool_calls[0]
        if call.name != "xray_wavelength":
            return False, f"turn 1 called the wrong tool: {call.name}"

        # Turn 2: hand the result back and require the model to use it.
        messages += [
            {"role": "assistant", "content": f"TOOL_CALL: {call.name}"},
            {"role": "user", "content": f"[Tool Result for {call.name}] {ANSWER} angstroms. "
                                        f"State the wavelength in one short sentence."},
        ]
        r2 = await asyncio.wait_for(
            provider.chat(messages, temperature=0.0, tools=TOOLS), timeout)
        if ANSWER not in (r2.content or ""):
            return False, f"turn 2 did not carry the result back: {(r2.content or '')[:80]!r}"
        return True, f"{model} — chained a tool call and used the result"
    except asyncio.TimeoutError:
        return False, f"timed out after {timeout:.0f}s"
    except Exception as e:                                     # noqa: BLE001
        return False, f"{type(e).__name__}: {str(e)[:140]}"
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        client = getattr(locals().get("provider", None), "_client", None)
        if client is not None:
            try:
                await client.aclose()
            except Exception:
                pass


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
