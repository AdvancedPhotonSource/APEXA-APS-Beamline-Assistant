"""Experiment identity — who is running what, and what to call the report.

APEXA has never known which experiment it is working on. ``ExperimentContext``
(argo_mcp_client.py) declares ``experiment_id``/``sample_name``/``beamline`` and
nothing has ever set them, so every session prints "Unnamed" and every artifact
is named after whatever directory it landed in.

The ESAF already carries exactly this: the number, the title, the PI, who is on
the floor, the beamline, the dates. Here it is read from a **file exported
beside the data** -- no credentials, no network, works on an air-gapped host.
``ESAFSource`` is an interface so a web-service backend can be added later
without touching anything that consumes a record.

Nothing here invents a value. A field the ESAF does not carry stays ``None`` and
is omitted downstream, because a report header that quietly guesses the PI is
worse than one that leaves it blank.

Leaf module: stdlib only at import time. pypdf / python-docx are imported lazily
and only when an ESAF arrives in those formats, so importing this never drags in
an optional dependency (and never drags in an MCP server).
"""
from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

__all__ = [
    "ESAF", "ESAFSource", "FileESAFSource", "load_esaf", "find_esaf_file",
    "report_filename", "unique_path", "slug",
]

# Where an exported ESAF is looked for, in order, walking UP from the data dir.
ESAF_BASENAMES: Sequence[str] = (
    "esaf.json", "ESAF.json", "esaf.csv", "ESAF.csv",
    "esaf.txt", "ESAF.txt", "esaf.pdf", "ESAF.pdf", "esaf.docx", "ESAF.docx",
)
# How far up to walk. A beamtime tree is typically
# /gdata/dm/<sector>/<cycle>/<proposal>/analysis/<run>, so the ESAF usually sits
# 2-4 levels above a run directory.
_WALK_UP = 6


# ── the record ────────────────────────────────────────────────────────────────
@dataclass
class ESAF:
    """One experiment's identity. Every field is optional on purpose."""
    esaf_id: Optional[str] = None
    title: Optional[str] = None
    pi: Optional[str] = None
    experimenters: List[str] = field(default_factory=list)
    beamline: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    proposal: Optional[str] = None
    source_path: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def is_empty(self) -> bool:
        """True when nothing identifying was recovered.

        Checked by callers before using this to name a file -- an all-None
        record must fall back to asking, not produce `None_None_...html`.
        """
        return not any((self.esaf_id, self.title, self.pi, self.beamline,
                        self.proposal, self.experimenters))

    def header_pairs(self) -> List[tuple]:
        """(label, value) for the report header, omitting what is unknown."""
        out: List[tuple] = []
        if self.esaf_id:
            out.append(("ESAF", self.esaf_id))
        if self.proposal:
            out.append(("Proposal", self.proposal))
        if self.beamline:
            out.append(("Beamline", self.beamline))
        if self.pi:
            out.append(("PI", self.pi))
        if self.experimenters:
            # The PI is usually also listed as an experimenter; showing the name
            # twice in a six-cell header reads like a bug. Compare on a sorted
            # token set, because the two lists rarely agree on order --
            # "Tripathi, Pawan" as PI against "Pawan Tripathi" in the roster.
            pi_key = _name_key(self.pi)
            others = [e for e in self.experimenters
                      if not pi_key or _name_key(e) != pi_key]
            if others:
                out.append(("Experimenters", ", ".join(others)))
        if self.start_date or self.end_date:
            span = " – ".join(x for x in (self.start_date, self.end_date) if x)
            out.append(("Dates", span))
        return out


# ── field mapping ─────────────────────────────────────────────────────────────
# Exports differ by tool and by who made them, so match on a normalised key
# rather than an exact spelling. Order matters: first alias that hits wins.
_ALIASES: Dict[str, Sequence[str]] = {
    "esaf_id":    ("esafid", "esaf", "esafnumber", "esafno", "id"),
    "title":      ("experimenttitle", "esaftitle", "title", "experimentname", "name"),
    "pi":         ("pi", "principalinvestigator", "experimentpi", "spokesperson",
                   "piname", "leader"),
    "beamline":   ("beamline", "beamlines", "sector", "station", "instrument"),
    "start_date": ("experimentstartdate", "startdate", "start", "begin", "from"),
    "end_date":   ("experimentenddate", "enddate", "end", "to", "until"),
    "proposal":   ("proposal", "proposalid", "gup", "gupnumber", "proposalnumber"),
}
_EXPERIMENTER_KEYS = ("experimenters", "experimenter", "users", "participants",
                      "experimenterlist", "people", "team")


def _norm(k: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(k).lower())


