import base64
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from apexa_canvas import science_view
from apexa_report import ReportSpec, Plate, render_html
from apexa_report_viewer import MAX_REPORT_BYTES, report_payload
from apexa_viewer_api import router


@pytest.fixture
def report_client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_generated_html_opens_with_embedded_figures(tmp_path, report_client):
    image = tmp_path / "figure.png"
    Image.new("RGB", (8, 8), "white").save(image)
    spec = ReportSpec(title="FF quality review", plates=[Plate("qc", "QC", base64.b64encode(image.read_bytes()).decode())])
    path = tmp_path / "report.html"
    path.write_text(render_html(spec), encoding="utf-8")
    result = science_view(str(path))
    assert result["status"] == "success"
    assert result["canvas"]["type"] == "html-report"
    assert result["canvas"]["data"]["location"] == "local"
    preview = report_client.get("/api/science/report", params={"path": result["canvas"]["data"]["path"]})
    assert preview.status_code == 200
    assert "FF quality review" in preview.json()["title"]
    assert "data:image/png;base64," in preview.json()["html"]
    assert "<script" not in preview.json()["html"]


def test_relative_figures_validate_content_and_stay_with_report(tmp_path):
    image = tmp_path / "figure.not_a_png_extension"
    Image.new("RGB", (8, 8), "white").save(image, format="PNG")
    path = tmp_path / "report.HTM"
    path.write_text('<html><head><title>Scan &amp; QC</title></head><body><img src="figure.not_a_png_extension"></body></html>')
    result = report_payload(str(path))
    assert result["title"] == "Scan & QC"
    assert "data:image/png;base64," in result["html"]
    assert result["warnings"] == []


@pytest.mark.parametrize("reference", ["../secret.png", "%2E%2E/secret.png", "linked.png", "https://example.test/secret.png", "//example.test/secret.png", "missing.png"])
def test_no_external_parent_or_symlink_figure_reads(tmp_path, reference):
    Image.new("RGB", (8, 8)).save(tmp_path / "secret.png")
    folder = tmp_path / "report"
    folder.mkdir()
    (folder / "linked.png").symlink_to(tmp_path / "secret.png")
    path = folder / "index.html"
    path.write_text(f'<h1>Report</h1><img src="{reference}">')
    result = report_payload(str(path))
    assert result["warnings"]
    assert "data:image" not in result["html"]


def test_static_preview_removes_active_document_content(tmp_path):
    path = tmp_path / "report.html"
    path.write_text('''<html><head><base href="https://example.test"><meta http-equiv="refresh" content="0;url=https://example.test"></head>
    <body onload="alert(1)"><h1>Scientific result</h1><script>alert(1)</script><iframe src="https://example.test"></iframe>
    <a href="https://example.test">external</a><a href="#qc">QC</a><img src="https://example.test" onerror="alert(1)"></body></html>''')
    result = report_payload(str(path))
    for forbidden in ["<script", "<iframe", "<base", "<meta", "onerror", "onload", "https://example.test"]:
        assert forbidden not in result["html"]
    assert 'href="#qc"' in result["html"]
    assert "Scientific result" in result["html"]
    assert result["warnings"]


@pytest.mark.parametrize("body", [b"", b"not an HTML report", b"<h1>bad\x00</h1>", b"\xff\xfe"])
def test_bad_contents_never_become_successful_reports(tmp_path, body, report_client):
    path = tmp_path / "report.html"
    path.write_bytes(body)
    with pytest.raises(ValueError):
        science_view(str(path), kind="report")
    assert report_client.get("/api/science/report", params={"path": str(path)}).status_code == 422


def test_missing_oversized_and_wrong_format_report(tmp_path, report_client):
    large = tmp_path / "large.html"
    with large.open("wb") as stream:
        stream.truncate(MAX_REPORT_BYTES + 1)
    unsupported = tmp_path / "report.pdf"
    unsupported.write_bytes(b"%PDF-1.7")
    for path in [large, unsupported, tmp_path / "missing.html", tmp_path]:
        assert report_client.get("/api/science/report", params={"path": str(path)}).status_code == 422


def test_refresh_reads_updated_report(tmp_path, report_client):
    path = tmp_path / "report.html"
    path.write_text("<h1>First snapshot</h1>")
    assert "First snapshot" in report_client.get("/api/science/report", params={"path": str(path)}).json()["html"]
    path.write_text("<h1>Second snapshot</h1>")
    assert "Second snapshot" in report_client.get("/api/science/report", params={"path": str(path)}).json()["html"]
