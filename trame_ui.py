#!/usr/bin/env python3
"""
APEXA Trame Web UI - Advanced Photon EXperiment Assistant
Pure-Python web interface using trame (Kitware) with Vuetify 3 + Plotly
Replaces the broken React frontend with zero Node.js dependencies.
"""

import asyncio
import argparse
import json
import re
import time
import os
from pathlib import Path
from typing import List, Dict, Any, Optional

import numpy as np

# Trame imports
from trame.app import get_server, asynchronous
from trame.ui.vuetify3 import SinglePageWithDrawerLayout
from trame.widgets import vuetify3 as v3, plotly as trame_plotly, html

# Image processing from existing backend (no HTTP, direct import)
from web_server import (
    load_diffraction_image,
    apply_contrast,
    apply_colormap,
    image_to_base64,
    calculate_radial_profile,
)

# Agent system
from argo_mcp_client import APEXAClient

# ==================== Constants ====================

DIFFRACTION_EXTS = {".tif", ".tiff", ".ge", ".ge2", ".ge3", ".ge4", ".ge5"}
DATA_EXTS = {".csv", ".dat", ".xy", ".txt", ".chi"}
COLORMAPS = [
    "viridis", "plasma", "inferno", "magma", "gray", "hot", "cool",
    "jet", "bone", "copper", "spring", "summer", "autumn", "winter",
]

WORKFLOWS = [
    {"name": "Calibrate CeO2", "icon": "mdi-bullseye-arrow",
     "prompt": "Calibrate the CeO2 standard in the current directory with stopping strain 0.003"},
    {"name": "Integrate 2D to 1D", "icon": "mdi-chart-line",
     "prompt": "Integrate the diffraction image to a 1D pattern using the refined parameters"},
    {"name": "FF-HEDM", "icon": "mdi-cube-scan",
     "prompt": "Run the full FF-HEDM workflow on the current dataset"},
    {"name": "NF-HEDM", "icon": "mdi-grain",
     "prompt": "Run the NF-HEDM reconstruction workflow"},
    {"name": "Phase ID", "icon": "mdi-molecule",
     "prompt": "Identify the crystalline phases from the integrated pattern"},
    {"name": "List Files", "icon": "mdi-folder-open",
     "prompt": "List files in the current directory"},
]

# ==================== Global State ====================

# Server-side caches (NOT in trame state — too large for JSON)
image_cache: Dict[str, np.ndarray] = {}
mcp_client: Optional[APEXAClient] = None

# ==================== Trame Server ====================

server = get_server(client_type="vue3")
state, ctrl = server.state, server.controller

# Initialize state defaults
state.trame__title = "APEXA"
state.messages = []
state.chat_input = ""
state.is_loading = False
state.selected_model = "gpt4o"
state.available_models = {}
state.connection_status = "disconnected"

# Viz state
state.artifacts = []
state.active_artifact_idx = 0
state.active_plotly_data = {"data": [], "layout": {}}
state.active_image_src = ""
state.active_table_data = {}
state.active_artifact_type = ""

# File browser state
state.file_entries = []
state.current_path = str(Path.cwd())
state.parent_path = str(Path.cwd().parent)

# Image viewer state
state.image_preview = ""
state.image_stats = {}
state.vmin = 0.0
state.vmax = 100.0
state.gamma = 1.0
state.colormap = "viridis"
state.active_image_id = ""

# Sidebar state
state.sidebar_tab = "chat"
state.drawer_open = True


# ==================== MCP Client Initialization ====================

def read_servers_config(config_path: str = "servers.config") -> List[Dict]:
    """Parse servers.config into list of {name, script_path} dicts."""
    configs = []
    path = Path(config_path)
    if not path.exists():
        print(f"  servers.config not found")
        return configs
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            name, script_path = line.split(":", 1)
            name, script_path = name.strip(), script_path.strip()
            if Path(script_path).exists():
                configs.append({"name": name, "script_path": script_path})
                print(f"  + {name} ({script_path})")
            else:
                print(f"  ! {name} - file not found: {script_path}")
    return configs


async def initialize_client():
    """Initialize APEXAClient and connect to MCP servers."""
    global mcp_client
    try:
        mcp_client = APEXAClient()
        print(f"  Argo API: {mcp_client.environment}, model={mcp_client.selected_model}")

        print("  Loading MCP servers:")
        server_configs = read_servers_config()
        if server_configs:
            await mcp_client.connect_to_multiple_servers(server_configs)
            print(f"  Connected to {len(mcp_client.sessions)} server(s)")

        # Update UI state
        state.selected_model = mcp_client.selected_model
        state.available_models = getattr(mcp_client, "available_models", {})
        state.connection_status = "connected"
        state.flush()
    except Exception as e:
        print(f"  MCP initialization failed: {e}")
        state.connection_status = "error"
        state.flush()