def _name_key(name: Optional[str]) -> str:
    """Order-insensitive identity for a person's name.

    "Tripathi, Pawan" and "Pawan Tripathi" are one person; a plain string
    compare says otherwise and the header lists them twice. Initials are kept
    as-is rather than expanded -- "J. Park" and "Jaeyoung Park" are NOT merged,
    because guessing that two people are one is the worse error.
    """
    if not name:
        return ""
    toks = [t for t in re.split(r"[^A-Za-z0-9]+", name.lower()) if t]
    return " ".join(sorted(toks))


def _as_people(v: Any) -> List[str]:
    """Normalise however an export spelled a people list into [str]."""
    if v is None:
        return []
    if isinstance(v, str):
        # "Smith, John; Doe, Jane" or newline/pipe separated. Comma alone is
        # ambiguous ("Last, First"), so only split on it when no stronger
        # separator is present AND it does not look like Last, First pairs.
        for sep in (";", "|", "\n"):
            if sep in v:
                return [p.strip() for p in v.split(sep) if p.strip()]
        parts = [p.strip() for p in v.split(",") if p.strip()]
        return parts if len(parts) > 2 else ([v.strip()] if v.strip() else [])
    if isinstance(v, dict):
        v = list(v.values())
    out: List[str] = []
    for item in v or []:
        if isinstance(item, str):
            if item.strip():
                out.append(item.strip())
        elif isinstance(item, dict):
            # {"firstName": ..., "lastName": ...} or {"name": ...}
            nm = (item.get("name") or item.get("fullName")
                  or " ".join(str(item.get(k, "")).strip()
                              for k in ("firstName", "first_name",
                                        "lastName", "last_name")).strip())
            if nm:
                out.append(re.sub(r"\s+", " ", nm))
    return out


def _from_mapping(d: Dict[str, Any]) -> ESAF:
    flat: Dict[str, Any] = {}

    def walk(obj: Any, depth: int = 0) -> None:
        # Exports nest the record under "esaf" or "experiment" about half the
        # time; flattening means one alias table covers both shapes.
        if depth > 4 or not isinstance(obj, dict):
            return
        for k, v in obj.items():
            nk = _norm(k)
            if isinstance(v, dict):
                walk(v, depth + 1)
                continue
            flat.setdefault(nk, v)

    walk(d)
    rec = ESAF(raw=d)
    for attr, aliases in _ALIASES.items():
        for a in aliases:
            if a in flat and flat[a] not in (None, "", []):
                setattr(rec, attr, str(flat[a]).strip())
                break
    for k in _EXPERIMENTER_KEYS:
        if k in flat:
            rec.experimenters = _as_people(flat[k])
            if rec.experimenters:
                break
    if not rec.experimenters:
        for key in _EXPERIMENTER_KEYS:
            if key in d:
                rec.experimenters = _as_people(d[key])
                break
    return rec


# ── text / PDF fallback ───────────────────────────────────────────────────────
_LABEL_RE = re.compile(
    r"^\s*(ESAF(?:\s*(?:Id|ID|Number|No\.?))?|Experiment\s+Title|Title|"
    r"Principal\s+Investigator|PI|Spokesperson|Beamline\(?s?\)?|Sector|"
    r"Experiment\s+Start\s+Date|Start\s+Date|Experiment\s+End\s+Date|End\s+Date|"
    r"Proposal(?:\s*(?:Id|ID|Number))?|GUP(?:\s*Number)?|Experimenters?)"
    r"\s*[:\-]\s*(.+?)\s*$", re.I)


def _from_text(text: str) -> ESAF:
    """Recover fields from a `Label: value` dump (PDF/DOCX/TXT export)."""
    pairs: Dict[str, Any] = {}
    for line in text.splitlines():
        m = _LABEL_RE.match(line)
        if m:
            pairs.setdefault(_norm(m.group(1)), m.group(2).strip())
    return _from_mapping(pairs)


def _pdf_text(path: Path) -> str:
    try:
        try:
            from pypdf import PdfReader
        except ImportError:
            from PyPDF2 import PdfReader  # type: ignore
    except ImportError:
        raise RuntimeError(
            "reading an ESAF PDF needs pypdf (pip install pypdf); "
            "export the ESAF as JSON or CSV instead")
    return "\n".join((p.extract_text() or "") for p in PdfReader(str(path)).pages)


def _docx_text(path: Path) -> str:
    try:
        import docx  # type: ignore
    except ImportError:
        raise RuntimeError(
            "reading an ESAF .docx needs python-docx; "
            "export the ESAF as JSON or CSV instead")
    d = docx.Document(str(path))
    lines = [p.text for p in d.paragraphs]
    for t in d.tables:                      # ESAF exports often use a 2-col table
        for row in t.rows:
            cells = [c.text.strip() for c in row.cells]
            if len(cells) >= 2 and cells[0]:
                lines.append(f"{cells[0]}: {cells[1]}")
    return "\n".join(lines)


