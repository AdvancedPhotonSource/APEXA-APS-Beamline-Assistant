"""The reply label's colour ladder.

Every other colour in the CLI is a 16-colour SGR code, which every terminal
since the VT100 renders. The reply label is the one place using 256-colour and
truecolor, so it is the one place that can emit an escape an old beamline
console prints as literal junk. These tests pin each rung.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import argo_mcp_client as A

TRUECOLOR = "\033[38;2;59;130;246m"
C256      = "\033[38;5;69m"
C16       = "\033[94m"


@pytest.fixture
def tty(monkeypatch):
    """A terminal, with the environment otherwise cleared."""
    monkeypatch.setattr(A.sys.stdout, "isatty", lambda: True, raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("COLORTERM", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")


def test_truecolor_terminal_gets_the_modern_blue(tty, monkeypatch):
    monkeypatch.setenv("COLORTERM", "truecolor")
    assert A._reply_color() == TRUECOLOR


def test_24bit_is_also_accepted(tty, monkeypatch):
    monkeypatch.setenv("COLORTERM", "24bit")
    assert A._reply_color() == TRUECOLOR


def test_256_colour_terminal_gets_the_nearest_256_neighbour(tty):
    assert A._reply_color() == C256


def test_an_old_console_never_sees_a_256_colour_escape(tty, monkeypatch):
    """The whole point: a vt100 would print \\033[38;5;69m as visible junk."""
    monkeypatch.setenv("TERM", "vt100")
    out = A._reply_color()
    assert "38;5;" not in out and "38;2;" not in out
    assert out == C16, "must still be blue, just a 16-colour blue"


def test_a_dumb_terminal_gets_no_escape_at_all(tty, monkeypatch):
    monkeypatch.setenv("TERM", "dumb")
    assert A._reply_color() == ""


def test_an_empty_TERM_gets_no_escape(tty, monkeypatch):
    monkeypatch.setenv("TERM", "")
    assert A._reply_color() == ""


def test_a_logfile_gets_no_escape(monkeypatch):
    """Redirecting APEXA's output to a file is routine at a beamline."""
    monkeypatch.setattr(A.sys.stdout, "isatty", lambda: False, raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    assert A._reply_color() == ""


def test_NO_COLOR_is_honoured(tty, monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    assert A._reply_color() == ""
    monkeypatch.setenv("NO_COLOR", "")       # the standard: set, even if empty
    assert A._reply_color() == ""


def test_detection_never_raises_when_stdout_has_no_isatty(monkeypatch):
    """Under capture, stdout is sometimes a bare object. Must not explode at
    import time -- this runs in the class body."""
    class Bare:
        pass
    monkeypatch.setattr(A.sys, "stdout", Bare())
    assert A._reply_color() == ""


def test_the_reply_blue_is_distinct_from_the_tool_marker_cyan():
    """The label exists to separate the answer from the steps above it."""
    for blue in (TRUECOLOR, C256, C16):
        assert blue != A.C.CYAN and blue != A.C.BCYAN


def test_every_other_cli_colour_is_universally_safe():
    """Guard against someone adding a second 256-colour code without a ladder:
    it would render as junk on the consoles this ladder exists to protect."""
    import re
    src = Path(A.__file__).read_text()
    body = src[src.index("class C:"):src.index("def _reply_color")]
    exotic = re.findall(r"\\033\[38;[25];", body)
    assert not exotic, f"256/truecolor code in class C outside the ladder: {exotic}"
