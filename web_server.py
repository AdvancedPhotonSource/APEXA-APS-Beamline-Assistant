#!/usr/bin/env python3
"""
Beamline Assistant Web Server
Integrates the web UI with MCP servers for diffraction analysis
Enhanced with image viewer capabilities for TIFF/GE diffraction images
"""

import asyncio
import json
import os
import re
import sys
import tempfile
import io
import base64

# Force UTF-8 stdio so the server runs cleanly on Windows, whose console defaults
# to cp1252 and raises UnicodeEncodeError on the ⚠/°/µ/→/✓ status prints.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
import uuid

import numpy as np
from PIL import Image
import tifffile
from scipy import ndimage

from fastapi import FastAPI, File, UploadFile, HTTPException, WebSocket, WebSocketDisconnect, Form, Body
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

# Import your MCP client
from argo_mcp_client import APEXAClient

app = FastAPI(title="Beamline Assistant API", version="0.1.0")

# CORS middleware for web UI
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global MCP client instance
mcp_client: Optional[APEXAClient] = None
upload_dir = Path("uploads")
upload_dir.mkdir(exist_ok=True)

# Image cache for viewer
image_cache: Dict[str, np.ndarray] = {}
image_paths: Dict[str, str] = {}  # Maps file_id to actual file path
calibration_cache: Dict[str, Dict[str, Any]] = {}


# ==================== Image Processing Functions ====================

def _to_2d(arr) -> np.ndarray:
    """Reduce any loaded array to a single 2D frame as float32.

    Handles multi-frame stacks (N,Y,X)→frame 0 and RGB(A) (Y,X,3/4)→luminance.
    """
    a = np.asarray(arr)
    if a.ndim == 3:
        if a.shape[-1] in (3, 4):
            a = a[..., :3].mean(axis=-1)          # RGB(A) → grayscale
        else:
            a = a[0]                               # multi-frame stack → first frame
    elif a.ndim > 3:
        a = a.reshape(-1, a.shape[-2], a.shape[-1])[0]
    return np.asarray(a, dtype=np.float32)


def _find_hdf5_image(h5obj):
    """Find the largest 2D/3D numeric dataset in an HDF5 file (best-effort)."""
    import h5py as _h5
    best = None
    best_score = 0
    def visit(name, obj):
        nonlocal best, best_score
        if isinstance(obj, _h5.Dataset) and obj.ndim in (2, 3) and obj.dtype.kind in 'iuf':
            # Prefer the common detector path ('data'), else the largest array.
            score = int(np.prod(obj.shape)) * (10 if 'data' in name.lower() else 1)
            if score > best_score:
                best_score = score
                best = obj
    h5obj.visititems(visit)
    return best


