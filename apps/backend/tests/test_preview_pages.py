import re

import pytest
from fastapi.testclient import TestClient

from app.services.typst_compile import (
    compile_preview_pages,
    compile_typst,
    compile_typst_pages,
    sanitize_typst_svg,
    typst_available,
)

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


_RAW_AMP = re.compile(r"&(?!(?:#\d+|#x[0-9A-Fa-f]+|[A-Za-z][A-Za-z0-9]*);)")

LINK_PAGE = """
#set page(width: 10cm, height: 4cm, margin: 8pt)
#link("https://example.com/search/?api=1&query=Taipei")[Maps]
"""


def test_sanitize_typst_svg_escapes_raw_ampersands_in_urls():
    raw = '<svg><a href="https://maps.example/?api=1&query=Taipei">x</a></svg>'
    out = sanitize_typst_svg(raw)
    assert "api=1&amp;query=Taipei" in out
    assert sanitize_typst_svg("a &amp; b &#123; c &#x1f;") == "a &amp; b &#123; c &#x1f;"


def test_compile_svg_escapes_ampersands_in_links():
    pages = compile_typst_pages(LINK_PAGE, "svg")
    svg = pages[0].decode("utf-8")
    assert "query=" in svg
    assert _RAW_AMP.search(svg) is None


def test_compile_png_pages_returns_png():
    pages = compile_typst_pages(ONE_PAGE, "png")
    assert len(pages) == 1
    assert pages[0][:8] == b"\x89PNG\r\n\x1a\n"


def test_compile_png_pages_arg_only_first_page():
    pages = compile_typst_pages(TWO_PAGE, "png", pages="1")
    assert len(pages) == 1
    assert pages[0][:8] == b"\x89PNG\r\n\x1a\n"


def test_compile_png_ppi_doubles_pixel_width():
    from io import BytesIO

    from PIL import Image

    lo = compile_typst_pages(ONE_PAGE, "png", ppi=72)
    hi = compile_typst_pages(ONE_PAGE, "png", ppi=144)
    w72 = Image.open(BytesIO(lo[0])).size[0]
    w144 = Image.open(BytesIO(hi[0])).size[0]
    assert w144 == pytest.approx(w72 * 2, rel=0.05)


SELECTABLE = """
#set page(width: 10cm, height: 8cm, margin: 8pt)
#text(size: 20pt)[Ada Lovelace]

Analytical Engine
"""


def _span_texts(layer: dict) -> list[str]:
    return [span["text"] for span in layer["spans"]]


def test_preview_pages_carry_a_text_layer_per_page():
    preview = compile_preview_pages(TWO_PAGE)
    assert len(preview["text_layers"]) == len(preview["pages"]) == 2
    assert "First page" in _span_texts(preview["text_layers"][0])
    assert "Second page" in _span_texts(preview["text_layers"][1])


def test_text_layer_coordinates_match_the_svg_viewbox():
    """The overlay only lines up if a span's units are the SVG's own units."""
    preview = compile_preview_pages(SELECTABLE)
    layer = preview["text_layers"][0]
    svg = preview["pages"][0]
    view_box = re.search(r'viewBox="0 0 ([0-9.]+) ([0-9.]+)"', svg)
    assert view_box
    assert layer["width"] == pytest.approx(float(view_box.group(1)), rel=1e-3)
    assert layer["height"] == pytest.approx(float(view_box.group(2)), rel=1e-3)

    name = next(s for s in layer["spans"] if s["text"] == "Ada Lovelace")
    # Measured from the top edge, inside the 8pt margin, and set at 20pt.
    assert name["x"] == pytest.approx(8, abs=1)
    assert 0 < name["y"] < 30
    assert name["size"] == pytest.approx(20, abs=0.1)
    assert name["width"] > name["size"]


def test_preview_endpoint_returns_text_layers(client: TestClient):
    created = client.post("/v1/resumes", json={"locale": "en", "title": "Pick"}).json()
    client.put(f"/v1/resumes/{created['id']}", json={"typst_source": SELECTABLE})
    body = client.get(f"/v1/resumes/{created['id']}/preview").json()
    assert len(body["text_layers"]) == 1
    assert "Ada Lovelace" in _span_texts(body["text_layers"][0])


def test_text_layer_is_skipped_rather_than_fatal(monkeypatch):
    """Losing the text must cost the selection, not the preview."""
    import app.services.typst_compile as module

    monkeypatch.setattr(
        module, "extract_text_layer", lambda _pdf: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    preview = compile_preview_pages(ONE_PAGE)
    assert len(preview["pages"]) == 1
    assert preview["text_layers"] == []
