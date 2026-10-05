#!/usr/bin/env python3
"""
APEXA - Advanced Photon EXperiment Assistant
AI-powered beamline scientist for synchrotron X-ray diffraction analysis

Developed for: Advanced Photon Source, Argonne National Laboratory
Author: Pawan Tripathi
"""

import asyncio
import json
import os
import sys
import shutil
import re
import signal
import contextlib
import psutil
from typing import Optional, Dict, Any, List
from contextlib import AsyncExitStack

# Force UTF-8 stdio so the CLI runs cleanly on Windows, whose console defaults to
# cp1252 and raises UnicodeEncodeError on the ⚠/°/µ/→/✓ status prints (which would
# otherwise abort a turn and can surface as a degenerate/empty response).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass
from datetime import datetime
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.shortcuts import CompleteStyle
from prompt_toolkit.formatted_text import FormattedText

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from dotenv import load_dotenv
from apexa_agents import ArgoProvider, OrchestratorAgent, DEV_ONLY_MODELS, select_provider
from apexa_timing import query_scope, record_tool_call
import time as _time

load_dotenv()


# ── ANSI terminal styling ──────────────────────────────────────────────────────
class C:
    """ANSI escape codes for terminal styling."""
    BOLD      = "\033[1m"
    DIM       = "\033[2m"
    ITALIC    = "\033[3m"
    UNDERLINE = "\033[4m"
    RESET     = "\033[0m"
    # Foreground
    RED       = "\033[31m"
    GREEN     = "\033[32m"
    YELLOW    = "\033[33m"
    BLUE      = "\033[34m"
    MAGENTA   = "\033[35m"
    CYAN      = "\033[36m"
    WHITE     = "\033[37m"
    GRAY      = "\033[90m"
    # Bright
    BRED      = "\033[91m"
    BGREEN    = "\033[92m"
    BYELLOW   = "\033[93m"
    BBLUE     = "\033[94m"
    BMAGENTA  = "\033[95m"
    BCYAN     = "\033[96m"


def clean_markdown(text: str) -> str:
    """Render markdown as ANSI-colored terminal output."""
    import re

    lines = text.split('\n')
    out = []
    in_code_block = False
    code_lang = ''

    for line in lines:
        # Code fence toggle
        if re.match(r'^```', line):
            if not in_code_block:
                in_code_block = True
                code_lang = line[3:].strip()
                if code_lang:
                    out.append(f"  {C.DIM}─── {code_lang} ───{C.RESET}")
                else:
                    out.append(f"  {C.DIM}───────────{C.RESET}")
                continue
            else:
                in_code_block = False
                out.append(f"  {C.DIM}───────────{C.RESET}")
                continue

        if in_code_block:
            out.append(f"  {C.CYAN}{line}{C.RESET}")
            continue

        # Headers: # / ## / ###
        m = re.match(r'^(#{1,6})\s+(.+)$', line)
        if m:
            level = len(m.group(1))
            content = _inline_format(m.group(2))
            if level == 1:
                out.append(f"\n{C.BOLD}{C.BBLUE}{content.upper()}{C.RESET}")
                out.append(f"{C.BBLUE}{'━' * min(len(m.group(2)) + 4, 50)}{C.RESET}")
            elif level == 2:
                out.append(f"\n{C.BOLD}{C.BCYAN}{content}{C.RESET}")
                out.append(f"{C.DIM}{'─' * min(len(m.group(2)) + 2, 40)}{C.RESET}")
            else:
                out.append(f"\n  {C.BOLD}{C.WHITE}{content}{C.RESET}")
            continue

        # Numbered section headers: "1) Title" or "1. Title" at top level (no indent)
        m = re.match(r'^(\d+)[.)]\s+(.+)$', line)
        if m:
            num = m.group(1)
            content = _inline_format(m.group(2))
            out.append(f"\n{C.BOLD}{C.BCYAN}{num}.{C.RESET} {C.BOLD}{C.WHITE}{content}{C.RESET}")
            continue

        # Horizontal rules
        if re.match(r'^[-*_]{3,}\s*$', line):
            out.append(f"{C.DIM}{'─' * 40}{C.RESET}")
            continue

        # Blockquotes
        if line.startswith('>'):
            content = line.lstrip('>').strip()
            content = _inline_format(content)
            out.append(f"  {C.CYAN}│{C.RESET} {C.ITALIC}{content}{C.RESET}")
            continue

        # Bullet lists — markdown (* / -) or literal (• / ▸ / ‣)
        m = re.match(r'^(\s*)(?:[*-]|[•▸‣])\s+(.+)$', line)
        if m:
            indent = m.group(1)
            depth = len(indent) // 2
            content = _inline_format(m.group(2))
            if depth == 0:
                out.append(f"  {C.BCYAN}▸{C.RESET} {content}")
            elif depth == 1:
                out.append(f"    {C.BLUE}•{C.RESET} {content}")
            else:
                out.append(f"      {C.GRAY}◦{C.RESET} {C.DIM}{content}{C.RESET}")
            continue

        # Numbered lists (indented, e.g. "  1. item")
        m = re.match(r'^(\s+)(\d+)[.)]\s+(.+)$', line)
        if m:
            indent = m.group(1)
            num = m.group(2)
            content = _inline_format(m.group(3))
            out.append(f"{indent}  {C.BCYAN}{num}.{C.RESET} {content}")
            continue

        # Table separator: skip
        if re.match(r'^\|[-:| ]+\|\s*$', line):
            continue

        # Table rows
        if re.match(r'^\|.+\|\s*$', line):
            cells = [c.strip() for c in line.strip('|').split('|')]
            formatted = f"  {C.DIM}│{C.RESET} ".join(_inline_format(c) for c in cells)
            out.append(f"  {C.DIM}│{C.RESET} {formatted} {C.DIM}│{C.RESET}")
            continue

        # Regular line: apply inline formatting
        if line.strip():
            out.append(_inline_format(line))
        else:
            out.append('')

    result = '\n'.join(out)
    # Collapse 3+ blank lines
    result = re.sub(r'\n{3,}', '\n\n', result)
    return result.strip()


def _inline_format(text: str) -> str:
    """Apply inline markdown formatting with ANSI codes."""
    import re
    # Bold: **text** → bright white bold (stands out from normal text)
    text = re.sub(r'\*\*(.+?)\*\*', f'{C.BOLD}{C.WHITE}\\1{C.RESET}', text)
    text = re.sub(r'__(.+?)__', f'{C.BOLD}{C.WHITE}\\1{C.RESET}', text)
    # Italic: *text*
    text = re.sub(r'(?<!\w)\*(.+?)\*(?!\w)', f'{C.ITALIC}{C.BCYAN}\\1{C.RESET}', text)
    # Strikethrough: ~~text~~
    text = re.sub(r'~~(.+?)~~', f'{C.DIM}\\1{C.RESET}', text)
    # Inline code: `code` → cyan with dim background feel
    text = re.sub(r'`([^`]+)`', f'{C.BOLD}{C.CYAN}\\1{C.RESET}', text)
    # Links: [text](url)
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', f'{C.UNDERLINE}{C.BBLUE}\\1{C.RESET} {C.DIM}(\\2){C.RESET}', text)
    # Images: ![alt](url)
    text = re.sub(r'!\[([^\]]*)\]\([^)]+\)', f'{C.DIM}[Image: \\1]{C.RESET}', text)
    # Inline academic citations: (Author Year) / (Author et al. Year) / (Author Year, p.36)
    # Handles multi-word surnames (Von Dreele), two-author (&/and), et al., page refs.
    # Surname: capitalized word, optionally followed by one more capitalized word
    # (handles "Von Dreele", "Le Roy", "Mac Donald") or a lowercase particle + word
    # (handles "van der Waals", "de la Cruz")
    _SURNAME = (
        r'[A-Z][A-Za-zÀ-ſ\-]+'
        r'(?:\s+(?:[A-Z][A-Za-zÀ-ſ\-]+|(?:van|von|de|der|den|du|le|la|di)\s+[A-Z][A-Za-zÀ-ſ\-]+))?'
    )
    citation_re = re.compile(
        r'\(('
        + _SURNAME
        + r'(?:\s*(?:&|and)\s*' + _SURNAME + r')?'         # Optional second author
        + r'(?:\s+et\s+al\.?)?'                            # Optional "et al."
        + r'\s*,?\s*'
        + r'(?:19|20)\d{2}[a-z]?'                          # Year
        + r'(?:[,;]\s*p{1,2}\.?\s*\d+(?:[-–]\d+)?)?'     # Optional page
        + r')\)'
    )
    text = citation_re.sub(f'{C.BYELLOW}(\\1){C.RESET}', text)
    # "Author (YEAR)" form (References list lines): "Bernier et al. (2020). Annu. Rev..."
    text = re.sub(
        r'\b(' + _SURNAME + r'(?:\s+et\s+al\.?)?)\s+\(((?:19|20)\d{2}[a-z]?)\)',
        f'{C.BOLD}\\1{C.RESET} {C.BYELLOW}(\\2){C.RESET}',
        text,
    )
    # DOI strings
    text = re.sub(
        r'\b(DOI:\s*)(10\.\d{4,9}/[^\s,)\]]+)',
        f'{C.DIM}\\1{C.RESET}{C.UNDERLINE}{C.BYELLOW}\\2{C.RESET}',
        text,
        flags=re.IGNORECASE,
    )
    return text


def _print_help():
    """Print styled help text for the CLI."""
    def _sec(title):
        print(f"\n  {C.BOLD}{C.BBLUE}{title}{C.RESET}")
        print(f"  {C.DIM}{'─' * 50}{C.RESET}")

    def _cmd(cmd, desc):
        print(f"  {C.CYAN}{cmd:<40}{C.RESET} {C.DIM}{desc}{C.RESET}")

    def _ex(text):
        print(f"  {C.BLUE}•{C.RESET} {C.DIM}{text}{C.RESET}")

    def _note(text):
        print(f"  {C.DIM}{text}{C.RESET}")

    print(f"\n  {C.BOLD}{C.BCYAN}APEXA{C.RESET} {C.DIM}Command Reference{C.RESET}")

    _sec("Analysis & Processing")
    _cmd("analyze <query>", "AI-powered analysis")
    _cmd("batch integrate <pattern> with ...", "Process multiple files")
    _cmd("workflow list | <name>", "Predefined workflows")

    _sec("Image Analysis")
    _cmd("image analyze <file>", "Full image analysis with AI")
    _cmd("image quality <file>", "Check signal, noise, saturation")
    _cmd("image rings <file>", "Detect diffraction rings")

    _sec("Plotting & Visualization")
    _cmd("plot 2d <file>", "Plot 2D diffraction image")
    _cmd("plot radial <file>", "Plot radial intensity profile")
    _cmd("plot 1d <file>", "Plot 1D integrated pattern")
    _cmd("plot compare <f1> <f2> ...", "Compare multiple patterns")

    _sec("Monitoring")
    _cmd("monitor start <directory>", "Watch for new images")
    _cmd("monitor stop | status | check", "Control monitoring")

    _sec("Sessions")
    _cmd("session new [name]", "Archive current & start a fresh session")
    _cmd("session save [name]", "Save current session (with conversation)")
    _cmd("session load <name>", "Load & resume a saved session (turns append to it)")
    _cmd("session switch <name>", "Switch active session (protects unsaved scratch work)")
    _cmd("session append <name>", "Merge current turns into an existing session & continue")
    _cmd("session resume", "Resume last session (autosaved on exit)")
    _cmd("session current", "Show the active session (name + message count)")
    _cmd("session list | summary", "Manage sessions (active marked ●)")
    _cmd("clear tools | forget tools", "Drop tool-call history (keep prose) — un-poison context")

    _sec("Configuration")
    _cmd("models", "Show available AI models")
    _cmd("model <name>", "Switch AI model")
    _cmd("tools", "List all analysis tools")
    _cmd("servers", "Show connected servers")
    _cmd("stats", "Interaction log stats")
    _cmd("timing", "Toggle API response timing")
    _cmd("clear", "Clear conversation history")
    _cmd("Ctrl-C", "Abort the running command (kills its compute); at idle prompt, quits")
    _cmd("quit", "Exit APEXA")

    _sec("Aborting a running command")
    _note("Ctrl-C during a command aborts it and kills that server's compute tree,")
    _note("then returns you to the prompt. Ctrl-C at the idle prompt exits APEXA.")
    _note("NOTE: abort is not a motion e-stop — a move already sent to the EPICS IOC")
    _note("keeps going; use 'stop' / stop_motor to halt motor motion.")

    _sec("Shell Commands (direct, no prefix needed)")
    _cmd("pwd, cd, cat, head, tail, grep ...", "Standard Unix commands")
    _cmd("git, python, uv, conda ...", "Dev tools")
    _cmd("caget, caput, camonitor ...", "EPICS commands")
    _cmd("./script.sh, /path/to/binary", "Run executables directly")

    _sec("Natural Language Examples")
    _ex('"Integrate data.ge5 with dark.ge5 using calib.txt"')
    _ex('"What are the peaks at 12.5, 18.2, 25.8 degrees?"')
    _ex('"Run FF-HEDM workflow in /path/to/data"')
    _ex('"Calibrate detector using CeO2 standard"')
    _ex('"Show motor positions"')

    print(f"\n  {C.DIM}↑/↓ command history  •  Enter to send  •  Ctrl+C to exit{C.RESET}\n")


# Shell commands recognized for direct execution (no 'run' prefix needed)
_SHELL_COMMANDS = {
    'ls', 'pwd', 'cat', 'head', 'tail', 'less', 'more',
    'cp', 'mv', 'mkdir', 'rmdir', 'touch', 'chmod',
    'grep', 'find', 'wc', 'sort', 'uniq', 'diff', 'sed', 'awk',
    'du', 'df', 'free', 'ps', 'which', 'whoami',
    'echo', 'date', 'hostname', 'uname', 'env',
    'tar', 'gzip', 'gunzip', 'zip', 'unzip',
    'curl', 'wget', 'ssh', 'scp', 'rsync',
    'git', 'python', 'python3', 'pip', 'uv', 'conda',
    'make', 'cmake', 'gcc', 'g++',
    'tree', 'file', 'stat', 'ln', 'readlink',
    'nproc', 'lscpu', 'uptime',
    'caget', 'caput', 'camonitor', 'cainfo',
}

# Shell-ish signals in the ARGUMENTS: a flag, a pipe/redirect/separator, a glob,
# or a quoted string. Natural language essentially never contains these.
_SHELLISH_RE = re.compile(r'[|><;&$*]|(?:^|\s)-{1,2}\w|["\']')


def _is_shell_command(user_input: str) -> bool:
    """Check if input looks like a direct shell command.

    Matching on the first word alone was too eager: `which`, `find`, `date`,
    `file`, `make`, `sort`, `tree` and `ps` are all ordinary English openers, so
    "which model are you?" was executed as `which` instead of being answered.
    Require the REST of the line to look like arguments too.
    """
    if not user_input:
        return False
    stripped = user_input.strip()
    first_word = stripped.split()[0].rstrip(';')

    # Paths to executables are unambiguous: ./script.sh, /usr/bin/x, ~/bin/tool
    if first_word.startswith(('./', '/', '~/')):
        return True
    if first_word not in _SHELL_COMMANDS:
        return False

    rest = stripped[len(first_word):].strip()
    if not rest:
        return True                      # bare `ls`, `pwd`, `date`
    if stripped.endswith('?'):
        return False                     # a question, not a command
    if _SHELLISH_RE.search(rest):
        return True                      # flags, pipes, globs, quotes
    if '/' in rest or '.' in rest.split()[0]:
        return True                      # a path or a filename argument
    # No shell signals left: short means command (`git status`), long means prose
    # ("find my grains in scan5", "sort the results by grain size").
    return len(rest.split()) <= 2