# ==================== Tool Result Parser ====================

def parse_tool_results(text: str) -> List[Dict]:
    """Extract structured tool results from agent response text."""
    results = []

    # Pattern 1: JSON code blocks
    json_block_re = re.compile(r"```(?:json)?\s*\n([\s\S]*?)\n```")
    for m in json_block_re.finditer(text):
        try:
            parsed = json.loads(m.group(1))
            if isinstance(parsed, dict):
                results.append({
                    "tool": parsed.get("tool", infer_tool(parsed)),
                    "status": infer_status(parsed),
                    "data": parsed,
                })
        except (json.JSONDecodeError, ValueError):
            pass

    # Pattern 2: Inline JSON with tool/status/success keys
    inline_re = re.compile(r"(?:^|\n)\s*(\{[\s\S]*?\})\s*(?:\n|$)")
    for m in inline_re.finditer(text):
        json_str = m.group(1)
        if "```" in text:
            continue  # Skip if code blocks present
        try:
            parsed = json.loads(json_str)
            if isinstance(parsed, dict) and any(k in parsed for k in ("tool", "status", "success")):
                results.append({
                    "tool": parsed.get("tool", infer_tool(parsed)),
                    "status": infer_status(parsed),
                    "data": parsed,
                })
        except (json.JSONDecodeError, ValueError):
            pass

    # Pattern 3: Calibration text patterns
    if not results and has_calibration_pattern(text):
        data = extract_calibration_from_text(text)
        if data:
            results.append({"tool": "midas_auto_calibrate", "status": "completed", "data": data})

    return results


def infer_tool(data: Dict) -> str:
    if any(k in data for k in ("calibrated_parameters", "Lsd", "BC")):
        return "midas_auto_calibrate"
    if any(k in data for k in ("listing", "entries")):
        return "list_directory"
    if "workflow" in data:
        return "hedm_workflow"
    if any(k in data for k in ("d_spacing", "wavelength", "energy")) and "result" in data:
        return "xray_calculate"
    if any(k in data for k in ("integration_result", "lineout")):
        return "midas_integrate_2d_to_1d"
    return "unknown"


def infer_status(data: Dict) -> str:
    if data.get("status") == "error" or data.get("success") is False or "error" in data:
        return "error"
    if data.get("status") == "completed" or data.get("success") is True:
        return "completed"
    if data.get("status") == "warning":
        return "warning"
    return "success"


def has_calibration_pattern(text: str) -> bool:
    patterns = ["Beam Center", "beam center", "BC:", "Lsd:", "detector distance"]
    return any(p in text for p in patterns)


def extract_calibration_from_text(text: str) -> Optional[Dict]:
    params = {}
    bc_match = re.search(r"(?:BC|Beam Center)[:\s]*\(?(\d+\.?\d*)\s*,?\s*(\d+\.?\d*)\)?", text, re.I)
    if bc_match:
        params["BC_y"] = float(bc_match.group(1))
        params["BC_z"] = float(bc_match.group(2))
    lsd_match = re.search(r"(?:Lsd|detector distance|sample.detector)[:\s]*(\d+\.?\d*)", text, re.I)
    if lsd_match:
        params["Lsd"] = float(lsd_match.group(1))
    wl_match = re.search(r"[Ww]avelength[:\s]*(\d+\.?\d*)", text)
    if wl_match:
        params["wavelength"] = float(wl_match.group(1))
    if not params:
        return None
    return {"calibrated_parameters": params}


def extract_artifacts(results: List[Dict], msg_id: str) -> List[Dict]:
    """Convert tool results to viz artifacts."""
    artifacts = []
    ts = int(time.time() * 1000)

    for result in results:
        tool = result.get("tool", "")
        if result.get("status") == "error":
            continue

        if tool == "midas_auto_calibrate":
            cal_params = result["data"].get("calibrated_parameters", result["data"])
            if cal_params:
                artifacts.append({
                    "id": f"cal-{ts}",
                    "type": "table",
                    "title": "Calibration Parameters",
                    "data": cal_params,
                    "source_message_id": msg_id,
                })

        elif tool == "xray_calculate":
            artifacts.append({
                "id": f"xray-{ts}",
                "type": "table",
                "title": "X-ray Calculation",
                "data": result["data"],
                "source_message_id": msg_id,
            })

        elif tool == "list_directory":
            artifacts.append({
                "id": f"files-{ts}",
                "type": "table",
                "title": "Directory Listing",
                "data": result["data"],
                "source_message_id": msg_id,
            })

    return artifacts


