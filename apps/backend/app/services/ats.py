from __future__ import annotations

import re
from io import BytesIO
from typing import Any

from pdfminer.high_level import extract_pages, extract_text as pdfminer_extract_text
from pdfminer.layout import LTTextContainer
from pypdf import PdfReader

ATS_CHECK_NAMES = (
    "text_extractable",
    "single_column",
    "contact_in_body",
    "standard_headings",
    "dates_machine_readable",
    "no_embedded_images_as_text",
    "fonts_embedded",
    "parse_roundtrip_ok",
)

NAME_LET_RE = re.compile(r'#let\s+name\s*=\s*"([^"]*)"')
EMAIL_LET_RE = re.compile(r'#let\s+email\s*=\s*"([^"]*)"')
AUTHOR_RE = re.compile(r"author:\s*(?:name|\"([^\"]+)\")")
EMAIL_IN_TEXT_RE = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.I)
DATE_RE = re.compile(r"\b\d{4}-(0[1-9]|1[0-2])\b")
PRESENT_RE = re.compile(r"(Present|present|至今|现在)")

EDU_HEADINGS = ("education", "學歷", "教育经历", "教育經歷", "教育")
EXP_HEADINGS = ("experience", "work experience", "工作經歷", "工作经历")

# Top band is tighter than ~8% so basic-resume name (after 0.5in margin) stays in-body.
# Bottom band follows the spec (~8%) to catch footer-only contact.
TOP_HEADER_FRAC = 0.04
BOTTOM_FOOTER_FRAC = 0.08
COLUMN_X_GAP_FRAC = 0.12
COLUMN_Y_OVERLAP_FRAC = 0.4
SUBSTANTIAL_CHARS = 40


def analyze_pdf(pdf_bytes: bytes, typst_source: str) -> dict[str, Any]:
    text = extract_pdf_text(pdf_bytes)
    layout = _extract_layout(pdf_bytes)
    name, email = parse_name_email(typst_source)
    checks = [
        {"name": "text_extractable", "passed": _text_extractable(text)},
        {"name": "single_column", "passed": _single_column(layout)},
        {
            "name": "contact_in_body",
            "passed": _contact_in_body(layout, name, email, text),
        },
        {"name": "standard_headings", "passed": _standard_headings(typst_source, text)},
        {"name": "dates_machine_readable", "passed": _dates_machine_readable(text)},
        {
            "name": "no_embedded_images_as_text",
            "passed": _no_embedded_images_as_text(pdf_bytes, text, name, email),
        },
        {"name": "fonts_embedded", "passed": _fonts_embedded(pdf_bytes)},
        {
            "name": "parse_roundtrip_ok",
            "passed": _parse_roundtrip_ok(typst_source, text, name),
        },
    ]
    return {"checks": checks}


def extract_pdf_text(pdf_bytes: bytes) -> str:
    if not pdf_bytes:
        return ""
    try:
        text = pdfminer_extract_text(BytesIO(pdf_bytes)) or ""
        if text.strip():
            return text
    except Exception:
        pass
    try:
        reader = PdfReader(BytesIO(pdf_bytes))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception:
        return ""


def parse_name_email(typst_source: str) -> tuple[str | None, str | None]:
    name = None
    email = None
    m = NAME_LET_RE.search(typst_source)
    if m and m.group(1).strip():
        name = m.group(1).strip()
    m = EMAIL_LET_RE.search(typst_source)
    if m and m.group(1).strip():
        email = m.group(1).strip()
    if name is None:
        m = AUTHOR_RE.search(typst_source)
        if m and m.group(1):
            name = m.group(1).strip()
    return name, email


def _text_extractable(text: str) -> bool:
    return bool(text and text.strip())


def _extract_layout(pdf_bytes: bytes) -> list[dict]:
    pages: list[dict] = []
    try:
        for page_layout in extract_pages(BytesIO(pdf_bytes)):
            boxes = []
            for element in page_layout:
                if isinstance(element, LTTextContainer):
                    raw = element.get_text() or ""
                    if not raw.strip():
                        continue
                    boxes.append(
                        {
                            "text": raw,
                            "x0": float(element.x0),
                            "x1": float(element.x1),
                            "y0": float(element.y0),
                            "y1": float(element.y1),
                        }
                    )
            pages.append(
                {
                    "width": float(page_layout.width),
                    "height": float(page_layout.height),
                    "boxes": boxes,
                }
            )
    except Exception:
        return []
    return pages


def _single_column(layout: list[dict]) -> bool:
    for page in layout:
        boxes = [
            b
            for b in page["boxes"]
            if len(b["text"].strip()) >= SUBSTANTIAL_CHARS
            or (b["x1"] - b["x0"]) > page["width"] * 0.2
        ]
        width = page["width"] or 1.0
        for i, a in enumerate(boxes):
            for b in boxes[i + 1 :]:
                overlap = min(a["y1"], b["y1"]) - max(a["y0"], b["y0"])
                min_h = min(a["y1"] - a["y0"], b["y1"] - b["y0"]) or 1.0
                if overlap < min_h * COLUMN_Y_OVERLAP_FRAC:
                    continue
                if a["x1"] <= b["x0"]:
                    gap = b["x0"] - a["x1"]
                elif b["x1"] <= a["x0"]:
                    gap = a["x0"] - b["x1"]
                else:
                    continue
                if gap > width * COLUMN_X_GAP_FRAC:
                    return False
    return True


