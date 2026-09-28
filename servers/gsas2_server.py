#!/usr/bin/env python3
"""
GSAS-II MCP Server
Provides Rietveld refinement and powder diffraction analysis tools

GSAS-II: General Structure Analysis System
- Rietveld refinement
- Powder diffraction analysis
- Single crystal diffraction
- PDF (Pair Distribution Function) analysis
- Texture analysis

Author: Beamline Assistant Team
Organization: Argonne National Laboratory
"""

from typing import Any, Optional, List, Dict
import json
import sys
import os
from pathlib import Path
import subprocess
import logging
from mcp.server.fastmcp import FastMCP

# Suppress verbose MCP server logging
logging.getLogger("mcp").setLevel(logging.WARNING)
logging.getLogger("fastmcp").setLevel(logging.WARNING)

# =============================================================================
# CONFIGURATION & PATHS
# =============================================================================

def find_gsas2_installation() -> Optional[Path]:
    """Find GSAS-II installation by checking common locations.

    Priority order:
    1. GSAS2_PATH environment variable
    2. ~/GSASII (standard installation)
    3. ~/.local/GSASII
    4. /opt/GSASII (system-wide)
    5. Current directory ./GSASII
    """
    if "GSAS2_PATH" in os.environ:
        gsas2_path = Path(os.environ["GSAS2_PATH"]).expanduser().absolute()
        if gsas2_path.exists():
            return gsas2_path

    common_paths = [
        Path.home() / "GSASII",
        Path.home() / ".local" / "GSASII",
        Path("/opt/GSASII"),
        Path.cwd() / "GSASII"
    ]

    for path in common_paths:
        if path.exists() and path.is_dir():
            print(f"Found GSAS-II installation at: {path}", file=sys.stderr)
            return path

    print(f"WARNING: GSAS-II not found. Install from https://subversion.xray.aps.anl.gov/pyGSAS/", file=sys.stderr)
    return None

GSAS2_ROOT = find_gsas2_installation()
GSAS2_AVAILABLE = GSAS2_ROOT is not None

# Initialize FastMCP server
mcp = FastMCP("gsas2-analysis")

# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def format_result(data: dict) -> str:
    """Format result as JSON string"""
    return json.dumps(data, indent=2, default=str)

# =============================================================================
# GSAS-II ANALYSIS TOOLS
# =============================================================================

@mcp.tool()
async def check_gsas2_installation() -> str:
    """Check if GSAS-II is installed and available.

    Returns:
        Installation status and version information
    """
    try:
        if not GSAS2_AVAILABLE:
            return format_result({
                "tool": "check_gsas2_installation",
                "status": "not_found",
                "available": False,
                "message": "GSAS-II not found. Please install from https://subversion.xray.aps.anl.gov/pyGSAS/",
                "installation_instructions": {
                    "method_1": "svn co https://subversion.xray.aps.anl.gov/pyGSAS/trunk ~/GSASII",
                    "method_2": "Download from https://gsas-ii.readthedocs.io/",
                    "environment_variable": "Set GSAS2_PATH=/path/to/GSASII"
                }
            })

        # Check for GSAS-II main script
        gsas2_script = GSAS2_ROOT / "GSASII.py"

        return format_result({
            "tool": "check_gsas2_installation",
            "status": "installed",
            "available": True,
            "installation_path": str(GSAS2_ROOT),
            "gsas2_script": str(gsas2_script) if gsas2_script.exists() else "Not found",
            "message": "GSAS-II is installed and ready to use"
        })

    except Exception as e:
        return format_result({
            "tool": "check_gsas2_installation",
            "status": "error",
            "error": str(e)
        })

@mcp.tool()
async def run_rietveld_refinement(
    project_file: str,
    histogram: str = "PWDR",
    refine_background: bool = True,
    refine_lattice: bool = True,
    refine_profile: bool = True,
    max_cycles: int = 10
) -> str:
    """Run Rietveld refinement on powder diffraction data.

    PLACEHOLDER: This is a template for future GSAS-II integration.

    Args:
        project_file: Path to GSAS-II project file (.gpx)
        histogram: Histogram name in project
        refine_background: Refine background parameters
        refine_lattice: Refine lattice parameters
        refine_profile: Refine profile parameters
        max_cycles: Maximum refinement cycles

    Returns:
        Refinement results and fit statistics
    """
    try:
        if not GSAS2_AVAILABLE:
            return format_result({
                "tool": "run_rietveld_refinement",
                "status": "unavailable",
                "error": "GSAS-II not installed"
            })

        # TODO: Implement GSAS-II Python API integration
        # This would require importing GSAS-II modules and running refinement

        return format_result({
            "tool": "run_rietveld_refinement",
            "status": "not_implemented",
            "message": "GSAS-II integration coming soon",
            "note": "This is a placeholder for future development"
        })

    except Exception as e:
        return format_result({
            "tool": "run_rietveld_refinement",
            "status": "error",
            "error": str(e)
        })

# =============================================================================
# SERVER STARTUP
# =============================================================================

if __name__ == "__main__":
    print("=" * 70, file=sys.stderr)
    print("GSAS-II MCP Server", file=sys.stderr)
    print("=" * 70, file=sys.stderr)
    print(f"GSAS-II Available: {GSAS2_AVAILABLE}", file=sys.stderr)
    if GSAS2_ROOT:
        print(f"GSAS-II Path: {GSAS2_ROOT}", file=sys.stderr)
    print("\nAvailable Tools:", file=sys.stderr)
    print("  - check_gsas2_installation", file=sys.stderr)
    print("  - run_rietveld_refinement (placeholder)", file=sys.stderr)
    print("=" * 70, file=sys.stderr)

    mcp.run(transport='stdio')