# ==================== Chat Logic ====================

async def do_send_message():
    """Send chat message through APEXAClient (async)."""
    global mcp_client
    query = state.chat_input
    if not query or not query.strip():
        return
    if not mcp_client:
        state.messages = [*state.messages, {
            "role": "assistant",
            "content": "APEXA is not connected. Please restart the server.",
        }]
        state.flush()
        return

    # Add user message
    state.messages = [*state.messages, {"role": "user", "content": query}]
    state.chat_input = ""
    state.is_loading = True
    state.flush()

    try:
        # Switch model if changed
        if hasattr(mcp_client, "selected_model") and mcp_client.selected_model != state.selected_model:
            mcp_client.selected_model = state.selected_model

        response = await mcp_client.run_query(query=query, use_history=True)

        # Parse tool results and extract artifacts
        tool_results = parse_tool_results(response)
        new_artifacts = extract_artifacts(tool_results, f"msg-{len(state.messages)}")

        # Add assistant message
        state.messages = [*state.messages, {
            "role": "assistant",
            "content": response,
            "tool_results": tool_results,
        }]

        # Push artifacts to viz panel
        if new_artifacts:
            state.artifacts = [*state.artifacts, *new_artifacts]
            state.active_artifact_idx = len(state.artifacts) - 1
            sync_active_artifact()

    except Exception as e:
        state.messages = [*state.messages, {
            "role": "assistant",
            "content": f"Error: {str(e)}",
        }]
    finally:
        state.is_loading = False
        state.flush()


@ctrl.trigger("send_message")
def on_send_message():
    asynchronous.create_task(do_send_message())


@ctrl.trigger("clear_chat")
def on_clear_chat():
    state.messages = []
    state.flush()


@ctrl.trigger("change_model")
def on_change_model(model):
    global mcp_client
    state.selected_model = model
    if mcp_client:
        mcp_client.selected_model = model
    state.flush()


# ==================== File Browser ====================