def load_diffraction_image(file_path: str) -> np.ndarray:
    """Load TIFF / GE / CBF / EDF / HDF5 / standard images as a single 2D float32
    frame. Uses fabio for detector formats (reads real dimensions from the header —
    no square assumption) and h5py for HDF5/NeXus, with graceful fallbacks."""
    path = Path(file_path)
    suf = path.suffix.lower()

    # TIFF (incl. multi-page)
    if suf in ('.tif', '.tiff'):
        return _to_2d(tifffile.imread(str(path)))

    # HDF5 / NeXus
    if suf in ('.h5', '.hdf5', '.hdf', '.nxs'):
        import h5py
        with h5py.File(str(path), 'r') as f:
            ds = _find_hdf5_image(f)
            if ds is None:
                raise ValueError(f"No 2D image dataset found in {path.name}")
            return _to_2d(ds[0] if ds.ndim == 3 else ds[()])

    # Detector binary formats — fabio knows the geometry from the header.
    if suf in ('.ge', '.ge1', '.ge2', '.ge3', '.ge4', '.ge5', '.cbf', '.edf', '.mar3450'):
        try:
            import fabio
            return _to_2d(fabio.open(str(path)).data)
        except Exception as fabio_err:
            # Manual GE fallback: 8192-byte header, uint16. Infer dims instead of
            # assuming square (real GE panels are 2048², but cropped/odd files exist).
            if suf.startswith('.ge'):
                with open(path, 'rb') as fh:
                    fh.seek(8192)
                    data = np.fromfile(fh, dtype=np.uint16)
                n = len(data)
                side = int(round(n ** 0.5))
                if side * side == n:
                    return np.asarray(data.reshape(side, side), dtype=np.float32)
                for dim in (2048, 4096, 1024, 512):
                    if n % dim == 0:
                        return np.asarray(data.reshape(n // dim, dim), dtype=np.float32)
                raise ValueError(
                    f"Cannot infer GE frame dimensions for {path.name} "
                    f"({n} pixels, not square or a multiple of a known panel size)."
                ) from fabio_err
            raise

    # Anything else → PIL
    return _to_2d(np.array(Image.open(path)))


def _safe_stats(img: np.ndarray) -> dict:
    """NaN/Inf-safe image stats (strict JSON can't encode NaN/Inf)."""
    finite = img[np.isfinite(img)]
    if finite.size == 0:
        finite = np.zeros(1, dtype=np.float32)
    return {
        "shape": list(img.shape),
        "dtype": str(img.dtype),
        "min": float(np.min(finite)),
        "max": float(np.max(finite)),
        "mean": float(np.mean(finite)),
        "std": float(np.std(finite)),
    }


def apply_contrast(img: np.ndarray, vmin: float = None, vmax: float = None,
                   gamma: float = 1.0) -> np.ndarray:
    """Apply contrast adjustment with gamma correction"""
    # Neutralize NaN/Inf so percentiles and the preview don't blow up.
    img = np.nan_to_num(img, nan=0.0, posinf=0.0, neginf=0.0)
    if vmin is None:
        vmin = np.percentile(img, 1)
    if vmax is None:
        vmax = np.percentile(img, 99)

    img_norm = np.clip(img, vmin, vmax)
    img_norm = (img_norm - vmin) / (vmax - vmin + 1e-10)

    if gamma != 1.0:
        img_norm = np.power(img_norm, gamma)

    return img_norm


def apply_colormap(img: np.ndarray, colormap: str = 'gray') -> np.ndarray:
    """Apply colormap to grayscale image"""
    import matplotlib.pyplot as plt

    cmap = plt.get_cmap(colormap)
    colored = cmap(img)
    rgb = (colored[:, :, :3] * 255).astype(np.uint8)
    return rgb


def image_to_base64(img: np.ndarray, format: str = 'png') -> str:
    """Convert numpy array to base64 encoded image"""
    if img.dtype != np.uint8:
        img_norm = ((img - img.min()) / (img.max() - img.min() + 1e-10) * 255).astype(np.uint8)
    else:
        img_norm = img

    if len(img_norm.shape) == 2:
        pil_img = Image.fromarray(img_norm, mode='L')
    else:
        pil_img = Image.fromarray(img_norm, mode='RGB')

    buffer = io.BytesIO()
    pil_img.save(buffer, format=format.upper())
    buffer.seek(0)

    img_base64 = base64.b64encode(buffer.read()).decode()
    return f"data:image/{format};base64,{img_base64}"


def calculate_radial_profile(img: np.ndarray, center: Tuple[int, int],
                             num_bins: int = 1000) -> Tuple[np.ndarray, np.ndarray]:
    """Calculate radial intensity profile from center point"""
    y, x = np.indices(img.shape)
    r = np.sqrt((x - center[0])**2 + (y - center[1])**2)
    r = r.astype(int)

    max_r = min(num_bins, int(r.max()))
    radial_profile = np.zeros(max_r)
    radial_counts = np.zeros(max_r)

    for i in range(max_r):
        mask = (r == i)
        if mask.any():
            radial_profile[i] = img[mask].mean()
            radial_counts[i] = mask.sum()

    radii = np.arange(max_r)
    return radii, radial_profile

def load_midas_calibration(cal_file: str) -> Dict[str, Any]:
    """Load MIDAS calibration file (refined_MIDAS_params.txt) and calculate ring positions"""
    import numpy as np

    calibration = {
        "beam_center": [0, 0],
        "pixel_size": 172.0,  # microns (default for typical detectors)
        "detector_distance": 1000.0,  # mm
        "wavelength": 0.0,  # Angstroms
        "rings": [],  # List of ring radii in pixels
        "d_spacings": [],  # Corresponding d-spacings
        "two_theta": []  # 2-theta angles
    }

    try:
        with open(cal_file, 'r') as f:
            lines = f.readlines()

        # Parse MIDAS refined_MIDAS_params.txt format
        for line in lines:
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            parts = line.split()
            if len(parts) < 2:
                continue

            param = parts[0]

            # MIDAS format parameters
            if param == 'BC':  # Beam center
                calibration["beam_center"] = [float(parts[1]), float(parts[2])]
            elif param == 'px':  # Pixel size
                calibration["pixel_size"] = float(parts[1])
            elif param == 'Lsd':  # Sample-to-detector distance (in microns!)
                calibration["detector_distance"] = float(parts[1]) / 1000.0  # Convert µm to mm
            elif param == 'Wavelength':
                calibration["wavelength"] = float(parts[1])

            # Old format support
            elif param == 'BeamCenter' and len(parts) >= 3:
                calibration["beam_center"] = [float(parts[1]), float(parts[2])]
            elif param == 'PixelSize':
                calibration["pixel_size"] = float(parts[1])
            elif param == 'Distance':
                calibration["detector_distance"] = float(parts[1])
            elif param == 'Ring':
                calibration["rings"].append(float(parts[1]))

        # Calculate ring positions for common calibrants (CeO2, LaB6, Si)
        wavelength = calibration["wavelength"]
        lsd_mm = calibration["detector_distance"]
        pixel_size_um = calibration["pixel_size"]

        if wavelength > 0 and lsd_mm > 0 and pixel_size_um > 0:
            # Common calibrant d-spacings (Å)
            # CeO2: (111)=3.124, (200)=2.706, (220)=1.913, (311)=1.632
            # LaB6: (100)=4.157, (110)=2.939, (111)=2.400, (200)=2.078
            # Si: (111)=3.136, (220)=1.920, (311)=1.638
            common_d_spacings = [
                3.124,  # CeO2 (111)
                3.136,  # Si (111)
                2.706,  # CeO2 (200)
                2.400,  # LaB6 (111)
                1.920,  # Si (220)
                1.913,  # CeO2 (220)
                1.638,  # Si (311)
                1.632,  # CeO2 (311)
            ]

            for d in common_d_spacings:
                # Bragg's law: sin(theta) = lambda / (2*d)
                sin_theta = wavelength / (2.0 * d)
                if sin_theta <= 1.0:  # Physical constraint
                    theta_rad = np.arcsin(sin_theta)
                    two_theta_rad = 2.0 * theta_rad
                    two_theta_deg = np.degrees(two_theta_rad)

                    # Calculate ring radius on detector
                    # r = L * tan(2*theta)
                    radius_mm = lsd_mm * np.tan(two_theta_rad)
                    radius_pixels = radius_mm * 1000.0 / pixel_size_um  # mm to pixels

                    calibration["rings"].append(round(radius_pixels, 2))
                    calibration["d_spacings"].append(round(d, 4))
                    calibration["two_theta"].append(round(two_theta_deg, 4))

    except Exception as e:
        print(f"Error loading calibration file: {e}", flush=True)
        import traceback
        traceback.print_exc()

    return calibration

def calculate_azimuthal_profile(img: np.ndarray, center: Tuple[int, int], radius: float, width: float = 5) -> Tuple[np.ndarray, np.ndarray]:
    """Calculate azimuthal (angular) profile around a ring"""
    cy, cx = center
    y, x = np.ogrid[:img.shape[0], :img.shape[1]]

    # Calculate distance and angle from center
    r = np.sqrt((x - cx)**2 + (y - cy)**2)
    theta = np.arctan2(y - cy, x - cx)  # Returns angle in radians (-π to π)
    theta_degrees = np.degrees(theta) % 360  # Convert to 0-360 degrees

    # Create mask for pixels within the ring (radius ± width/2)
    mask = np.abs(r - radius) <= (width / 2)

    if not mask.any():
        return np.array([]), np.array([])

    # Bin by angle (0-360 degrees, 360 bins = 1 degree per bin)
    num_bins = 360
    azimuthal_profile = np.zeros(num_bins)
    azimuthal_counts = np.zeros(num_bins)

    for i in range(num_bins):
        angle_mask = mask & (theta_degrees >= i) & (theta_degrees < i + 1)
        if angle_mask.any():
            azimuthal_profile[i] = img[angle_mask].mean()
            azimuthal_counts[i] = angle_mask.sum()

    # Handle bins with no data by interpolating
    angles = np.arange(num_bins)
    valid = azimuthal_counts > 0
    if valid.sum() > 0:
        azimuthal_profile[~valid] = np.interp(angles[~valid], angles[valid], azimuthal_profile[valid])

    return angles, azimuthal_profile

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def send_personal_message(self, message: dict, websocket: WebSocket):
        try:
            await websocket.send_text(json.dumps(message))
        except:
            pass

    async def broadcast(self, message: dict):
        for connection in self.active_connections[:]:
            try:
                await connection.send_text(json.dumps(message))
            except:
                self.active_connections.remove(connection)

manager = ConnectionManager()

async def initialize_mcp_client():
    """Initialize the MCP client with server configurations from servers.config"""
    global mcp_client

    try:
        mcp_client = APEXAClient()

        # Read server configurations from servers.config
        server_configs = []
        config_file = Path("servers.config")

        if config_file.exists():
            with open(config_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    # Skip comments and empty lines
                    if line and not line.startswith('#'):
                        if ':' in line:
                            name, script_path = line.split(':', 1)
                            server_configs.append({
                                "name": name.strip(),
                                "script_path": script_path.strip()
                            })
            print(f"Loaded {len(server_configs)} server(s) from servers.config")
        else:
            print("⚠️  Warning: servers.config not found, using fallback configuration")
            # Must mirror servers.config. The old fallback named the retired
            # filesystem/executor servers and omitted core+motor entirely, so a
            # missing servers.config silently brought the UI up with no file ops
            # and no motor control.
            server_configs = [
                {"name": "core",  "script_path": "./beamline_core_server.py"},
                {"name": "midas", "script_path": "./midas_comprehensive_server.py"},
                {"name": "motor", "script_path": "./epics_motor_server.py"},
            ]

        await mcp_client.connect_to_multiple_servers(server_configs)
        print("MCP client initialized successfully")

    except Exception as e:
        print(f"Failed to initialize MCP client: {e}")
        mcp_client = None

# Use startup event handler for now (we'll fix deprecation later)
@app.on_event("startup")
async def startup_event():
    """Initialize MCP client on startup"""
    await initialize_mcp_client()

@app.on_event("shutdown") 
async def shutdown_event():
    """Cleanup on shutdown"""
    if mcp_client:
        await mcp_client.cleanup()

# Mount React frontend static assets if build exists
_frontend_dist = Path("frontend/dist")
if (_frontend_dist / "assets").exists():
    app.mount("/assets", StaticFiles(directory=_frontend_dist / "assets"), name="frontend_assets")
if _frontend_dist.exists():
    @app.get("/favicon.svg")
    async def serve_favicon():
        fav = _frontend_dist / "favicon.svg"
        if fav.exists():
            return FileResponse(fav, media_type="image/svg+xml")
        raise HTTPException(404)

@app.get("/debug", response_class=HTMLResponse)
async def serve_debug():
    """Minimal diagnostic page to verify server works"""
    return HTMLResponse("""<!DOCTYPE html>
<html><head><title>APEXA Debug</title></head>
<body style="background:#18181b;color:#fafafa;font-family:system-ui;padding:40px">
<h1>APEXA Server OK</h1>
<p>If you see this, the server is working. The React app may have a JS error.</p>
<p>Check browser Console (F12) on the main page for red errors.</p>
<pre id="out"></pre>
<script>
fetch('/api/status').then(r=>r.json()).then(d=>{
  document.getElementById('out').textContent = JSON.stringify(d, null, 2);
}).catch(e=>{
  document.getElementById('out').textContent = 'API error: ' + e.message;
});
</script>
</body></html>""")

@app.get("/", response_class=HTMLResponse)
async def serve_web_ui():
    """Serve the React app (or fall back to legacy HTML)"""
    react_index = Path("frontend/dist/index.html")
    if react_index.exists():
        return FileResponse(react_index)
    html_file = Path("beamline_web_ui.html")
    if html_file.exists():
        return FileResponse(html_file)
    return HTMLResponse("""
    <html><body>
    <h1>APEXA</h1>
    <p>No frontend found. Run <code>cd frontend && npm run build</code> first.</p>
    </body></html>
    """)

@app.get("/test_viewer.html", response_class=HTMLResponse)
async def serve_test_viewer():
    """Serve the image viewer test page"""
    test_file = Path("test_viewer.html")
    if test_file.exists():
        return FileResponse(test_file)
    else:
        raise HTTPException(status_code=404, detail="Test viewer not found")

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    """Handle file upload"""
    try:
        # Generate unique filename
        file_id = str(uuid.uuid4())
        file_extension = Path(file.filename).suffix
        saved_filename = f"{file_id}{file_extension}"
        file_path = upload_dir / saved_filename
        
        # Save uploaded file
        with open(file_path, "wb") as buffer:
            content = await file.read()
            buffer.write(content)
        
        return {
            "success": True,
            "file_id": file_id,
            "filename": file.filename,
            "saved_path": str(file_path),
            "size": len(content),
            "type": file.content_type
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")

@app.post("/api/analyze")
async def analyze_file(request: Dict[str, Any]):
    """Start diffraction analysis"""
    if not mcp_client:
        raise HTTPException(status_code=503, detail="MCP client not available")

    # Extract parameters from request
    file_id = request.get("file_id")
    analysis_type = request.get("analysis_type", "comprehensive")
    parameters = request.get("parameters")
    
    try:
        # Find uploaded file
        file_path = None
        for ext in ['.tif', '.tiff', '.png', '.dat', '.xy', '.txt']:
            potential_path = upload_dir / f"{file_id}{ext}"
            if potential_path.exists():
                file_path = potential_path
                break
        
        if not file_path:
            raise HTTPException(status_code=404, detail="File not found")
        
        # Broadcast analysis start
        await manager.broadcast(json.dumps({
            "type": "analysis_start",
            "file_id": file_id,
            "analysis_type": analysis_type
        }))
        
        # Run analysis based on file type
        if file_path.suffix.lower() in ['.tif', '.tiff', '.png']:
            # 2D image analysis
            result = await analyze_2d_image(str(file_path), parameters or {})
        else:
            # 1D pattern analysis  
            result = await analyze_1d_pattern(str(file_path), parameters or {})
        
        # Broadcast results
        await manager.broadcast(json.dumps({
            "type": "analysis_complete",
            "file_id": file_id,
            "results": result
        }))
        
        return result
        
    except Exception as e:
        error_msg = f"Analysis failed: {str(e)}"
        await manager.broadcast(json.dumps({
            "type": "analysis_error", 
            "file_id": file_id,
            "error": error_msg
        }))
        raise HTTPException(status_code=500, detail=error_msg)

async def analyze_2d_image(image_path: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
    """Analyze 2D diffraction image"""
    results = {}
    
    # Step 1: Detect rings
    await manager.broadcast(json.dumps({
        "type": "analysis_progress",
        "step": "Detecting diffraction rings",
        "progress": 20
    }))
    
    ring_result = await mcp_client.execute_tool_call(
        "midas_detect_diffraction_rings",
        {
            "image_path": image_path,
            "detector_distance": parameters.get("detector_distance", 1000.0),
            "wavelength": parameters.get("wavelength", 0.2066)
        }
    )
    results["ring_detection"] = json.loads(ring_result)
    
    # Step 2: Integrate to 1D
    await manager.broadcast(json.dumps({
        "type": "analysis_progress", 
        "step": "Integrating 2D to 1D pattern",
        "progress": 40
    }))
    
    integration_result = await mcp_client.execute_tool_call(
        "midas_integrate_2d_to_1d",
        {
            "image_path": image_path,
            "unit": "2th_deg",
            "detector_distance": parameters.get("detector_distance", 1000.0),
            "wavelength": parameters.get("wavelength", 0.2066)
        }
    )
    results["integration"] = json.loads(integration_result)
    
    # Step 3: Find peaks
    await manager.broadcast(json.dumps({
        "type": "analysis_progress",
        "step": "Analyzing diffraction peaks", 
        "progress": 60
    }))
    
    peak_result = await mcp_client.execute_tool_call(
        "midas_analyze_diffraction_peaks",
        {"pattern_file": f"{image_path}_integrated.dat"}
    )
    results["peak_analysis"] = json.loads(peak_result)
    
    # Step 4: Identify phases
    await manager.broadcast(json.dumps({
        "type": "analysis_progress",
        "step": "Identifying crystalline phases",
        "progress": 80
    }))
    
    # Extract peak positions from peak analysis
    peak_data = results["peak_analysis"].get("peak_data", [])
    peak_positions = [peak["position_2theta"] for peak in peak_data]
    
    if peak_positions:
        phase_result = await mcp_client.execute_tool_call(
            "midas_identify_crystalline_phases",
            {
                "peak_positions": peak_positions,
                "material_system": parameters.get("material_system", "unknown"),
                "temperature": parameters.get("temperature", 25.0)
            }
        )
        results["phase_identification"] = json.loads(phase_result)
    
    await manager.broadcast(json.dumps({
        "type": "analysis_progress",
        "step": "Analysis complete",
        "progress": 100
    }))
    
    return results

async def analyze_1d_pattern(pattern_path: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
    """Analyze 1D diffraction pattern"""
    results = {}
    
    # Step 1: Peak analysis
    await manager.broadcast(json.dumps({
        "type": "analysis_progress",
        "step": "Analyzing diffraction peaks",
        "progress": 40
    }))
    
    peak_result = await mcp_client.execute_tool_call(
        "midas_analyze_diffraction_peaks",
        {
            "pattern_file": pattern_path,
            "min_peak_height": parameters.get("min_peak_height", 0.05)
        }
    )
    results["peak_analysis"] = json.loads(peak_result)
    
    # Step 2: Phase identification
    await manager.broadcast(json.dumps({
        "type": "analysis_progress",
        "step": "Identifying crystalline phases",
        "progress": 80
    }))
    
    peak_data = results["peak_analysis"].get("peak_data", [])
    peak_positions = [peak["position_2theta"] for peak in peak_data]
    
    if peak_positions:
        phase_result = await mcp_client.execute_tool_call(
            "midas_identify_crystalline_phases",
            {
                "peak_positions": peak_positions,
                "material_system": parameters.get("material_system", "unknown"),
                "temperature": parameters.get("temperature", 25.0)
            }
        )
        results["phase_identification"] = json.loads(phase_result)
    
    await manager.broadcast(json.dumps({
        "type": "analysis_progress",
        "step": "Analysis complete", 
        "progress": 100
    }))
    
    return results

@app.post("/api/quick_analysis")
async def quick_phase_identification(
    peak_positions: List[float],
    material_system: str = "unknown",
    temperature: float = 25.0
):
    """Quick phase identification from peak positions"""
    if not mcp_client:
        raise HTTPException(status_code=503, detail="MCP client not available")
    
    try:
        result = await mcp_client.execute_tool_call(
            "midas_identify_crystalline_phases",
            {
                "peak_positions": peak_positions,
                "material_system": material_system,
                "temperature": temperature
            }
        )
        return json.loads(result)
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Quick analysis failed: {str(e)}")

@app.post("/api/chat")
async def chat_with_assistant(
    message: str = Form(...),
    file_id: Optional[str] = Form(None),
    model: str = Form(""),
    thread_id: str = Form("")
):
    """Chat with the AI assistant"""
    if not mcp_client:
        raise HTTPException(status_code=503, detail="MCP client not available")

    try:
        # Only switch on an EXPLICIT, non-empty model. Defaulting this to a fixed
        # model (e.g. gpt4o) would silently reset the session model on every
        # untargeted request (image upload, HTTP fallback) — keep the session's
        # own selected_model (ARGO_MODEL default) unless the caller overrides it.
        if model and model != mcp_client.selected_model:
            mcp_client.selected_model = model
        
        # Find file path if file_id provided
        image_path = None
        if file_id:
            for ext in ['.tif', '.tiff', '.png', '.dat', '.xy', '.txt']:
                potential_path = upload_dir / f"{file_id}{ext}"
                if potential_path.exists():
                    image_path = str(potential_path)
                    break
        
        # Prepend image path context if provided, then route through orchestrator
        full_query = message
        if image_path:
            full_query = f"Image file: {image_path}\n\n{message}"
        # Per-thread memory (HTTP fallback mirrors the WS path): activate the
        # frontend thread, run, persist — serialized under the thread lock.
        async with mcp_client._web_thread_lock:
            await mcp_client.activate_web_thread(thread_id or None)
            response = await mcp_client.run_query(full_query)
            mcp_client._save_web_thread()

        return {"response": response}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Chat failed: {str(e)}")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time communication"""
    await manager.connect(websocket)
    print(f"WebSocket client connected. Total connections: {len(manager.active_connections)}")

    _ansi_re = re.compile(r'\x1b\[[0-9;]*m')

    # Deletion-confirmation round-trip: the agent's permission gate sends a
    # `confirm_required` message and awaits a Future keyed by confirm_id; the WS
    # receive loop resolves it when the browser replies `confirm_response`. The
    # frontend (chatStore.ts + ConfirmModal) already implements this contract.
    pending_confirms: Dict[str, asyncio.Future] = {}

    async def _web_permission(tool_name: str, arguments: dict, command: str) -> bool:
        """Prompt the browser to approve a file deletion; block until it answers.

        Passed to run_query → set as mcp_client.permission_callback → invoked by
        the deletion gate in APEXAClient.execute_tool_call. Fail-safe DENY on
        timeout / disconnect / any error.
        """
        cid = uuid.uuid4().hex
        fut = asyncio.get_event_loop().create_future()
        pending_confirms[cid] = fut
        try:
            await manager.send_personal_message({
                "type": "confirm_required",
                "confirm_id": cid,
                "action": "delete files",
                "detail": command,
                "danger": True,
                "safety": "This permanently deletes files on beamline storage (no undo).",
            }, websocket)
            return bool(await asyncio.wait_for(fut, timeout=300))
        except Exception:
            return False
        finally:
            pending_confirms.pop(cid, None)

    async def _handle_chat(message_data):
        """Process one chat message end-to-end (context build → run_query → reply).

        Launched as a task so the receive loop keeps reading — that lets a
        `confirm_response` arrive and resolve _web_permission's Future while a
        deletion-bearing query is mid-flight.
        """
        if True:  # noqa: retains original block indentation after extraction to a task fn
                if mcp_client:
                    try:
                        # Build context about currently loaded images
                        context = ""
                        if image_cache or calibration_cache:
                            context += f"\n\n==== IMPORTANT CONTEXT ====\n"
                            context += "The user has loaded files in the image viewer. When they say 'this image' or 'the uploaded file', they mean:\n\n"

                        if image_cache:
                            context += f"LOADED DIFFRACTION IMAGES (already open in the viewer):\n"
                            for file_id in image_cache.keys():
                                file_path = image_paths.get(file_id, "unknown")
                                arr = image_cache[file_id]
                                img_shape = arr.shape
                                try:
                                    finite = arr[np.isfinite(arr)]
                                    imn, imx, imean = float(finite.min()), float(finite.max()), float(finite.mean())
                                    stat_line = f"    Intensity: min {imn:.0f}, max {imx:.0f}, mean {imean:.1f}\n"
                                except Exception:
                                    stat_line = ""
                                context += f"  • File: {file_id}\n"
                                context += f"    Full path: {file_path}\n"
                                context += f"    Size: {img_shape[1]}x{img_shape[0]} pixels\n"
                                context += stat_line
                                context += f"    USE THIS FILE PATH when running MIDAS tools\n\n"
                            context += (
                                "HOW TO ANSWER ABOUT A LOADED IMAGE:\n"
                                "• If the user asks an INFORMATIONAL question ('what's this image?', "
                                "'is it saturated?', 'what calibrant?'), ANSWER IN TEXT (1–3 sentences) "
                                "using the size/intensity above; call inspect_dataset_file ONLY if you "
                                "need frame count / omega / geometry. Do NOT launch a GUI viewer "
                                "(run_midas_viewer) and do NOT call list_directory for this — the image "
                                "is already shown in the viewer panel.\n"
                                "• Launch run_midas_viewer ONLY when the user explicitly says open / view / "
                                "show / overlay / plot.\n\n"
                            )

                        if calibration_cache:
                            context += f"LOADED CALIBRATION FILES:\n"
                            for cal_id, cal_data in calibration_cache.items():
                                rings = cal_data.get('rings', [])
                                center = cal_data.get('beam_center', [0, 0])
                                wavelength = cal_data.get('wavelength', 0)
                                distance = cal_data.get('detector_distance', 0)
                                context += f"  • Calibration: {cal_id}\n"
                                context += f"    Beam center: ({center[0]:.1f}, {center[1]:.1f})\n"
                                context += f"    Number of rings: {len(rings)}\n"
                                if wavelength > 0:
                                    context += f"    Wavelength: {wavelength:.4f} Å\n"
                                if distance > 0:
                                    context += f"    Detector distance: {distance:.1f} mm\n"
                                if rings:
                                    context += f"    Ring radii: {', '.join([f'{r:.1f}' for r in rings[:5]])} pixels\n"
                                context += "\n"

                        # Append context to user message
                        user_message = message_data["message"]
                        if context:
                            context += "When the user asks to analyze 'this image' or 'the uploaded file', use the file path(s) shown above.\n"
                            context += "==== END CONTEXT ====\n"
                            user_message += context

                        print(f"Sending to AI with context: {user_message[:500]}...")  # Debug log

                        async def _on_tool_start(tool_name: str, arguments: dict):
                            # Push a "tool starting" event so the browser shows a live
                            # "Running <tool>…" line during long tools instead of a
                            # static "Thinking" (users thought the UI had died).
                            try:
                                await manager.send_personal_message({
                                    "type": "tool_start",
                                    "tool": tool_name,
                                    "args": arguments,
                                }, websocket)
                            except Exception as e:
                                print(f"Warning: tool_start WS send failed: {e}")

                        async def _on_tool_result(tool_name: str, arguments: dict, result: str):
                            try:
                                clean = _ansi_re.sub('', result)
                                await manager.send_personal_message({
                                    "type": "tool_result",
                                    "tool": tool_name,
                                    "args": arguments,          # for the UI "in:" view
                                    "result": clean,
                                }, websocket)
                            except Exception as e:
                                print(f"Warning: tool_result WS send failed: {e}")

                        # Per-thread memory: the frontend sends the localStorage
                        # thread id so APEXA remembers within a conversation (and
                        # across restarts) instead of starting fresh each turn.
                        # Serialize activate→run→save under the client's thread
                        # lock (one shared orchestrator, single beamline user).
                        thread_id = message_data.get("thread_id")
                        async with mcp_client._web_thread_lock:
                            await mcp_client.activate_web_thread(thread_id)
                            response = await mcp_client.run_query(
                                user_message, on_tool_result=_on_tool_result,
                                permission_callback=_web_permission,
                                on_tool_start=_on_tool_start,
                            )
                            mcp_client._save_web_thread()
                        clean_response = _ansi_re.sub('', response)
                        # FF-HEDM graph mode (APEXA_WORKFLOW_MODE=graph): if this turn
                        # ended on a human-in-the-loop gate, flag it so the UI can mark
                        # the message as awaiting a decision. Additive — older clients
                        # that only read `.message` ignore it and still show the question.
                        gate = None
                        try:
                            orch = getattr(mcp_client, "orchestrator", None)
                            if orch is not None and hasattr(orch, "ffhedm_pending_gate"):
                                gate = orch.ffhedm_pending_gate()
                        except Exception:
                            gate = None
                        await manager.send_personal_message({
                            "type": "chat_response",
                            "message": clean_response,
                            "awaiting_input": bool(gate),
                            "gate": gate,
                        }, websocket)
                    except Exception as e:
                        err = str(e)
                        if "502" in err or "503" in err:
                            msg = "Argo API gateway is temporarily unavailable. Please try again in a moment."
                        elif "timeout" in err.lower():
                            msg = "Request timed out. The AI model may be under heavy load — please retry."
                        else:
                            msg = f"Chat processing failed: {err}"
                        await manager.send_personal_message({
                            "type": "error",
                            "message": msg
                        }, websocket)
                else:
                    await manager.send_personal_message({
                        "type": "error",
                        "message": "MCP client not available"
                    }, websocket)

    try:
        while True:
            data = await websocket.receive_text()
            message_data = json.loads(data)
            print(f"Received WebSocket message: {message_data}")

            mtype = message_data.get("type")
            # Handle different message types
            if mtype == "chat":
                # Fire-and-forget so the receive loop keeps reading — required so a
                # `confirm_response` (deletion approval) can arrive and resolve the
                # permission Future while this query is still running.
                asyncio.create_task(_handle_chat(message_data))

            elif mtype == "confirm_response":
                # Browser answered a `confirm_required` prompt (see _web_permission).
                fut = pending_confirms.get(message_data.get("confirm_id"))
                if fut is not None and not fut.done():
                    fut.set_result(bool(message_data.get("approved")))

            elif mtype == "change_model":
                if mcp_client:
                    mcp_client.selected_model = message_data["model"]
                    await manager.send_personal_message({
                        "type": "model_changed",
                        "model": message_data["model"]
                    }, websocket)

            elif mtype == "delete_thread":
                # Frontend deleted a conversation — drop its server-side memory
                # bucket so it can't resurface (or bleed into) a later thread.
                if mcp_client:
                    async with mcp_client._web_thread_lock:
                        mcp_client.delete_web_thread(message_data.get("thread_id"))

    except WebSocketDisconnect:
        manager.disconnect(websocket)
        # Fail-safe: unblock any pending deletion prompts as DENY so orphaned
        # run_query tasks don't hang for the full timeout after a disconnect.
        for _fut in pending_confirms.values():
            if not _fut.done():
                _fut.set_result(False)
        print(f"WebSocket client disconnected. Total connections: {len(manager.active_connections)}")

@app.get("/api/status")
async def get_status():
    """Get server status"""
    return {
        "status": "running",
        "mcp_client_connected": mcp_client is not None,
        "connected_servers": list(mcp_client.sessions.keys()) if mcp_client else [],
        "active_connections": len(manager.active_connections),
        "upload_directory": str(upload_dir),
        "available_models": list(mcp_client.available_models.keys()) if mcp_client else []
    }

@app.post("/api/feedback")
async def post_feedback(payload: dict = Body(...)):
    """Record user feedback on a specific answer.

    The client sends what the USER saw and approved, not whatever the server
    could scrape: the web UI shows the attached context in the dialog and lets it
    be removed before sending. Nothing here reaches the network — records are
    appended to JSONL under ~/.apexa/feedback (APEXA_FEEDBACK_DIR to override),
    which is the only option that works at network tier `internal`/`data`.
    """
    import apexa_feedback as fb
    ok, detail = fb.record(
        str(payload.get("kind", "")),
        comment=str(payload.get("comment", "") or ""),
        category=str(payload.get("category", "") or ""),
        session_id=str(payload.get("session_id", "") or ""),
        message_id=str(payload.get("message_id", "") or ""),
        model=str(payload.get("model", "") or ""),
        context=payload.get("context") or {},
        do_redact=bool(payload.get("redact", True)),
    )
    if not ok:
        return JSONResponse(status_code=400, content={"ok": False, "error": detail})
    return {"ok": True, "id": detail}


@app.get("/api/feedback/summary")
async def feedback_summary():
    """Triage view. Same data as `python apexa_feedback.py summary`."""
    import apexa_feedback as fb
    return fb.summarize()


@app.get("/api/feedback/context")
async def feedback_context():
    """What APEXA would attach to a report, so the dialog can show it BEFORE the
    user sends anything. Deliberately a separate call: the user reviews the real
    payload, not a description of it."""
    import apexa_feedback as fb
    ctx = {
        "model": (mcp_client.selected_model if mcp_client else None),
        "servers": (list(mcp_client.sessions.keys()) if mcp_client else []),
        "versions": fb._apexa_versions(),
    }
    try:                       # the executed-tool trace for the last turn
        led = getattr(mcp_client, "ledger", None)
        entries = getattr(led, "entries", None) if led is not None else None
        if entries:
            ctx["tools"] = [
                {"name": e.name, "status": e.status,
                 "elapsed_s": round(getattr(e, "elapsed_s", 0.0), 2)}
                for e in list(entries)[-12:]
            ]
    except Exception:
        pass
    return ctx


@app.get("/api/models")
async def get_available_models():
    """Get available AI models for the frontend model selector"""
    if mcp_client:
        return {"models": mcp_client.available_models, "selected": mcp_client.selected_model}
    return {"models": {}, "selected": os.getenv("ARGO_MODEL", "claudeopus5")}

@app.get("/api/files")
async def list_uploaded_files():
    """List uploaded files"""
    files = []
    for file_path in upload_dir.glob("*"):
        if file_path.is_file():
            stat = file_path.stat()
            files.append({
                "file_id": file_path.stem,
                "filename": file_path.name,
                "size": stat.st_size,
                "modified": stat.st_mtime,
                "extension": file_path.suffix
            })
    return {"files": files}

@app.get("/api/browse")
async def browse_directory(path: str = ".", show_hidden: bool = False):
    """Browse directories for the file browser UI"""
    try:
        dir_path = Path(path).expanduser().resolve()
        if not dir_path.exists():
            raise HTTPException(status_code=404, detail=f"Path not found: {path}")
        if not dir_path.is_dir():
            raise HTTPException(status_code=400, detail=f"Not a directory: {path}")

        entries = []
        try:
            items = sorted(dir_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except PermissionError:
            raise HTTPException(status_code=403, detail="Permission denied")

        # Limit to prevent large directory listings
        DIFFRACTION_EXTS = {'.tif', '.tiff', '.ge', '.ge2', '.ge3', '.ge4', '.ge5',
                           '.h5', '.hdf5', '.nxs', '.zip', '.csv', '.dat', '.xy',
                           '.txt', '.bin', '.npy', '.mic', '.map'}

        for item in items[:500]:
            if not show_hidden and item.name.startswith('.'):
                continue
            try:
                stat = item.stat()
                entry = {
                    "name": item.name,
                    "path": str(item),
                    "is_dir": item.is_dir(),
                    "size": stat.st_size if item.is_file() else None,
                    "modified": stat.st_mtime,
                }
                if item.is_file():
                    entry["ext"] = item.suffix.lower()
                    entry["is_diffraction"] = item.suffix.lower() in DIFFRACTION_EXTS
                entries.append(entry)
            except (PermissionError, OSError):
                continue

        return {
            "path": str(dir_path),
            "parent": str(dir_path.parent) if dir_path != dir_path.parent else None,
            "entries": entries,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/data/csv")
async def read_csv_data(path: str, max_rows: int = 100000):
    """Read CSV/DAT/XY file and return as JSON for Plotly rendering"""
    import pandas as pd
    try:
        file_path = Path(path).expanduser().resolve()
        if not file_path.exists():
            raise HTTPException(status_code=404, detail=f"File not found: {path}")

        ext = file_path.suffix.lower()

        # Try to detect separator
        if ext in ['.csv']:
            # Check first line for separator
            with open(file_path) as f:
                first_lines = [f.readline() for _ in range(5)]
            # Skip comment lines
            header_lines = [l for l in first_lines if not l.startswith('%') and not l.startswith('#')]
            if header_lines and '\t' in header_lines[0]:
                sep = '\t'
            elif header_lines and ',' in header_lines[0]:
                sep = ','
            else:
                sep = r'\s+'
            df = pd.read_csv(file_path, sep=sep, comment='%', nrows=max_rows, on_bad_lines='skip')
        elif ext in ['.dat', '.xy', '.txt']:
            df = pd.read_csv(file_path, sep=r'\s+', comment='#', header=None, nrows=max_rows, on_bad_lines='skip')
            # Try to name columns sensibly
            if len(df.columns) == 2:
                df.columns = ['x', 'y']
            elif len(df.columns) == 3:
                df.columns = ['x', 'y', 'z']
        else:
            df = pd.read_csv(file_path, nrows=max_rows, on_bad_lines='skip')

        return {
            "columns": list(df.columns),
            "data": {col: df[col].tolist() for col in df.columns},
            "rows": len(df),
            "file": str(file_path),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read CSV: {str(e)}")

@app.post("/api/viewer/load_path")
async def load_viewer_image_by_path(path: str = Form(...)):
    """Load an image from a filesystem path (for file browser integration)"""
    try:
        file_path = Path(path).expanduser().resolve()
        if not file_path.exists():
            raise HTTPException(status_code=404, detail=f"File not found: {path}")

        img = load_diffraction_image(str(file_path))
        file_id = f"browse_{file_path.stem}_{uuid.uuid4().hex[:6]}"
        image_cache[file_id] = img
        image_paths[file_id] = str(file_path)

        # Generate preview
        preview = apply_contrast(img)
        preview_colored = apply_colormap(preview, 'viridis')
        preview_b64 = image_to_base64(preview_colored)

        return {
            "success": True,
            "file_id": file_id,
            "filename": file_path.name,
            "stats": _safe_stats(img),
            "preview": preview_b64,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load image: {str(e)}")

@app.get("/api/viewer/status")
async def get_viewer_status():
    """Get current viewer state for debugging"""
    return {
        "loaded_images": list(image_cache.keys()),
        "image_paths": image_paths,
        "loaded_calibrations": list(calibration_cache.keys())
    }


# ==================== Image Viewer Endpoints ====================

@app.post("/api/viewer/load")
async def load_viewer_image(file: UploadFile = File(...)):
    """Load image for viewer with proper handling"""
    try:
        content = await file.read()
        temp_path = upload_dir / file.filename
        with open(temp_path, "wb") as f:
            f.write(content)

        img = load_diffraction_image(str(temp_path))
        file_id = file.filename
        image_cache[file_id] = img
        image_paths[file_id] = str(temp_path)  # Store the file path

        stats = {
            "shape": list(img.shape),
            "dtype": str(img.dtype),
            "min": float(img.min()),
            "max": float(img.max()),
            "mean": float(img.mean()),
            "std": float(img.std())
        }

        # Debug: Log what we're caching
        print(f"[DEBUG] Loaded image: file_id={file_id}, dtype={img.dtype}, shape={img.shape}, min={img.min()}, max={img.max()}")

        img_preview = apply_contrast(img)
        preview_base64 = image_to_base64(img_preview)

        return JSONResponse({
            "success": True,
            "file_id": file_id,
            "stats": stats,
            "preview": preview_base64
        })

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/viewer/adjust")
async def adjust_viewer_image(
    file_id: str = Form(...),
    vmin: Optional[float] = Form(None),
    vmax: Optional[float] = Form(None),
    gamma: float = Form(1.0),
    colormap: str = Form('gray')
):
    """Apply contrast/colormap adjustments"""
    if file_id not in image_cache:
        raise HTTPException(status_code=404, detail="Image not loaded")

    try:
        img = image_cache[file_id]
        img_adjusted = apply_contrast(img, vmin, vmax, gamma)

        if colormap != 'gray':
            img_colored = apply_colormap(img_adjusted, colormap)
        else:
            img_colored = (img_adjusted * 255).astype(np.uint8)

        img_base64 = image_to_base64(img_colored)

        return JSONResponse({
            "success": True,
            "image": img_base64
        })

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/viewer/radial_profile")
async def get_viewer_radial_profile(
    file_id: str = Form(...),
    center_x: int = Form(...),
    center_y: int = Form(...),
    num_bins: int = Form(1000)
):
    """Calculate radial profile"""
    if file_id not in image_cache:
        raise HTTPException(status_code=404, detail="Image not loaded")

    try:
        img = image_cache[file_id]
        radii, profile = calculate_radial_profile(img, (center_x, center_y), num_bins)

        return JSONResponse({
            "success": True,
            "radii": radii.tolist(),
            "intensity": profile.tolist()
        })

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/viewer/azimuthal_profile")
async def get_viewer_azimuthal_profile(
    file_id: str = Form(...),
    center_x: int = Form(...),
    center_y: int = Form(...),
    radius: float = Form(...),
    width: float = Form(5.0)
):
    """Calculate azimuthal (angular) profile around a ring"""
    if file_id not in image_cache:
        raise HTTPException(status_code=404, detail="Image not loaded")

    try:
        img = image_cache[file_id]
        angles, profile = calculate_azimuthal_profile(img, (center_x, center_y), radius, width)

        return JSONResponse({
            "success": True,
            "angles": angles.tolist(),
            "intensity": profile.tolist()
        })

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/viewer/load_calibration")
async def load_calibration_file(file: UploadFile = File(...)):
    """Load MIDAS calibration file and cache it"""
    try:
        content = await file.read()
        temp_path = upload_dir / file.filename
        with open(temp_path, "wb") as f:
            f.write(content)

        calibration = load_midas_calibration(str(temp_path))
        file_id = file.filename
        calibration_cache[file_id] = calibration

        return JSONResponse({
            "success": True,
            "file_id": file_id,
            "calibration": calibration
        })

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/viewer/colormaps")
async def get_colormaps():
    """Get list of available colormaps"""
    return JSONResponse({
        "colormaps": [
            "gray", "viridis", "plasma", "inferno", "magma",
            "jet", "hot", "cool", "spring", "summer",
            "autumn", "winter", "bone", "copper"
        ]
    })


@app.get("/api/viewer/pixel_value")
async def get_pixel_value(file_id: str, x: int, y: int):
    """Get raw pixel intensity at specified coordinates"""
    if file_id not in image_cache:
        raise HTTPException(status_code=404, detail="Image not found in cache")

    img = image_cache[file_id]

    # Check bounds
    if y < 0 or y >= img.shape[0] or x < 0 or x >= img.shape[1]:
        return JSONResponse({"intensity": 0, "error": "Out of bounds"})

    # Get value (handle different types)
    val = img[y, x]

    # Debug: Log what we're returning
    print(f"[DEBUG] Pixel ({x}, {y}): value={val}, dtype={img.dtype}, shape={img.shape}, max={img.max()}, min={img.min()}")

    return JSONResponse({
        "intensity": float(val),
        "x": x,
        "y": y
    })


@app.get("/api/viewer/brightest_pixel")
async def get_brightest_pixel(file_id: str):
    """Find the location of the brightest pixel in the image"""
    if file_id not in image_cache:
        raise HTTPException(status_code=404, detail="Image not found in cache")

    img = image_cache[file_id]

    # Find brightest pixel
    max_val = img.max()
    max_loc = np.unravel_index(img.argmax(), img.shape)

    print(f"[DEBUG] Brightest pixel: ({max_loc[1]}, {max_loc[0]}): value={max_val}")

    return JSONResponse({
        "x": int(max_loc[1]),
        "y": int(max_loc[0]),
        "intensity": float(max_val)
    })


@app.post("/api/auto_calibrate")
async def auto_calibrate(
    image_file_id: str = Form(...),
    space_group: int = Form(...),
    lattice_a: float = Form(...),
    lattice_b: float = Form(...),
    lattice_c: float = Form(...),
    lattice_alpha: float = Form(...),
    lattice_beta: float = Form(...),
    lattice_gamma: float = Form(...),
    lsd: float = Form(...),
    lsd_guess: float = Form(...),
    bc_x: float = Form(...),
    bc_y: float = Form(...),
    bc_guess_x: float = Form(0.0),
    bc_guess_y: float = Form(0.0),
    px: float = Form(...),
    wavelength: float = Form(...),
    tx: float = Form(0.0),
    ty: float = Form(0.0),
    tz: float = Form(0.0),
    stopping_strain: float = Form(0.0005),
    convert_file: int = Form(1),
    im_trans_opt: int = Form(0),
    make_plots: int = Form(1),
    bad_px_intensity: float = Form(-2.0),
    gap_intensity: float = Form(-1.0),
    save_plots_hdf: Optional[str] = Form(None),
    mask_file: Optional[UploadFile] = File(None)
):
    """Run MIDAS auto-calibration on uploaded image"""
    try:
        # Get the image file path
        if image_file_id not in image_paths:
            raise HTTPException(status_code=404, detail="Image file not found")

        image_path = image_paths[image_file_id]

        # Read the actual image dimensions
        import tifffile
        img = tifffile.imread(image_path)
        nr_pixels_y, nr_pixels_z = img.shape[:2] if len(img.shape) >= 2 else (img.shape[0], img.shape[0])

        # Create a parameter file
        param_file = upload_dir / "calib_params_temp.txt"
        with open(param_file, 'w') as f:
            f.write(f"SpaceGroup {space_group}\n")
            f.write(f"LatticeParameter {lattice_a} {lattice_b} {lattice_c} {lattice_alpha} {lattice_beta} {lattice_gamma}\n")
            f.write(f"px {px}\n")
            f.write(f"Wavelength {wavelength}\n")
            f.write(f"tx {tx}\n")
            f.write(f"ty {ty}\n")
            f.write(f"tz {tz}\n")
            f.write(f"NrPixelsY {nr_pixels_y}\n")
            f.write(f"NrPixelsZ {nr_pixels_z}\n")
            f.write(f"ImTransOpt {im_trans_opt}\n")
            f.write(f"BadPxIntensity {bad_px_intensity}\n")
            f.write(f"GapIntensity {gap_intensity}\n")

        # Save mask file if provided
        mask_path = None
        if mask_file:
            mask_path = upload_dir / mask_file.filename
            with open(mask_path, "wb") as f:
                f.write(await mask_file.read())

        # Prepare AutoCalibrateZarr command
        import subprocess
        import sys

        # Import find_midas_python and get_midas_env from midas_comprehensive_server
        sys.path.insert(0, str(Path(__file__).parent))
        from midas_comprehensive_server import find_midas_python, get_midas_env

        # Find AutoCalibrateZarr.py
        midas_path = Path.home() / "opt" / "MIDAS" / "utils" / "AutoCalibrateZarr.py"
        if not midas_path.exists():
            midas_path = Path("/Users/b324240/Git/MIDAS/utils/AutoCalibrateZarr.py")

        if not midas_path.exists():
            raise HTTPException(status_code=500, detail="AutoCalibrateZarr.py not found")

        # Use MIDAS Python environment and environment variables
        midas_python = find_midas_python()
        midas_env = get_midas_env()

        cmd = [
            midas_python,
            str(midas_path),
            "-dataFN", str(image_path),
            "-ConvertFile", str(convert_file),
            "-paramFN", str(param_file),
            "-LsdGuess", str(lsd_guess),
            "-StoppingStrain", str(stopping_strain),
            "-MakePlots", str(make_plots),
            "-ImTransOpt", str(im_trans_opt),
            "-BadPxIntensity", str(bad_px_intensity),
            "-GapIntensity", str(gap_intensity)
        ]

        # Add BC guess if provided
        if bc_guess_x != 0.0 or bc_guess_y != 0.0:
            cmd.extend(["-BCGuess", str(bc_guess_x), str(bc_guess_y)])

        # Add SavePlotsHDF if specified
        if save_plots_hdf:
            cmd.extend(["-SavePlotsHDF", str(upload_dir / save_plots_hdf)])

        # Run calibration with MIDAS environment
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=midas_env)

        # Find refined parameters file
        refined_params = upload_dir / "refined_MIDAS_params.txt"
        if not refined_params.exists():
            # Try in current directory
            refined_params = Path("refined_MIDAS_params.txt")

        return JSONResponse({
            "success": result.returncode == 0,
            "output_file": str(refined_params) if refined_params.exists() else None,
            "stdout": result.stdout,
            "stderr": result.stderr
        })

    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail="Calibration timed out after 10 minutes")
    except Exception as e:
        import traceback
        return JSONResponse({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        })


@app.post("/api/midas_integrate")
async def midas_integrate(
    result_folder: str = Form(...),
    param_file_id: Optional[str] = Form(None),
    param_file_upload: Optional[UploadFile] = File(None),
    data_file: str = Form(...),
    dark_file: Optional[str] = Form(""),
    start_file_nr: int = Form(-1),
    end_file_nr: int = Form(-1),
    data_loc: str = Form("/exchange/data"),
    dark_loc: str = Form("/exchange/data"),
    num_frame_chunks: int = Form(-1),
    preproc_thresh: int = Form(-1),
    convert_files: int = Form(1),
    map_detector: int = Form(1),
    n_cpus: int = Form(4),
    write_mat: int = Form(0),
    skip_existing: int = Form(0)
):
    """Run MIDAS integration (caking) on data files"""
    try:
        # Get parameter file path
        param_file_path = None
        if param_file_id and param_file_id in image_paths:
            param_file_path = image_paths[param_file_id]
        elif param_file_upload:
            # Save uploaded parameter file
            param_file_path = upload_dir / param_file_upload.filename
            with open(param_file_path, "wb") as f:
                f.write(await param_file_upload.read())

        if not param_file_path or not Path(param_file_path).exists():
            raise HTTPException(status_code=400, detail="Parameter file required")

        # Create result folder if it doesn't exist
        result_folder_path = Path(result_folder)
        if not result_folder_path.is_absolute():
            result_folder_path = upload_dir / result_folder
        result_folder_path.mkdir(parents=True, exist_ok=True)

        # Import find_midas_python and get_midas_env from midas_comprehensive_server
        sys.path.insert(0, str(Path(__file__).parent))
        from midas_comprehensive_server import find_midas_python, get_midas_env

        # Find integrator.py
        integrator_path = Path.home() / "opt" / "MIDAS" / "utils" / "integrator.py"
        if not integrator_path.exists():
            integrator_path = Path("/Users/b324240/Git/MIDAS/utils/integrator.py")

        if not integrator_path.exists():
            raise HTTPException(status_code=500, detail="integrator.py not found")

        # Use MIDAS Python environment and environment variables
        midas_python = find_midas_python()
        midas_env = get_midas_env()

        # Build command
        cmd = [
            midas_python,
            str(integrator_path),
            "-resultFolder", str(result_folder_path),
            "-paramFN", str(param_file_path),
            "-dataFN", data_file,
            "-dataLoc", data_loc,
            "-darkLoc", dark_loc,
            "-numFrameChunks", str(num_frame_chunks),
            "-preProcThresh", str(preproc_thresh),
            "-startFileNr", str(start_file_nr),
            "-endFileNr", str(end_file_nr),
            "-convertFiles", str(convert_files),
            "-mapDetector", str(map_detector),
            "-nCPUs", str(n_cpus),
            "-writeMat", str(write_mat)
        ]

        # Add dark file if provided
        if dark_file and dark_file.strip():
            cmd.extend(["-darkFN", dark_file])

        # Add skip existing flag if enabled
        if skip_existing == 1:
            cmd.append("-skipExisting")

        # Run integration with MIDAS environment
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, env=midas_env)

        return JSONResponse({
            "success": result.returncode == 0,
            "result_folder": str(result_folder_path),
            "stdout": result.stdout,
            "stderr": result.stderr
        })

    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail="Integration timed out after 30 minutes")
    except Exception as e:
        import traceback
        return JSONResponse({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        })


@app.post("/api/export_paraview")
async def export_to_paraview(
    results_path: str = Form(...),
    data_type: str = Form("grains")
):
    """
    Export MIDAS analysis results to VTK format for ParaView visualization

    Args:
        results_path: Path to MIDAS output file (CSV or H5)
        data_type: Type of data ("grains", "voxels", or "peaks")

    Returns:
        VTK file for download
    """
    try:
        from paraview_export import grains_to_vtk, nf_voxels_to_vtk, peaks_to_vtk

        # Resolve path
        input_path = Path(results_path)
        if not input_path.exists():
            raise HTTPException(status_code=404, detail=f"File not found: {results_path}")

        # Create output path
        output_path = upload_dir / f"{data_type}.vtp"

        # Convert based on type
        if data_type == "grains":
            grains_to_vtk(str(input_path), str(output_path))
        elif data_type == "voxels":
            nf_voxels_to_vtk(str(input_path), str(output_path))
        elif data_type == "peaks":
            peaks_to_vtk(str(input_path), str(output_path))
        else:
            raise HTTPException(status_code=400, detail=f"Unknown data type: {data_type}")

        return FileResponse(
            path=output_path,
            filename=f"{data_type}.vtp",
            media_type="application/octet-stream"
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/analysis/phases")
async def identify_phases(
    file_id: str = Form(...),
    elements: str = Form(...)
):
    """
    Identify crystalline phases using MIDAS MCP tool
    """
    try:
        if not mcp_client:
            raise HTTPException(status_code=503, detail="MCP Client not connected")
            
        file_path = image_paths.get(file_id)
        if not file_path:
            raise HTTPException(status_code=404, detail="File not found")
            
        # Call MIDAS tool
        result = await mcp_client.execute_tool_call(
            "midas_identify_crystalline_phases",
            {
                "image_path": file_path,
                "elements": elements
            }
        )
        
        return JSONResponse({
            "success": True,
            "result": result
        })
        
    except Exception as e:
        # Fallback for demo/testing if tool fails or not found
        print(f"Phase ID failed: {e}")
        return JSONResponse({
            "success": False,
            "error": str(e),
            "message": "Phase ID tool unavailable. Please check MCP connection."
        })


@app.post("/api/analysis/peaks")
async def analyze_peaks(
    file_id: str = Form(...)
):
    """
    Analyze diffraction peaks using MIDAS MCP tool
    """
    try:
        if not mcp_client:
            raise HTTPException(status_code=503, detail="MCP Client not connected")
            
        file_path = image_paths.get(file_id)
        if not file_path:
            raise HTTPException(status_code=404, detail="File not found")
            
        # Call MIDAS tool
        result = await mcp_client.execute_tool_call(
            "midas_analyze_diffraction_peaks",
            {
                "image_path": file_path
            }
        )
        
        return JSONResponse({
            "success": True,
            "result": result
        })
        
    except Exception as e:
        print(f"Peak analysis failed: {e}")
        return JSONResponse({
            "success": False,
            "error": str(e),
            "message": "Peak analysis tool unavailable."
        })


@app.post("/api/calibrate")
async def run_calibration(
    file_id: str = Form(...),
    calibrant: str = Form("CeO2"),
    distance: float = Form(1000.0),
    wavelength: float = Form(0.2066),
    center_x: Optional[float] = Form(None),
    center_y: Optional[float] = Form(None)
):
    """
    Run MIDAS auto-calibration on uploaded diffraction image
    
    Args:
        file_id: ID of uploaded image
        calibrant: Calibrant material (CeO2, LaB6, Si, etc.)
        distance: Initial detector distance (mm)
        wavelength: X-ray wavelength (Angstroms)
        center_x: Initial beam center X (pixels)
        center_y: Initial beam center Y (pixels)
    
    Returns:
        Refined calibration parameters
    """
    if not mcp_client:
        raise HTTPException(status_code=503, detail="MCP client not connected")
    
    try:
        # Get image path
        if file_id not in image_paths:
            raise HTTPException(status_code=404, detail=f"Image {file_id} not found")
        
        image_path = image_paths[file_id]
        img_array = image_cache[file_id]
        
        # Set default center if not provided
        if center_x is None:
            center_x = img_array.shape[1] / 2
        if center_y is None:
            center_y = img_array.shape[0] / 2
        
        # Call MIDAS auto-calibration via MCP
        # Note: Requires AutoCalibrateZarr tool to be available in midas_comprehensive_server
        prompt = f"""Run detector calibration on {image_path} with:
- Calibrant: {calibrant}
- Distance: {distance} mm
- Wavelength: {wavelength} Å
- Beam center: ({center_x}, {center_y}) pixels

Please use the AutoCalibrateZarr tool and return the refined parameters."""
        
        response = await mcp_client.run_query(prompt)
        
        return {"success": True, "calibration": response, "message": "Calibration completed"}
        
    except Exception as e:
        print(f"Calibration error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Visualization API — server-side Plotly rendering of MIDAS analysis outputs
# ---------------------------------------------------------------------------

from viz_api import (
    lineout_to_plotly,
    calibrant_to_plotly,
    caked_to_plotly,
    integrator_peaks_to_plotly,
    discover_viz_files,
    grains_to_plotly,
    spot_matrix_to_plotly,
    microstructure_to_plotly,
    lineout_comparison_to_plotly,
    caked_peaks_to_plotly,
)


@app.post("/api/viz/lineout")
async def viz_lineout(
    file: str = Form(...),
    peaks_csv: Optional[str] = Form(None),
    show_raw: bool = Form(True),
    show_bg: bool = Form(True),
    log_y: bool = Form(False),
):
    """Render MIDAS lineout XY file as interactive Plotly chart."""
    result = lineout_to_plotly(file, peaks_csv, show_raw, show_bg, log_y)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.post("/api/viz/calibrant")
async def viz_calibrant(
    file: str = Form(...),
    x_axis: str = Form("Eta"),
    y_axis: str = Form("Strain"),
    color_by: str = Form("RingNr"),
    filter_outliers: bool = Form(True),
):
    """Render calibrant _corr.csv as interactive Plotly scatter."""
    result = calibrant_to_plotly(file, x_axis, y_axis, color_by, filter_outliers)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.post("/api/viz/caked")
async def viz_caked(
    file: str = Form(...),
    frame: int = Form(-1),
    colorscale: str = Form("Viridis"),
    log_intensity: bool = Form(False),
):
    """Render caked zarr as interactive Plotly 2D heatmap."""
    result = caked_to_plotly(file, frame, colorscale, log_intensity)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.post("/api/viz/integrator_peaks")
async def viz_integrator_peaks(
    file: str = Form(...),
    corr_csv: Optional[str] = Form(None),
    frame: int = Form(-1),
):
    """Render integrator peak analysis as Plotly scatter."""
    result = integrator_peaks_to_plotly(file, corr_csv, frame)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.post("/api/viz/grains")
async def viz_grains(
    file: str = Form(...),
    color_by: str = Form("confidence"),
):
    """Render Grains.csv as 3D grain centroid scatter."""
    result = grains_to_plotly(file, color_by)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.post("/api/viz/spots")
async def viz_spots(
    file: str = Form(...),
    color_by: str = Form("ringNr"),
):
    """Render SpotMatrix.csv as 2D diffraction spot scatter."""
    result = spot_matrix_to_plotly(file, color_by)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.post("/api/viz/microstructure")
async def viz_microstructure(
    file: str = Form(...),
    color_by: str = Form("confidence"),
    min_confidence: float = Form(0.0),
):
    """Render .mic/.map NF orientation map as 2D scatter."""
    result = microstructure_to_plotly(file, color_by, min_confidence)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.post("/api/viz/lineout_comparison")
async def viz_lineout_comparison(
    files: str = Form(...),
    param_file: Optional[str] = Form(None),
    log_y: bool = Form(False),
):
    """Overlay multiple lineout XY files on a single plot."""
    file_list = [f.strip() for f in files.split(',') if f.strip()]
    result = lineout_comparison_to_plotly(file_list, param_file, log_y)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.post("/api/viz/caked_peaks")
async def viz_caked_peaks(
    file: str = Form(...),
    zarr_file: Optional[str] = Form(None),
    frame: int = Form(-1),
):
    """Render _caked_peaks.h5 with fitted peak overlay."""
    result = caked_peaks_to_plotly(file, zarr_file, frame)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.get("/api/viz/discover")
async def viz_discover(path: str):
    """Discover MIDAS analysis output files in a directory."""
    result = discover_viz_files(path)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


if __name__ == "__main__":
    print("- .env file with ANL_USERNAME and ARGO_MODEL")
    print("")
    print("Dependencies should be installed with uv:")
    print("  uv add fastapi uvicorn websockets python-multipart")

    # reload=True spawns a file-watcher subprocess that is flaky on Windows and
    # unnecessary for running the app; default OFF, opt in with APEXA_WEB_RELOAD=1.
    _reload = os.environ.get("APEXA_WEB_RELOAD", "").strip() in ("1", "true", "yes")
    _host = os.environ.get("APEXA_WEB_HOST", "0.0.0.0")
    _port = int(os.environ.get("APEXA_WEB_PORT", "8001"))
    print(f"\n🚀 Web UI:  http://localhost:{_port}   (host={_host}, reload={_reload})\n")
    uvicorn.run(
        "web_server:app" if _reload else app,
        host=_host,
        port=_port,
        reload=_reload,
        log_level="info",
    )