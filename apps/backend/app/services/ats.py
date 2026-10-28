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

ATS_CHECK_HINTS = {
    "text_extractable": "Text can be extracted (not image-only)",
    "single_column": "Single-column layout",
    "contact_in_body": "Name and email in the body (not header/footer only)",
    "standard_headings": "Standard headings (Experience / Education)",
    "dates_machine_readable": "Every date range resolves to a month (YYYY-MM or Mon YYYY)",
    "no_embedded_images_as_text": "Name/contact is not image-only",
    "fonts_embedded": "Fonts embedded",
    "parse_roundtrip_ok": "Extract recovers source text",
}

NAME_LET_RE = re.compile(r'#let\s+name\s*=\s*"([^"]*)"')
EMAIL_LET_RE = re.compile(r'#let\s+email\s*=\s*"([^"]*)"')
AUTHOR_RE = re.compile(r"author:\s*(?:name|\"([^\"]+)\")")
EMAIL_IN_TEXT_RE = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.I)
# Month names in the form ATS vendors' own guidance prescribes ("Mar 2021").
_MONTH_NAME_SRC = (
    r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?"
    r"|Aug(?:ust)?|Sep(?:t|tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?"
)

# One ordered pass over the extracted text yields every date-shaped token; the
# group that matched says whether it resolves to a month. Order is load-bearing:
# YYYY-MM must be tried before a bare year so "2018-09" is one token rather than
# the year 2018 followed by stray digits.
DATE_TOKEN_RE = re.compile(
    r"(?P<year_month>\b\d{4}-(?:0[1-9]|1[0-2])\b)"
    rf"|(?P<month_name>\b(?:{_MONTH_NAME_SRC})\.?[ \t]+\d{{4}}\b)"
    r"|(?P<season>\b(?:Spring|Summer|Fall|Autumn|Winter)[ \t]+\d{4}\b)"
    r"|(?P<apostrophe_year>(?<!\w)['\u2019]\d{2}\b)"
    r"|(?P<present>\bpresent\b|至今|现在)"
    r"|(?P<year>\b(?:19|20)\d{2}\b)",
    re.IGNORECASE,
)

# What may sit between the two endpoints of one range. Horizontal whitespace
# only: a dash across a line break is more likely a bullet than a range.
RANGE_SEP_RE = re.compile(
    r"[ \t]*(?:[-\u2010\u2013\u2014\u2212~]+|to|until|through|至)[ \t]*",
    re.IGNORECASE,
)

# Tokens that pin down both a month and a year. "Present" closes an open range
# but cannot open one, and a bare year never resolves on its own.
RESOLVED_DATE_KINDS = frozenset({"year_month", "month_name"})
UNRESOLVED_DATE_KINDS = frozenset({"season", "apostrophe_year"})

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


def read_ats_report(typst_source: str) -> dict[str, Any]:
    """Compile the live Typst source and return ATS pass/fail checks."""
    from fastapi import HTTPException
    from app.services import typst_compile

    try:
        pdf = typst_compile.compile_typst(typst_source, "pdf")
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else "Typst compile failed"
        return {"error": str(detail)[:2000]}
    report = analyze_pdf(pdf, typst_source)
    checks_out: list[dict[str, Any]] = []
    failed: list[str] = []
    passed = 0
    for check in report.get("checks") or []:
        if not isinstance(check, dict):
            continue
        name = str(check.get("name") or "")
        ok = bool(check.get("passed"))
        row: dict[str, Any] = {"name": name, "passed": ok}
        if not ok:
            failed.append(name)
            hint = ATS_CHECK_HINTS.get(name)
            if hint:
                row["hint"] = hint
        else:
            passed += 1
        checks_out.append(row)
    return {
        "checks": checks_out,
        "passed": passed,
        "total": len(checks_out),
        "failed": failed,
    }


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


def _date_entries(text: str) -> list[bool]:
    """One bool per date entry found in the text: did it resolve to a month?

    An entry is a range ("2021-03 – 2024-06") or a lone date ("Summer 2020").
    A lone year or a lone "Present" is prose — "ICPC 2022", "present the
    results" — so it is neither an entry nor a failure.
    """
    tokens = list(DATE_TOKEN_RE.finditer(text))
    entries: list[bool] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        if nxt is not None and RANGE_SEP_RE.fullmatch(text[token.end() : nxt.start()]):
            opens = token.lastgroup in RESOLVED_DATE_KINDS
            closes = nxt.lastgroup in RESOLVED_DATE_KINDS or nxt.lastgroup == "present"
            entries.append(opens and closes)
            i += 2
            continue
        if token.lastgroup in RESOLVED_DATE_KINDS:
            entries.append(True)
        elif token.lastgroup in UNRESOLVED_DATE_KINDS:
            entries.append(False)
        i += 1
    return entries


def _dates_machine_readable(text: str) -> bool:
    """Every date entry must resolve to a month and a year.

    This is all-of, not any-of. A vendor extractor that cannot resolve a range
    leaves its Dates field blank, and a blank required field can keep the
    application out of recruiter search entirely — so one current role saying
    "Present" must not rescue a resume whose other ranges are year-only.
    """
    if not text:
        return False
    entries = _date_entries(text)
    return bool(entries) and all(entries)


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
