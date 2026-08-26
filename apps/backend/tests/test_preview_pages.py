import re

import pytest
from fastapi.testclient import TestClient

from app.services.typst_compile import compile_typst, compile_typst_pages, typst_available

pytestmark = pytest.mark.skipif(not typst_available(), reason="Typst CLI is required")

# Distinct page heights so we can tell pages apart: Typst SVG emits glyphs, not text.
TWO_PAGE = """
#set page(width: 10cm, height: 8cm, margin: 8pt)
First page
#pagebreak()
#set page(width: 10cm, height: 14cm, margin: 8pt)
Second page
"""

ONE_PAGE = """
#set page(width: 10cm, height: 8cm, margin: 8pt)
Only page
"""


def _svg_height_pt(blob: bytes) -> float:
    match = re.search(rb'\bheight="([0-9.]+)pt"', blob)
    assert match, blob[:200]
    return float(match.group(1))


def test_compile_svg_pages_returns_every_page():
    pages = compile_typst_pages(TWO_PAGE, "svg")
    assert len(pages) == 2
    for blob in pages:
        assert b"<svg" in blob[:200]
    assert _svg_height_pt(pages[0]) == pytest.approx(8 * 72 / 2.54, rel=0.02)
    assert _svg_height_pt(pages[1]) == pytest.approx(14 * 72 / 2.54, rel=0.02)


def test_compile_svg_pages_single_page_document():
    pages = compile_typst_pages(ONE_PAGE, "svg")
    assert len(pages) == 1
    assert b"<svg" in pages[0][:200]
    assert _svg_height_pt(pages[0]) == pytest.approx(8 * 72 / 2.54, rel=0.02)


def test_compile_pdf_stays_one_file_for_multipage_source():
    pdf = compile_typst(TWO_PAGE, "pdf")
    assert pdf.startswith(b"%PDF")
    pages = compile_typst_pages(TWO_PAGE, "pdf")
    assert len(pages) == 1
    assert pages[0].startswith(b"%PDF")


def test_preview_endpoint_returns_all_svg_pages(client: TestClient):
    created = client.post("/v1/resumes", json={"locale": "en", "title": "Multi"}).json()
    put = client.put(
        f"/v1/resumes/{created['id']}",
        json={"typst_source": TWO_PAGE},
    )
    assert put.status_code == 200
    response = client.get(f"/v1/resumes/{created['id']}/preview")
    assert response.status_code == 200
    pages = response.json()["pages"]
    assert len(pages) == 2
    blobs = [page.encode("utf-8") for page in pages]
    assert _svg_height_pt(blobs[0]) == pytest.approx(8 * 72 / 2.54, rel=0.02)
    assert _svg_height_pt(blobs[1]) == pytest.approx(14 * 72 / 2.54, rel=0.02)