def _csv_record(text: str) -> Dict[str, Any]:
    """Accept both CSV shapes seen in the wild."""
    rows = list(csv.reader(io.StringIO(text)))
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        return {}
    # header + one data row
    if len(rows) >= 2 and len(rows[0]) > 2:
        return {k: v for k, v in zip(rows[0], rows[1]) if str(k).strip()}
    # key,value per line
    return {r[0]: (r[1] if len(r) > 1 else "") for r in rows if r and r[0].strip()}


# ── loading ───────────────────────────────────────────────────────────────────
def load_esaf(path) -> ESAF:
    """Parse one exported ESAF. Raises only on an unreadable/unknown file."""
    p = Path(path).expanduser()
    if not p.is_file():
        raise FileNotFoundError(f"no ESAF file at {p}")
    ext = p.suffix.lower()
    if ext == ".json":
        rec = _from_mapping(json.loads(p.read_text(errors="ignore")))
    elif ext == ".csv":
        rec = _from_mapping(_csv_record(p.read_text(errors="ignore")))
    elif ext == ".pdf":
        rec = _from_text(_pdf_text(p))
    elif ext == ".docx":
        rec = _from_text(_docx_text(p))
    elif ext in (".txt", ".md", ""):
        rec = _from_text(p.read_text(errors="ignore"))
    else:
        raise ValueError(f"unsupported ESAF format {ext!r} "
                         f"(use .json, .csv, .txt, .pdf or .docx)")
    rec.source_path = str(p)
    return rec


def find_esaf_file(start, explicit=None) -> Optional[Path]:
    """Locate an exported ESAF at or above ``start``.

    Walks up because the ESAF describes the whole beamtime while a report is
    generated from one run directory several levels below it.
    """
    if explicit:
        p = Path(explicit).expanduser()
        if p.is_file():
            return p
        if p.is_dir():
            for name in ESAF_BASENAMES:
                if (p / name).is_file():
                    return p / name
        return None
    d = Path(start).expanduser()
    d = d if d.is_dir() else d.parent
    for _ in range(_WALK_UP):
        for name in ESAF_BASENAMES:
            cand = d / name
            if cand.is_file():
                return cand
        if d.parent == d:
            break
        d = d.parent
    return None


class ESAFSource:
    """Where experiment identity comes from. One method, so the web-service
    backend can slot in later without changing a single consumer."""

    def get(self, run_dir, explicit=None) -> Optional[ESAF]:  # pragma: no cover
        raise NotImplementedError


class FileESAFSource(ESAFSource):
    """An ESAF exported beside the data. Works air-gapped; the default."""

    def get(self, run_dir, explicit=None) -> Optional[ESAF]:
        path = find_esaf_file(run_dir, explicit)
        if not path:
            return None
        try:
            rec = load_esaf(path)
        except Exception:
            # A malformed ESAF must not take the report down with it -- the
            # report is still worth having without a header.
            return None
        return None if rec.is_empty else rec


# ── naming ────────────────────────────────────────────────────────────────────
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def slug(s: Optional[str], maxlen: int = 40) -> str:
    """Filename-safe, cross-platform, never empty-after-cleaning surprises."""
    if not s:
        return ""
    out = _SAFE.sub("-", str(s).strip()).strip("-._")
    if len(out) > maxlen:
        out = out[:maxlen].rstrip("-._")
    return out


def unique_path(directory, base: str, ext: str = ".html") -> Path:
    """`base.ext`, or `base-2.ext`, … Never overwrites an existing report."""
    d = Path(directory)
    cand = d / f"{base}{ext}"
    n = 2
    while cand.exists():
        cand = d / f"{base}-{n}{ext}"
        n += 1
    return cand


def report_filename(esaf: Optional[ESAF] = None, *, sample: Optional[str] = None,
                    stage: Optional[str] = None, scans: Optional[str] = None,
                    when: Optional[datetime] = None, fallback: str = "run") -> str:
    """Build the report basename (no extension, no directory).

        123456_Ceria_calibration_000268_20261013-1540

    Only parts that are actually known appear; nothing is invented. If the ESAF
    and every hint are missing, the caller still gets a timestamped name rather
    than an exception -- a report that exists under a dull name beats no report.
    """
    when = when or datetime.now()
    parts = [slug(esaf.esaf_id if esaf else None, 16),
             slug(sample, 32), slug(stage, 24), slug(scans, 24)]
    parts = [p for p in parts if p]
    if not parts:
        parts = [slug(esaf.title if esaf else None, 32) or fallback]
    parts.append(when.strftime("%Y%m%d-%H%M"))
    return "_".join(parts)
