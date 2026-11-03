#!/usr/bin/env python3
"""Measure actual SessionStore operations with synthetic exact plot arrays.

All storage is temporary. JSON results go to stdout; no source-tree outputs.
"""
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from apexa_sessions import SessionStore


def main():
    points = 250000
    artifact = {"id": "large", "type": "plotly", "title": "Synthetic measurement", "sourceMessageId": "",
                "data": {"data": [{"x": list(range(points)), "y": [i * .25 for i in range(points)]}]}}
    with tempfile.TemporaryDirectory(prefix="apexa-workspace-benchmark-") as folder:
        store = SessionStore(Path(folder))
        start = time.perf_counter()
        store.workspace("fixture", {"artifacts": [artifact]}, 0)
        first = time.perf_counter() - start
        timings = []
        for i in range(30):
            start = time.perf_counter()
            store.workspace("fixture", {"draft": str(i)}, i + 1, artifact_slots=[{"reuse": 0}])
            timings.append((time.perf_counter() - start) * 1000)
        full = store.get("fixture")
        lazy = store.get("fixture", lazy_artifacts=True)
        assert full["workspace"]["artifacts"][0] == artifact
        print(json.dumps({"fixture": "synthetic, 250000 exact x/y points", "samples": len(timings),
            "first_save_ms": first * 1000, "draft_save_p50_ms": statistics.median(timings),
            "draft_save_p95_ms": sorted(timings)[28], "session_manifest_bytes": store.path("fixture").stat().st_size,
            "full_session_response_bytes": len(json.dumps(full).encode()),
            "lazy_session_response_bytes": len(json.dumps(lazy).encode()),
            "exact_arrays_preserved": True, "scope": "local storage operations; no model, network or native science"}, indent=2))


if __name__ == "__main__":
    main()
