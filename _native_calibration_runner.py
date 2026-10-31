"""Isolate the optional native v1 calibration in an owned child process."""
from __future__ import annotations

import contextlib
import json
import sys


def main() -> int:
    request = json.load(sys.stdin)
    try:
        # Keep the protocol stdout limited to JSON even if the native engine
        # prints progress. This interpreter owns its thread/device environment.
        with contextlib.redirect_stdout(sys.stderr):
            from apexa_midas_native import native_autocalibrate
            result = native_autocalibrate(**request)
        print(json.dumps(result, allow_nan=False))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "error", "error": str(exc),
                          "exception_type": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
