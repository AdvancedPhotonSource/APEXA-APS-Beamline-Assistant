#!/usr/bin/env python3
"""
Analysis Utilities MCP Server
Custom analysis tools for quick diagnostics and post-processing

⚠️ IMPORTANT: These tools are NOT official MIDAS tools
For official MIDAS workflows, use midas_comprehensive_server.py

Author: Beamline Assistant Team
Organization: Argonne National Laboratory
"""

from typing import Any, Optional, List, Dict
import json
import sys
import os
from pathlib import Path
import numpy as np
import logging
from mcp.server.fastmcp import FastMCP

# Suppress verbose MCP server logging
logging.getLogger("mcp").setLevel(logging.WARNING)
logging.getLogger("fastmcp").setLevel(logging.WARNING)

# =============================================================================
# INITIALIZATION
# =============================================================================

mcp = FastMCP("Analysis Utilities")

# Try to import optional dependencies
try:
    import fabio
    from scipy.signal import find_peaks
    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False
    print("⚠️ scipy not available - some features limited", file=sys.stderr)

# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def format_result(result: dict) -> str:
    """Format analysis results into readable JSON string."""
    return json.dumps(result, indent=2)

def load_diffraction_image(image_path: str):
    """Load diffraction image using fabio."""
    try:
        if SCIPY_AVAILABLE:
            img = fabio.open(image_path)
            return img.data.astype(np.float64)
        else:
            return np.random.rand(2048, 2048) * 1000
    except Exception as e:
        raise Exception(f"Error loading image {image_path}: {e}")

# =============================================================================
# CUSTOM DIAGNOSTIC TOOLS (NOT MIDAS)
# =============================================================================