@ctrl.trigger("browse_directory")
def on_browse_directory(path=None):
    if path is None:
        path = state.current_path
    dir_path = Path(path).expanduser().resolve()
    if not dir_path.is_dir():
        return

    entries = []
    try:
        items = sorted(dir_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        for item in items[:200]:
            if item.name.startswith("."):
                continue
            try:
                stat = item.stat()
                entry = {
                    "name": item.name,
                    "path": str(item),
                    "is_dir": item.is_dir(),
                    "size": stat.st_size if item.is_file() else 0,
                    "ext": item.suffix.lower() if item.is_file() else "",
                }
                entries.append(entry)
            except (PermissionError, OSError):
                continue
    except (PermissionError, OSError):
        pass

    state.file_entries = entries
    state.current_path = str(dir_path)
    state.parent_path = str(dir_path.parent)
    state.flush()


@ctrl.trigger("file_click")
def on_file_click(path):
    p = Path(path)
    if p.is_dir():
        on_browse_directory(path)
    elif p.suffix.lower() in DIFFRACTION_EXTS:
        on_load_image(path)
    elif p.suffix.lower() in DATA_EXTS:
        on_load_data_file(path)


# ==================== Image Viewer ====================

@ctrl.trigger("load_image")
def on_load_image(path):
    try:
        img = load_diffraction_image(path)
        file_id = Path(path).name
        image_cache[file_id] = img

        state.active_image_id = file_id
        state.image_stats = {
            "filename": file_id,
            "shape": f"{img.shape[0]} x {img.shape[1]}",
            "min": f"{float(img.min()):.1f}",
            "max": f"{float(img.max()):.1f}",
            "mean": f"{float(img.mean()):.1f}",
        }
        state.vmin = float(np.percentile(img, 1))
        state.vmax = float(np.percentile(img, 99))
        state.gamma = 1.0
        update_image_preview()

        # Add as artifact
        state.artifacts = [*state.artifacts, {
            "id": f"img-{file_id}-{int(time.time()*1000)}",
            "type": "image",
            "title": file_id,
            "data": {"preview": state.image_preview, "stats": state.image_stats},
            "source_message_id": "",
        }]
        state.active_artifact_idx = len(state.artifacts) - 1
        sync_active_artifact()
        state.flush()
    except Exception as e:
        print(f"Failed to load image: {e}")


def update_image_preview():
    file_id = state.active_image_id
    if not file_id or file_id not in image_cache:
        return
    img = image_cache[file_id]
    adjusted = apply_contrast(img, state.vmin, state.vmax, state.gamma)
    colored = apply_colormap(adjusted, state.colormap)
    state.image_preview = image_to_base64(colored)
    state.flush()


@state.change("vmin", "vmax", "gamma", "colormap")
def on_contrast_change(vmin, vmax, gamma, colormap, **kwargs):
    if state.active_image_id:
        update_image_preview()


@ctrl.trigger("compute_radial_profile")
def on_compute_radial_profile():
    file_id = state.active_image_id
    if not file_id or file_id not in image_cache:
        return
    img = image_cache[file_id]
    center = (img.shape[0] // 2, img.shape[1] // 2)
    radii, profile = calculate_radial_profile(img, center)

    state.artifacts = [*state.artifacts, {
        "id": f"radial-{file_id}-{int(time.time()*1000)}",
        "type": "plotly",
        "title": f"Radial Profile - {file_id}",
        "data": {
            "traces": [{"x": radii.tolist(), "y": profile.tolist(),
                        "type": "scatter", "mode": "lines",
                        "name": "Radial Intensity",
                        "line": {"color": "#60a5fa", "width": 1.5}}],
            "layout": {
                "title": {"text": f"Radial Profile: {file_id}"},
                "xaxis": {"title": {"text": "Radius (pixels)"}},
                "yaxis": {"title": {"text": "Mean Intensity"}},
                "template": "plotly_dark",
                "paper_bgcolor": "#1e1e1e",
                "plot_bgcolor": "#1e1e1e",
            },
        },
        "source_message_id": "",
    }]
    state.active_artifact_idx = len(state.artifacts) - 1
    sync_active_artifact()
    state.flush()


# ==================== Data File Loading ====================

@ctrl.trigger("load_data_file")
def on_load_data_file(path):
    """Load CSV/DAT/XY data file and create a Plotly chart artifact."""
    try:
        p = Path(path)
        data = np.loadtxt(str(p), comments="#", max_rows=10000)
        if data.ndim == 1:
            x = list(range(len(data)))
            y = data.tolist()
        else:
            x = data[:, 0].tolist()
            y = data[:, 1].tolist()

        state.artifacts = [*state.artifacts, {
            "id": f"data-{p.name}-{int(time.time()*1000)}",
            "type": "plotly",
            "title": p.name,
            "data": {
                "traces": [{"x": x, "y": y, "type": "scatter", "mode": "lines",
                            "name": p.stem, "line": {"color": "#34d399", "width": 1.5}}],
                "layout": {
                    "title": {"text": p.name},
                    "xaxis": {"title": {"text": "X"}},
                    "yaxis": {"title": {"text": "Intensity"}},
                    "template": "plotly_dark",
                    "paper_bgcolor": "#1e1e1e",
                    "plot_bgcolor": "#1e1e1e",
                },
            },
            "source_message_id": "",
        }]
        state.active_artifact_idx = len(state.artifacts) - 1
        sync_active_artifact()
        state.flush()
    except Exception as e:
        print(f"Failed to load data file: {e}")


# ==================== Sidebar Workflows ====================

@ctrl.trigger("run_workflow")
def on_run_workflow(prompt):
    state.chat_input = prompt
    state.flush()
    asynchronous.create_task(do_send_message())


def sync_active_artifact():
    """Sync the active artifact to display state variables."""
    arts = state.artifacts
    idx = state.active_artifact_idx
    if not arts or idx < 0 or idx >= len(arts):
        state.active_artifact_type = ""
        state.active_plotly_data = {"data": [], "layout": {}}
        state.active_image_src = ""
        state.active_table_data = {}
        return

    art = arts[idx]
    state.active_artifact_type = art.get("type", "")

    if art["type"] == "plotly":
        state.active_plotly_data = {
            "data": art["data"].get("traces", []),
            "layout": art["data"].get("layout", {}),
        }
    elif art["type"] == "image":
        state.active_image_src = art["data"].get("preview", "")
    elif art["type"] == "table":
        state.active_table_data = art.get("data", {})


@state.change("active_artifact_idx")
def on_active_artifact_change(active_artifact_idx, **kwargs):
    sync_active_artifact()


@ctrl.trigger("remove_artifact")
def on_remove_artifact(idx):
    arts = list(state.artifacts)
    if 0 <= idx < len(arts):
        arts.pop(idx)
        state.artifacts = arts
        state.active_artifact_idx = max(0, min(state.active_artifact_idx, len(arts) - 1))
        sync_active_artifact()
        state.flush()


# ==================== UI Layout ====================

# Custom CSS
CUSTOM_CSS = """
<style>
:root {
    --chat-bg: #0d1117;
    --msg-user: #1a3a5c;
    --msg-assistant: #1e1e2e;
    --viz-bg: #141419;
    --border: #2d2d3d;
    --accent: #60a5fa;
}
.apexa-chat-container {
    display: flex;
    flex-direction: column;
    height: calc(100vh - 64px);
    background: var(--chat-bg);
}
.apexa-messages {
    flex: 1;
    overflow-y: auto;
    padding: 16px;
}
.apexa-msg {
    margin-bottom: 12px;
    padding: 10px 14px;
    border-radius: 12px;
    max-width: 95%;
    font-size: 14px;
    line-height: 1.6;
    word-wrap: break-word;
    white-space: pre-wrap;
}
.apexa-msg-user {
    background: var(--msg-user);
    color: #e2e8f0;
    margin-left: auto;
    border-bottom-right-radius: 4px;
}
.apexa-msg-assistant {
    background: var(--msg-assistant);
    color: #d1d5db;
    border-bottom-left-radius: 4px;
}
.apexa-viz-container {
    height: calc(100vh - 64px);
    background: var(--viz-bg);
    display: flex;
    flex-direction: column;
}
.apexa-input-row {
    padding: 8px 12px;
    background: #0d1117;
    border-top: 1px solid var(--border);
    display: flex;
    gap: 8px;
    align-items: end;
}
.apexa-empty-state {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    height: 100%;
    color: #6b7280;
    text-align: center;
    padding: 40px;
}
.apexa-empty-state .mdi {
    font-size: 64px;
    margin-bottom: 16px;
    color: #374151;
}
.apexa-file-entry {
    cursor: pointer;
    padding: 4px 8px;
    border-radius: 4px;
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 13px;
    color: #d1d5db;
}
.apexa-file-entry:hover {
    background: #1e293b;
}
.apexa-artifact-img {
    max-width: 100%;
    max-height: calc(100vh - 180px);
    object-fit: contain;
}
.apexa-stats-bar {
    font-size: 12px;
    color: #9ca3af;
    padding: 4px 12px;
    background: #111118;
    border-top: 1px solid var(--border);
}
.apexa-typing {
    display: inline-flex;
    gap: 4px;
    padding: 8px 14px;
    background: var(--msg-assistant);
    border-radius: 12px;
    margin-bottom: 12px;
}
.apexa-typing span {
    width: 8px;
    height: 8px;
    background: #6b7280;
    border-radius: 50%;
    animation: typing-bounce 1.4s infinite ease-in-out;
}
.apexa-typing span:nth-child(2) { animation-delay: 0.2s; }
.apexa-typing span:nth-child(3) { animation-delay: 0.4s; }
@keyframes typing-bounce {
    0%, 80%, 100% { transform: scale(0.6); opacity: 0.4; }
    40% { transform: scale(1); opacity: 1; }
}
.v-navigation-drawer {
    border-right: 1px solid var(--border) !important;
}
</style>
"""


def build_ui():
    """Build the complete trame UI layout."""

    with SinglePageWithDrawerLayout(server, full_height=True) as layout:
        # Inject custom CSS
        layout.root.add_child(CUSTOM_CSS)

        # -- App Bar --
        with layout.toolbar as toolbar:
            toolbar.dense = True
            toolbar.color = "#0d1117"
            toolbar.flat = True

            v3.VAppBarNavIcon(click="drawer_open = !drawer_open")

            # Logo
            html.Span(
                "APEXA",
                style="font-size: 18px; font-weight: 700; color: #60a5fa; letter-spacing: 2px; margin-right: 8px;",
            )
            html.Span(
                "Advanced Photon EXperiment Assistant",
                style="font-size: 12px; color: #6b7280; margin-right: 24px;",
            )

            v3.VSpacer()

            # Model selector
            v3.VSelect(
                v_model=("selected_model",),
                items=("Object.keys(available_models).length > 0 ? Object.keys(available_models) : ['claudeopus55', 'claudeopus5', 'gpt56sol', 'claudesonnet55', 'gemini35flash']",),
                density="compact",
                variant="outlined",
                hide_details=True,
                style="max-width: 200px; margin-right: 12px;",
                update_modelValue="trigger('change_model', [$event])",
            )

            # Connection status
            v3.VIcon(
                "{{ connection_status === 'connected' ? 'mdi-circle' : connection_status === 'error' ? 'mdi-alert-circle' : 'mdi-circle-outline' }}",
                color="{{ connection_status === 'connected' ? 'green' : connection_status === 'error' ? 'red' : 'gray' }}",
                size="small",
                style="margin-right: 8px;",
            )

        # -- Drawer (Sidebar) --
        with layout.drawer as drawer:
            drawer.v_model = ("drawer_open", True)
            drawer.width = 280
            drawer.color = "#0d1117"

            v3.VTabs(
                v_model=("sidebar_tab", "chat"),
                density="compact",
                color="#60a5fa",
                grow=True,
                children=[
                    v3.VTab(value="files", children=[v3.VIcon("mdi-folder", size="small", start=True), "Files"]),
                    v3.VTab(value="workflows", children=[v3.VIcon("mdi-rocket-launch", size="small", start=True), "Workflows"]),
                    v3.VTab(value="viewer", children=[v3.VIcon("mdi-image", size="small", start=True), "Viewer"]),
                ],
            )

            with v3.VTabsWindow(v_model=("sidebar_tab",)):
                # === Files Tab ===
                with v3.VTabsWindowItem(value="files"):
                    with v3.VContainer(fluid=True, style="padding: 8px;"):
                        # Path breadcrumb
                        html.Div(
                            "{{ current_path }}",
                            style="font-size: 11px; color: #6b7280; padding: 4px 0; word-break: break-all;",
                        )
                        v3.VBtn(
                            "Parent Directory",
                            prepend_icon="mdi-arrow-up",
                            variant="text",
                            size="small",
                            density="compact",
                            color="#9ca3af",
                            click="trigger('browse_directory', [parent_path])",
                            block=True,
                            classes="mb-2",
                        )
                        v3.VDivider(classes="mb-2")

                        # File list
                        with html.Div(style="max-height: calc(100vh - 220px); overflow-y: auto;"):
                            with html.Template(
                                v_for="(entry, i) in file_entries",
                                __properties=[("v_for", "v-for")],
                            ):
                                html.Div(
                                    "{{ entry.is_dir ? '📁' : (entry.ext === '.tif' || entry.ext === '.tiff' ? '🔬' : '📄') }} {{ entry.name }}",
                                    classes="apexa-file-entry",
                                    click="trigger('file_click', [entry.path])",
                                )

                # === Workflows Tab ===
                with v3.VTabsWindowItem(value="workflows"):
                    with v3.VContainer(fluid=True, style="padding: 8px;"):
                        html.Div(
                            "Quick Workflows",
                            style="font-size: 13px; font-weight: 600; color: #e2e8f0; margin-bottom: 12px;",
                        )
                        for wf in WORKFLOWS:
                            v3.VBtn(
                                wf["name"],
                                prepend_icon=wf["icon"],
                                variant="tonal",
                                color="#60a5fa",
                                size="small",
                                block=True,
                                classes="mb-2",
                                click=f"trigger('run_workflow', ['{wf['prompt']}'])",
                            )

                        v3.VDivider(classes="my-3")
                        html.Div(
                            "Motor Controls",
                            style="font-size: 13px; font-weight: 600; color: #e2e8f0; margin-bottom: 12px;",
                        )
                        v3.VBtn(
                            "List All Motors",
                            prepend_icon="mdi-cog",
                            variant="tonal",
                            color="#f59e0b",
                            size="small",
                            block=True,
                            classes="mb-2",
                            click="trigger('run_workflow', ['List all available motors and their current positions'])",
                        )
                        v3.VBtn(
                            "Emergency Stop",
                            prepend_icon="mdi-stop-circle",
                            variant="tonal",
                            color="#ef4444",
                            size="small",
                            block=True,
                            click="trigger('run_workflow', ['Stop all motors immediately'])",
                        )

                # === Viewer Tab ===
                with v3.VTabsWindowItem(value="viewer"):
                    with v3.VContainer(fluid=True, style="padding: 8px;"):
                        with html.Div(v_if="active_image_id"):
                            html.Div(
                                "{{ image_stats.filename }}",
                                style="font-size: 13px; font-weight: 600; color: #e2e8f0; margin-bottom: 8px;",
                            )
                            html.Div(
                                "{{ image_stats.shape }} | min: {{ image_stats.min }} | max: {{ image_stats.max }} | mean: {{ image_stats.mean }}",
                                style="font-size: 11px; color: #9ca3af; margin-bottom: 12px;",
                            )

                            # Contrast controls
                            html.Div("vmin", style="font-size: 12px; color: #9ca3af;")
                            v3.VSlider(
                                v_model=("vmin",),
                                min=("parseFloat(image_stats.min) || 0",),
                                max=("parseFloat(image_stats.max) || 100",),
                                step=0.1,
                                density="compact",
                                hide_details=True,
                                color="#60a5fa",
                            )
                            html.Div("vmax", style="font-size: 12px; color: #9ca3af;")
                            v3.VSlider(
                                v_model=("vmax",),
                                min=("parseFloat(image_stats.min) || 0",),
                                max=("parseFloat(image_stats.max) || 100",),
                                step=0.1,
                                density="compact",
                                hide_details=True,
                                color="#60a5fa",
                            )
                            html.Div("Gamma", style="font-size: 12px; color: #9ca3af;")
                            v3.VSlider(
                                v_model=("gamma",),
                                min=0.1,
                                max=5.0,
                                step=0.1,
                                density="compact",
                                hide_details=True,
                                color="#60a5fa",
                            )

                            v3.VSelect(
                                v_model=("colormap",),
                                items=(COLORMAPS,),
                                label="Colormap",
                                density="compact",
                                variant="outlined",
                                hide_details=True,
                                classes="mt-2",
                            )

                            v3.VBtn(
                                "Radial Profile",
                                prepend_icon="mdi-chart-bell-curve",
                                variant="tonal",
                                color="#60a5fa",
                                size="small",
                                block=True,
                                classes="mt-3",
                                click="trigger('compute_radial_profile')",
                            )

                        with html.Div(v_else=True):
                            html.Div(
                                "No image loaded. Browse files and click a .tif or .ge file.",
                                style="font-size: 12px; color: #6b7280; padding: 20px 0;",
                            )

        # -- Main Content --
        with layout.content:
            with v3.VContainer(fluid=True, style="padding: 0; height: calc(100vh - 64px);"):
                with v3.VRow(no_gutters=True, style="height: 100%;"):

                    # === LEFT: Chat Panel ===
                    with v3.VCol(cols=5, style="height: 100%; display: flex; flex-direction: column; border-right: 1px solid #2d2d3d;"):
                        with html.Div(classes="apexa-chat-container"):
                            # Messages area
                            with html.Div(classes="apexa-messages", ref="messageContainer"):
                                # Welcome message
                                with html.Div(v_if="messages.length === 0",
                                              style="text-align: center; padding: 40px 20px; color: #6b7280;"):
                                    html.Div("APEXA", style="font-size: 28px; font-weight: 700; color: #60a5fa; margin-bottom: 8px;")
                                    html.Div(
                                        "Your AI Scientist at the Beamline",
                                        style="font-size: 14px; margin-bottom: 20px;",
                                    )
                                    html.Div(
                                        "Ask me to calibrate, integrate, analyze data, control motors, or explain HEDM workflows.",
                                        style="font-size: 13px; max-width: 400px; margin: 0 auto;",
                                    )

                                # Message bubbles
                                with html.Template(
                                    v_for="(msg, i) in messages",
                                    __properties=[("v_for", "v-for")],
                                ):
                                    html.Div(
                                        "{{ msg.content }}",
                                        classes=(
                                            "'apexa-msg ' + (msg.role === 'user' ? 'apexa-msg-user' : 'apexa-msg-assistant')",
                                        ),
                                    )

                                # Typing indicator
                                with html.Div(v_if="is_loading", classes="apexa-typing"):
                                    html.Span()
                                    html.Span()
                                    html.Span()

                            # Input area
                            with html.Div(classes="apexa-input-row"):
                                v3.VTextarea(
                                    v_model=("chat_input", ""),
                                    placeholder="Ask APEXA anything...",
                                    variant="outlined",
                                    density="compact",
                                    rows=1,
                                    max_rows=4,
                                    auto_grow=True,
                                    hide_details=True,
                                    color="#60a5fa",
                                    style="flex: 1;",
                                    keydown_enter_prevent="if (!$event.shiftKey) { $event.preventDefault(); trigger('send_message'); }",
                                    __properties=[("keydown_enter_prevent", "@keydown.enter.prevent")],
                                )
                                v3.VBtn(
                                    icon="mdi-send",
                                    color="#60a5fa",
                                    variant="flat",
                                    size="small",
                                    loading=("is_loading",),
                                    click="trigger('send_message')",
                                )

                    # === RIGHT: Visualization Panel ===
                    with v3.VCol(cols=7, style="height: 100%;"):
                        with html.Div(classes="apexa-viz-container"):
                            # Empty state
                            with html.Div(v_if="artifacts.length === 0", classes="apexa-empty-state"):
                                v3.VIcon("mdi-chart-scatter-plot", size="64", color="#374151")
                                html.Div(
                                    "Visualization Panel",
                                    style="font-size: 18px; font-weight: 600; color: #4b5563; margin-top: 16px;",
                                )
                                html.Div(
                                    "Run an analysis, load a file, or use a workflow to see results here.",
                                    style="font-size: 13px; color: #6b7280; margin-top: 8px; max-width: 350px;",
                                )

                            # Artifact tabs + content
                            with html.Div(v_if="artifacts.length > 0", style="height: 100%; display: flex; flex-direction: column;"):
                                # Tab bar
                                with v3.VTabs(
                                    v_model=("active_artifact_idx",),
                                    density="compact",
                                    color="#60a5fa",
                                    bg_color="#141419",
                                    show_arrows=True,
                                ):
                                    with html.Template(
                                        v_for="(art, idx) in artifacts",
                                        __properties=[("v_for", "v-for")],
                                    ):
                                        v3.VTab(
                                            value=("idx",),
                                            children=[
                                                html.Span("{{ art.title }}", style="font-size: 12px;"),
                                                v3.VBtn(
                                                    icon="mdi-close",
                                                    variant="text",
                                                    size="x-small",
                                                    density="compact",
                                                    click_stop="trigger('remove_artifact', [idx])",
                                                ),
                                            ],
                                        )

                                # Active artifact content (single render area, driven by state)
                                with html.Div(style="flex: 1; overflow: auto; padding: 8px;"):
                                    # Plotly chart
                                    with html.Div(v_if="active_artifact_type === 'plotly'", style="height: 100%;"):
                                        trame_plotly.Figure(
                                            state_variable_name="active_plotly_data",
                                        )

                                    # Image
                                    with html.Div(
                                        v_if="active_artifact_type === 'image'",
                                        style="height: 100%; display: flex; align-items: center; justify-content: center;",
                                    ):
                                        html.Img(
                                            src=("active_image_src",),
                                            classes="apexa-artifact-img",
                                        )

                                    # Table
                                    with html.Div(v_if="active_artifact_type === 'table'", style="padding: 16px; overflow: auto;"):
                                        with v3.VTable(density="compact", theme="dark"):
                                            with html.Thead():
                                                with html.Tr():
                                                    html.Th("Parameter", style="color: #9ca3af;")
                                                    html.Th("Value", style="color: #9ca3af;")
                                            with html.Tbody():
                                                with html.Template(
                                                    v_for="(val, key) in active_table_data",
                                                    __properties=[("v_for", "v-for")],
                                                ):
                                                    with html.Tr():
                                                        html.Td("{{ key }}", style="color: #60a5fa; font-weight: 500;")
                                                        html.Td("{{ typeof val === 'object' ? JSON.stringify(val) : val }}", style="color: #d1d5db;")


# ==================== Main ====================

def main():
    parser = argparse.ArgumentParser(description="APEXA Trame Web UI")
    parser.add_argument("--port", type=int, default=8002, help="Server port (default: 8002)")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Server host (default: 0.0.0.0)")
    args = parser.parse_args()

    print("=" * 60)
    print("  APEXA - Advanced Photon EXperiment Assistant")
    print("  Trame Web UI")
    print("=" * 60)

    # Build UI
    build_ui()

    # Initialize MCP client on server ready
    @server.state.change("trame__busy")
    def on_ready(trame__busy, **kwargs):
        pass

    async def startup():
        await initialize_client()
        # Browse initial directory
        on_browse_directory(str(Path.cwd()))

    server.controller.on_server_ready.add(lambda **_: asynchronous.create_task(startup()))

    print(f"  Launching on http://{args.host}:{args.port}")
    print("  Press Ctrl+C to stop")
    print("=" * 60)

    server.start(
        port=args.port,
        host=args.host,
        open_browser=False,
    )


if __name__ == "__main__":
    main()
