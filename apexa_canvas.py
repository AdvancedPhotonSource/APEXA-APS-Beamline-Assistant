"""Validated, typed artifacts that MCP tools can deliver to the web canvas."""
from pathlib import Path
from typing import Literal


def science_view(path: str, kind: Literal["auto", "structure", "sequence", "report"] = "auto",
                 dataset: str = "", pattern: str = "*") -> dict:
    from apexa_viewer_data import structure_payload, inspect_sequence, read_frame

    source = Path(path).expanduser().resolve(strict=True)
    if kind not in ("auto", "structure", "sequence", "report"):
        raise ValueError("kind must be auto, structure, sequence, or report")
    if kind == "auto":
        kind = "report" if source.suffix.lower() in (".html", ".htm") else "structure" if source.suffix.lower() == ".cif" else "sequence"
    data = {"path": str(source), "location": "local"}
    title = source.name
    if kind == "report":
        from apexa_report_viewer import report_payload
        payload = report_payload(str(source))
        kind = "html-report"
        title = payload["title"]
        summary = {"title": title, "warnings": payload["warnings"], "validation": "static HTML parsed"}
    elif kind == "structure":
        # A suffix chooses a parser; only successfully parsed contents qualify.
        structure = structure_payload(str(source))
        summary = {"name": structure["name"], "space_group": structure["space_group"],
                   "sites": len(structure["atoms"]), "cell": structure["cell"]}
        data["source"] = "Local CIF"
    else:
        manifest = inspect_sequence(str(source), pattern=pattern, dataset=dataset)
        # Check that the first selected frame is readable, not just its header.
        read_frame(manifest["sources"][0], 0)
        summary = {"frames": manifest["frames"], "shape": manifest["shape"],
                   "ordering": manifest["ordering"], "validation": "first frame read; remaining frames inspected by metadata"}
        data.update(dataset=dataset, pattern=pattern)
    return {"tool": "open_science_view", "status": "success", "summary": summary,
            "canvas": {"schema_version": 1, "type": kind, "title": title,
                       "data": data},
            "message": "Validated file; native canvas view requested. The browser reports rendering errors separately."}