class ExperimentContext:
    """Smart context manager for APEXA sessions"""
    def __init__(self, session_dir: Path = None):
        self.session_dir = session_dir or Path.home() / ".apexa" / "sessions"
        self.session_dir.mkdir(parents=True, exist_ok=True)

        self.metadata = {
            "experiment_id": None,
            "sample_name": None,
            "beamline": None,
            "start_time": datetime.now().isoformat(),
            "user": os.getenv("ANL_USERNAME", "unknown"),
            "current_directory": str(Path.cwd()),
            "analysis_history": [],
            "key_findings": [],
            "active_files": []
        }
        # Conversation transcript stashed by load_session() for the caller to
        # replay into the orchestrator. Empty until a session is loaded.
        self.loaded_conversation: List[Dict] = []
        # Compacted running summary stashed by load_session() (the digest of
        # turns older than the recent window). Empty until a session is loaded.
        self.loaded_summary: str = ""

        # Append-only transcript model: exactly one "active" session is being
        # written at a time. Starts as the rolling autosave slot; a named
        # `session save` switches the active session to that name.
        self.active_session: str = "_autosave"
        # Pointer to the most-recently-active session so `session resume`
        # (no name) can find it across restarts.
        self._last_pointer = self.session_dir / ".last_session"

    def _transcript_file(self, name: str) -> Path:
        """Path to a session's append-only JSONL transcript."""
        return self.session_dir / f"{name}.jsonl"

    def _remember_active(self):
        """Record the active session name so `session resume` can find it."""
        try:
            self._last_pointer.write_text(self.active_session)
        except Exception:
            pass  # pointer is a convenience, never fatal

    def last_active(self) -> str:
        """Most-recently-active session name (for `session resume`)."""
        try:
            name = self._last_pointer.read_text().strip()
            return name or "_autosave"
        except Exception:
            return "_autosave"

    def start_new(self, name: str = None) -> str:
        """Wind up the current session and begin a fresh, empty one.

        Only resets the *active* slot so subsequent turns start from a clean
        transcript — archive the previous conversation first (``save_session``)
        if it is worth keeping. A named new session must not clobber an existing
        one; the unnamed case reuses the rolling ``_autosave`` slot (wiped clean).
        """
        self.loaded_conversation = []
        self.loaded_summary = ""
        if name:
            if (self._transcript_file(name).exists()
                    or (self.session_dir / f"{name}.json").exists()):
                raise FileExistsError(name)
            self.active_session = name
        else:
            self.active_session = "_autosave"
            tf = self._transcript_file("_autosave")
            try:
                if tf.exists():
                    tf.unlink()
            except Exception:
                pass
        self._remember_active()
        return self.active_session

    def append_to(self, target_name: str, conversation: List[Dict],
                  summary: str = None) -> int:
        """Non-destructively merge the current conversation into an existing
        session, then continue in it.

        Unlike ``save_session`` (which copies/overwrites the target transcript),
        this *appends* the given turns after the target's existing transcript —
        so carrying scratch work into a named session never clobbers what is
        already there. The target becomes the active session; subsequent turns
        keep appending to it.

        Returns the target transcript's total message count after the merge.
        """
        # Already writing into the target — nothing to carry over.
        if target_name == self.active_session:
            return len(self.read_transcript(target_name))

        source_was_scratch = (self.active_session == "_autosave")

        # Switch active to the target FIRST so append_message() (which writes to
        # the active slot) lands after the target's existing turns.
        self.active_session = target_name
        for msg in conversation:
            role = msg.get("role")
            content = msg.get("content")
            if role is not None and content is not None:
                self.append_message(role, content)

        # Drain the scratch slot so its turns can't be double-merged later.
        if source_was_scratch:
            self.discard_scratch()

        # Restore state mirrors load_session(): the full merged transcript is the
        # canonical conversation, and the summary comes from the caller or the
        # target's sidecar.
        self.loaded_conversation = self.read_transcript(target_name)
        if summary is not None:
            self.loaded_summary = summary
        else:
            self.loaded_summary = ""
            sidecar = self.session_dir / f"{target_name}.json"
            if sidecar.exists():
                try:
                    with open(sidecar) as f:
                        self.loaded_summary = json.load(f).get("running_summary", "")
                except Exception:
                    pass
        self._remember_active()
        # loaded_conversation is now the full merged transcript (existing + new).
        return len(self.loaded_conversation)

    def discard_scratch(self):
        """Wipe the rolling autosave scratch transcript."""
        tf = self._transcript_file("_autosave")
        try:
            if tf.exists():
                tf.unlink()
        except Exception:
            pass

    def append_message(self, role: str, content: str):
        """Append one message to the active session's append-only transcript.

        This is the source of truth for the conversation. Each line is a
        self-contained JSON record, flushed and fsync'd immediately, so a
        crash (even kill -9) loses at most the single in-flight message and
        never corrupts earlier turns — the modern JSONL transcript pattern.
        """
        try:
            rec = {
                "ts": datetime.now().isoformat(),
                "role": role,
                "content": content,
            }
            target = self._transcript_file(self.active_session)
            with open(target, "a") as f:
                f.write(json.dumps(rec) + "\n")
                f.flush()
                os.fsync(f.fileno())
            self._remember_active()
        except Exception as e:
            print(f"  {C.DIM}(transcript append skipped: {e}){C.RESET}",
                  file=sys.stderr)

    def read_transcript(self, name: str) -> List[Dict]:
        """Read a full append-only transcript back as [{role, content}, ...].

        Tolerates a torn final line (from a crash mid-write) by skipping
        any line that does not parse — earlier lines are always intact.
        """
        target = self._transcript_file(name)
        if not target.exists():
            return []
        msgs: List[Dict] = []
        with open(target) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue  # torn final line — ignore, keep the rest
                if isinstance(rec, dict) and "role" in rec and "content" in rec:
                    msgs.append({"role": rec["role"], "content": rec["content"]})
        return msgs

    def update(self, key: str, value: Any):
        """Update experiment metadata"""
        self.metadata[key] = value

    def add_analysis(self, analysis_type: str, result: str):
        """Record analysis performed"""
        self.metadata["analysis_history"].append({
            "timestamp": datetime.now().isoformat(),
            "type": analysis_type,
            "result": result[:500]  # Truncate long results
        })

    def add_finding(self, finding: str):
        """Record key scientific finding"""
        self.metadata["key_findings"].append({
            "timestamp": datetime.now().isoformat(),
            "finding": finding
        })

    def save_session(self, session_name: str = None,
                     conversation: List[Dict] = None,
                     summary: str = None):
        """Save current session to disk.

        Writes the experiment metadata AND, when provided, the full
        conversation transcript so the session can be genuinely resumed
        (the model sees its prior turns), not just summarised.
        """
        if not session_name:
            session_name = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        # Build the metadata sidecar without mutating live metadata. The JSONL
        # transcript (written per-message) is the source of truth for the
        # conversation; conversation_history here is a convenience snapshot.
        record = dict(self.metadata)
        record["saved_at"] = datetime.now().isoformat()
        if conversation is not None:
            record["conversation_history"] = conversation
        if summary is not None:
            record["running_summary"] = summary

        session_file = self.session_dir / f"{session_name}.json"
        # Atomic write: tmp then replace, so an interrupted save never leaves a
        # half-written (unparseable) sidecar.
        tmp_file = session_file.with_suffix(".json.tmp")
        with open(tmp_file, 'w') as f:
            json.dump(record, f, indent=2)
        os.replace(tmp_file, session_file)

        # Named checkpoint: copy the active append-only transcript under the new
        # name and switch the active session to it, so subsequent turns continue
        # appending to <name>.jsonl. (No-op when saving the already-active slot,
        # e.g. autosave.)
        if session_name != self.active_session:
            src = self._transcript_file(self.active_session)
            dst = self._transcript_file(session_name)
            try:
                if src.exists() and src.resolve() != dst.resolve():
                    shutil.copyfile(src, dst)
            except Exception as e:
                print(f"  {C.DIM}(transcript checkpoint skipped: {e}){C.RESET}",
                      file=sys.stderr)
            self.active_session = session_name
        self._remember_active()

        # Full transcript becomes the canonical restored conversation.
        self.loaded_conversation = self.read_transcript(session_name) or \
            (list(conversation) if conversation else [])
        return session_file

    def load_session(self, session_name: str):
        """Load a previous session and make it the active one.

        Prefers the full append-only JSONL transcript; falls back to the
        conversation snapshot embedded in the .json sidecar (older sessions
        saved before JSONL existed). Stashes the restored conversation on
        self.loaded_conversation for the caller to hand to the orchestrator.
        Returns True if either artifact exists.
        """
        json_file = self.session_dir / f"{session_name}.json"
        jsonl_file = self._transcript_file(session_name)
        if not json_file.exists() and not jsonl_file.exists():
            return False

        snapshot_fallback: List[Dict] = []
        self.loaded_summary = ""
        if json_file.exists():
            with open(json_file, 'r') as f:
                data = json.load(f)
            snapshot_fallback = data.pop("conversation_history", [])
            self.loaded_summary = data.pop("running_summary", "")
            self.metadata = data

        # Prefer the complete transcript; fall back to the sidecar snapshot.
        transcript = self.read_transcript(session_name)
        self.loaded_conversation = transcript if transcript else snapshot_fallback

        # Continue this session: subsequent turns append to its transcript.
        self.active_session = session_name
        self._remember_active()
        return True

    def list_sessions(self) -> List[str]:
        """List all saved sessions (the _autosave slot is hidden)."""
        names = {f.stem for f in self.session_dir.glob("*.json")}
        names |= {f.stem for f in self.session_dir.glob("*.jsonl")}
        return sorted(n for n in names if n != "_autosave")

    def get_summary(self) -> str:
        """Get a summary of current experiment"""
        summary = f"Experiment: {self.metadata.get('experiment_id', 'Unnamed')}\n"
        summary += f"Sample: {self.metadata.get('sample_name', 'N/A')}\n"
        summary += f"Analyses performed: {len(self.metadata['analysis_history'])}\n"
        summary += f"Key findings: {len(self.metadata['key_findings'])}\n"
        return summary

class ProactiveSuggestions:
    """Generate smart next-step suggestions based on analysis results"""

    @staticmethod
    def suggest_after_phase_id(phases_found: List[str]) -> str:
        """Suggest next steps after phase identification"""
        suggestions = []

        if len(phases_found) == 1:
            suggestions.append("📊 **Suggested next steps:**")
            suggestions.append("• Quantify phase fraction using Rietveld refinement")
            suggestions.append("• Check for preferred orientation (texture analysis)")
            suggestions.append("• Calculate lattice parameters and compare to literature")
        elif len(phases_found) > 1:
            suggestions.append("📊 **Suggested next steps:**")
            suggestions.append("• Quantify relative phase fractions")
            suggestions.append("• Map phase distribution (if using HEDM)")
            suggestions.append("• Analyze phase transformation conditions")

        return "\n".join(suggestions)

    @staticmethod
    def suggest_after_ring_detection(num_rings: int) -> str:
        """Suggest next steps after ring detection"""
        suggestions = ["📊 **Suggested next steps:**"]

        if num_rings > 5:
            suggestions.append("• Integrate rings to 1D pattern for phase ID")
            suggestions.append("• Check calibration quality (ring circularity)")
            suggestions.append("• Perform full FF-HEDM reconstruction")
        else:
            suggestions.append("• Check if sample is single crystal (few rings)")
            suggestions.append("• Verify detector calibration")
            suggestions.append("• Consider if more exposure time needed")

        return "\n".join(suggestions)

    @staticmethod
    def suggest_after_ff_hedm(num_grains: int) -> str:
        """Suggest next steps after FF-HEDM reconstruction"""
        suggestions = ["📊 **Suggested next steps:**"]
        suggestions.append(f"• Analyze grain size distribution ({num_grains} grains found)")
        suggestions.append("• Calculate grain orientations and texture")
        suggestions.append("• Track grains through deformation series (if applicable)")
        suggestions.append("• Export to DREAM.3D for visualization")
        suggestions.append("• Calculate misorientation statistics")

        return "\n".join(suggestions)

    @staticmethod
    def suggest_after_integration() -> str:
        """Suggest next steps after 2D to 1D integration"""
        return """📊 **Suggested next steps:**
• Identify phases from peak positions
• Perform Rietveld refinement
• Check for peak splitting (sample stress/strain)
• Compare with reference patterns"""

    @staticmethod
    def get_suggestion(tool_name: str, result: str) -> Optional[str]:
        """Get proactive suggestion based on tool used"""

        # Parse result to extract key info
        if "identify_crystalline_phases" in tool_name:
            # Count phases mentioned in result
            phases = []
            if "phase" in result.lower():
                return ProactiveSuggestions.suggest_after_phase_id(["phase"])

        elif "detect_diffraction_rings" in tool_name:
            # Try to extract number of rings (uses module-level `re`; a local
            # `import re` here would make `re` function-local for the WHOLE method
            # and leave the run_ff_hedm branch below with an unbound `re`).
            match = re.search(r'(\d+)\s+rings?', result.lower())
            num_rings = int(match.group(1)) if match else 5
            return ProactiveSuggestions.suggest_after_ring_detection(num_rings)

        elif "run_ff_hedm" in tool_name:
            match = re.search(r'(\d+)\s+grains?', result.lower())
            num_grains = int(match.group(1)) if match else 0
            return ProactiveSuggestions.suggest_after_ff_hedm(num_grains)

        elif "integrate_2d_to_1d" in tool_name:
            return ProactiveSuggestions.suggest_after_integration()

        return None

class BatchProcessor:
    """Smart batch processing for multiple files"""

    @staticmethod
    async def process_batch(client, operation: str, files: List[str], **kwargs) -> Dict[str, Any]:
        """Process multiple files with the same operation

        Args:
            client: APEXAClient instance
            operation: Tool name to execute
            files: List of file paths
            **kwargs: Additional arguments for the tool

        Returns:
            Dictionary with results for each file
        """
        results = {
            "operation": operation,
            "total_files": len(files),
            "successful": 0,
            "failed": 0,
            "results": []
        }

        for i, file_path in enumerate(files, 1):
            print(f"\n[{i}/{len(files)}] Processing: {Path(file_path).name}")

            try:
                # Merge file path with other arguments
                args = {**kwargs, "image_path": file_path}
                result = await client.execute_tool_call(operation, args)

                results["results"].append({
                    "file": file_path,
                    "status": "success",
                    "result": result
                })
                results["successful"] += 1

            except Exception as e:
                results["results"].append({
                    "file": file_path,
                    "status": "failed",
                    "error": str(e)
                })
                results["failed"] += 1

        return results

