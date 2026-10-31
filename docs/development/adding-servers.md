# Adding New Analysis Servers

## Overview

APEXA loads active MCP servers from `servers.config`. This guide explains the
server interface and how to add an adapter. The root-level files named there are
the active integrations; `servers/` contains examples and optional check servers,
not a list of everything APEXA currently runs. For diffraction-tool status, see
[`../diffraction-software-survey.md`](../diffraction-software-survey.md).

## Architecture

```
APEXA root-level active servers (from servers.config)
├── beamline_core_server.py
├── midas_comprehensive_server.py
├── gsas2_server.py
├── tomo_server.py
└── epics_motor_server.py

servers/ (examples/optional checks; not all enabled)
├── gsas2_server.py
└── maud_server.py  # installation check only; disabled unless added to servers.config
```

## Quick Start

### 1. Create a New Server

Copy a template:
```bash
cp servers/gsas2_server.py servers/mynew_server.py
```

### 2. Edit servers.config

Add your server:
```bash
# My New Analysis Tool
mynew:servers/mynew_server.py
```

### 3. Restart

```bash
./start_beamline_assistant.sh
```

That's it! Your server is now loaded.

## Server Template

Here's a minimal server template:

```python
#!/usr/bin/env python3
"""
MyTool MCP Server
Brief description of what this tool does

Author: Your Name
"""

from typing import Any, Optional, List, Dict
import json
import sys
import os
from pathlib import Path
import logging
from mcp.server.fastmcp import FastMCP

# Suppress verbose logging
logging.getLogger("mcp").setLevel(logging.WARNING)
logging.getLogger("fastmcp").setLevel(logging.WARNING)

# Initialize server
mcp = FastMCP("mytool-analysis")

def format_result(data: dict) -> str:
    """Format result as JSON"""
    return json.dumps(data, indent=2, default=str)

@mcp.tool()
async def my_analysis_function(
    input_file: str,
    parameter: float = 1.0
) -> str:
    """Brief description of what this function does.

    Args:
        input_file: Description
        parameter: Description

    Returns:
        JSON with analysis results
    """
    try:
        # Your analysis code here
        result = {
            "tool": "my_analysis_function",
            "status": "success",
            "results": {}
        }
        return format_result(result)

    except Exception as e:
        return format_result({
            "tool": "my_analysis_function",
            "status": "error",
            "error": str(e)
        })

if __name__ == "__main__":
    print("MyTool MCP Server", file=sys.stderr)
    mcp.run(transport='stdio')
```

## Detailed Steps

### Step 1: Auto-Detection (Optional but Recommended)

Add installation path detection like MIDAS does:

```python
def find_mytool_installation() -> Optional[Path]:
    """Find MyTool installation."""

    # Check environment variable
    if "MYTOOL_PATH" in os.environ:
        path = Path(os.environ["MYTOOL_PATH"]).expanduser().absolute()
        if path.exists():
            return path

    # Check common locations
    common_paths = [
        Path.home() / ".mytool",
        Path.home() / "mytool",
        Path("/opt/mytool")
    ]

    for path in common_paths:
        if path.exists() and path.is_dir():
            print(f"Found MyTool at: {path}", file=sys.stderr)
            return path

    print("WARNING: MyTool not found", file=sys.stderr)
    return None

MYTOOL_ROOT = find_mytool_installation()
MYTOOL_AVAILABLE = MYTOOL_ROOT is not None
```

### Step 2: Define Tools with @mcp.tool()

Each function becomes an available tool:

```python
@mcp.tool()
async def process_diffraction_data(
    data_file: str,
    wavelength: float,
    output_format: str = "csv"
) -> str:
    """Process diffraction data.

    Args:
        data_file: Path to input data file
        wavelength: X-ray wavelength in Angstroms
        output_format: Output format (csv, json, hdf5)

    Returns:
        Processing results as JSON string
    """
    try:
        data_path = Path(data_file).expanduser().absolute()

        if not data_path.exists():
            return format_result({
                "status": "error",
                "error": f"File not found: {data_file}"
            })

        # Your processing code
        # ...

        return format_result({
            "status": "success",
            "input_file": str(data_path),
            "wavelength": wavelength,
            "output_format": output_format,
            "results": {
                # Your results
            }
        })

    except Exception as e:
        return format_result({
            "status": "error",
            "error": str(e)
        })
```

### Step 3: Add to servers.config

```bash
# servers.config

# Core servers
filesystem:filesystem_server.py
executor:command_executor_server.py

# MIDAS
midas:midas_comprehensive_server.py

# GSAS-II
gsas2:servers/gsas2_server.py

# Your new tool
mytool:servers/mytool_server.py
```

