"""Shared pytest fixtures.

`tmpbase` existed only inside test_ffhedm_graph.py's standalone main(), which
handed each test a tempfile.TemporaryDirectory(). Under pytest the fixture was
never defined, so its three tests ERRORed at setup and had never actually run --
including the one guarding "calibrate EXACTLY ONCE per chosen input", which is the
duplicate-calibration bug the file was written for. Defining it here makes
`python -m pytest tests/` do what the module docstring already promises.
"""
import shutil
import tempfile

import pytest


@pytest.fixture
def tmpbase():
    """A throwaway base directory, as a plain str (the tests os.path.join onto it)."""
    d = tempfile.mkdtemp(prefix="apexa-test-")
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)