class ErrorPreventor:
    """Validate inputs and prevent common errors before execution"""

    @staticmethod
    def validate_ff_hedm_params(args: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate parameters for FF-HEDM workflow"""
        example_dir = args.get("example_dir")

        if not example_dir:
            return False, "example_dir parameter is required"

        dir_path = Path(example_dir).expanduser()
        if not dir_path.exists():
            return False, f"Directory not found: {example_dir}"

        if not dir_path.is_dir():
            return False, f"Path is not a directory: {example_dir}"

        # Check for Parameters.txt
        param_file = dir_path / "Parameters.txt"
        if not param_file.exists():
            return False, f"Parameters.txt not found in {example_dir}"

        return True, None

class WorkflowBuilder:
    """Natural language workflow builder for complex analysis sequences"""

    def __init__(self):
        self.workflows = {
            "phase_analysis": [
                {"tool": "midas_integrate_2d_to_1d", "description": "Integrate 2D image to 1D pattern"},
                {"tool": "midas_identify_crystalline_phases", "description": "Identify phases from peaks"}
            ],
            "full_hedm": [
                {"tool": "filesystem_list_directory", "description": "Check data directory"},
                {"tool": "midas_run_ff_hedm_full_workflow", "description": "Run FF-HEDM reconstruction"}
            ],
            "calibration_check": [
                {"tool": "midas_detect_diffraction_rings", "description": "Detect rings for calibration"},
                {"tool": "midas_integrate_2d_to_1d", "description": "Integrate to verify calibration"}
            ]
        }

    def get_workflow(self, workflow_name: str) -> Optional[List[Dict[str, str]]]:
        """Get predefined workflow steps"""
        return self.workflows.get(workflow_name)

    def suggest_workflow(self, user_query: str) -> Optional[str]:
        """Suggest appropriate workflow based on user query"""
        query_lower = user_query.lower()

        if "phase" in query_lower and "identif" in query_lower:
            return "phase_analysis"
        elif "ff-hedm" in query_lower or "hedm" in query_lower:
            return "full_hedm"
        elif "calibrat" in query_lower:
            return "calibration_check"

        return None

class ImageAnalyzer:
    """Multimodal image analysis - AI can see and analyze diffraction images"""

    @staticmethod
    def analyze_image_quality(image_path: str) -> Dict[str, Any]:
        """Analyze diffraction image quality using vision

        Args:
            image_path: Path to diffraction image

        Returns:
            Dictionary with quality metrics and AI observations
        """
        try:
            import fabio
            import numpy as np
            from scipy import ndimage

            img = fabio.open(image_path)
            data = img.data.astype(float)

            # Calculate quality metrics
            metrics = {
                "image_path": image_path,
                "dimensions": data.shape,
                "data_type": str(data.dtype),
                "statistics": {
                    "min": float(np.min(data)),
                    "max": float(np.max(data)),
                    "mean": float(np.mean(data)),
                    "median": float(np.median(data)),
                    "std": float(np.std(data))
                }
            }

            # Signal-to-noise ratio
            background = np.percentile(data, 10)
            signal = np.percentile(data, 99)
            snr = (signal - background) / np.std(data[data < np.percentile(data, 20)])
            metrics["signal_to_noise"] = float(snr)

            # Detect saturation
            max_val = np.max(data)
            if data.dtype == np.uint16:
                saturation_threshold = 65535 * 0.95
            else:
                saturation_threshold = max_val * 0.95

            saturated_pixels = np.sum(data >= saturation_threshold)
            saturation_percent = (saturated_pixels / data.size) * 100
            metrics["saturation_percent"] = float(saturation_percent)

            # Hot pixel detection
            median_filtered = ndimage.median_filter(data, size=3)
            diff = np.abs(data - median_filtered)
            hot_pixels = np.sum(diff > 10 * np.std(diff))
            metrics["hot_pixels"] = int(hot_pixels)

            # Overall quality assessment
            quality = "Excellent"
            issues = []

            if snr < 5:
                quality = "Poor"
                issues.append("Low signal-to-noise ratio")
            elif snr < 10:
                quality = "Fair"
                issues.append("Moderate signal-to-noise ratio")

            if saturation_percent > 1:
                quality = "Poor" if quality != "Poor" else quality
                issues.append(f"Saturation detected ({saturation_percent:.1f}% pixels)")

            if hot_pixels > data.size * 0.01:
                issues.append(f"Many hot pixels detected ({hot_pixels})")

            metrics["overall_quality"] = quality
            metrics["issues"] = issues

            return metrics

        except Exception as e:
            return {
                "error": str(e),
                "image_path": image_path
            }

    @staticmethod
    def detect_rings_visual(image_path: str) -> Dict[str, Any]:
        """Detect diffraction rings visually

        Args:
            image_path: Path to diffraction image

        Returns:
            Ring detection results
        """
        try:
            import fabio
            import numpy as np
            from scipy import ndimage

            img = fabio.open(image_path)
            data = img.data.astype(float)

            # Simple ring detection via radial integration
            center_y, center_x = np.array(data.shape) // 2
            y, x = np.indices(data.shape)
            r = np.sqrt((x - center_x)**2 + (y - center_y)**2).astype(int)

            # Radial profile
            radial_profile = ndimage.mean(data, labels=r, index=np.arange(0, r.max()))

            # Find peaks in radial profile
            from scipy.signal import find_peaks
            peaks, properties = find_peaks(radial_profile,
                                          height=np.percentile(radial_profile, 75),
                                          distance=20)

            ring_radii = peaks.tolist()
            ring_intensities = [float(radial_profile[p]) for p in peaks]

            return {
                "image_path": image_path,
                "rings_detected": len(ring_radii),
                "ring_radii_pixels": ring_radii,
                "ring_intensities": ring_intensities,
                "center_position": [int(center_x), int(center_y)],
                "quality": "Good" if len(ring_radii) > 3 else "Check calibration"
            }

        except Exception as e:
            return {
                "error": str(e),
                "image_path": image_path
            }

    @staticmethod
    def create_image_summary(image_path: str) -> str:
        """Create human-readable summary for AI vision

        Args:
            image_path: Path to image

        Returns:
            Text summary suitable for AI multimodal understanding
        """
        quality = ImageAnalyzer.analyze_image_quality(image_path)
        rings = ImageAnalyzer.detect_rings_visual(image_path)

        summary = f"""
📸 Image Analysis: {Path(image_path).name}

Image Properties:
  Dimensions: {quality.get('dimensions', 'N/A')}
  Signal-to-Noise: {quality.get('signal_to_noise', 0):.1f}
  Overall Quality: {quality.get('overall_quality', 'Unknown')}

Quality Issues:
{chr(10).join('  • ' + issue for issue in quality.get('issues', [])) if quality.get('issues') else '  ✓ No issues detected'}

Diffraction Rings:
  Rings Detected: {rings.get('rings_detected', 0)}
  Ring Radii: {rings.get('ring_radii_pixels', [])}
  Assessment: {rings.get('quality', 'N/A')}

Statistics:
  Min/Max Intensity: {quality.get('statistics', {}).get('min', 0):.0f} / {quality.get('statistics', {}).get('max', 0):.0f}
  Mean Intensity: {quality.get('statistics', {}).get('mean', 0):.0f}
  Saturation: {quality.get('saturation_percent', 0):.2f}%
  Hot Pixels: {quality.get('hot_pixels', 0)}
"""
        return summary

class RealtimeFeedback:
    """Real-time experiment feedback during beamtime"""

    def __init__(self):
        self.monitoring = False
        self.watch_directory = None
        self.last_check_time = None
        self.processed_files = set()
        self.alerts = []

    def start_monitoring(self, directory: str, check_interval: int = 5):
        """Start monitoring directory for new diffraction images

        Args:
            directory: Directory to watch
            check_interval: Check for new files every N seconds
        """
        self.monitoring = True
        self.watch_directory = Path(directory)
        self.last_check_time = datetime.now()
        self.check_interval = check_interval

        return {
            "status": "monitoring_started",
            "directory": str(self.watch_directory),
            "check_interval": check_interval,
            "message": f"Real-time monitoring active on {directory}"
        }

    def stop_monitoring(self):
        """Stop real-time monitoring"""
        self.monitoring = False
        return {
            "status": "monitoring_stopped",
            "files_processed": len(self.processed_files),
            "alerts_generated": len(self.alerts)
        }

    def check_new_files(self) -> List[Dict[str, Any]]:
        """Check for new diffraction images and analyze them

        Returns:
            List of new file analyses
        """
        if not self.monitoring or not self.watch_directory:
            return []

        new_analyses = []

        # Find new image files
        for ext in ['.tif', '.tiff', '.ge2', '.ge5', '.ed5', '.edf']:
            for img_file in self.watch_directory.glob(f'*{ext}'):
                if img_file.stat().st_mtime > self.last_check_time.timestamp():
                    if str(img_file) not in self.processed_files:
                        # New file detected! Analyze it
                        analysis = self._analyze_and_alert(img_file)
                        new_analyses.append(analysis)
                        self.processed_files.add(str(img_file))

        self.last_check_time = datetime.now()
        return new_analyses

    def _analyze_and_alert(self, image_path: Path) -> Dict[str, Any]:
        """Analyze new image and generate alerts if needed

        Args:
            image_path: Path to new image

        Returns:
            Analysis with alerts
        """
        quality = ImageAnalyzer.analyze_image_quality(str(image_path))
        rings = ImageAnalyzer.detect_rings_visual(str(image_path))

        analysis = {
            "timestamp": datetime.now().isoformat(),
            "file": image_path.name,
            "quality": quality,
            "rings": rings,
            "alerts": []
        }

        # Generate alerts for issues
        if quality.get('overall_quality') == 'Poor':
            alert = {
                "level": "WARNING",
                "message": f"Poor image quality detected in {image_path.name}",
                "details": quality.get('issues', [])
            }
            analysis["alerts"].append(alert)
            self.alerts.append(alert)

        if quality.get('saturation_percent', 0) > 1:
            alert = {
                "level": "CRITICAL",
                "message": f"Detector saturation in {image_path.name}",
                "details": f"{quality.get('saturation_percent', 0):.1f}% pixels saturated"
            }
            analysis["alerts"].append(alert)
            self.alerts.append(alert)

        if rings.get('rings_detected', 0) < 3:
            alert = {
                "level": "INFO",
                "message": f"Few diffraction rings in {image_path.name}",
                "details": f"Only {rings.get('rings_detected', 0)} rings detected"
            }
            analysis["alerts"].append(alert)

        return analysis

    def get_session_summary(self) -> Dict[str, Any]:
        """Get summary of monitoring session

        Returns:
            Session statistics
        """
        return {
            "monitoring_active": self.monitoring,
            "directory": str(self.watch_directory) if self.watch_directory else None,
            "files_processed": len(self.processed_files),
            "total_alerts": len(self.alerts),
            "critical_alerts": len([a for a in self.alerts if a['level'] == 'CRITICAL']),
            "warning_alerts": len([a for a in self.alerts if a['level'] == 'WARNING']),
            "recent_alerts": self.alerts[-5:] if self.alerts else []
        }

class PlottingEngine:
    """Advanced plotting for diffraction data visualization"""

    def __init__(self, output_dir: Path = None):
        self.output_dir = output_dir or Path.home() / ".apexa" / "plots"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def plot_2d_image(self, image_path: str, scale: str = "linear", save: bool = True, show: bool = False) -> Dict[str, Any]:
        """Plot 2D diffraction image with enhancements

        Args:
            image_path: Path to diffraction image
            scale: "linear" or "log" for intensity scale
            save: Save plot to file
            show: Display plot interactively

        Returns:
            Dictionary with plot info and statistics
        """
        try:
            import numpy as np
            import matplotlib.pyplot as plt
            import matplotlib.colors as colors
            import fabio

            # Load image
            img = fabio.open(str(Path(image_path).expanduser().absolute()))
            data = img.data.astype(float)

            # Create figure
            fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))

            # Linear scale
            im1 = ax1.imshow(data, cmap='viridis', origin='lower')
            ax1.set_title(f'{Path(image_path).name} - Linear Scale')
            ax1.set_xlabel('X (pixels)')
            ax1.set_ylabel('Y (pixels)')
            plt.colorbar(im1, ax=ax1, label='Intensity')

            # Log scale
            data_log = np.copy(data)
            data_log[data_log <= 0] = 1  # Avoid log(0)
            im2 = ax2.imshow(data_log, cmap='viridis', norm=colors.LogNorm(), origin='lower')
            ax2.set_title(f'{Path(image_path).name} - Log Scale')
            ax2.set_xlabel('X (pixels)')
            ax2.set_ylabel('Y (pixels)')
            plt.colorbar(im2, ax=ax2, label='Intensity (log)')

            plt.tight_layout()

            # Save or show
            output_path = None
            if save:
                output_path = self.output_dir / f"{Path(image_path).stem}_2d.png"
                plt.savefig(output_path, dpi=150, bbox_inches='tight')

            if show:
                plt.show()
            else:
                plt.close()

            # Statistics
            stats = {
                "mean": float(np.mean(data)),
                "max": float(np.max(data)),
                "min": float(np.min(data)),
                "std": float(np.std(data))
            }

            return {
                "status": "success",
                "plot_saved": str(output_path) if output_path else None,
                "statistics": stats,
                "message": f"2D plot created for {Path(image_path).name}"
            }

        except Exception as e:
            return {
                "status": "error",
                "error": str(e)
            }

    def plot_radial_profile(self, image_path: str, save: bool = True, show: bool = False) -> Dict[str, Any]:
        """Plot radial intensity profile with peak detection

        Args:
            image_path: Path to diffraction image
            save: Save plot to file
            show: Display plot interactively

        Returns:
            Dictionary with plot info and detected peaks
        """
        try:
            import numpy as np
            import matplotlib.pyplot as plt
            import fabio
            from scipy import signal

            # Load image
            img = fabio.open(str(Path(image_path).expanduser().absolute()))
            data = img.data.astype(float)

            # Calculate center
            center_y, center_x = np.array(data.shape) / 2

            # Create radial profile
            y, x = np.indices(data.shape)
            r = np.sqrt((x - center_x)**2 + (y - center_y)**2)
            r = r.astype(int)

            # Bin by radius
            tbin = np.bincount(r.ravel(), data.ravel())
            nr = np.bincount(r.ravel())
            radial_prof = tbin / nr

            # Remove NaN values
            radial_prof = radial_prof[~np.isnan(radial_prof)]
            radii = np.arange(len(radial_prof))

            # Detect peaks
            peaks, properties = signal.find_peaks(radial_prof, prominence=np.std(radial_prof))

            # Create plot
            fig, ax = plt.subplots(figsize=(10, 6))
            ax.plot(radii, radial_prof, 'b-', linewidth=1, label='Radial Profile')
            ax.plot(peaks, radial_prof[peaks], 'ro', markersize=8, label=f'Peaks ({len(peaks)} found)')

            ax.set_xlabel('Radius (pixels)')
            ax.set_ylabel('Average Intensity')
            ax.set_title(f'Radial Profile - {Path(image_path).name}')
            ax.legend()
            ax.grid(True, alpha=0.3)

            plt.tight_layout()

            # Save or show
            output_path = None
            if save:
                output_path = self.output_dir / f"{Path(image_path).stem}_radial.png"
                plt.savefig(output_path, dpi=150, bbox_inches='tight')

            if show:
                plt.show()
            else:
                plt.close()

            return {
                "status": "success",
                "plot_saved": str(output_path) if output_path else None,
                "peaks_detected": len(peaks),
                "peak_positions": peaks.tolist(),
                "message": f"Radial profile plotted with {len(peaks)} rings detected"
            }

        except Exception as e:
            return {
                "status": "error",
                "error": str(e)
            }

    def plot_1d_pattern(self, pattern_file: str, save: bool = True, show: bool = False) -> Dict[str, Any]:
        """Plot 1D integrated diffraction pattern

        Args:
            pattern_file: Path to 1D pattern file (.dat, .xy, .chi)
            save: Save plot to file
            show: Display plot interactively

        Returns:
            Dictionary with plot info and peak information
        """
        try:
            import numpy as np
            import matplotlib.pyplot as plt
            from scipy import signal

            # Load 1D pattern
            pattern_path = Path(pattern_file).expanduser().absolute()
            data = np.loadtxt(pattern_path)

            if data.ndim == 1:
                # Single column - assume it's intensity only
                q = np.arange(len(data))
                intensity = data
            else:
                # Two columns - Q and intensity
                q = data[:, 0]
                intensity = data[:, 1]

            # Detect peaks
            peaks, properties = signal.find_peaks(intensity, prominence=np.std(intensity)*2)

            # Create plot with two subplots (linear and log)
            fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8))

            # Linear scale
            ax1.plot(q, intensity, 'b-', linewidth=1, label='Integrated Pattern')
            ax1.plot(q[peaks], intensity[peaks], 'ro', markersize=6, label=f'Peaks ({len(peaks)})')
            ax1.set_ylabel('Intensity')
            ax1.set_title(f'1D Pattern - {Path(pattern_file).name} (Linear)')
            ax1.legend()
            ax1.grid(True, alpha=0.3)

            # Log scale
            ax2.semilogy(q, intensity, 'b-', linewidth=1, label='Integrated Pattern')
            ax2.semilogy(q[peaks], intensity[peaks], 'ro', markersize=6, label=f'Peaks ({len(peaks)})')
            ax2.set_xlabel('Q (Å⁻¹)' if q.max() < 20 else '2θ (degrees)')
            ax2.set_ylabel('Intensity (log)')
            ax2.set_title(f'1D Pattern - {Path(pattern_file).name} (Log)')
            ax2.legend()
            ax2.grid(True, alpha=0.3)

            plt.tight_layout()

            # Save or show
            output_path = None
            if save:
                output_path = self.output_dir / f"{Path(pattern_file).stem}_1d.png"
                plt.savefig(output_path, dpi=150, bbox_inches='tight')

            if show:
                plt.show()
            else:
                plt.close()

            return {
                "status": "success",
                "plot_saved": str(output_path) if output_path else None,
                "peaks_detected": len(peaks),
                "peak_positions": q[peaks].tolist(),
                "message": f"1D pattern plotted with {len(peaks)} peaks detected"
            }

        except Exception as e:
            return {
                "status": "error",
                "error": str(e)
            }

    def plot_comparison(self, files: list, labels: list = None, save: bool = True, show: bool = False) -> Dict[str, Any]:
        """Compare multiple 1D patterns in one plot

        Args:
            files: List of pattern file paths
            labels: Optional custom labels for each pattern
            save: Save plot to file
            show: Display plot interactively

        Returns:
            Dictionary with plot info
        """
        try:
            import numpy as np
            import matplotlib.pyplot as plt

            if not labels:
                labels = [Path(f).stem for f in files]

            fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10))
            colors = plt.cm.tab10(np.linspace(0, 1, len(files)))

            for i, (file, label, color) in enumerate(zip(files, labels, colors)):
                # Load pattern
                data = np.loadtxt(Path(file).expanduser().absolute())

                if data.ndim == 1:
                    q = np.arange(len(data))
                    intensity = data
                else:
                    q = data[:, 0]
                    intensity = data[:, 1]

                # Normalize for comparison
                intensity_norm = intensity / np.max(intensity)

                # Plot
                ax1.plot(q, intensity_norm, color=color, linewidth=1.5,
                        label=label, alpha=0.8)
                ax2.semilogy(q, intensity_norm, color=color, linewidth=1.5,
                           label=label, alpha=0.8)

            # Linear scale
            ax1.set_ylabel('Normalized Intensity')
            ax1.set_title('Pattern Comparison (Linear)')
            ax1.legend()
            ax1.grid(True, alpha=0.3)

            # Log scale
            ax2.set_xlabel('Q (Å⁻¹)' if q.max() < 20 else '2θ (degrees)')
            ax2.set_ylabel('Normalized Intensity (log)')
            ax2.set_title('Pattern Comparison (Log)')
            ax2.legend()
            ax2.grid(True, alpha=0.3)

            plt.tight_layout()

            # Save or show
            output_path = None
            if save:
                output_path = self.output_dir / f"comparison_{len(files)}patterns.png"
                plt.savefig(output_path, dpi=150, bbox_inches='tight')

            if show:
                plt.show()
            else:
                plt.close()

            return {
                "status": "success",
                "plot_saved": str(output_path) if output_path else None,
                "patterns_compared": len(files),
                "message": f"Comparison plot created for {len(files)} patterns"
            }

        except Exception as e:
            return {
                "status": "error",
                "error": str(e)
            }

class SmartCache:
    """Cache expensive operations to reduce AI costs and improve speed"""

    def __init__(self, cache_dir: Path = None):
        self.cache_dir = cache_dir or Path.home() / ".apexa" / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.memory_cache = {}

    def get_cache_key(self, operation: str, args: Dict[str, Any]) -> str:
        """Generate cache key from operation and arguments"""
        import hashlib
        # Sort args for consistent hashing
        sorted_args = json.dumps(args, sort_keys=True)
        key_str = f"{operation}:{sorted_args}"
        return hashlib.md5(key_str.encode()).hexdigest()

    def get(self, operation: str, args: Dict[str, Any]) -> Optional[Any]:
        """Get cached result if available"""
        cache_key = self.get_cache_key(operation, args)

        # Check memory cache first
        if cache_key in self.memory_cache:
            return self.memory_cache[cache_key]

        # Check disk cache
        cache_file = self.cache_dir / f"{cache_key}.json"
        if cache_file.exists():
            with open(cache_file, 'r') as f:
                cached_data = json.load(f)
                self.memory_cache[cache_key] = cached_data
                return cached_data

        return None

    def set(self, operation: str, args: Dict[str, Any], result: Any):
        """Cache result for future use"""
        cache_key = self.get_cache_key(operation, args)

        # Save to memory
        self.memory_cache[cache_key] = result

        # Save to disk
        cache_file = self.cache_dir / f"{cache_key}.json"
        try:
            with open(cache_file, 'w') as f:
                json.dump(result, f)
        except Exception:
            pass  # Non-critical if caching fails

# Destructive-command detector for the deletion permission gate. Fires only when
# rm/rmdir/unlink appears in COMMAND position — at the start, after a shell
# operator (; & | newline ` ( ), after `xargs`, or as a `bash -c/-lc` script —
# so it catches every real deletion path (incl. shell-wrapped and piped) but NOT
# `rm` used as a plain argument or substring (e.g. `grep rm f`, `ls /var/rm`).
# Errs toward firing on ambiguous quoted forms (safe direction for a destructive
# gate). See execute_tool_call's gate + run_query's permission_callback.
_DELETE_CMD_RE = re.compile(
    r'(?:^|[;&|`\n(]|\bxargs\s+(?:-\S+\s+)*|-\w*c\s*["\']?)\s*(rm|rmdir|unlink)\b',
    re.I,
)


def _is_delete_command(cmd: str) -> bool:
    """True if a shell command would delete files (rm/rmdir/unlink in command position)."""
    return bool(_DELETE_CMD_RE.search(cmd or ""))


class APEXAClient:
    def __init__(self):
        self.sessions = {}
        self.exit_stack = AsyncExitStack()

        # Abort/reconnect state (Ctrl-C aborts a running command; see
        # _abort_active_query / _reconnect_server). Each MCP server gets its own
        # AsyncExitStack so a single session can be torn down + rebuilt after its
        # process tree is killed, without disturbing the others.
        self._server_stacks:  Dict[str, AsyncExitStack] = {}   # name → per-server stack
        self._server_configs: Dict[str, str]            = {}   # name → script_path (for reconnect)
        self._server_pids:    Dict[str, int]            = {}   # name → server subprocess PID
        self._busy_server:    Optional[str]             = None # server with an in-flight call_tool
        self._abort_busy:     Optional[str]             = None # busy server captured at Ctrl-C (before unwind)
        self._active_task:    Optional[asyncio.Task]    = None # the running run_query task, if any

        # Smart context manager for session persistence
        self.context = ExperimentContext()

        # Initialize smart features
        self.batch_processor = BatchProcessor()
        self.error_preventor = ErrorPreventor()
        self.workflow_builder = WorkflowBuilder()
        self.cache = SmartCache()
        self.image_analyzer = ImageAnalyzer()
        self.realtime_feedback = RealtimeFeedback()
        self.plotting = PlottingEngine()

        # Determine environment based on model (dev models require dev endpoint)
        self.anl_username = os.getenv("ANL_USERNAME")
        self.selected_model = os.getenv("ARGO_MODEL", "claudeopus5")

        # All current models on PROD (March 2026 Argo update)
        # Future beta models: add to DEV_ONLY_MODELS in apexa_agents.py
        self.environment = "DEV" if self.selected_model in DEV_ONLY_MODELS else "PROD"

        # Phase 2: tool registry (built at connection time, not per-query)
        self._tool_registry:   Dict[str, str]       = {}   # bare_tool_name → server_name
        self._available_tools: List[Dict[str, Any]] = []   # pre-built tool definitions
        self.orchestrator:     Optional[OrchestratorAgent] = None

        # Deletion permission gate: UI supplies an async callback
        # (tool_name, arguments, command) -> bool that prompts a human before any
        # rm/rmdir/unlink runs. Set per-query in run_query (single beamline user →
        # queries are serialized, so a single shared attribute is safe). None →
        # fail-safe DENY (no interface can confirm → deletion is blocked).
        self.permission_callback = None

        # ── Web-UI per-thread conversation memory ────────────────────────────
        # The web/desktop frontend keeps ChatGPT-style threads in localStorage
        # but the orchestrator has ONE global conversation_history. Without a
        # thread→memory mapping the web UI "starts fresh every time" (the
        # in-memory global is lost on restart) and bleeds context across threads.
        # We bucket the orchestrator's history+summary per frontend thread_id and
        # persist each bucket to disk so a thread survives a restart — mirroring
        # the frontend's own localStorage durability. Kept in a dedicated dir,
        # separate from the CLI ExperimentContext session store (no pollution).
        # activate_web_thread()/_save_web_thread() are serialized by this lock
        # because there is a single shared orchestrator.
        self._web_threads_dir = Path.home() / ".apexa" / "web_threads"
        self._active_web_thread: Optional[str] = None
        self._web_thread_lock = asyncio.Lock()

        if not self.anl_username:
            raise ValueError("ANL_USERNAME must be set in environment (.env file)")

        self.available_models = {
            "OpenAI": {
                "gpt4o":       "GPT-4o (128K ctx, 16K out) — fastest",
                "gpt41":       "GPT-4.1 (1M ctx, 16K out)",
                "gpt41mini":   "GPT-4.1 Mini (1M ctx, 16K out)",
                "gpt41nano":   "GPT-4.1 Nano (1M ctx, 16K out)",
                "gpto3mini":   "o3-mini (200K ctx, 100K out)",
                "gpto4mini":   "o4-mini",
                "gpt5":        "GPT-5 (272K ctx, 128K out)",
                "gpt5mini":    "GPT-5 Mini (272K ctx, 128K out)",
                "gpt5nano":    "GPT-5 Nano (272K ctx, 128K out)",
                "gpt51":       "GPT-5.1 (400K ctx, 128K out)",
                "gpt52":       "GPT-5.2 (400K ctx, 128K out)",
                "gpt54":       "GPT-5.4 (1M ctx, 128K out) — best all-round; temp ok",
                "gpt55":       "GPT-5.5 (1M ctx, 128K out) — temp=1 only; ~2× gpt54 cost",
                "gpt56sol":    "GPT-5.6 Sol (1.05M ctx, 128K out) — NEW; frontier GPT-5.6",
                "gpt56terra":  "GPT-5.6 Terra (1.05M ctx, 128K out) — NEW; mini tier (intelligence/cost balance)",
                "gpt56luna":   "GPT-5.6 Luna (1.05M ctx, 128K out) — NEW; nano tier (cost-sensitive, high-volume)",
            },
            "Anthropic": {
                "claudeopus5":   "Claude Opus 5 (1M ctx, 128K out) — NEWEST Opus; agentic coding/enterprise; no sampling params",
                "claudeopus48":  "Claude Opus 4.8 (1M ctx, 128K out) — best planning/reasoning; no sampling params",
                "claudeopus47":  "Claude Opus 4.7 (1M ctx, 128K out) — no sampling params",
                "claudeopus46":  "Claude Opus 4.6 (200K ctx, 128K out) — requires temp+top_p",
                "claudeopus45":  "Claude Opus 4.5 (200K ctx, 64K out)",
                "claudeopus41":  "Claude Opus 4.1 (200K ctx, 32K out)",
                "claudesonnet5": "Claude Sonnet 5 (newest Sonnet) — temp only, no top_p",
                "claudesonnet46":"Claude Sonnet 4.6 (1M ctx, 64K out) — temp only, no top_p",
                "claudesonnet45":"Claude Sonnet 4.5 (200K ctx, 64K out) — temp only, no top_p",
                "claudehaiku45": "Claude Haiku 4.5 (200K ctx, 64K out) — temp only, no top_p",
            },
            "Google": {
                "gemini35flash":    "Gemini 3.5 Flash (1M ctx, 65K out)",
                "gemini31flashlite":"Gemini 3.1 Flash Lite (1M ctx, 65K out)",
            }
        }

        # ALCF Inference Service — open models on ANL hardware, authenticated with
        # the USER'S OWN Globus identity. Listed from the single candidate table in
        # apexa_llm_endpoints so the CLI can never drift from what's qualified.
        # `model <id>` switches cluster + credential too (see the handler).
        try:
            from apexa_llm_endpoints import ALCF_CANDIDATES as _ALCF
            for _cluster, _label in (("alcf-sophia",  "ALCF Sophia (open, per-user Globus)"),
                                     ("alcf-minerva", "ALCF Minerva (open, per-user Globus)")):
                _group = {c["model"]: f"[{c['flags']}] {c['note']}"
                          for c in _ALCF if c["preset"] == _cluster}
                if _group:
                    self.available_models[_label] = _group
        except Exception as _e:      # never let the model list break startup
            print(f"  {C.DIM}(ALCF model list unavailable: {_e}){C.RESET}", file=sys.stderr)

    @staticmethod
    def _server_command(script_path: str) -> str:
        """Resolve the interpreter/command used to spawn a server script.

        Uses the venv Python if available, otherwise the current interpreter.
        Cross-platform: Windows venv is .venv\\Scripts\\python.exe, Unix is
        .venv/bin/python3. sys.executable is the uv-managed venv under `uv run`
        and is the correct fallback on every OS.
        """
        if script_path.endswith('.py'):
            _cands = [Path(".venv/Scripts/python.exe"),
                      Path(".venv/bin/python3"), Path(".venv/bin/python")]
            _vp = next((p for p in _cands if p.exists()), None)
            return str(_vp) if _vp else sys.executable
        return "node"

    async def _connect_one_server(self, name: str, script_path: str,
                                  register_tools: bool = True):
        """Spawn + initialize a single MCP server on its OWN AsyncExitStack.

        Isolating each server in its own stack lets us tear down and rebuild a
        single session (after killing its process tree on abort) without
        touching the others. Captures the server subprocess PID via psutil so
        _abort_active_query can kill the whole MIDAS process tree. On reconnect
        the tool set is unchanged, so pass register_tools=False.
        """
        # Snapshot our current children so we can identify the newly-spawned one.
        _me = psutil.Process()
        _before = {c.pid for c in _me.children(recursive=True)}

        stack = AsyncExitStack()
        server_params = StdioServerParameters(
            command=self._server_command(script_path),
            args=[script_path],
            env=None,
        )
        stdio_transport = await stack.enter_async_context(stdio_client(server_params))
        stdio, write = stdio_transport
        session = await stack.enter_async_context(ClientSession(stdio, write))
        await session.initialize()

        self._server_stacks[name] = stack
        self._server_configs[name] = script_path
        self.sessions[name] = session

        # Identify the server subprocess: the new child whose cmdline references
        # the script. mcp spawns it with start_new_session=True (its own process
        # group), so killing this PID's tree also kills MIDAS grandchildren.
        self._server_pids.pop(name, None)
        try:
            for c in _me.children(recursive=True):
                if c.pid in _before:
                    continue
                try:
                    if any(script_path in part for part in c.cmdline()):
                        self._server_pids[name] = c.pid
                        break
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except Exception:
            pass  # PID capture is best-effort; abort degrades to client-side cancel

        response = await session.list_tools()
        if register_tools:
            for tool in response.tools:
                self._tool_registry[tool.name] = name
                self._available_tools.append({
                    "type": "function",
                    "function": {
                        "name":        tool.name,
                        "description": f"[{name.upper()}] {tool.description}",
                        # MCP 2.x renamed the Python attribute to ``input_schema``
                        # (``inputSchema`` is now only the wire-serialization alias,
                        # not an attribute). getattr-fallback keeps 1.x working too.
                        "parameters":  getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None),
                    },
                })
        return len(response.tools)

    async def connect_to_multiple_servers(self, server_configs: List[Dict[str, str]]):
        self.sessions = {}

        for config in server_configs:
            name = config["name"]
            script_path = config["script_path"]
            try:
                n = await self._connect_one_server(name, script_path)
                print(f"  {name}: {n} tools")
            except Exception as e:
                print(f"  {name}: FAILED ({e})")

        if "midas" in self.sessions:
            self.session = self.sessions["midas"]

        # LLM transport preflight. Runs once at startup so a misconfigured or
        # not-yet-started argo-proxy sidecar fails HERE — visibly, before any
        # science — instead of surfacing as a failed query mid-experiment.
        # Under APEXA_LLM_STRICT (default on when APEXA_LLM_MODE=proxy) an
        # unreachable proxy aborts startup rather than silently downgrading to the
        # legacy text protocol. Consistent with the startup-diagnostics rule:
        # show warnings and errors, stay quiet on success.
        try:
            from apexa_provider_openai import (
                preflight as _llm_preflight,
                proxy_mode_enabled as _proxy_on,
                strict_mode as _llm_strict,
            )
            _ok, _detail = await _llm_preflight(self.anl_username, self.selected_model)
            if not _ok:
                if _llm_strict():
                    print(f"  {C.RED}✗ LLM transport unavailable{C.RESET}: {_detail}")
                    print(f"  {C.DIM}APEXA_LLM_STRICT is on — refusing to run on the "
                          f"legacy text protocol. Start the sidecar, fix "
                          f"APEXA_LLM_BASE_URL, or set APEXA_LLM_STRICT=0.{C.RESET}")
                    raise RuntimeError(f"LLM transport preflight failed: {_detail}")
                print(f"  {C.YELLOW}⚠ LLM transport degraded{C.RESET}: {_detail}")
                print(f"  {C.YELLOW}  → running on the legacy Argo text protocol "
                      f"(weaker execution-integrity guarantees).{C.RESET}")
            elif _proxy_on():
                print(f"  {C.DIM}LLM transport: {_detail}{C.RESET}")
        except RuntimeError:
            raise
        except Exception as _e:      # preflight must never break startup itself
            print(f"  {C.YELLOW}⚠ LLM preflight skipped: {_e}{C.RESET}", file=sys.stderr)

        # Initialise the orchestrator (Phase 2 core)
        self.orchestrator = OrchestratorAgent(
            execute_tool_fn=self.execute_tool_call,
            all_tools=self._available_tools,
            context=self.context,
        )

    async def _reconnect_server(self, name: str):
        """Rebuild a single MCP session after its process tree was killed.

        Called from _abort_active_query. The session is dead once we kill the
        server's process tree, so we close its (now-broken) stack best-effort and
        spawn a fresh one. Tools are unchanged → registry is left intact.
        """
        script_path = self._server_configs.get(name)
        if not script_path:
            return
        old = self._server_stacks.pop(name, None)
        if old is not None:
            with contextlib.suppress(Exception):
                await old.aclose()
        try:
            await self._connect_one_server(name, script_path, register_tools=False)
            if name == "midas":
                self.session = self.sessions.get("midas")
            print(f"  {C.GREEN}✓{C.RESET} reconnected {C.CYAN}{name}{C.RESET}")
        except Exception as e:
            print(f"  {C.RED}✗{C.RESET} failed to reconnect {name}: {e}")

    @staticmethod
    def _kill_process_tree(pid: int):
        """Terminate a server subprocess and every descendant (cross-platform).

        The MIDAS compute runs as a subprocess.run() child of the server, so we
        must kill the whole tree, not just the server. SIGTERM first, then SIGKILL
        any survivors after a short grace period.
        """
        try:
            parent = psutil.Process(pid)
        except psutil.NoSuchProcess:
            return
        procs = parent.children(recursive=True) + [parent]
        for p in procs:
            with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
                p.terminate()
        _gone, alive = psutil.wait_procs(procs, timeout=3)
        for p in alive:
            with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
                p.kill()

    def _on_sigint(self):
        """SIGINT handler active ONLY while a query runs.

        prompt_toolkit installs its own SIGINT handler for the duration of
        prompt_async and restores ours afterward, so this fires only during a
        running command — cancelling the query task. An idle-prompt Ctrl-C is
        prompt_toolkit's (→ KeyboardInterrupt → top-level handler → quit).
        """
        task = self._active_task
        if task is not None and not task.done():
            print(f"\n  {C.YELLOW}⏹{C.RESET} Aborting…", flush=True)
            # Snapshot the busy server NOW, before task.cancel() unwinds
            # execute_tool_call's finally (which clears _busy_server).
            self._abort_busy = self._busy_server
            task.cancel()

    async def _abort_active_query(self):
        """Abort the in-flight query: cancel the task, kill the busy server's
        compute tree, and transparently reconnect that session.

        On the POSIX path _on_sigint has already cancelled the task and stashed the
        busy server in _abort_busy (execute_tool_call's finally clears _busy_server
        as cancellation unwinds, so we can't read it here). On the Windows fallback
        path (KeyboardInterrupt raised at the await) the task hasn't unwound yet, so
        _busy_server is still live — hence the fallback.
        """
        busy = self._abort_busy or self._busy_server
        self._abort_busy = None
        task = self._active_task
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, KeyboardInterrupt):
                await task
        if busy:
            pid = self._server_pids.get(busy)
            if pid:
                self._kill_process_tree(pid)
                print(f"  {C.DIM}killed {busy} compute (pid {pid}){C.RESET}")
            await self._reconnect_server(busy)
        self._busy_server = None
        print(f"  {C.DIM}back to prompt — Ctrl-C again to quit{C.RESET}\n")

    async def execute_tool_call(self, tool_name: str, arguments: Dict[str, Any]) -> str:

        # ===== NETWORK TIER GATE =====
        # A tool that needs the public internet must be refused HERE, at the same
        # single chokepoint as the deletion gate, rather than dispatched to a host
        # that has no route out. Left to itself such a call does not fail — it
        # BLOCKS, which is how the copland launch hung. Returning an explicit
        # unavailable message keeps the model honest (it can adapt or ask the user)
        # instead of stalling or inventing the result.
        try:
            from apexa_network import tool_unavailable_reason
            _blocked = tool_unavailable_reason(tool_name)
        except Exception:
            _blocked = None
        if _blocked:
            print(f"  {C.YELLOW}⚠ unavailable on this host{C.RESET} {C.DIM}({tool_name}){C.RESET}")
            return f"⛔ Tool unavailable: {_blocked}"

        # ===== SMART CACHING =====
        # Check cache for expensive read-only operations
        cacheable_operations = ["filesystem_read_file", "filesystem_list_directory"]
        if tool_name in cacheable_operations:
            cached_result = self.cache.get(tool_name, arguments)
            if cached_result:
                print(" (from cache)")
                return cached_result

        # Registry lookup (built once at connection time — O(1), no live RPCs)
        server_name = self._tool_registry.get(tool_name, "midas")

        if server_name not in self.sessions:
            return f"Error: Server '{server_name}' not connected"

        # Normalize path-like arguments to absolute paths
        path_arg_names = {"path", "file_path", "image_file", "calibration_file",
                          "parameters_file", "result_folder", "dark_file",
                          "working_dir", "param_file", "data_file"}
        for key in list(arguments.keys()):
            if key in path_arg_names and isinstance(arguments[key], str):
                val = arguments[key]
                if val and val not in (".", "~") and not val.startswith("http"):
                    resolved = str(Path(val).expanduser().resolve())
                    if resolved != val:
                        print(f"  Path resolved: {val} -> {resolved}", file=sys.stderr)
                    arguments[key] = resolved

        # ===== DELETION PERMISSION GATE =====
        # rm/rmdir/unlink are irreversible on beamline scratch (no recycle bin).
        # This is the single chokepoint every tool-calling path funnels through
        # (Mode 1 native tool_calls, Mode 2 text, orchestrator fast-path, and the
        # FF-HEDM workflow graph), so the human-approval check lives here. The UI
        # supplies self.permission_callback (set per-query in run_query). Fail-safe:
        # no callback / callback error / non-yes → DENY (nothing runs; return an
        # explicit "not run" string so the model can't hallucinate success).
        if tool_name in ("run_command", "run_remote_command"):
            _cmd = str(arguments.get("command", ""))
            if _is_delete_command(_cmd):
                cb = self.permission_callback
                approved = False
                if cb is not None:
                    try:
                        approved = bool(await cb(tool_name, arguments, _cmd))
                    except Exception as e:
                        print(f"  \033[31m⛔ permission check failed ({e}); denying\033[0m")
                        approved = False
                if not approved:
                    reason = ("no confirmation channel in this interface"
                              if cb is None else "denied by user")
                    print(f"  \033[31m⛔ deletion blocked ({reason}):\033[0m {_cmd}")
                    return (f"⛔ Deletion NOT run ({reason}). The command `{_cmd}` was "
                            "blocked before execution. The files were NOT deleted. Do "
                            "not retry or claim success; ask the user how to proceed.")

        # ===== TECHNIQUE SCOPE GATE =====
        # Each technique's handbook ("capsule") is beamline-scoped — its geometry
        # and conventions do NOT transfer across beamlines. When APEXA_BEAMLINE is
        # configured and a technique-workflow tool's capsule names beamlines that
        # exclude it, block before running (fail-closed), same chokepoint principle
        # as the deletion gate. Fail-open when unconfigured, unknown-technique, or
        # explicitly overridden (APEXA_IGNORE_SCOPE_GATE=1). The natural-language
        # halt conditions the model must also satisfy are surfaced separately, at
        # dispatch time, by AgentRunner._maybe_capsule_msg.
        _beamline = os.environ.get("APEXA_BEAMLINE", "").strip()
        if _beamline and os.environ.get("APEXA_IGNORE_SCOPE_GATE", "").strip().lower() \
                not in ("1", "true", "yes", "on"):
            _scope_blocked, _scope_reason = False, ""
            try:
                import capsule_registry as _cr
                _tech = _cr.technique_for_tool(tool_name)
                if _tech:
                    _scope_blocked, _scope_reason = _cr.scope_conflict(_tech, _beamline)
            except Exception:
                _scope_blocked = False  # fail-open: a broken registry never blocks
            if _scope_blocked:
                print(f"  \033[31m⛔ scope gate:\033[0m {tool_name} — {_scope_reason}")
                return (f"⛔ Scope gate: `{tool_name}` NOT run. {_scope_reason} "
                        "The tool did not execute; do not claim success.")

        _t0 = _time.monotonic()
        try:
            session = self.sessions[server_name]
            # Mark this server busy so a Ctrl-C abort knows whose process tree to
            # kill. Cleared in the finally (also on CancelledError, which is a
            # BaseException and skips the `except Exception` below — _abort_active_query
            # captures _busy_server BEFORE it awaits the cancelled task).
            self._busy_server = server_name
            result = await session.call_tool(tool_name, arguments)
            result_text = str(result.content[0].text if result.content else "No result")

            # Cache result if applicable
            if tool_name in cacheable_operations:
                self.cache.set(tool_name, arguments, result_text)

            # Record analysis in context
            self.context.add_analysis(tool_name, result_text)

            # Add proactive suggestion to result
            suggestion = ProactiveSuggestions.get_suggestion(tool_name, result_text)
            if suggestion:
                result_text += f"\n\n{suggestion}"

            record_tool_call(_time.monotonic() - _t0)
            return result_text
        except Exception as e:
            record_tool_call(_time.monotonic() - _t0)
            error_msg = f"Error: {str(e)}"
            print(f"✗ {error_msg}")
            return error_msg
        finally:
            self._busy_server = None

    # ── Web-UI per-thread memory helpers ─────────────────────────────────────
    def _web_thread_file(self, thread_id: str) -> Path:
        """Path of the on-disk bucket for a frontend thread_id.

        The id comes from the browser (localStorage `sess-…`); sanitize it to a
        safe filename so a crafted id can never escape the buckets dir.
        """
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", thread_id or "")[:128] or "_default"
        return self._web_threads_dir / f"{safe}.json"

    async def activate_web_thread(self, thread_id: Optional[str]) -> None:
        """Make ``thread_id``'s conversation the orchestrator's active memory.

        Saves the currently-active thread's history to disk, then loads the
        requested thread's bucket (full-fidelity in single mode). A thread the
        backend has never seen (new chat, or first message after a restart with
        no saved bucket) starts empty. No-op when the thread is already active or
        when there is no orchestrator. Caller MUST hold ``_web_thread_lock`` so
        the swap is atomic against the single shared orchestrator.
        """
        if not self.orchestrator or not thread_id:
            return
        if thread_id == self._active_web_thread:
            return
        # Flush the outgoing thread so its latest turns aren't lost on the swap.
        if self._active_web_thread is not None:
            self._save_web_thread()
        # Load the incoming thread (or start clean if it has no bucket yet).
        loaded = False
        try:
            f = self._web_thread_file(thread_id)
            if f.exists():
                data = json.loads(f.read_text())
                self.orchestrator.clear_history()
                self.orchestrator.import_history(data.get("conversation_history", []))
                self.orchestrator.import_summary(data.get("running_summary", ""))
                loaded = True
        except Exception as e:
            print(f"  (web thread load skipped for {thread_id}: {e})",
                  file=sys.stderr)
        if not loaded:
            self.orchestrator.clear_history()
        self._active_web_thread = thread_id

    def _save_web_thread(self) -> None:
        """Persist the active thread's history+summary to its on-disk bucket.

        Best-effort and atomic (tmp then replace): a failed save must never crash
        a chat turn. Called after every web turn so the bucket survives a restart.
        """
        if not self.orchestrator or self._active_web_thread is None:
            return
        try:
            self._web_threads_dir.mkdir(parents=True, exist_ok=True)
            record = {
                "conversation_history": self.orchestrator.export_history(),
                "running_summary": self.orchestrator.export_summary(),
                "saved_at": datetime.now().isoformat(),
            }
            f = self._web_thread_file(self._active_web_thread)
            tmp = f.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(record, indent=2))
            os.replace(tmp, f)
        except Exception as e:
            print(f"  (web thread save skipped: {e})", file=sys.stderr)

    def delete_web_thread(self, thread_id: str) -> None:
        """Drop a thread's on-disk bucket (frontend deleted the conversation).

        If it was the active thread, also clear the live orchestrator memory so
        the next turn genuinely starts fresh. Best-effort."""
        if not thread_id:
            return
        try:
            self._web_thread_file(thread_id).unlink(missing_ok=True)
        except Exception as e:
            print(f"  (web thread delete skipped: {e})", file=sys.stderr)
        if thread_id == self._active_web_thread:
            if self.orchestrator:
                self.orchestrator.clear_history()
            self._active_web_thread = None

    async def run_query(self, query: str, use_history: bool = True,
                        on_tool_result=None, permission_callback=None) -> str:
        """Route query through the multi-agent orchestrator (Phase 2 entry point).

        Wrapped in ``query_scope`` so Tier-2 instrumentation emits one summary
        JSONL row per user query (round-trip count, LLM time, tool time,
        wall-clock). See docs/INSTRUMENTATION.md.

        ``permission_callback`` — optional async ``(tool_name, arguments, command)
        -> bool`` used by the deletion gate in ``execute_tool_call`` to prompt a
        human before any rm/rmdir/unlink. Set on the client for the duration of
        this query (restored afterward) so the gate sees it on every tool path.
        """
        if not self.orchestrator:
            return "Error: Not connected to any MCP servers."
        # APEXA_PROVIDER=streaming + a Claude model → Anthropic-native streaming
        # for real TTFT/TPOT metrics; otherwise Argo /chat/ blocking.
        provider = select_provider(self.anl_username, self.selected_model)
        _prev_perm_cb = self.permission_callback
        self.permission_callback = permission_callback
        try:
            with query_scope(query=query) as _qctx:
                result = await self.orchestrator.process(
                    query, provider, use_history,
                    on_tool_result=on_tool_result,
                )
                # Late-bind the agent name — the orchestrator only knows which
                # specialist it routed to after process() returns.
                try:
                    last = getattr(self.orchestrator, "_last_agent", None)
                    if last is not None:
                        _qctx.agent = getattr(last, "name", "") or ""
                except Exception:
                    pass
                return result
        finally:
            self.permission_callback = _prev_perm_cb

    def _autosave_session(self):
        """Persist the live conversation to the _autosave slot.

        Refreshes the metadata sidecar for the *active* session (the full
        conversation is already on disk via the per-message JSONL transcript).
        Called after every turn and on every exit path so a session is never
        silently lost — even if the user never runs 'session save'. Recover
        with 'session resume'. Best-effort: a failed autosave must never crash
        the CLI or mask the user's actual exit.
        """
        try:
            if not self.orchestrator:
                return
            convo = self.orchestrator.export_history()
            if not convo:
                return
            self.context.save_session(self.context.active_session,
                                      conversation=convo,
                                      summary=self.orchestrator.export_summary())
        except Exception as e:
            print(f"  {C.DIM}(autosave skipped: {e}){C.RESET}", file=sys.stderr)

    def _print_response(self, response: str) -> None:
        """Print an APEXA reply behind an ``APEXA:`` label.

        The interactive *input* prompt is ``APEXA>``; this ``APEXA:`` label marks
        where the *reply* begins so it's easy to find in a long scroll, and mirrors
        the prompt. Greppable: ``APEXA:``.
        """
        print(f"\n{C.BOLD}{C.BCYAN}APEXA:{C.RESET} {clean_markdown(response)}\n")

    def show_available_models(self):
        # The list is no longer Argo-only — ALCF's open models are here too, and
        # their ids are up to 41 chars, so a hardcoded ":18" column misaligned
        # every description. Size the column to the content instead.
        try:
            from apexa_llm_endpoints import preset_for_model
        except Exception:
            preset_for_model = lambda _m: None      # noqa: E731

        width = max((len(m) for g in self.available_models.values() for m in g),
                    default=18)
        width = min(max(width, 18), 44)

        active_preset = (os.environ.get("APEXA_LLM_PRESET") or "argo-proxy").strip()
        print(f"\n  {C.BOLD}Available Models{C.RESET}  "
              f"{C.DIM}(active: {self.selected_model} via {active_preset}){C.RESET}")
        print(f"  {C.DIM}{'─' * (width + 34)}{C.RESET}")

        for provider, models in self.available_models.items():
            print(f"\n  {C.BOLD}{C.WHITE}{provider}{C.RESET}")
            for model_id, description in models.items():
                sel = model_id == self.selected_model
                bullet = f"{C.BGREEN}● " if sel else f"{C.DIM}  {C.RESET}"
                name = (f"{C.BOLD}{model_id:{width}}{C.RESET}" if sel
                        else f"{C.CYAN}{model_id:{width}}{C.RESET}")
                print(f"  {bullet}{name} {C.DIM}{description}{C.RESET}")

        print(f"\n  {C.DIM}`model <id>` switches model AND endpoint — an ALCF id moves "
              f"you to that cluster{C.RESET}")
        print(f"  {C.DIM}(bare names expand: gpt-oss-120b → openai/gpt-oss-120b){C.RESET}")

    def _is_valid_model(self, model_name: str) -> bool:
        for provider, models in self.available_models.items():
            if model_name in models:
                return True
        return False

    async def interactive_analysis_session(self):
        n_tools = len(self._available_tools)
        servers = ', '.join(self.sessions.keys())
        print(f"\n  {C.BOLD}{C.BCYAN}APEXA{C.RESET} {C.DIM}— Advanced Photon EXperiment Assistant{C.RESET}")
        print(f"  {C.DIM}{'─' * 48}{C.RESET}")
        print(f"  {C.GRAY}Model:{C.RESET} {C.BOLD}{self.selected_model}{C.RESET} {C.DIM}({self.environment}){C.RESET}  {C.GRAY}│{C.RESET}  {C.BGREEN}{n_tools}{C.RESET} {C.GRAY}tools{C.RESET}  {C.GRAY}│{C.RESET}  {C.GRAY}Servers:{C.RESET} {C.CYAN}{servers}{C.RESET}")
        print(f"  {C.DIM}Type {C.RESET}{C.CYAN}help{C.RESET}{C.DIM} for commands, {C.RESET}{C.CYAN}quit{C.RESET}{C.DIM} to exit{C.RESET}\n")
        
        # prompt_toolkit: tab completion, history, bracketed paste
        import glob as _glob

        _SESSION_VERBS = ['new', 'save', 'load', 'switch', 'resume',
                          'append', 'list', 'summary', 'current']
        _SESSION_NAME_VERBS = {'load', 'switch', 'resume', 'append', 'save'}

        class _APEXACompleter(Completer):
            def __init__(self, commands, sessions_provider=None, active_provider=None):
                self._commands = sorted(commands)
                self._sessions_provider = sessions_provider
                self._active_provider = active_provider

            def _session_names(self):
                # Fail-open: a provider error must not kill completion entirely.
                if not self._sessions_provider:
                    return []
                try:
                    return list(self._sessions_provider())
                except Exception:
                    return []

            def _active_name(self):
                if not self._active_provider:
                    return None
                try:
                    return self._active_provider()
                except Exception:
                    return None

            def get_completions(self, document, complete_event):
                text = document.text_before_cursor
                word = document.get_word_before_cursor(WORD=True)
                tokens = text.split()

                # `session <verb> [name]` — complete verbs, then saved session names
                if tokens and tokens[0] == 'session':
                    completing = '' if text.endswith(' ') else (tokens[-1] if tokens else '')
                    pfx = completing.lower()
                    idx = len(tokens) - (0 if text.endswith(' ') else 1)
                    if idx == 1:                       # completing the verb
                        for v in _SESSION_VERBS:
                            if v.lower().startswith(pfx):
                                yield Completion(v, start_position=-len(completing))
                        return
                    if idx == 2 and tokens[1] in _SESSION_NAME_VERBS:  # completing a name
                        active = self._active_name()
                        for nm in sorted(self._session_names()):
                            if nm.lower().startswith(pfx):
                                yield Completion(
                                    nm, start_position=-len(completing),
                                    display_meta='active' if nm == active else '',
                                )
                        return

                if not tokens or (len(tokens) == 1 and not text.endswith(' ')):
                    for cmd in self._commands:
                        if cmd.startswith(word):
                            yield Completion(cmd, start_position=-len(word))
                else:
                    for path in _glob.glob(word + '*'):
                        display = path + '/' if os.path.isdir(path) else path
                        yield Completion(display, start_position=-len(word))

        _apexa_commands = sorted({
            'help', 'quit', 'exit', 'clear', 'model', 'timing',
            'history', 'status', 'verbose', 'session',
        } | _SHELL_COMMANDS)

        pt_session = PromptSession(
            history=InMemoryHistory(),
            completer=_APEXACompleter(
                _apexa_commands,
                sessions_provider=self.context.list_sessions,
                active_provider=lambda: getattr(self.context, 'active_session', None),
            ),
            complete_style=CompleteStyle.MULTI_COLUMN,   # TAB shows all names in a grid
            enable_history_search=True,
        )

        async def _cli_permission(tool_name: str, arguments: dict, command: str) -> bool:
            """Blocking y/N confirmation before APEXA deletes files (Claude-Code-style).

            Passed to run_query → set as self.permission_callback → invoked by the
            deletion gate in execute_tool_call. Runs on the same event loop as the
            in-flight query, so it truly blocks tool execution until the user answers.
            """
            print(f"\n  \033[33m⚠  APEXA wants to DELETE files:\033[0m\n     {command}")
            try:
                ans = (await pt_session.prompt_async("  Proceed with deletion? (y/N): ")).strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("  \033[31m⛔ deletion cancelled\033[0m")
                return False
            return ans in ("y", "yes")

        def _prompt_text() -> FormattedText:
            """Prompt shows the active session name (unless it's the scratch slot)."""
            active = getattr(self.context, "active_session", "_autosave")
            if active and active != "_autosave":
                return FormattedText([
                    ("bold ansibrightcyan", "APEXA"),
                    ("ansibrightblack", "("),
                    ("ansigreen", active),
                    ("ansibrightblack", ")> "),
                ])
            return FormattedText([
                ("bold ansibrightcyan", "APEXA"),
                ("ansibrightblack", "> "),
            ])

        async def _read_user_input() -> str:
            """Read one input, coalescing a pasted multi-line block into ONE query.

            On terminals with bracketed paste, the whole paste arrives as a single
            input (embedded newlines) and is used as-is. On terminals WITHOUT it
            (many SSH/tmux/older setups), each pasted line is accepted separately —
            which turned a pasted report into N separate queries (the wall of
            one-line answers). Here, after the first line, we DRAIN any remaining
            pasted lines still sitting in the OS input buffer (non-blocking) and
            join them into one query.

            Strictly safe: with nothing buffered (a normally-typed command) the
            zero-timeout select returns instantly — no delay — and where it can't
            apply (Windows stdin, or prompt_toolkit having already consumed the
            bytes) it's a no-op, never worse than the per-line behavior.
            """
            line = await pt_session.prompt_async(_prompt_text())
            if "\n" in line:                       # bracketed paste already merged it
                return line
            try:
                import select as _select
                extra = ""
                fd = sys.stdin.fileno()
                while _select.select([sys.stdin], [], [], 0)[0]:
                    chunk = os.read(fd, 65536)
                    if not chunk:
                        break
                    extra += chunk.decode("utf-8", "replace")
                if extra.strip():
                    return "\n".join([line, *extra.splitlines()])
            except Exception:
                pass
            return line

        history = []

        # Install a SIGINT handler that aborts the *running* query (Ctrl-C) rather
        # than exiting. prompt_toolkit owns SIGINT while prompt_async is active, so
        # this fires only during a running command; an idle-prompt Ctrl-C stays
        # prompt_toolkit's → KeyboardInterrupt → top-level handler → quit.
        # add_signal_handler is unsupported on Windows ProactorEventLoop → the
        # (KeyboardInterrupt) branch of the query try/except is the fallback there.
        try:
            asyncio.get_running_loop().add_signal_handler(signal.SIGINT, self._on_sigint)
        except (NotImplementedError, RuntimeError, ValueError):
            pass

        while True:
            try:
                user_input = (await _read_user_input()).strip()

                if not user_input:
                    continue
                    
                if user_input and (not history or history[-1] != user_input):
                    history.append(user_input)
                
                if user_input.lower() == 'quit':
                    self._autosave_session()
                    break
                elif user_input.lower() == 'clear':
                    if self.orchestrator:
                        self.orchestrator.clear_history()
                    print(f"  {C.GREEN}✓{C.RESET} Conversation history cleared")
                elif user_input.lower() in ('clear tools', 'forget tools'):
                    n = self.orchestrator.clear_tool_history() if self.orchestrator else 0
                    print(f"  {C.GREEN}✓{C.RESET} Cleared {n} tool-call turn(s); "
                          f"conversation prose kept")
                elif user_input.lower() == 'models':
                    self.show_available_models()
                elif user_input.lower() == 'servers':
                    print(f"  {C.GREEN}●{C.RESET} Connected: {C.CYAN}{', '.join(self.sessions.keys())}{C.RESET}")

                # ===== NEW SMART COMMANDS =====
                elif user_input.lower().startswith('batch '):
                    # Batch processing command
                    # Example: batch integrate *.ge5 with calib.txt dark.tif
                    parts = user_input[6:].strip().split()
                    if len(parts) < 2:
                        print("Usage: batch integrate <pattern> with <calibration_file> [dark_file]")
                        continue

                    # operation = parts[0]  # e.g., "integrate" - reserved for future
                    pattern = parts[1]    # e.g., "*.ge5"

                    # Parse additional arguments
                    calibration_file = None
                    dark_file = None

                    if 'with' in parts:
                        with_idx = parts.index('with')
                        calibration_file = parts[with_idx + 1] if len(parts) > with_idx + 1 else None
                        dark_file = parts[with_idx + 2] if len(parts) > with_idx + 2 else None

                    # Find files matching pattern
                    from glob import glob
                    files = glob(pattern)

                    if not files:
                        print(f"No files found matching: {pattern}")
                        continue

                    print(f"Found {len(files)} files to process")
                    confirm = await pt_session.prompt_async(f"Process all {len(files)} files? (yes/no): ")

                    if confirm.lower() in ['yes', 'y']:
                        kwargs = {}
                        if calibration_file:
                            kwargs['calibration_file'] = calibration_file
                        if dark_file:
                            kwargs['dark_file'] = dark_file

                        results = await self.batch_processor.process_batch(
                            self,
                            "midas_integrate_2d_to_1d",
                            files,
                            **kwargs
                        )

                        print(f"\n  {C.DIM}{'─' * 44}{C.RESET}")
                        print(f"  {C.BOLD}Batch Processing Complete{C.RESET}")
                        print(f"  {C.GRAY}Total:{C.RESET}       {results['total_files']}")
                        print(f"  {C.GREEN}✓ Successful:{C.RESET} {C.BGREEN}{results['successful']}{C.RESET}")
                        print(f"  {C.RED}✗ Failed:{C.RESET}     {C.BRED}{results['failed']}{C.RESET}")
                        print(f"  {C.DIM}{'─' * 44}{C.RESET}\n")

                elif user_input.lower().startswith('workflow '):
                    # Workflow command
                    # Example: workflow phase_analysis or workflow list
                    workflow_cmd = user_input[9:].strip()

                    if workflow_cmd == 'list':
                        print("\nAvailable Workflows:")
                        print("="*50)
                        for name, steps in self.workflow_builder.workflows.items():
                            print(f"\n{name}:")
                            for i, step in enumerate(steps, 1):
                                print(f"  {i}. {step['description']}")
                    else:
                        workflow = self.workflow_builder.get_workflow(workflow_cmd)
                        if workflow:
                            print(f"\nExecuting workflow: {workflow_cmd}")
                            print("="*50)
                            for i, step in enumerate(workflow, 1):
                                print(f"\nStep {i}: {step['description']}")
                                # Note: Would need user input for arguments
                                print(f"  Tool: {step['tool']}")
                            print("\nNote: Use natural language queries to execute workflows with your data")
                        else:
                            print(f"Unknown workflow: {workflow_cmd}")
                            print("Use 'workflow list' to see available workflows")

                elif user_input.lower().startswith('session '):
                    # Session management
                    # Example: session save my_experiment, session load my_experiment, session list
                    session_cmd = user_input[8:].strip().split()

                    if not session_cmd:
                        print("Usage: session <new|save|load|switch|append|resume|list|summary> [name]")
                        continue

                    action = session_cmd[0]

                    if action == 'new':
                        # Wind up the current session (archive it if non-empty),
                        # then start a fresh, empty one.
                        convo = self.orchestrator.export_history() if self.orchestrator else []
                        if convo:
                            summ = self.orchestrator.export_summary() if self.orchestrator else ""
                            archived = self.context.save_session(
                                None, conversation=convo, summary=summ)
                            print(f"  {C.GREEN}✓{C.RESET} Previous session archived: "
                                  f"{C.CYAN}{archived.stem}{C.RESET} "
                                  f"{C.DIM}({len(convo)} messages — resume later with "
                                  f"'session load {archived.stem}'){C.RESET}")
                        new_name = session_cmd[1] if len(session_cmd) > 1 else None
                        try:
                            slot = self.context.start_new(new_name)
                        except FileExistsError:
                            print(f"  {C.RED}✗{C.RESET} Session '{new_name}' already exists — "
                                  f"pick another name, or 'session load {new_name}' to continue it")
                            continue
                        if self.orchestrator:
                            self.orchestrator.clear_history()
                        label = slot if slot != "_autosave" else "new session (autosaved on exit)"
                        print(f"  {C.GREEN}✓{C.RESET} Started fresh: {C.CYAN}{label}{C.RESET}")

                    elif action == 'save':
                        session_name = session_cmd[1] if len(session_cmd) > 1 else None
                        convo = self.orchestrator.export_history() if self.orchestrator else []
                        summ = self.orchestrator.export_summary() if self.orchestrator else ""
                        saved_file = self.context.save_session(
                            session_name, conversation=convo, summary=summ)
                        extra = f", +summary" if summ else ""
                        print(f"  {C.GREEN}✓{C.RESET} Session saved: {C.CYAN}{saved_file}{C.RESET} "
                              f"{C.DIM}({len(convo)} messages{extra}){C.RESET}")

                    elif action in ('load', 'resume', 'switch'):
                        # load/switch <name>: resume a session AND make it active,
                        #   so subsequent turns append to it (continue it).
                        # resume (no name): reload the most-recent session.
                        if action == 'resume' and len(session_cmd) < 2:
                            session_name = self.context.last_active()
                        elif len(session_cmd) < 2:
                            print(f"Usage: session {action} <session_name>")
                            continue
                        else:
                            session_name = session_cmd[1]

                        # Guard against orphaning unsaved scratch work: if we are
                        # in the rolling _autosave slot with real turns and are
                        # about to switch AWAY to a different named session, those
                        # turns would be stranded in _autosave.jsonl. Named
                        # sessions are always safe on disk, so this only fires from
                        # scratch.
                        convo = self.orchestrator.export_history() if self.orchestrator else []
                        if (self.context.active_session == "_autosave"
                                and convo
                                and session_name != "_autosave"
                                and session_name != self.context.active_session):
                            summ = self.orchestrator.export_summary() if self.orchestrator else ""
                            print(f"  {C.YELLOW}⚠{C.RESET} You have {len(convo)} unsaved "
                                  f"message(s) in the current scratch session.")
                            print(f"    {C.CYAN}[a]{C.RESET}ppend into '{session_name}'   "
                                  f"{C.CYAN}[s]{C.RESET}ave separately   "
                                  f"{C.CYAN}[d]{C.RESET}iscard   "
                                  f"{C.CYAN}[c]{C.RESET}ancel")
                            choice = (await pt_session.prompt_async("  Choice: ")).strip().lower()
                            if choice in ('a', 'append'):
                                merged = self.context.append_to(
                                    session_name, convo, summary=summ)
                                if self.orchestrator:
                                    self.orchestrator.import_history(self.context.loaded_conversation)
                                    self.orchestrator.import_summary(self.context.loaded_summary)
                                print(f"  {C.GREEN}✓{C.RESET} Appended {len(convo)} message(s) "
                                      f"into {C.CYAN}{session_name}{C.RESET} "
                                      f"{C.DIM}({merged - len(convo)}→{merged}; now active){C.RESET}")
                                print(self.context.get_summary())
                                continue
                            elif choice in ('s', 'save'):
                                newname = (await pt_session.prompt_async(
                                    "  Save scratch as (name): ")).strip()
                                if not newname:
                                    print(f"  {C.DIM}No name given — staying put.{C.RESET}")
                                    continue
                                saved_file = self.context.save_session(
                                    newname, conversation=convo, summary=summ)
                                print(f"  {C.GREEN}✓{C.RESET} Scratch saved: "
                                      f"{C.CYAN}{saved_file.stem}{C.RESET} "
                                      f"{C.DIM}({len(convo)} messages){C.RESET}")
                                # fall through to load the requested target below
                            elif choice in ('d', 'discard'):
                                self.context.discard_scratch()
                                print(f"  {C.DIM}Scratch discarded.{C.RESET}")
                                # fall through to load the requested target below
                            else:
                                print(f"  {C.DIM}Cancelled — staying in the current "
                                      f"session.{C.RESET}")
                                continue

                        # winding up the current session is automatic: its turns
                        # are already on disk in its append-only transcript.
                        if self.context.load_session(session_name):
                            restored = self.context.loaded_conversation
                            restored_summary = self.context.loaded_summary
                            if self.orchestrator:
                                self.orchestrator.import_history(restored)
                                self.orchestrator.import_summary(restored_summary)
                            extra = ", +summary" if restored_summary else ""
                            print(f"  {C.GREEN}✓{C.RESET} Session loaded: {C.CYAN}{session_name}{C.RESET} "
                                  f"{C.DIM}({len(restored)} messages restored{extra}){C.RESET}")
                            print(self.context.get_summary())
                        else:
                            where = "autosave" if session_name == "_autosave" else session_name
                            print(f"  {C.RED}✗{C.RESET} Session not found: {where}")

                    elif action == 'append':
                        # session append <name>: merge the current turns into an
                        # existing (or new) session and continue there.
                        if len(session_cmd) < 2:
                            print("Usage: session append <session_name>")
                            continue
                        session_name = session_cmd[1]
                        if session_name == self.context.active_session:
                            print(f"  {C.DIM}Already in '{session_name}' — nothing to "
                                  f"append.{C.RESET}")
                            continue
                        convo = self.orchestrator.export_history() if self.orchestrator else []
                        summ = self.orchestrator.export_summary() if self.orchestrator else ""
                        if not convo:
                            print(f"  {C.DIM}No current turns to append — use "
                                  f"'session load {session_name}' instead.{C.RESET}")
                            continue
                        merged = self.context.append_to(
                            session_name, convo, summary=summ)
                        if self.orchestrator:
                            self.orchestrator.import_history(self.context.loaded_conversation)
                            self.orchestrator.import_summary(self.context.loaded_summary)
                        print(f"  {C.GREEN}✓{C.RESET} Appended {len(convo)} message(s) into "
                              f"{C.CYAN}{session_name}{C.RESET} "
                              f"{C.DIM}({merged - len(convo)}→{merged}; now active){C.RESET}")
                        print(self.context.get_summary())

                    elif action == 'list':
                        sessions = self.context.list_sessions()
                        active = self.context.active_session
                        if sessions:
                            print("\nSaved Sessions:")
                            for session in sessions:
                                if session == active:
                                    print(f"  {C.GREEN}●{C.RESET} {C.CYAN}{session}{C.RESET} "
                                          f"{C.DIM}(active){C.RESET}")
                                else:
                                    print(f"    {session}")
                        else:
                            print("No saved sessions found")

                    elif action == 'summary':
                        print("\nCurrent Session:")
                        print(self.context.get_summary())

                    elif action in ('current', 'status'):
                        active = self.context.active_session
                        where = "autosave (unsaved scratch)" if active == "_autosave" else active
                        n_msg = len(self.orchestrator.export_history()) if self.orchestrator else 0
                        print(f"\n  {C.GREEN}●{C.RESET} Current session: {C.CYAN}{where}{C.RESET} "
                              f"{C.DIM}({n_msg} messages){C.RESET}")
                        print(self.context.get_summary())

                    else:
                        print(f"Unknown session command: {action}")
                        print("Available: new, save, load, switch, append, resume, list, summary, current")

                elif user_input.lower().startswith('image '):
                    # Image analysis command
                    # Example: image analyze sample.ge5, image quality sample.ge5
                    # Also handles: "image quality of the file.ge5", "image quality for file.ge5"
                    cmd_text = user_input[6:].strip()

                    # Extract action (analyze, quality, rings)
                    action = None
                    for possible_action in ['analyze', 'quality', 'rings']:
                        if cmd_text.lower().startswith(possible_action):
                            action = possible_action
                            # Remove action from text
                            cmd_text = cmd_text[len(possible_action):].strip()
                            break

                    if not action:
                        print("Usage: image <analyze|quality|rings> <image_path>")
                        print("Examples:")
                        print("  image quality sample.ge5")
                        print("  image quality of the .tiff file")
                        print("  image analyze data.ge2")
                        continue

                    # Remove common filler words to find the actual file
                    filler_words = ['of', 'the', 'for', 'in', 'file', 'image', 'this', 'directory', 'a', 'an']
                    words = cmd_text.split()

                    # Find file extensions in the text
                    image_path = None
                    for word in words:
                        # Check if it looks like a file path or has an extension
                        if '.' in word and any(word.endswith(ext) for ext in ['.tif', '.tiff', '.ge2', '.ge5', '.ed5', '.edf']):
                            image_path = word
                            break
                        # Check if it contains a path separator
                        if '/' in word or word.startswith('~'):
                            image_path = word
                            break

                    # If no explicit path found, try to find files with mentioned extension
                    if not image_path:
                        # Look for extension mentions like ".tiff" or ".ge5"
                        for word in words:
                            if word.startswith('.'):
                                # Find files with this extension in current directory
                                from glob import glob
                                ext = word
                                matching_files = glob(f'*{ext}')
                                if matching_files:
                                    image_path = matching_files[0]
                                    print(f"Found: {image_path}")
                                    break

                    if not image_path:
                        # Try to use any word that's not a filler word
                        for word in words:
                            if word.lower() not in filler_words:
                                image_path = word
                                break

                    if not image_path:
                        print("Could not find image file in command")
                        print("Please specify the image file name")
                        print("Examples:")
                        print("  image quality sample.ge5")
                        print("  image quality /path/to/data.tiff")
                        continue

                    if action == 'analyze':
                        print(f"\n  {C.CYAN}▸{C.RESET} Analyzing image: {C.BOLD}{image_path}{C.RESET}")
                        summary = self.image_analyzer.create_image_summary(image_path)
                        print(summary)

                    elif action == 'quality':
                        print(f"\n  {C.CYAN}▸{C.RESET} Quality check: {C.BOLD}{image_path}{C.RESET}")
                        quality = self.image_analyzer.analyze_image_quality(image_path)
                        if 'error' in quality:
                            print(f"  {C.RED}✗{C.RESET} Error: {quality['error']}")
                        else:
                            qcolor = C.BGREEN if quality['overall_quality'] == 'good' else C.BYELLOW if quality['overall_quality'] == 'fair' else C.BRED
                            print(f"  {C.GRAY}Quality:{C.RESET}     {qcolor}{C.BOLD}{quality['overall_quality']}{C.RESET}")
                            print(f"  {C.GRAY}SNR:{C.RESET}         {quality['signal_to_noise']:.1f}")
                            print(f"  {C.GRAY}Saturation:{C.RESET}  {quality['saturation_percent']:.2f}%")
                            if quality['issues']:
                                print(f"  {C.YELLOW}Issues:{C.RESET}")
                                for issue in quality['issues']:
                                    print(f"    {C.YELLOW}•{C.RESET} {issue}")

                    elif action == 'rings':
                        print(f"\n  {C.CYAN}▸{C.RESET} Ring detection: {C.BOLD}{image_path}{C.RESET}")
                        rings = self.image_analyzer.detect_rings_visual(image_path)
                        if 'error' in rings:
                            print(f"  {C.RED}✗{C.RESET} Error: {rings['error']}")
                        else:
                            print(f"  {C.GRAY}Rings:{C.RESET}   {C.BGREEN}{rings['rings_detected']}{C.RESET}")
                            print(f"  {C.GRAY}Radii:{C.RESET}   {rings['ring_radii_pixels']}")
                            print(f"  {C.GRAY}Quality:{C.RESET} {rings['quality']}")

                    else:
                        print(f"Unknown image command: {action}")
                        print("Available: analyze, quality, rings")

                elif user_input.lower().startswith('monitor '):
                    # Real-time monitoring command
                    # Example: monitor start /data/experiment, monitor stop, monitor status
                    parts = user_input[8:].strip().split()
                    if not parts:
                        print("Usage: monitor <start|stop|status|check> [directory]")
                        continue

                    action = parts[0]

                    if action == 'start':
                        if len(parts) < 2:
                            print("Usage: monitor start <directory>")
                            continue
                        directory = parts[1]
                        result = self.realtime_feedback.start_monitoring(directory)
                        print(f"\n🔄 {result['message']}")
                        print(f"   Checking every {result['check_interval']} seconds")
                        print(f"   Press Ctrl+C to stop or use 'monitor stop'")

                    elif action == 'stop':
                        result = self.realtime_feedback.stop_monitoring()
                        print(f"\n⏹️  Monitoring stopped")
                        print(f"   Files processed: {result['files_processed']}")
                        print(f"   Alerts generated: {result['alerts_generated']}")

                    elif action == 'status':
                        summary = self.realtime_feedback.get_session_summary()
                        print(f"\n📊 Monitoring Status:")
                        print(f"   Active: {summary['monitoring_active']}")
                        if summary['monitoring_active']:
                            print(f"   Directory: {summary['directory']}")
                        print(f"   Files Processed: {summary['files_processed']}")
                        print(f"   Total Alerts: {summary['total_alerts']}")
                        print(f"     ⚠️  Warnings: {summary['warning_alerts']}")
                        print(f"     🚨 Critical: {summary['critical_alerts']}")

                        if summary['recent_alerts']:
                            print(f"\n   Recent Alerts:")
                            for alert in summary['recent_alerts']:
                                icon = "🚨" if alert['level'] == 'CRITICAL' else "⚠️" if alert['level'] == 'WARNING' else "ℹ️"
                                print(f"     {icon} {alert['message']}")

                    elif action == 'check':
                        new_files = self.realtime_feedback.check_new_files()
                        if not new_files:
                            print("\n✓ No new files detected")
                        else:
                            print(f"\n🆕 Found {len(new_files)} new file(s):")
                            for analysis in new_files:
                                print(f"\n  📁 {analysis['file']}")
                                print(f"     Quality: {analysis['quality']['overall_quality']}")
                                print(f"     Rings: {analysis['rings']['rings_detected']}")
                                if analysis['alerts']:
                                    for alert in analysis['alerts']:
                                        icon = "🚨" if alert['level'] == 'CRITICAL' else "⚠️" if alert['level'] == 'WARNING' else "ℹ️"
                                        print(f"     {icon} {alert['message']}")

                    else:
                        print(f"Unknown monitor command: {action}")
                        print("Available: start, stop, status, check")

                elif user_input.lower().startswith('plot '):
                    # Plotting command
                    # Examples: plot 2d sample.ge5, plot radial data.tiff, plot 1d pattern.dat
                    #           plot compare file1.dat file2.dat file3.dat
                    cmd_text = user_input[5:].strip()

                    # Extract plot type
                    plot_type = None
                    for possible_type in ['2d', 'radial', '1d', 'pattern', 'compare', 'comparison']:
                        if cmd_text.lower().startswith(possible_type):
                            plot_type = possible_type
                            cmd_text = cmd_text[len(possible_type):].strip()
                            break

                    if not plot_type:
                        # Unrecognized plot subcommand — let APEXA handle it naturally
                        response = await self.run_query(user_input, permission_callback=_cli_permission)
                        self._print_response(response)
                        continue

                    # Parse file path(s)
                    files = cmd_text.split()
                    if not files:
                        print("Please specify file(s) to plot")
                        continue

                    # Handle different plot types
                    if plot_type == '2d':
                        if len(files) != 1:
                            print(f"  {C.YELLOW}!{C.RESET} 2D plot requires exactly one image file")
                            continue

                        print(f"\n  {C.CYAN}▸{C.RESET} Plotting 2D image: {C.BOLD}{files[0]}{C.RESET}")
                        result = self.plotting.plot_2d_image(files[0])

                        if result['status'] == 'success':
                            print(f"  {C.GREEN}✓{C.RESET} Plot saved: {C.CYAN}{result['plot_saved']}{C.RESET}")
                            print(f"  {C.GRAY}Mean:{C.RESET} {result['statistics']['mean']:.1f}  {C.GRAY}Max:{C.RESET} {result['statistics']['max']:.1f}  {C.GRAY}Std:{C.RESET} {result['statistics']['std']:.1f}")
                        else:
                            print(f"  {C.RED}✗{C.RESET} Error: {result['error']}")

                    elif plot_type == 'radial':
                        if len(files) != 1:
                            print(f"  {C.YELLOW}!{C.RESET} Radial plot requires exactly one image file")
                            continue

                        print(f"\n  {C.CYAN}▸{C.RESET} Plotting radial profile: {C.BOLD}{files[0]}{C.RESET}")
                        result = self.plotting.plot_radial_profile(files[0])

                        if result['status'] == 'success':
                            print(f"  {C.GREEN}✓{C.RESET} {result['message']}")
                            print(f"  {C.GRAY}Saved:{C.RESET} {C.CYAN}{result['plot_saved']}{C.RESET}")
                        else:
                            print(f"  {C.RED}✗{C.RESET} Error: {result['error']}")

                    elif plot_type in ['1d', 'pattern']:
                        if len(files) != 1:
                            print(f"  {C.YELLOW}!{C.RESET} 1D pattern plot requires exactly one data file")
                            continue

                        print(f"\n  {C.CYAN}▸{C.RESET} Plotting 1D pattern: {C.BOLD}{files[0]}{C.RESET}")
                        result = self.plotting.plot_1d_pattern(files[0])

                        if result['status'] == 'success':
                            print(f"  {C.GREEN}✓{C.RESET} {result['message']}")
                            print(f"  {C.GRAY}Saved:{C.RESET} {C.CYAN}{result['plot_saved']}{C.RESET}")
                        else:
                            print(f"  {C.RED}✗{C.RESET} Error: {result['error']}")

                    elif plot_type in ['compare', 'comparison']:
                        if len(files) < 2:
                            print(f"  {C.YELLOW}!{C.RESET} Comparison requires at least 2 pattern files")
                            continue

                        print(f"\n  {C.CYAN}▸{C.RESET} Comparing {C.BOLD}{len(files)}{C.RESET} patterns...")
                        result = self.plotting.plot_comparison(files)

                        if result['status'] == 'success':
                            print(f"  {C.GREEN}✓{C.RESET} {result['message']}")
                            print(f"  {C.GRAY}Saved:{C.RESET} {C.CYAN}{result['plot_saved']}{C.RESET}")
                        else:
                            print(f"  {C.RED}✗{C.RESET} Error: {result['error']}")

                elif user_input.lower() == 'tools':
                    tools = self._available_tools
                    print(f"\n  {C.BOLD}Available Tools{C.RESET} {C.DIM}({len(tools)}){C.RESET}")
                    print(f"  {C.DIM}{'─' * 44}{C.RESET}")
                    for tool in tools:
                        print(f"  {C.CYAN}{tool['function']['name']:<35}{C.RESET} {C.DIM}{tool['function']['description'][:60]}{C.RESET}")
                elif user_input.lower() == 'stats':
                    stats = self.orchestrator.logger.stats()
                    if stats["total"] == 0:
                        print(f"  {C.DIM}No interactions logged yet.{C.RESET}")
                    else:
                        print(f"\n  {C.BOLD}APEXA Stats{C.RESET} {C.DIM}({stats['total']} queries){C.RESET}")
                        print(f"  {C.DIM}{'─' * 44}{C.RESET}")
                        print(f"  {C.GRAY}Success:{C.RESET}    {C.BGREEN}{stats['success_rate']}{C.RESET}   {C.GRAY}│{C.RESET}  {C.GRAY}Loops:{C.RESET} {stats['loop_rate']}")
                        print(f"  {C.GRAY}Avg tools:{C.RESET}  {stats['avg_tool_calls']}   {C.GRAY}│{C.RESET}  {C.GRAY}Avg iters:{C.RESET} {stats['avg_iterations']}")
                        if stats.get("agent_counts"):
                            print(f"\n  {C.BOLD}Per-agent:{C.RESET}")
                            for agent, count in stats["agent_counts"].items():
                                loop_rate = stats["agent_loop_rates"].get(agent, "0%")
                                print(f"    {C.CYAN}{agent:20s}{C.RESET} {count:4d} queries  {C.DIM}{loop_rate} looped{C.RESET}")
                        print()
                elif user_input.lower() == 'timing':
                    current = os.environ.get("APEXA_SHOW_TIMING")
                    if current:
                        del os.environ["APEXA_SHOW_TIMING"]
                        print(f"  {C.GRAY}Timing display{C.RESET} {C.RED}OFF{C.RESET}")
                    else:
                        os.environ["APEXA_SHOW_TIMING"] = "1"
                        print(f"  {C.GRAY}Timing display{C.RESET} {C.GREEN}ON{C.RESET}")
                elif user_input.lower() == 'help':
                    _print_help()
                elif user_input.startswith('model '):
                    model_name = user_input[6:].strip()
                    # An ALCF model lives on a different CLUSTER, not just a
                    # different name — switching it must also move the preset
                    # (base URL + credential), or we'd ask argo-proxy for a model
                    # it has never heard of. select_provider() re-reads the env
                    # per query, so setting it here takes effect on the next turn.
                    try:
                        from apexa_llm_endpoints import canonical_model_id, preset_for_model
                        _canon = canonical_model_id(model_name)
                        _preset = preset_for_model(_canon)
                    except Exception:
                        _canon, _preset = model_name, None

                    if _preset:
                        os.environ["APEXA_LLM_PRESET"] = _preset
                        os.environ["APEXA_LLM_MODE"] = "proxy"   # ALCF is proxy-path only
                        os.environ.pop("APEXA_LLM_BASE_URL", None)  # let the preset supply it
                        self.selected_model = _canon
                        self.environment = _preset
                        print(f"  {C.GREEN}✓{C.RESET} Switched to: {C.BOLD}{C.CYAN}{_canon}{C.RESET} "
                              f"{C.DIM}via {_preset}{C.RESET}")
                        from apexa_provider_openai import preflight as _pf
                        _ok, _detail = await _pf(self.anl_username, _canon)
                        if not _ok:
                            print(f"  {C.YELLOW}⚠{C.RESET} {_detail}")
                    elif self._is_valid_model(model_name):
                        # An Argo model — restore the Argo transport if we had
                        # previously switched away to an ALCF cluster.
                        if (os.environ.get("APEXA_LLM_PRESET") or "").startswith("alcf"):
                            os.environ["APEXA_LLM_PRESET"] = "argo-proxy"
                            print(f"  {C.DIM}(back to argo-proxy transport){C.RESET}")
                        self.selected_model = model_name
                        self.environment = "DEV" if model_name in DEV_ONLY_MODELS else "PROD"
                        print(f"  {C.GREEN}✓{C.RESET} Switched to: {C.BOLD}{C.CYAN}{model_name}{C.RESET}")
                    else:
                        print(f"  {C.RED}✗{C.RESET} Invalid model: {model_name}")
                        try:
                            from apexa_llm_endpoints import suggest_models
                            _sugg = suggest_models(model_name, 3)
                        except Exception:
                            _sugg = []
                        if not _sugg:
                            # Fall back to the Argo catalogue on token overlap.
                            _toks = set(re.findall(r"[a-z0-9]+", model_name.lower()))
                            _sugg = [m for g in self.available_models.values() for m in g
                                     if _toks & set(re.findall(r"[a-z0-9]+", m.lower()))][:3]
                        if _sugg:
                            print(f"  {C.DIM}Did you mean:{C.RESET} "
                                  + f"{C.DIM},{C.RESET} ".join(f"{C.CYAN}{m}{C.RESET}" for m in _sugg))
                        print(f"  {C.DIM}Type {C.RESET}{C.CYAN}models{C.RESET}{C.DIM} to see available{C.RESET}")
                elif user_input == 'ls' or (user_input.startswith('ls ') and not user_input[3:].lstrip().startswith('-')):
                    path = user_input[2:].strip() or "."
                    if "core" in self.sessions:
                        result = await self.execute_tool_call("list_directory", {"path": path})
                        try:
                            r = json.loads(result)
                            listing = r.get("listing", "")
                            if listing:
                                print(f"\n{listing}\n")
                            else:
                                print(f"\n{result}\n")
                        except (json.JSONDecodeError, TypeError):
                            print(f"\n{result}\n")
                    else:
                        print(f"  {C.RED}✗{C.RESET} Core server not connected")

                elif _is_shell_command(user_input):
                    if "core" in self.sessions:
                        cmd = user_input
                        parts = cmd.split()
                        if parts[0] == 'ls':
                            if not any(f in parts for f in ['-1', '-C', '-m', '-x']):
                                parts.insert(1, '-C')
                            parts.insert(1, '--color=always')
                            cmd = ' '.join(parts)
                        print(f"  {C.DIM}$ {user_input}{C.RESET}")
                        result = await self.execute_tool_call("run_command", {"command": cmd})
                        try:
                            r = json.loads(result)
                            stdout = r.get("stdout", "")
                            stderr = r.get("stderr", "")
                            exit_code = r.get("exit_code", r.get("returncode", None))
                            if stdout:
                                print(stdout.rstrip())
                            if stderr:
                                print(f"{C.RED}{stderr.rstrip()}{C.RESET}")
                            if exit_code and exit_code != 0:
                                print(f"  {C.RED}exit {exit_code}{C.RESET}")
                        except (json.JSONDecodeError, TypeError):
                            print(result)
                    else:
                        print(f"  {C.RED}✗{C.RESET} Core server not connected")

                elif user_input:
                    # Append-only transcript: record the prompt before running
                    # so a crash mid-turn still leaves the question on disk.
                    self.context.append_message("user", user_input)
                    # Run the query as a cancellable task. Ctrl-C during it fires
                    # _on_sigint (POSIX) → task.cancel() → CancelledError here; on
                    # Windows (no signal handler) Ctrl-C raises KeyboardInterrupt at
                    # this await. Either way we abort + kill the busy compute and
                    # return to the prompt instead of exiting the CLI.
                    self._active_task = asyncio.create_task(
                        self.run_query(user_input, permission_callback=_cli_permission))
                    try:
                        response = await self._active_task
                    except (asyncio.CancelledError, KeyboardInterrupt):
                        await self._abort_active_query()
                        continue
                    finally:
                        self._active_task = None
                    self._print_response(response)
                    self.context.append_message("assistant", response)
                    self._autosave_session()

            except KeyboardInterrupt:
                self._autosave_session()
                print(f"\n  {C.DIM}Exiting... (resume with 'session resume'){C.RESET}")
                break
            except EOFError:
                self._autosave_session()
                print(f"\n  {C.DIM}Exiting... (resume with 'session resume'){C.RESET}")
                break
            except Exception as e:
                print(f"  {C.RED}✗{C.RESET} {C.BOLD}Error:{C.RESET} {str(e)}")

    async def cleanup(self):
        # Close each per-server stack (transport + ClientSession) first, then the
        # shared stack. Suppress Exception AND CancelledError: tearing down an mcp
        # stdio_client cancels its internal anyio task group (blocked on the server
        # subprocess's wait()), which surfaces as asyncio.CancelledError. That is a
        # BaseException, so contextlib.suppress(Exception) alone would NOT catch it
        # and it would escape as an ugly traceback on quit. A server whose process
        # tree we already killed on abort may also raise from aclose().
        for name, stack in list(self._server_stacks.items()):
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await stack.aclose()
        self._server_stacks.clear()
        with contextlib.suppress(Exception, asyncio.CancelledError):
            await self.exit_stack.aclose()

async def main():
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python argo_mcp_client.py <server_configs...>")
        sys.exit(1)

    client = APEXAClient()
    
    try:
        server_configs = []
        
        for arg in sys.argv[1:]:
            if ":" in arg:
                name, script_path = arg.split(":", 1)
                server_configs.append({"name": name, "script_path": script_path})
            else:
                server_configs.append({"name": "midas", "script_path": arg})
        
        await client.connect_to_multiple_servers(server_configs)
        
        if not client.sessions:
            print(f"  {C.RED}✗{C.RESET} Failed to connect to any servers")
            sys.exit(1)
            
        await client.interactive_analysis_session()
        
    except KeyboardInterrupt:
        print(f"\n  {C.DIM}Goodbye!{C.RESET}")
    finally:
        await client.cleanup()

if __name__ == "__main__":
    asyncio.run(main())