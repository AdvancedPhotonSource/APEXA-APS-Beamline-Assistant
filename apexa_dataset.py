"""Versioned acquisition snapshots; no inferred detector completion.

The event producer owns closure and frame identifiers. SHA-256 identities bind
local bytes to that declaration; they do not authenticate the event producer.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def file_identity(path, *, frame_ids=None, data_location=""):
    """Producer helper. Call only after closing the detector/correction writer."""
    path = Path(path).expanduser().resolve()
    before = path.stat()
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        raise ValueError(f"file changed while fingerprinting: {path}")
    result = {"path": str(path), "size_bytes": after.st_size, "sha256": digest}
    if frame_ids is not None:
        result.update(frame_ids=list(frame_ids), data_location=data_location)
    return result


def _strings(value, field, *, nonempty=False):
    if (not isinstance(value, list) or any(not isinstance(v, str) or not v for v in value)
            or len(set(value)) != len(value) or (nonempty and not value)):
        raise ValueError(f"{field} must be a list of distinct nonempty strings")


def _file(value, *, detector=False):
    if not isinstance(value, dict):
        raise ValueError("file identity must be an object")
    allowed = {"path", "size_bytes", "sha256"} | ({"frame_ids", "data_location"} if detector else set())
    if set(value) != allowed:
        raise ValueError(f"file identity requires exactly {sorted(allowed)}")
    if not isinstance(value["path"], str) or not Path(value["path"]).is_absolute():
        raise ValueError("file identity requires an absolute local path")
    if type(value["size_bytes"]) is not int or value["size_bytes"] <= 0:
        raise ValueError("file size_bytes must be positive")
    if not isinstance(value["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", value["sha256"]):
        raise ValueError("file requires a lowercase SHA-256 identity")
    if detector:
        _strings(value["frame_ids"], "frame_ids", nonempty=True)
        if not isinstance(value["data_location"], str):
            raise ValueError("data_location must be a string")
        if Path(value["path"]).suffix.lower() in (".h5", ".hdf5") and not value["data_location"]:
            raise ValueError("HDF5 input requires an explicit data_location")


class DatasetManifest:
    """Immutable, complete snapshot for one acquisition revision (schema 1).

    Partial snapshots are valid records but are never execution-ready. Every
    revision is a full snapshot, allowing delivery in arbitrary revision order.
    """
    def __init__(self, value):
        value = json.loads(canonical(value))
        required = {"schema_version", "acquisition_id", "revision", "producer", "closed",
                    "expected_frames", "files", "required_corrections", "corrections"}
        if not isinstance(value, dict) or set(value) != required:
            raise ValueError(f"manifest requires exactly {sorted(required)}")
        if type(value["schema_version"]) is not int or value["schema_version"] != 1:
            raise ValueError("unsupported dataset schema_version")
        for field in ("acquisition_id", "producer"):
            if not isinstance(value[field], str) or not value[field].strip() or len(value[field]) > 256:
                raise ValueError(f"{field} must be a nonempty string up to 256 characters")
        if type(value["revision"]) is not int or value["revision"] < 1:
            raise ValueError("revision must be a positive integer")
        if type(value["closed"]) is not bool:
            raise ValueError("closed must be an explicit boolean")
        _strings(value["expected_frames"], "expected_frames", nonempty=True)
        _strings(value["required_corrections"], "required_corrections")
        if not isinstance(value["files"], list) or not isinstance(value["corrections"], dict):
            raise ValueError("files must be a list and corrections an object")
        seen_frames, seen_paths = set(), set()
        for entry in value["files"]:
            _file(entry, detector=True)
            ids = set(entry["frame_ids"])
            if seen_frames & ids or entry["path"] in seen_paths:
                raise ValueError("duplicate detector frame or file identity")
            if not ids <= set(value["expected_frames"]):
                raise ValueError("observed frame is absent from expected_frames")
            seen_frames.update(ids)
            seen_paths.add(entry["path"])
        # Event array order is transport detail; expected_frames defines order.
        order = {frame: i for i, frame in enumerate(value["expected_frames"])}
        value["files"].sort(key=lambda item: order[item["frame_ids"][0]])
        for name, correction in value["corrections"].items():
            if (not isinstance(name, str) or not name or not isinstance(correction, dict)
                    or set(correction) != {"revision", "files"}
                    or type(correction["revision"]) is not int or correction["revision"] < 1
                    or not isinstance(correction["files"], list) or not correction["files"]):
                raise ValueError("correction requires positive revision and nonempty files")
            for entry in correction["files"]:
                _file(entry)
            if len({entry["path"] for entry in correction["files"]}) != len(correction["files"]):
                raise ValueError("duplicate correction file")
        value["required_corrections"].sort()
        self._encoded = canonical(value)

    @classmethod
    def from_dict(cls, value):
        return cls(value)

    def to_dict(self):
        return json.loads(self._encoded)

    @property
    def version(self):
        return hashlib.sha256(self._encoded.encode()).hexdigest()

    def issues(self):
        value = self.to_dict()
        issues = []
        if not value["closed"]:
            issues.append("acquisition writer has not declared closure")
        observed = [frame for file in value["files"] for frame in file["frame_ids"]]
        if set(observed) != set(value["expected_frames"]):
            issues.append(f"partial acquisition: {len(observed)}/{len(value['expected_frames'])} frames")
        elif observed != value["expected_frames"]:
            issues.append("frame order cannot be represented by whole-file execution")
        for name in value["required_corrections"]:
            if name not in value["corrections"]:
                issues.append(f"required correction is pending: {name}")
        return issues

    def verify_local(self):
        """Check closed snapshot content and physical detector frame counts.

        The source must remain immutable through execution. Verification cannot
        make a concurrently mutable filesystem atomic; see the runtime contract.
        """
        if issues := self.issues():
            raise ValueError("; ".join(issues))
        value = self.to_dict()
        files = value["files"] + [entry for corr in value["corrections"].values() for entry in corr["files"]]
        checked = {}
        for entry in files:
            path = entry["path"]
            identity = (entry["size_bytes"], entry["sha256"])
            if path in checked and checked[path] != identity:
                raise ValueError(f"conflicting file identities: {path}")
            if path not in checked:
                actual = file_identity(path)
                if (actual["size_bytes"], actual["sha256"]) != identity:
                    raise ValueError(f"input content no longer matches manifest: {path}")
                checked[path] = identity
            if "frame_ids" not in entry:
                continue
            suffix = Path(path).suffix.lower()
            if suffix in (".h5", ".hdf5"):
                import h5py
                with h5py.File(path, "r") as stream:
                    shape = stream[entry["data_location"]].shape
                    count = shape[0] if len(shape) == 3 else 1 if len(shape) == 2 else 0
            elif suffix in (".tif", ".tiff"):
                import tifffile
                with tifffile.TiffFile(path) as stream:
                    count = len(stream.pages)
            else:
                # v1 accepts one plain-text powder observation per file. Other
                # detector containers require a qualified frame-count adapter.
                if suffix not in (".xy", ".xye", ".dat", ".fxye", ".csv", ".txt"):
                    raise ValueError(f"unqualified detector frame-count format: {suffix}")
                count = 1
            if count != len(entry["frame_ids"]):
                raise ValueError(f"detector frame count mismatch: {path}")