### Step 4: Update .env.template (Optional)

If your tool needs configuration:

```bash
# MyTool Configuration
# MYTOOL_PATH=/path/to/mytool
```

## Best Practices

### 1. Error Handling

Always wrap tool functions in try-except:

```python
@mcp.tool()
async def my_function(...) -> str:
    try:
        # Your code
        return format_result({"status": "success", ...})
    except Exception as e:
        return format_result({
            "status": "error",
            "error": str(e)
        })
```

### 2. Path Handling

Always use pathlib and expanduser:

```python
from pathlib import Path

path = Path(user_input).expanduser().absolute()
if not path.exists():
    return error_result()
```

### 3. Return Format

Always return JSON strings:

```python
def format_result(data: dict) -> str:
    return json.dumps(data, indent=2, default=str)

@mcp.tool()
async def my_tool(...) -> str:  # Return type is str
    return format_result({...})  # Not dict!
```

### 4. Documentation

Use clear docstrings:

```python
@mcp.tool()
async def analyze_texture(
    data_file: str,
    phases: List[str],
    pole_figures: Optional[List[str]] = None
) -> str:
    """Analyze crystallographic texture from diffraction data.

    Calculates orientation distribution functions (ODF) and pole
    figures for specified crystallographic phases.

    Args:
        data_file: Path to diffraction data file (.xy, .chi, .dat)
        phases: List of phase names to analyze (e.g., ["austenite", "ferrite"])
        pole_figures: Optional list of pole figures to calculate (e.g., ["111", "200"])
                     If None, calculates standard pole figures for each phase

    Returns:
        JSON string containing:
        - Texture strength (MRD - Multiples of Random Distribution)
        - Pole figure data (intensities and angles)
        - ODF completeness metrics
        - Export file paths

    Example:
        result = await analyze_texture(
            data_file="/data/sample_001.xy",
            phases=["austenite"],
            pole_figures=["111", "200", "220"]
        )
    """
```

### 5. Logging

Use stderr for server logs:

```python
import sys

print(f"Processing {filename}...", file=sys.stderr)
```

This keeps stdout clean for MCP protocol.

## Examples

### Example 1: GSAS-II Server

See [servers/gsas2_server.py](../../servers/gsas2_server.py) for a legacy server example with:
- Installation detection
- Multiple tools
- Proper error handling
- Documentation

### Example 2: MAUD Server

See [servers/maud_server.py](../../servers/maud_server.py) for an optional MAUD
installation check. Refinement uses the limited MIDAS `run_gsas_refinement`
backend; this check server does not run MAUD refinements.

### Example 3: MIDAS Server

See [midas_comprehensive_server.py](../../midas_comprehensive_server.py) for the active MIDAS server with:
- 20+ tools
- Complex workflows
- Python API integration
- Executable wrappers

## Testing Your Server

### 1. Standalone Test

```bash
cd servers
python3 mytool_server.py
```

Should start without errors.

### 2. Integration Test

Enable your server in servers.config and run:

```bash
./start_beamline_assistant.sh
```

Check that it appears in "Active Servers" list.

### 3. Interactive Test

```bash
Beamline> tools
# Should show your tools

Beamline> call mytool_my_function with /path/to/data
# Test the tool
```

## Common Patterns

### Pattern 1: Wrapper for Command-Line Tool

```python
@mcp.tool()
async def run_mytool_command(
    param_file: str,
    data_file: str,
    timeout: int = 300
) -> str:
    """Run MyTool executable."""
    try:
        exe_path = MYTOOL_ROOT / "bin" / "mytool"

        if not exe_path.exists():
            return format_result({
                "status": "error",
                "error": "Executable not found"
            })

        # Run command
        result = subprocess.run(
            [str(exe_path), param_file, data_file],
            capture_output=True,
            text=True,
            timeout=timeout
        )

        return format_result({
            "status": "success" if result.returncode == 0 else "failed",
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode
        })

    except subprocess.TimeoutExpired:
        return format_result({
            "status": "timeout",
            "error": f"Command timed out after {timeout}s"
        })
    except Exception as e:
        return format_result({
            "status": "error",
            "error": str(e)
        })
```

### Pattern 2: Python API Integration

```python
@mcp.tool()
async def analyze_with_python_api(data_file: str) -> str:
    """Use MyTool Python API."""
    try:
        # Import MyTool modules
        import mytool
        from mytool import analyzer

        # Load data
        data = mytool.load(data_file)

        # Analyze
        result = analyzer.process(data)

        return format_result({
            "status": "success",
            "results": {
                "metric1": result.metric1,
                "metric2": result.metric2
            }
        })

    except ImportError:
        return format_result({
            "status": "error",
            "error": "MyTool Python API not available"
        })
    except Exception as e:
        return format_result({
            "status": "error",
            "error": str(e)
        })
```