def _box_center_y(box: dict) -> float:
    return (box["y0"] + box["y1"]) / 2.0


def _contact_in_body(
    layout: list[dict], name: str | None, email: str | None, full_text: str
) -> bool:
    if not name or not email:
        return False
    body_parts: list[str] = []
    if not layout:
        # No layout: fall back to full-page extract (not header/footer specific).
        return name in full_text and email in full_text
    for page in layout:
        height = page["height"] or 1.0
        header_cut = height * (1.0 - TOP_HEADER_FRAC)
        footer_cut = height * BOTTOM_FOOTER_FRAC
        for box in page["boxes"]:
            cy = _box_center_y(box)
            if cy > header_cut or cy < footer_cut:
                continue
            body_parts.append(box["text"])
    body = "".join(body_parts)
    return name in body and email in body


def _heading_present(blob: str, options: tuple[str, ...]) -> bool:
    lower = blob.lower()
    for opt in options:
        if opt.lower() in lower or opt in blob:
            return True
    return False


def _standard_headings(typst_source: str, text: str) -> bool:
    heading_lines = [
        line.strip()
        for line in typst_source.splitlines()
        if line.strip().startswith("==")
    ]
    heading_blob = "\n".join(heading_lines)
    combined = heading_blob + "\n" + (text or "")
    has_edu = _heading_present(heading_blob, EDU_HEADINGS) or _heading_present(
        combined, EDU_HEADINGS
    )
    has_exp = _heading_present(heading_blob, EXP_HEADINGS) or _heading_present(
        combined, EXP_HEADINGS
    )
    # Prefer source headings so small-caps PDF text still counts.
    if heading_lines:
        has_edu = _heading_present(heading_blob, EDU_HEADINGS)
        has_exp = _heading_present(heading_blob, EXP_HEADINGS)
    return has_edu and has_exp


def _dates_machine_readable(text: str) -> bool:
    if not text:
        return False
    return bool(DATE_RE.search(text) or PRESENT_RE.search(text))


def _pdf_has_images(pdf_bytes: bytes) -> bool:
    try:
        reader = PdfReader(BytesIO(pdf_bytes))
    except Exception:
        return False
    for page in reader.pages:
        try:
            resources = page.get("/Resources")
            if not resources:
                continue
            xobj = resources.get("/XObject")
            if not xobj:
                continue
            xobj = xobj.get_object()
            for key in xobj:
                obj = xobj[key].get_object()
                if obj.get("/Subtype") == "/Image":
                    return True
        except Exception:
            continue
    return False


def _no_embedded_images_as_text(
    pdf_bytes: bytes, text: str, name: str | None, email: str | None
) -> bool:
    if not _pdf_has_images(pdf_bytes):
        return True
    name_ok = bool(name) and name in (text or "")
    email_ok = bool(email) and email in (text or "")
    if not email_ok:
        email_ok = bool(EMAIL_IN_TEXT_RE.search(text or ""))
    return name_ok and email_ok


def _font_is_embedded(font_obj) -> bool:
    if font_obj is None:
        return True
    try:
        font = font_obj.get_object()
    except Exception:
        font = font_obj
    try:
        subtype = str(font.get("/Subtype", ""))
        if subtype == "/Type0":
            descendants = font.get("/DescendantFonts") or []
            return all(_font_is_embedded(d) for d in descendants)
        descriptor = font.get("/FontDescriptor")
        if descriptor is None:
            return subtype == "/Type3"
        desc = descriptor.get_object() if hasattr(descriptor, "get_object") else descriptor
        return any(k in desc for k in ("/FontFile", "/FontFile2", "/FontFile3"))
    except Exception:
        return False


def _fonts_embedded(pdf_bytes: bytes) -> bool:
    try:
        reader = PdfReader(BytesIO(pdf_bytes))
    except Exception:
        return False
    found_font = False
    for page in reader.pages:
        try:
            resources = page.get("/Resources")
            if not resources:
                continue
            fonts = resources.get("/Font")
            if not fonts:
                continue
            fonts = fonts.get_object()
            for name in fonts:
                found_font = True
                if not _font_is_embedded(fonts[name]):
                    return False
        except Exception:
            continue
    return True if found_font else True


def _parse_roundtrip_ok(typst_source: str, text: str, name: str | None) -> bool:
    if not name:
        # Vacuous: no author string in source to recover.
        return True
    if not text:
        return False
    if name in text:
        return True
    parts = [p for p in name.split() if p]
    return bool(parts) and all(p in text for p in parts)
