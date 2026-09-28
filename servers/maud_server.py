#!/usr/bin/env python3
"""MAUD installation-check MCP server.

Standalone Rietveld refinement now flows through the MIDAS server's
`run_gsas_refinement(..., engine="maud")` route, which dispatches to
`apexa_maud_milk.py`. This server only exposes a single check tool so an
operator can verify MAUD is installed without launching a refinement.

The texture/QPA stubs that previously lived here (run_texture_analysis,
run_quantitative_phase_analysis) were never implemented and have been
removed; Phase 2 will add `refine_texture=` and `refine_microstructure=`
parameters to `run_gsas_refinement(engine="maud")` rather than reviving
parallel tools.

Author: Beamline Assistant Team
Organization: Argonne National Laboratory
"""
from __future__ import annotations

import json
import logging
import sys

from mcp.server.fastmcp import FastMCP

from apexa_engines import find_maud_installation, maud_install_hint

# Quiet the MCP framework
logging.getLogger("mcp").setLevel(logging.WARNING)
logging.getLogger("fastmcp").setLevel(logging.WARNING)

mcp = FastMCP("maud-analysis")


@mcp.tool()
async def check_maud_installation() -> str:
    """Check whether MAUD is installed and where it was found.

    Returns:
        JSON with status ("installed" or "not_found"), the resolved install
        path when available, and an install hint when not.
    """
    maud = find_maud_installation()
    if maud is None:
        return json.dumps({
            "tool": "check_maud_installation",
            "status": "not_found",
            "available": False,
            "install_hint": maud_install_hint(),
        }, indent=2)
    return json.dumps({
        "tool": "check_maud_installation",
        "status": "installed",
        "available": True,
        "installation_path": str(maud),
        "note": (
            "Refinement runs through the MIDAS server: "
            "`run_gsas_refinement(..., engine=\"maud\")`."
        ),
    }, indent=2)


if __name__ == "__main__":
    maud = find_maud_installation()
    print("=" * 70, file=sys.stderr)
    print("MAUD installation-check server", file=sys.stderr)
    print("=" * 70, file=sys.stderr)
    print(f"MAUD available: {maud is not None}", file=sys.stderr)
    if maud:
        print(f"MAUD path:      {maud}", file=sys.stderr)
    print("Tool: check_maud_installation", file=sys.stderr)
    print("=" * 70, file=sys.stderr)
    mcp.run(transport="stdio")