### Pattern 3: Multi-Step Workflow

```python
@mcp.tool()
async def run_complete_workflow(
    input_dir: str,
    config_file: str,
    n_cpus: int = 4
) -> str:
    """Run complete multi-step analysis workflow."""
    try:
        workflow_status = {
            "steps": [],
            "overall_status": "in_progress"
        }

        # Step 1: Data preparation
        prep_result = await prepare_data(input_dir)
        workflow_status["steps"].append({
            "name": "data_preparation",
            "status": "completed",
            "result": prep_result
        })

        # Step 2: Main analysis
        analysis_result = await run_analysis(
            prep_result["output_file"],
            config_file,
            n_cpus
        )
        workflow_status["steps"].append({
            "name": "analysis",
            "status": "completed",
            "result": analysis_result
        })

        # Step 3: Post-processing
        post_result = await post_process(analysis_result["output_file"])
        workflow_status["steps"].append({
            "name": "post_processing",
            "status": "completed",
            "result": post_result
        })

        workflow_status["overall_status"] = "completed"

        return format_result(workflow_status)

    except Exception as e:
        workflow_status["overall_status"] = "failed"
        workflow_status["error"] = str(e)
        return format_result(workflow_status)
```

## Enabling/Disabling Servers

### Enable a Server

Uncomment in servers.config:
```bash
# Before
# gsas2:servers/gsas2_server.py

# After
gsas2:servers/gsas2_server.py
```

### Disable a Server

Comment out in servers.config:
```bash
# Before
maud:servers/maud_server.py

# After
# maud:servers/maud_server.py
```

### Create Server Profiles

Create different config files for different use cases:

```bash
# servers.hedm.config - Just HEDM tools
filesystem:filesystem_server.py
executor:command_executor_server.py
midas:midas_comprehensive_server.py

# servers.powder.config - Powder diffraction tools
filesystem:filesystem_server.py
executor:command_executor_server.py
gsas2:servers/gsas2_server.py
maud:servers/maud_server.py

# servers.all.config - Everything
filesystem:filesystem_server.py
executor:command_executor_server.py
midas:midas_comprehensive_server.py
gsas2:servers/gsas2_server.py
maud:servers/maud_server.py
pyfai:servers/pyfai_server.py
```

Use with:
```bash
cp servers.powder.config servers.config
./start_beamline_assistant.sh
```

## Package Management with uv

The project uses `uv` for package management.

### Adding Dependencies

If your server needs new packages:

```bash
# Add to project
uv add package-name

# Or add to requirements
echo "package-name>=1.0.0" >> requirements.txt
uv pip install -r requirements.txt
```

### Common Dependencies

For diffraction analysis:
```bash
uv add numpy scipy matplotlib
uv add fabio pyFAI h5py
uv add scikit-image scikit-learn
```

## Troubleshooting

### Server Not Loading

```bash
./start_beamline_assistant.sh
# Check output for:
  ✗ myserver (not found: servers/myserver.py)
```

**Solution:** Check file path in servers.config

### Import Errors

```
ModuleNotFoundError: No module named 'mytool'
```

**Solution:** Install dependencies with uv:
```bash
uv add mytool
```

### Tool Not Appearing

```bash
Beamline> tools
# Your tool is missing
```

**Solution:** Check @mcp.tool() decorator is present

### JSON Encoding Errors

```
TypeError: Object of type 'ndarray' is not JSON serializable
```

**Solution:** Convert numpy arrays:
```python
import numpy as np

result = {
    "data": array.tolist()  # Convert to list
}
```

Or use `default=str` in format_result.

## Next Steps

1. Look at template servers in `servers/` directory
2. Copy and modify a template
3. Add to `servers.config`
4. Test standalone
5. Test integrated
6. Document your tools
7. Share with community!

## Resources

- [FastMCP Documentation](https://github.com/jlowin/fastmcp)
- [Model Context Protocol Spec](https://modelcontextprotocol.io/)
- [MIDAS Server](../../midas_comprehensive_server.py)
- [Legacy GSAS-II Server Example](../../servers/gsas2_server.py)
- [Optional MAUD Installation Check](../../servers/maud_server.py)

## Contributing

When you create a useful server, consider:
1. Adding it to the servers/ directory
2. Documenting it in this guide
3. Adding template parameter files
4. Creating example workflows
5. Sharing with the beamline community!
