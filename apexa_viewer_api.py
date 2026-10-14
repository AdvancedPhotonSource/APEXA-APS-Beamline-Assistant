"""Threadpool-backed HTTP adapters for the scientific canvas."""
from collections import OrderedDict
import hashlib
import json
import threading
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from apexa_viewer_data import (inspect_sequence, inspect_image, read_frame,
                              correct_frame, frame_payload, structure_payload, simulated_rings)

router = APIRouter(prefix="/api/science")
_sequences = OrderedDict()
_lock = threading.Lock()


class SequenceRequest(BaseModel):
    path: str
    pattern: str = "*"
    dataset: str = ""


class FrameRequest(BaseModel):
    sequence_id: str
    index: int = Field(0, ge=0)
    dark_path: str = ""
    dark_dataset: str = ""
    dark_frame: int = Field(0, ge=0)
    mask_path: str = ""
    mask_dataset: str = ""
    corrected: bool = True
    vmin: float | None = None
    vmax: float | None = None
    log: bool = False
    colormap: str = "viridis"
    center_x: float | None = None
    center_y: float | None = None
    preview_max_side: int = Field(1024, ge=64, le=4096)


class RingRequest(BaseModel):
    path: str
    energy_kev: float = Field(gt=0)
    distance_mm: float = Field(gt=0)
    pixel_um: float = Field(gt=0)
    max_radius: float = Field(gt=0)


@router.get("/structure")
def structure(path: str):
    try:
        return structure_payload(path)
    except Exception as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/report")
def report(path: str):
    try:
        from apexa_report_viewer import report_payload
        return report_payload(path)
    except Exception as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/sequence")
def sequence(request: SequenceRequest):
    try:
        manifest = inspect_sequence(**request.model_dump())
        sid = uuid.uuid4().hex
        with _lock:
            _sequences[sid] = manifest
            while len(_sequences) > 32:
                _sequences.popitem(last=False)
        return {**manifest, "sequence_id": sid}
    except Exception as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/frame")
def frame(request: FrameRequest):
    try:
        with _lock:
            manifest = _sequences.get(request.sequence_id)
            if manifest is not None:
                _sequences.move_to_end(request.sequence_id)
        if manifest is None:
            raise ValueError("Sequence expired or server restarted; reload the sequence")
        index = request.index
        if index >= manifest["frames"]:
            raise ValueError("Frame outside the sequence")
        source = manifest["sources"][0]
        for candidate in manifest["sources"]:
            source = candidate
            if index < source["frames"]:
                break
            index -= source["frames"]
        raw = read_frame(source, index)
        dark = mask = None
        correction_sources = {}
        if request.corrected and request.dark_path:
            info = inspect_image(request.dark_path, request.dark_dataset)
            dark = read_frame(info, request.dark_frame)
            correction_sources["dark"] = {**info, "frame": request.dark_frame}
        if request.corrected and request.mask_path:
            info = inspect_image(request.mask_path, request.mask_dataset)
            if info["frames"] != 1:
                raise ValueError("Mask must contain exactly one frame")
            mask = read_frame(info, 0)
            correction_sources["mask"] = info
        corrected = correct_frame(raw, dark, mask)
        result = frame_payload(corrected, vmin=request.vmin, vmax=request.vmax,
                               log=request.log, colormap=request.colormap,
                               center_x=request.center_x, center_y=request.center_y,
                               preview_max_side=request.preview_max_side)
        context = {"source": source, "file_frame": index, "sequence_frame": request.index,
                   "corrections": correction_sources, "mask_convention": "nonzero = excluded",
                   "settings": request.model_dump(exclude={"sequence_id"}),
                   "display_limits": [result["vmin"], result["vmax"]]}
        key = hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest()[:20]
        return {**result, "context": context, "analysis_key": key, "filename": source["path"]}
    except Exception as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/rings")
def rings(request: RingRequest):
    try:
        return simulated_rings(**request.model_dump())
    except Exception as exc:
        raise HTTPException(422, str(exc)) from exc