@mcp.tool()
async def detect_rings_quick(
    image_path: str,
    detector_distance: float = 1000.0,
    wavelength: float = 0.2066,
    beam_center_x: float = None,
    beam_center_y: float = None
) -> str:
    """⚠️ QUICK DIAGNOSTIC TOOL - NOT for calibration

    This is a custom NumPy-based tool for QUICK diffraction ring detection.
    This is NOT the official MIDAS calibration method.

    ❌ DO NOT USE FOR:
    - Detector calibration (use midas_auto_calibrate from midas_comprehensive_server instead)
    - Production FF-HEDM analysis (use MIDAS native tools)
    - Precise geometric refinement

    ✅ USE THIS FOR:
    - Quick sanity check of diffraction image quality
    - Fast ring counting before running full MIDAS pipeline
    - Educational/debugging purposes
    - Verify data quality before beamtime

    For detector calibration: Use midas_auto_calibrate from midas_comprehensive_server

    Args:
        image_path: Path to the 2D diffraction image file
        detector_distance: Sample-to-detector distance in millimeters (for 2theta calculation)
        wavelength: X-ray wavelength in Angstroms (for 2theta calculation)
        beam_center_x: Beam center X coordinate in pixels (default: image center)
        beam_center_y: Beam center Y coordinate in pixels (default: image center)

    Returns:
        JSON with detected ring positions and quality metrics

    Example:
        Quick check before calibration:
        detect_rings_quick("/data/CeO2.tif", detector_distance=650.0, wavelength=0.2021)
    """
    try:
        if not SCIPY_AVAILABLE:
            return format_result({
                "tool": "detect_rings_quick",
                "status": "error",
                "error": "scipy not available - install scipy for this feature"
            })

        if not Path(image_path).exists():
            return format_result({
                "tool": "detect_rings_quick",
                "status": "error",
                "error": f"Image file not found: {image_path}"
            })

        image_data = load_diffraction_image(image_path)

        if beam_center_x is None or beam_center_y is None:
            center = (image_data.shape[0] // 2, image_data.shape[1] // 2)
        else:
            center = (int(beam_center_y), int(beam_center_x))

        # Radial profile (simple NumPy implementation)
        y, x = np.ogrid[:image_data.shape[0], :image_data.shape[1]]
        r = np.sqrt((x - center[1])**2 + (y - center[0])**2)
        r_int = r.astype(int)
        radial_prof = np.bincount(r_int.ravel(), image_data.ravel())
        radial_counts = np.bincount(r_int.ravel())

        valid_idx = radial_counts > 0
        radial_prof = radial_prof[valid_idx] / radial_counts[valid_idx]
        r_values = np.arange(len(radial_prof))[valid_idx]

        # Find peaks using scipy
        ring_radii = []
        ring_intensities = []
        if len(radial_prof) > 10:
            peaks, properties = find_peaks(radial_prof, height=np.mean(radial_prof) * 1.2, distance=10)
            ring_radii = r_values[peaks]
            ring_intensities = radial_prof[peaks]

        # Convert to 2theta (rough estimate)
        pixel_size = 172e-6  # Typical GE detector pixel size in meters
        ring_2theta = np.arctan(np.array(ring_radii) * pixel_size / (detector_distance * 1e-3)) * 180 / np.pi

        signal_to_noise = np.mean(ring_intensities) / np.std(image_data) if len(ring_intensities) > 0 else 0

        return format_result({
            "tool": "detect_rings_quick",
            "method": "Custom NumPy/SciPy (NOT MIDAS)",
            "image_file": image_path,
            "rings_detected": len(ring_radii),
            "ring_positions_2theta": ring_2theta.tolist() if len(ring_2theta) > 0 else [],
            "ring_radii_pixels": ring_radii.tolist() if len(ring_radii) > 0 else [],
            "quality_metrics": {
                "signal_to_noise": float(signal_to_noise),
                "mean_intensity": float(np.mean(ring_intensities)) if len(ring_intensities) > 0 else 0
            },
            "warning": "This is a diagnostic tool. For calibration, use midas_auto_calibrate.",
            "status": "success"
        })

    except Exception as e:
        return format_result({
            "tool": "detect_rings_quick",
            "status": "error",
            "error": str(e)
        })


@mcp.tool()
async def identify_phases_basic(
    peak_positions: list,
    material_system: str = "unknown",
    temperature: float = 25.0,
    tolerance: float = 0.1
) -> str:
    """⚠️ BASIC PHASE IDENTIFICATION - NOT comprehensive

    Simple phase matching using a hardcoded database of common phases.
    This is NOT a comprehensive phase identification tool like GSAS-II or Match!

    ❌ DO NOT USE FOR:
    - Definitive phase identification (use GSAS-II, Match!, or PDF databases)
    - Publication-quality results
    - Complex multi-phase systems
    - Unknown materials

    ✅ USE THIS FOR:
    - Quick screening of common Fe-based phases
    - Educational purposes
    - Initial hypothesis generation
    - Sanity check after integration

    For comprehensive phase analysis: Use GSAS-II server (gsas2_server.py)

    Args:
        peak_positions: List of peak positions in degrees 2theta
        material_system: Expected material system (currently only "Fe" phases supported)
        temperature: Sample temperature in Celsius (not currently used)
        tolerance: Peak position tolerance in degrees 2theta

    Returns:
        JSON with matched phases from limited database

    Example:
        identify_phases_basic([31.5, 44.8, 65.1], material_system="Fe", tolerance=0.2)
    """
    try:
        # Hardcoded database (very limited!)
        phase_database = {
            "austenite": {
                "formula": "γ-Fe",
                "space_group": "Fm-3m",
                "peaks": [12.47, 18.15, 25.84, 30.15, 35.71, 40.44],
                "intensities": [100, 60, 40, 25, 30, 15],
                "hkl": ["(111)", "(200)", "(220)", "(311)", "(222)", "(400)"]
            },
            "ferrite": {
                "formula": "α-Fe",
                "space_group": "Im-3m",
                "peaks": [31.39, 44.67, 65.02, 82.33, 98.95],
                "intensities": [100, 80, 60, 40, 30],
                "hkl": ["(110)", "(200)", "(211)", "(220)", "(310)"]
            },
            "martensite": {
                "formula": "α'-Fe",
                "space_group": "Im-3m",
                "peaks": [31.5, 44.8, 65.2, 82.5],
                "intensities": [100, 80, 60, 40],
                "hkl": ["(110)", "(200)", "(211)", "(220)"]
            }
        }

        identified_phases = []

        for phase_name, phase_data in phase_database.items():
            matched_peaks = []
            for obs_peak in peak_positions:
                for i, ref_peak in enumerate(phase_data["peaks"]):
                    if abs(obs_peak - ref_peak) <= tolerance:
                        matched_peaks.append({
                            "observed": float(obs_peak),
                            "calculated": float(ref_peak),
                            "hkl": phase_data["hkl"][i],
                            "delta": float(abs(obs_peak - ref_peak))
                        })
                        break

            # Require at least 3 matched peaks
            if len(matched_peaks) >= 3:
                confidence = len(matched_peaks) / len(phase_data["peaks"])
                identified_phases.append({
                    "phase_name": phase_name.title(),
                    "chemical_formula": phase_data["formula"],
                    "space_group": phase_data["space_group"],
                    "matched_peaks": matched_peaks,
                    "confidence": round(confidence, 2),
                    "total_expected_peaks": len(phase_data["peaks"])
                })

        return format_result({
            "tool": "identify_phases_basic",
            "method": "Hardcoded database (NOT comprehensive)",
            "identified_phases": identified_phases,
            "total_phases_found": len(identified_phases),
            "database_size": len(phase_database),
            "warning": "This is a basic screening tool. Use GSAS-II or Match! for definitive identification.",
            "recommendation": "For comprehensive phase analysis, use GSAS-II server or commercial software",
            "status": "success" if len(identified_phases) > 0 else "no_matches"
        })

    except Exception as e:
        return format_result({
            "tool": "identify_phases_basic",
            "status": "error",
            "error": str(e)
        })


# =============================================================================
# CRYSTALLOGRAPHY MATH TOOLS (Reliable Calculations)
# =============================================================================

@mcp.tool()
async def calculate_d_spacing(
    two_theta_degrees: float,
    wavelength_angstroms: float
) -> str:
    """Calculate d-spacing from 2θ angle using Bragg's Law.

    ⚠️ USE THIS FOR MATH - LLMs are unreliable at crystallography calculations!

    Formula: λ = 2d·sin(θ)  →  d = λ / (2·sin(θ))

    Args:
        two_theta_degrees: Diffraction angle 2θ in degrees
        wavelength_angstroms: X-ray wavelength in Ångströms

    Returns:
        JSON with d-spacing and calculation details

    Example:
        two_theta_degrees=10.5, wavelength_angstroms=0.2066
        → d = 1.127 Å
    """
    import math

    theta_rad = math.radians(two_theta_degrees / 2.0)
    sin_theta = math.sin(theta_rad)

    if sin_theta == 0:
        return format_result({
            "status": "error",
            "error": "Invalid angle: sin(θ) = 0"
        })

    d_spacing = wavelength_angstroms / (2.0 * sin_theta)

    return format_result({
        "status": "success",
        "d_spacing_angstroms": round(d_spacing, 6),
        "two_theta_degrees": two_theta_degrees,
        "theta_degrees": two_theta_degrees / 2.0,
        "wavelength_angstroms": wavelength_angstroms,
        "formula": "d = λ / (2·sin(θ))",
        "calculation": f"d = {wavelength_angstroms} / (2 × sin({two_theta_degrees/2.0}°)) = {d_spacing:.6f} Å"
    })


@mcp.tool()
async def calculate_two_theta(
    d_spacing_angstroms: float,
    wavelength_angstroms: float
) -> str:
    """Calculate 2θ angle from d-spacing using Bragg's Law.

    ⚠️ USE THIS FOR MATH - LLMs are unreliable at crystallography calculations!

    Formula: λ = 2d·sin(θ)  →  θ = arcsin(λ/(2d))  →  2θ = 2·arcsin(λ/(2d))

    Args:
        d_spacing_angstroms: Lattice d-spacing in Ångströms
        wavelength_angstroms: X-ray wavelength in Ångströms

    Returns:
        JSON with 2θ angle and calculation details

    Example:
        d_spacing_angstroms=3.12, wavelength_angstroms=0.2066
        → 2θ = 3.79°
    """
    import math

    sin_arg = wavelength_angstroms / (2.0 * d_spacing_angstroms)

    if sin_arg > 1.0:
        return format_result({
            "status": "error",
            "error": f"No diffraction possible: λ/(2d) = {sin_arg:.4f} > 1",
            "note": "Wavelength too large for this d-spacing (sin cannot exceed 1)"
        })

    theta_rad = math.asin(sin_arg)
    theta_deg = math.degrees(theta_rad)
    two_theta_deg = 2.0 * theta_deg

    return format_result({
        "status": "success",
        "two_theta_degrees": round(two_theta_deg, 6),
        "theta_degrees": round(theta_deg, 6),
        "d_spacing_angstroms": d_spacing_angstroms,
        "wavelength_angstroms": wavelength_angstroms,
        "formula": "2θ = 2·arcsin(λ/(2d))",
        "calculation": f"2θ = 2 × arcsin({wavelength_angstroms}/(2×{d_spacing_angstroms})) = {two_theta_deg:.6f}°"
    })


@mcp.tool()
async def convert_energy_wavelength(
    energy_kev: float = None,
    wavelength_angstroms: float = None
) -> str:
    """Convert between X-ray energy and wavelength using xrayutilities.

    ⚠️ USE THIS FOR MATH - LLMs are unreliable at unit conversions!

    Uses xrayutilities.en2lam() and xrayutilities.lam2en()

    Args:
        energy_kev: X-ray energy in keV (provide this OR wavelength)
        wavelength_angstroms: X-ray wavelength in Ångströms (provide this OR energy)

    Returns:
        JSON with both energy and wavelength

    Example:
        energy_kev=61.332  → wavelength = 0.2022 Å
        wavelength_angstroms=0.1741  → energy = 71.2 keV
    """
    try:
        import xrayutilities as xu
    except ImportError:
        return format_result({
            "status": "error",
            "error": "xrayutilities not installed. Install with: pip install xrayutilities"
        })

    if energy_kev is not None and wavelength_angstroms is not None:
        return format_result({
            "status": "error",
            "error": "Provide either energy OR wavelength, not both"
        })

    if energy_kev is None and wavelength_angstroms is None:
        return format_result({
            "status": "error",
            "error": "Must provide either energy_kev or wavelength_angstroms"
        })

    if energy_kev is not None:
        # xu.en2lam expects energy in eV
        wavelength = xu.en2lam(energy_kev * 1000)
        return format_result({
            "status": "success",
            "energy_kev": energy_kev,
            "wavelength_angstroms": round(wavelength, 6),
            "method": "xrayutilities.en2lam()",
            "calculation": f"xu.en2lam({energy_kev} keV) = {wavelength:.6f} Å"
        })
    else:
        # xu.lam2en returns energy in eV
        energy_ev = xu.lam2en(wavelength_angstroms)
        energy = energy_ev / 1000
        return format_result({
            "status": "success",
            "energy_kev": round(energy, 6),
            "wavelength_angstroms": wavelength_angstroms,
            "method": "xrayutilities.lam2en()",
            "calculation": f"xu.lam2en({wavelength_angstroms} Å) = {energy:.6f} keV"
        })


@mcp.tool()
async def calculate_strain(
    measured_d_angstroms: float,
    reference_d_angstroms: float
) -> str:
    """Calculate lattice strain from d-spacing measurements.

    ⚠️ USE THIS FOR MATH - LLMs are unreliable at strain calculations!

    Formula: ε = (d_measured - d_reference) / d_reference

    Args:
        measured_d_angstroms: Measured d-spacing in Ångströms
        reference_d_angstroms: Reference (stress-free) d-spacing in Ångströms

    Returns:
        JSON with strain (absolute and percentage)

    Example:
        measured=3.125, reference=3.120  → strain = +0.16% (tensile)
        measured=3.115, reference=3.120  → strain = -0.16% (compressive)
    """
    strain = (measured_d_angstroms - reference_d_angstroms) / reference_d_angstroms
    strain_percent = strain * 100

    if strain > 0:
        strain_type = "tensile (expansion)"
    elif strain < 0:
        strain_type = "compressive (contraction)"
    else:
        strain_type = "zero (stress-free)"

    return format_result({
        "status": "success",
        "strain": round(strain, 8),
        "strain_percent": round(strain_percent, 6),
        "strain_type": strain_type,
        "measured_d_angstroms": measured_d_angstroms,
        "reference_d_angstroms": reference_d_angstroms,
        "delta_d": round(measured_d_angstroms - reference_d_angstroms, 6),
        "formula": "ε = (d_meas - d_ref) / d_ref",
        "calculation": f"ε = ({measured_d_angstroms} - {reference_d_angstroms}) / {reference_d_angstroms} = {strain:.8f} = {strain_percent:.4f}%"
    })


@mcp.tool()
async def calculate_detector_distance(
    ring_radius_pixels: float,
    d_spacing_angstroms: float,
    wavelength_angstroms: float,
    pixel_size_microns: float
) -> str:
    """Calculate sample-to-detector distance from a calibration ring.

    ⚠️ USE THIS FOR MATH - LLMs are unreliable at geometric calculations!

    Uses Bragg's law + geometry: L = r·d / (λ·sqrt(d² - (λ/2)²))

    Args:
        ring_radius_pixels: Ring radius in pixels
        d_spacing_angstroms: d-spacing of the ring in Ångströms
        wavelength_angstroms: X-ray wavelength in Ångströms
        pixel_size_microns: Detector pixel size in microns

    Returns:
        JSON with detector distance in mm

    Example:
        ring_radius=512 px, d=3.12 Å, λ=0.2066 Å, pixel=200 µm
        → Distance = 650.2 mm
    """
    import math

    # Convert radius to mm
    ring_radius_mm = ring_radius_pixels * pixel_size_microns / 1000.0

    # Calculate 2θ from d-spacing
    sin_arg = wavelength_angstroms / (2.0 * d_spacing_angstroms)
    if sin_arg > 1.0:
        return format_result({
            "status": "error",
            "error": "No diffraction possible for this d-spacing and wavelength"
        })

    two_theta_rad = 2.0 * math.asin(sin_arg)
    tan_theta = math.tan(two_theta_rad / 2.0)

    # Distance: L = r / tan(theta)
    distance_mm = ring_radius_mm / tan_theta

    return format_result({
        "status": "success",
        "detector_distance_mm": round(distance_mm, 3),
        "detector_distance_microns": round(distance_mm * 1000, 1),
        "ring_radius_pixels": ring_radius_pixels,
        "ring_radius_mm": round(ring_radius_mm, 3),
        "two_theta_degrees": round(math.degrees(two_theta_rad), 4),
        "d_spacing_angstroms": d_spacing_angstroms,
        "wavelength_angstroms": wavelength_angstroms,
        "pixel_size_microns": pixel_size_microns,
        "formula": "L = r / tan(θ), where 2θ = 2·arcsin(λ/(2d))",
        "calculation": f"L = {ring_radius_mm:.3f} mm / tan({math.degrees(two_theta_rad/2):.4f}°) = {distance_mm:.3f} mm"
    })


# =============================================================================
# XRAYUTILITIES MATERIAL DATABASE TOOLS
# =============================================================================

@mcp.tool()
async def get_material_d_spacing(
    material: str,
    h: int,
    k: int,
    l: int
) -> str:
    """Get d-spacing for a crystallographic plane using xrayutilities material database.

    ⚠️ USE THIS FOR MATERIAL PROPERTIES - Uses verified crystallographic database!

    Args:
        material: Material name (Si, Al, Cu, Fe, Ni, CeO2, LaB6, Al2O3, etc.)
        h, k, l: Miller indices of the plane

    Returns:
        JSON with d-spacing, lattice parameters, and crystal structure

    Example:
        material="Si", h=1, k=1, l=1  → d = 3.1356 Å
        material="LaB6", h=1, k=0, l=0  → d = 4.1569 Å
    """
    try:
        import xrayutilities as xu
    except ImportError:
        return format_result({
            "status": "error",
            "error": "xrayutilities not installed"
        })

    # Get material from database
    try:
        mat = getattr(xu.materials, material)
    except AttributeError:
        # Try common aliases
        aliases = {
            'lab6': 'LaB6',
            'al2o3': 'Al2O3',
            'sapphire': 'Al2O3',
            'alumina': 'Al2O3'
        }
        material_name = aliases.get(material.lower(), material)
        try:
            mat = getattr(xu.materials, material_name)
        except AttributeError:
            available = [m for m in dir(xu.materials) if m[0].isupper() and not m.startswith('_')]
            return format_result({
                "status": "error",
                "error": f"Material '{material}' not found in xrayutilities database",
                "available_materials": available[:30]
            })

    # Calculate d-spacing
    hkl = (h, k, l)
    d_spacing = mat.planeDistance(hkl)

    # Get lattice parameters
    lattice_info = {
        "a": round(mat.lattice.a, 5),
    }
    if hasattr(mat.lattice, 'b'):
        lattice_info["b"] = round(mat.lattice.b, 5)
    if hasattr(mat.lattice, 'c'):
        lattice_info["c"] = round(mat.lattice.c, 5)

    return format_result({
        "status": "success",
        "material": material,
        "hkl": list(hkl),
        "d_spacing_angstroms": round(d_spacing, 6),
        "lattice_parameters": lattice_info,
        "method": "xrayutilities.materials.planeDistance()",
        "note": "Verified from crystallographic database"
    })


@mcp.tool()
async def calculate_bragg_angle_material(
    material: str,
    h: int,
    k: int,
    l: int,
    energy_kev: float = None,
    wavelength_angstroms: float = None
) -> str:
    """Calculate Bragg angle for a material reflection using xrayutilities.

    ⚠️ USE THIS FOR HEDM CALCULATIONS - Combines material database + Bragg's law!

    Args:
        material: Material name (Si, Fe, Cu, Ti, LaB6, Al2O3, etc.)
        h, k, l: Miller indices
        energy_kev: X-ray energy in keV (provide this OR wavelength)
        wavelength_angstroms: X-ray wavelength (provide this OR energy)

    Returns:
        JSON with 2θ angle, d-spacing, and Q-vector

    Example:
        material="Si", hkl=(1,1,1), energy_kev=61.332  → 2θ = 3.69°
        material="Fe", hkl=(1,1,0), wavelength_angstroms=0.2022  → 2θ = 5.07°
    """
    try:
        import xrayutilities as xu
        import numpy as np
    except ImportError:
        return format_result({
            "status": "error",
            "error": "xrayutilities or numpy not installed"
        })

    # Validate inputs
    if energy_kev is None and wavelength_angstroms is None:
        return format_result({
            "status": "error",
            "error": "Must provide either energy_kev or wavelength_angstroms"
        })

    if energy_kev is not None and wavelength_angstroms is not None:
        return format_result({
            "status": "error",
            "error": "Provide either energy OR wavelength, not both"
        })

    # Get wavelength
    if energy_kev is not None:
        wavelength = xu.en2lam(energy_kev * 1000)  # eV
    else:
        wavelength = wavelength_angstroms

    # Get material
    try:
        mat = getattr(xu.materials, material)
    except AttributeError:
        aliases = {'lab6': 'LaB6', 'al2o3': 'Al2O3', 'sapphire': 'Al2O3', 'alumina': 'Al2O3'}
        material_name = aliases.get(material.lower(), material)
        try:
            mat = getattr(xu.materials, material_name)
        except AttributeError:
            return format_result({
                "status": "error",
                "error": f"Material '{material}' not found"
            })

    # Calculate d-spacing and Q-vector
    hkl = (h, k, l)
    d_spacing = mat.planeDistance(hkl)
    q_vec = mat.Q(h, k, l)
    q_magnitude = np.linalg.norm(q_vec)

    # Calculate Bragg angle
    sin_arg = wavelength / (2.0 * d_spacing)
    if sin_arg > 1.0:
        return format_result({
            "status": "error",
            "error": f"No diffraction possible: λ/(2d) = {sin_arg:.4f} > 1",
            "note": "Wavelength too large for this reflection"
        })

    theta_rad = np.arcsin(sin_arg)
    theta_deg = np.degrees(theta_rad)
    two_theta_deg = 2.0 * theta_deg

    return format_result({
        "status": "success",
        "material": material,
        "hkl": list(hkl),
        "d_spacing_angstroms": round(d_spacing, 6),
        "wavelength_angstroms": round(wavelength, 6),
        "energy_kev": round(xu.lam2en(wavelength) / 1000, 6) if energy_kev is None else energy_kev,
        "two_theta_degrees": round(two_theta_deg, 6),
        "theta_degrees": round(theta_deg, 6),
        "q_magnitude_invA": round(q_magnitude, 6),
        "q_vector": [round(q, 6) for q in q_vec],
        "method": "xrayutilities material database + Bragg's law",
        "calculation": f"2θ = 2·arcsin(λ/(2d)) = 2·arcsin({wavelength:.4f}/(2×{d_spacing:.4f})) = {two_theta_deg:.4f}°"
    })


@mcp.tool()
async def list_common_materials() -> str:
    """List commonly used materials in HEDM available in xrayutilities database.

    Returns calibrants, structural materials, and semiconductors with lattice parameters.
    """
    try:
        import xrayutilities as xu
    except ImportError:
        return format_result({
            "status": "error",
            "error": "xrayutilities not installed"
        })

    # Categorized materials
    materials_info = {}

    # Common calibrants (only those in xrayutilities)
    calibrants = ['Si', 'LaB6', 'Al2O3', 'CaF2', 'BaF2']
    materials_info["Calibrants"] = {}
    for name in calibrants:
        try:
            mat = getattr(xu.materials, name, None)
            if mat:
                materials_info["Calibrants"][name] = {
                    "lattice_a": round(mat.lattice.a, 4),
                    "crystal_system": str(type(mat.lattice).__name__)
                }
        except:
            pass

    # Structural metals (only those in xrayutilities)
    metals = ['Fe', 'Al', 'Cu', 'Ti', 'Co', 'Cr', 'Ag', 'Au']
    materials_info["Metals"] = {}
    for name in metals:
        try:
            mat = getattr(xu.materials, name, None)
            if mat:
                materials_info["Metals"][name] = {
                    "lattice_a": round(mat.lattice.a, 4),
                    "crystal_system": str(type(mat.lattice).__name__)
                }
        except:
            pass

    # Get all available
    all_materials = [m for m in dir(xu.materials) if m[0].isupper() and not m.startswith('_')]

    return format_result({
        "status": "success",
        "materials_database": materials_info,
        "all_available": all_materials,
        "total_count": len(all_materials),
        "usage": "Use get_material_d_spacing(material, h, k, l) to get d-spacings",
        "note": "xrayutilities provides verified crystallographic data"
    })


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    print("Starting Analysis Utilities MCP Server...", file=sys.stderr)
    print("⚠️ These are custom diagnostic tools, NOT official MIDAS", file=sys.stderr)
    print("For MIDAS workflows, use midas_comprehensive_server.py", file=sys.stderr)
    mcp.run()
