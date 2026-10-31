from __future__ import annotations

from collections.abc import Iterator
from io import BytesIO
from typing import Any

from pdfminer.high_level import extract_pages
from pdfminer.layout import LTChar, LTPage, LTTextLine

# A glyph box narrower than this is a rounding artefact, not a word.
_MIN_SPAN_WIDTH = 0.01


def _text_lines(node: Any) -> Iterator[LTTextLine]:
    """Every text line under ``node``, in reading order.

    A text line is a leaf as far as we care: its own children are glyphs.
    """
    if isinstance(node, LTTextLine):
        yield node
        return
    if not hasattr(node, "__iter__"):
        return
    for child in node:
        yield from _text_lines(child)


def _line_size(line: LTTextLine) -> float:
    """The font size most of the line's glyphs are set in.

    A line that mixes sizes (a bold lead-in, a superscript) still gets one
    span, because the overlay only has to cover the line, not reproduce it.
    """
    counts: dict[float, int] = {}
    for glyph in line:
        if isinstance(glyph, LTChar):
            key = round(glyph.size, 2)
            counts[key] = counts.get(key, 0) + 1
    if not counts:
        return 0.0
    # Ties go to the larger size so the span never undershoots the glyphs.
    return max(counts.items(), key=lambda item: (item[1], item[0]))[0]


def _page_layer(page: LTPage) -> dict[str, Any]:
    """One page's selectable text, in the SVG's own coordinate space.

    Typst writes its SVG with ``viewBox="0 0 <width-pt> <height-pt>"``, so a
    PDF point is an SVG unit — the only conversion needed is flipping the PDF's
    bottom-left origin to the SVG's top-left one.
    """
    spans: list[dict[str, Any]] = []
    for line in _text_lines(page):
        text = line.get_text().replace("\n", "")
        if not text.strip():
            continue
        if line.width < _MIN_SPAN_WIDTH:
            continue
        size = _line_size(line)
        if size <= 0:
            continue
        spans.append(
            {
                "text": text,
                "x": line.x0 - page.x0,
                "y": page.y1 - line.y1,
                "width": line.width,
                "height": line.height,
                "size": size,
            }
        )
    return {"width": page.width, "height": page.height, "spans": spans}


def extract_text_layer(pdf_bytes: bytes) -> list[dict[str, Any]]:
    """Positioned text for every page of ``pdf_bytes``, one layer per page.

    The preview is a Typst SVG, and Typst SVG carries glyph outlines rather
    than text — nothing in it can be selected. Compiling the same source to
    PDF gives back the text with its positions, which the client overlays on
    the picture as transparent spans.
    """
    return [_page_layer(page) for page in extract_pages(BytesIO(pdf_bytes))]
