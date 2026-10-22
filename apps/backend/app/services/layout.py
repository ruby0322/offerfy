"""Render-level facts about the compiled resume.

Two kinds of question live here, and the split is the whole point.

Geometry, always: how many pages, which text wrapped, what the page break cut
in half, how full each page is. The PDF knows all of it exactly, pdfminer is
already a dependency (`ats.py` parses the same bytes), and the answer costs
nothing and cannot be imagined.

Vision, only when asked: "does this page look crowded". No bounding box answers
that, so `questions` go to the Decisions API over the rendered PNGs -- one call,
images billed once however many questions ride along, input tokens only. A
caller that asks no questions never rasterizes and never leaves the process.

Do not send the vision side something geometry can answer. gpt-6-luna is a
compact model whose OCR is its weakest axis, so "did this bullet wrap" is both
the question it is most likely to get wrong and the one the row table answers
exactly.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from collections import Counter
from io import BytesIO
from typing import Any

import httpx
from fastapi import HTTPException
from pdfminer.high_level import extract_pages
from pdfminer.layout import LTChar, LTTextContainer, LTTextLine

from app.config import get_settings

logger = logging.getLogger(__name__)

# Two lines belong to the same visual row when they overlap this much of the
# shorter one's height. Resume templates park dates and locations on the same
# baseline as the title they belong to, and those are one row to a reader.
ROW_OVERLAP_FRAC = 0.5
# A line starting past this fraction of the page is the right-aligned metadata
# column, not body text. It must not count toward the body's right edge.
RIGHT_COLUMN_FRAC = 0.5
# Overflow tolerance. pdfminer's bbox for a line that wraps includes the
# advance of the last glyph before the break, which puts ordinary wrapped text
# a few points past the text column. Only a real spill (a long URL, a wide
# table) clears six tenths of an em.
OVERFLOW_EM = 0.6
# A page holding less than this much of its text area reads as "nearly empty",
# which for a resume usually means one line spilled and should be pulled back.
SPARSE_PAGE_FILL = 0.25
# Below this many words a prefix match means nothing, so a target that needs
# more trimming than that is reported as not found instead of mismatched.
MIN_TARGET_WORDS = 4

MAX_TARGETS = 6
MAX_QUESTIONS = 6
MAX_SCORES = 2
MAX_FINDINGS = 6
TEXT_CLIP = 80

# A4 at 144 ppi is 1191x1684 px, which is ceil(1191/32) * ceil(1684/32) = 2,014
# patches of 32px -- inside the 2,500-patch `high` budget, so the page is billed
# whole rather than shrunk. At the 1.2x multiplier that is ~2.4k input tokens,
# or $0.00024 a page at the Decisions rate of $0.10 per 1M input. Raising this
# buys nothing a reader of the page would notice; lowering it below ~110 starts
# to cost the model the ability to read 10pt body text, which is most of why it
# is being shown the page at all.
VISION_PPI = 144
MAX_VISION_PAGES = 4
VISION_TIMEOUT = 30.0
DEFAULT_DECISIONS_MODEL = "gpt-6-luna"

# Score questions use fixed rubrics rather than letting the caller invent levels,
# for two measured reasons. The answer depends heavily on how the levels are
# worded -- a `choice` question with caller-written options confidently called a
# 99%-full page "too_sparse" in the same request where a score rubric called it
# Dense -- and a fixed scale is the only way two calls are comparable, which is
# what makes "did my edit improve this" answerable at all. The model is
# deterministic, so a fixed rubric gives a stable number. Free-form judgements
# still have `questions`, which measured well.
#
# A third rubric, `balance` ("how evenly is content distributed across the
# page"), was written, measured and removed. It scored 0.01-0.92 with 0.92-0.99
# confidence on every document tried, including a perfectly ordinary resume: a
# left-aligned single column *is* left-heavy, so the rubric could not
# discriminate, and acting on it would mean adding a right-hand column -- which
# would break the `single_column` check in `ats.py`. Vertical distribution, the
# part that is a real resume problem, is already measured exactly by
# `page_fill` and the findings.
SCORE_RUBRICS: dict[str, dict[str, Any]] = {
    # 0..3 with the good answer in the middle: both ends are defects.
    "density": {
        "instructions": "How densely is this page filled with content?",
        "levels": [
            {
                "label": "Airy",
                "description": "Large empty areas; the page looks unfinished or padded out.",
            },
            {
                "label": "Comfortable",
                "description": "Content and whitespace are in balance; easy to scan.",
            },
            {
                "label": "Dense",
                "description": "Little whitespace left, but still comfortable to read.",
            },
            {
                "label": "Cramped",
                "description": "Text runs together; lines and sections have too little room.",
            },
        ],
    },
    # 0..3, higher is better.
    "hierarchy": {
        "instructions": (
            "How clearly can a reader tell the sections, titles and body text "
            "apart at a glance?"
        ),
        "levels": [
            {
                "label": "Flat",
                "description": "Headings, titles and body text all look alike; nothing stands out.",
            },
            {
                "label": "Weak",
                "description": "Some difference in size or weight, but the structure is hard to see.",
            },
            {
                "label": "Clear",
                "description": "Sections and titles are easy to pick out while skimming.",
            },
            {
                "label": "Strong",
                "description": "The structure is obvious at a glance, emphasised consistently.",
            },
        ],
    },
}

VISION_PREAMBLE = (
    "These are the rendered pages of a one- or two-page resume, in order, as a "
    "reader sees them. Judge only what is visible: spacing, density, balance, "
    "alignment, and where things sit on the page."
)

_BULLET_PREFIX = re.compile(r"^[\s•‣◦·⁃−–—*+-]+")
_WS = re.compile(r"\s+")


# --- geometry ---------------------------------------------------------------


def _clip(text: str, limit: int = TEXT_CLIP) -> str:
    flat = _WS.sub(" ", text or "").strip()
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _normalize(text: str) -> str:
    return _WS.sub(" ", text or "").strip().casefold()


def _text_lines(pdf_bytes: bytes) -> list[dict]:
    """Every rendered text line, with its box. Containers are deliberately not
    trusted as paragraphs: pdfminer regularly files the second line of a wrapped
    bullet under a container of its own, so a container's line count is not the
    wrap count."""
    lines: list[dict] = []
    try:
        for number, page in enumerate(extract_pages(BytesIO(pdf_bytes)), start=1):
            for element in page:
                if not isinstance(element, LTTextContainer):
                    continue
                for line in element:
                    if not isinstance(line, LTTextLine):
                        continue
                    text = (line.get_text() or "").strip()
                    if not text:
                        continue
                    sizes = [c.size for c in line if isinstance(c, LTChar)]
                    lines.append(
                        {
                            "page": number,
                            "page_width": float(page.width),
                            "page_height": float(page.height),
                            "text": text,
                            "x0": float(line.x0),
                            "x1": float(line.x1),
                            "y0": float(line.y0),
                            "y1": float(line.y1),
                            "size": round(max(sizes), 1) if sizes else 0.0,
                        }
                    )
    except Exception:
        logger.warning("layout: pdfminer could not read the compiled PDF", exc_info=True)
        return []
    return lines


def _rows(lines: list[dict]) -> list[dict]:
    """Group lines into visual rows, top to bottom, left to right."""
    rows: list[dict] = []
    for page in sorted({line["page"] for line in lines}):
        on_page = sorted(
            (line for line in lines if line["page"] == page),
            key=lambda line: (-line["y1"], line["x0"]),
        )
        groups: list[list[dict]] = []
        for line in on_page:
            placed = False
            if groups:
                last = groups[-1]
                top = max(member["y1"] for member in last)
                bottom = min(member["y0"] for member in last)
                overlap = min(top, line["y1"]) - max(bottom, line["y0"])
                shortest = min(top - bottom, line["y1"] - line["y0"]) or 1.0
                if overlap >= shortest * ROW_OVERLAP_FRAC:
                    last.append(line)
                    placed = True
            if not placed:
                groups.append([line])
        for group in groups:
            group.sort(key=lambda line: line["x0"])
            width = group[0]["page_width"]
            body = [line for line in group if line["x0"] < width * RIGHT_COLUMN_FRAC]
            rows.append(
                {
                    "page": page,
                    "page_width": width,
                    "page_height": group[0]["page_height"],
                    "text": " ".join(line["text"] for line in group),
                    "x0": min(line["x0"] for line in group),
                    "x1": max(line["x1"] for line in group),
                    # The body column's own right edge. A row whose date sits at
                    # the right margin must not read as a full-width line.
                    "body_x1": max((line["x1"] for line in body), default=None),
                    "y0": min(line["y0"] for line in group),
                    "y1": max(line["y1"] for line in group),
                    "size": max(line["size"] for line in group),
                }
            )
    return rows


def _body_size(rows: list[dict]) -> float:
    """The most common size by text volume. Headings are rarer than body text."""
    weights: Counter[float] = Counter()
    for row in rows:
        weights[row["size"]] += len(row["text"])
    return weights.most_common(1)[0][0] if weights else 0.0


def _body_left(rows: list[dict], body_size: float) -> float:
    lefts = [
        round(row["x0"], 1)
        for row in rows
        if row["body_x1"] is not None and abs(row["size"] - body_size) < 0.6
    ]
    if not lefts:
        return min((row["x0"] for row in rows), default=0.0)
    return Counter(lefts).most_common(1)[0][0]


def _is_heading(row: dict, body_size: float) -> bool:
    return row["size"] > body_size + 0.5


def _is_continuation(row: dict, body_left: float, body_size: float) -> bool:
    """Does this row look like the tail of something that started earlier?

    An indented row at body size with no bullet is a wrapped list item, which is
    what the page break splits in practice. A wrapped plain paragraph resumes at
    the left margin instead and is only guessable from its first character, so
    the lowercase test backs the indent test up for Latin scripts. Neither test
    can see a wrapped CJK paragraph that resumes at the margin -- pass that text
    as a `target` and the row span answers it exactly.
    """
    if abs(row["size"] - body_size) >= 0.6:
        return False
    stripped = row["text"].lstrip()
    if _BULLET_PREFIX.match(row["text"]) and row["x0"] <= body_left + 1:
        return False
    if row["x0"] > body_left + 1:
        return True
    return bool(stripped[:1].isalpha() and stripped[:1].islower())


def _page_geometry(rows: list[dict]) -> tuple[list[int], list[float]]:
    """Rows per page, and how much of the text area each page holds."""
    pages = sorted({row["page"] for row in rows})
    counts: list[int] = []
    fills: list[float] = []
    top_margin = min(
        (row["page_height"] - row["y1"] for row in rows),
        default=0.0,
    )
    for page in pages:
        on_page = [row for row in rows if row["page"] == page]
        counts.append(len(on_page))
        height = on_page[0]["page_height"]
        text_height = height - 2 * top_margin
        used = max(row["y1"] for row in on_page) - min(row["y0"] for row in on_page)
        fills.append(round(used / text_height, 2) if text_height > 0 else 0.0)
    return counts, fills


def _findings(rows: list[dict], fills: list[float]) -> list[dict]:
    body_size = _body_size(rows)
    body_left = _body_left(rows, body_size)
    pages = sorted({row["page"] for row in rows})
    out: list[dict] = []

    for page in pages[1:]:
        first = next((row for row in rows if row["page"] == page), None)
        if first is not None and _is_continuation(first, body_left, body_size):
            out.append(
                {
                    "kind": "item_split_across_pages",
                    "page": page,
                    "text": _clip(first["text"]),
                }
            )

    for page in pages[:-1]:
        # Only a heading with a page after it can be stranded. A heading near
        # the end of the last page is just where the document stops.
        on_page = [row for row in rows if row["page"] == page]
        for index, row in enumerate(on_page):
            if not _is_heading(row, body_size):
                continue
            after = len(on_page) - index - 1
            if after < 2:
                out.append(
                    {
                        "kind": "heading_orphaned_at_page_end",
                        "page": page,
                        "text": _clip(row["text"]),
                        "rows_after": after,
                    }
                )

    tolerance = max(2.0, body_size * OVERFLOW_EM)
    for row in rows:
        if row["body_x1"] is None:
            continue
        limit = row["page_width"] - body_left
        over = row["body_x1"] - limit
        if over > tolerance:
            out.append(
                {
                    "kind": "right_margin_overflow",
                    "page": row["page"],
                    "pt": round(over, 1),
                    "text": _clip(row["text"]),
                }
            )

    if len(pages) > 1 and fills and fills[-1] < SPARSE_PAGE_FILL:
        last = [row for row in rows if row["page"] == pages[-1]]
        out.append(
            {
                "kind": "last_page_nearly_empty",
                "page": pages[-1],
                "fill": fills[-1],
                "rows": len(last),
            }
        )

    if len(out) <= MAX_FINDINGS:
        return out
    return out[:MAX_FINDINGS] + [{"kind": "truncated", "dropped": len(out) - MAX_FINDINGS}]


# --- target matching --------------------------------------------------------


def _haystack(rows: list[dict]) -> tuple[str, list[tuple[int, int, int]]]:
    """One searchable string over the rows, plus each row's span inside it.

    A row ending in a hyphen is joined to the next without a space: that hyphen
    was inserted by the line breaker, and text copied from the source has no
    hyphen in it.
    """
    buf = ""
    spans: list[tuple[int, int, int]] = []
    for index, row in enumerate(rows):
        piece = _normalize(row["text"])
        if not piece:
            continue
        if buf.endswith("-"):
            buf = buf[:-1]
            start, _, previous = spans[-1]
            spans[-1] = (start, len(buf), previous)
        elif buf:
            buf += " "
        start = len(buf)
        buf += piece
        spans.append((start, len(buf), index))
    return buf, spans


def _rows_for(
    buf: str, spans: list[tuple[int, int, int]], needle: str
) -> list[int] | None:
    at = buf.find(needle)
    if at < 0:
        return None
    end = at + len(needle)
    return [row for start, stop, row in spans if start < end and stop > at]


def _match_target(
    rows: list[dict], buf: str, spans: list[tuple[int, int, int]], raw: str, body_left: float
) -> dict:
    needle = _normalize(_BULLET_PREFIX.sub("", raw))
    hit = _rows_for(buf, spans, needle) if needle else None
    match = "exact"
    if hit is None and needle:
        words = needle.split(" ")
        while len(words) > MIN_TARGET_WORDS and hit is None:
            words.pop()
            hit = _rows_for(buf, spans, " ".join(words))
        match = "prefix"
    if not hit:
        return {
            "text": _clip(raw),
            "found": False,
            "hint": "Nothing rendered matched. Quote the text as the PDF shows it.",
        }
    first = rows[hit[0]]
    last = rows[hit[-1]]
    limit = last["page_width"] - body_left
    right = last["body_x1"] if last["body_x1"] is not None else last["x1"]
    span = limit - body_left
    out: dict[str, Any] = {
        "text": _clip(raw),
        "found": True,
        "page": first["page"],
        "rows": len(hit),
        "wrapped": len(hit) > 1,
        # Room left on the final line, in points and as a share of the column.
        # This is the number to reason with: negative-ish room means a word or
        # two must go for it to fit on one line.
        "spare_pt": round(limit - right, 1),
        "last_row_fill": round((right - body_left) / span, 2) if span > 0 else None,
    }
    if match != "exact":
        out["match"] = match
    if last["page"] != first["page"]:
        out["split_across_pages"] = [first["page"], last["page"]]
    return out


# --- the geometry report ----------------------------------------------------


def geometry_report(typst_source: str, targets: list[str] | None = None) -> dict:
    """Compile the source and answer the questions the PDF can answer itself."""
    from app.services import typst_compile

    try:
        pdf = typst_compile.compile_typst(typst_source, "pdf")
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else "Typst compile failed"
        return {"error": str(detail)[:2000]}
    return report_from_pdf(pdf, targets)


def report_from_pdf(pdf: bytes, targets: list[str] | None = None) -> dict:
    """The report, given bytes someone else already paid to compile."""
    rows = _rows(_text_lines(pdf))
    if not rows:
        return {
            "error": "The compiled PDF has no readable text lines.",
            "hint": "Check read_ats: text may not be extractable.",
        }

    counts, fills = _page_geometry(rows)
    report: dict[str, Any] = {
        "pages": len(counts),
        "page_size_pt": [round(rows[0]["page_width"]), round(rows[0]["page_height"])],
        "rows_per_page": counts,
        # Share of the usable text height each page holds. 1.0 is a full page;
        # a short last page is the usual reason a resume runs long by one line.
        "page_fill": fills,
        "font_sizes_pt": sorted({row["size"] for row in rows}),
        "findings": _findings(rows, fills),
    }
    if targets:
        buf, spans = _haystack(rows)
        body_left = _body_left(rows, _body_size(rows))
        report["targets"] = [
            _match_target(rows, buf, spans, target, body_left) for target in targets
        ]
    return report


def compile_digest(typst_source: str) -> dict:
    """One compile, two answers: does it build, and what did it do to the page.

    `apply_typst_edit` already pays for a PDF to check the compile when
    `verify_compile` is on, so deriving the page digest from those same bytes
    makes the layout consequence of an edit free. That matters more than it
    looks: the alternative is the agent spending a whole extra round calling
    `read_layout`, and a round of the main model costs ~66x the vision call it
    would be paying for.

    Deliberately smaller than `geometry_report`: this rides along on every edit,
    so it carries what changed about the page and nothing else.
    """
    from app.services import typst_compile

    if not typst_compile.typst_available():
        return {"compile": {"ok": True, "skipped": True}}
    try:
        pdf = typst_compile.compile_typst(typst_source, "pdf")
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else "Typst compile failed"
        return {"compile": {"ok": False, "error": str(detail)[:2000]}}

    report = report_from_pdf(pdf)
    if "error" in report:
        # It compiled, so the compile is fine; there is just nothing to say
        # about a page we could not read.
        return {"compile": {"ok": True}}
    return {
        "compile": {"ok": True},
        "layout": {
            "pages": report["pages"],
            "page_fill": report["page_fill"],
            "findings": report["findings"],
        },
    }


# --- the vision side --------------------------------------------------------


def decisions_url(base: str) -> str:
    base = (base or "").rstrip("/")
    if base.endswith("/decisions"):
        return base
    for suffix in ("/chat/completions", "/responses"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    if base.endswith("/v1"):
        return base + "/decisions"
    return base + "/v1/decisions"


def _error_detail(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except ValueError:
        data = None
    if isinstance(data, dict):
        error = data.get("error")
        message = error.get("message") if isinstance(error, dict) else error
        if isinstance(message, str) and message.strip():
            return message.strip()[:300]
    return f"Decisions request failed ({resp.status_code})"


def _render_pages(typst_source: str) -> list[bytes]:
    from app.services import typst_compile

    return typst_compile.compile_typst_pages(typst_source, "png", ppi=VISION_PPI)


def _chosen_pages(pngs: list[bytes], wanted: list[int] | None) -> list[tuple[int, bytes]]:
    """Which pages to show, as (page number, png).

    Scoping exists because one request answers one question about its whole
    input: asked to score the density of a two-page resume whose first page was
    99% full and whose second held six lines, the model returned a single
    document-wide 0.07 ("Sparse"). Narrow the input and the answer is about the
    page the caller meant.
    """
    numbered = list(enumerate(pngs, start=1))
    if wanted:
        picked = sorted({n for n in wanted if 1 <= n <= len(pngs)})
        if picked:
            numbered = [(n, pngs[n - 1]) for n in picked]
    return numbered[:MAX_VISION_PAGES]


def _score_questions(rubrics: list[str]) -> list[dict]:
    return [
        {
            "type": "score",
            "name": name,
            "instructions": SCORE_RUBRICS[name]["instructions"],
            "levels": SCORE_RUBRICS[name]["levels"],
        }
        for name in rubrics
    ]


def _vision_body(
    model: str,
    pages: list[tuple[int, bytes]],
    total: int,
    questions: list[str],
    rubrics: list[str] | None = None,
) -> dict:
    content: list[dict[str, Any]] = [{"type": "input_text", "text": VISION_PREAMBLE}]
    for number, png in pages:
        encoded = base64.b64encode(png).decode("ascii")
        content.append({"type": "input_text", "text": f"Page {number} of {total}:"})
        # Hosted URLs and file_ids are not accepted here; the bytes ride inline,
        # which is also why this feature needs no object storage.
        content.append(
            {"type": "input_image", "image_url": f"data:image/png;base64,{encoded}"}
        )
    return {
        "model": model,
        "input": [{"role": "user", "content": content}],
        # Independent questions share one input, so the pages are billed once
        # no matter how many of these ride along.
        "questions": [
            {"type": "predicate", "name": f"q{index}", "instructions": question}
            for index, question in enumerate(questions, start=1)
        ]
        + _score_questions(rubrics or []),
    }


def _vision_answers(data: dict, questions: list[str]) -> list[dict]:
    asked = {f"q{index}": question for index, question in enumerate(questions, start=1)}
    answers: list[dict] = []
    for item in data.get("answers") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name"))
        row: dict[str, Any] = (
            {"rubric": name} if name in SCORE_RUBRICS else {"question": asked.get(name, "")}
        )
        # A refusal is its own answer type, not a low probability. Reading
        # `probability` off one would invent a confident "no".
        if item.get("type") == "refusal":
            row["refused"] = True
            reason = item.get("reason") or item.get("refusal")
            if isinstance(reason, str) and reason.strip():
                row["reason"] = reason.strip()[:200]
            answers.append(row)
            continue
        for key in ("probability", "choice", "score", "confidence"):
            if item.get(key) is not None:
                row[key] = item[key]
        # A bare score is uninterpretable without its scale, and the caller
        # cannot see the rubric it did not write. Carry the labels back.
        levels = item.get("probabilities")
        if item.get("type") == "score" and isinstance(levels, list):
            row["levels"] = [
                {"label": level.get("label"), "probability": level.get("probability")}
                for level in levels
                if isinstance(level, dict)
            ]
        answers.append(row)
    return answers


async def vision_report(
    typst_source: str,
    questions: list[str],
    pages: list[int] | None = None,
    rubrics: list[str] | None = None,
) -> dict:
    """Ask gpt-6-luna about the rendered pages. Never raises: the geometry half
    of the report is still worth returning when the beta endpoint is not there."""
    settings = get_settings()
    if not (settings.openai_api_key or "").strip():
        return {"skipped": "llm_not_configured"}
    try:
        rendered = await asyncio.to_thread(_render_pages, typst_source)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else "Typst render failed"
        return {"error": str(detail)[:500]}
    if not rendered:
        return {"error": "The resume rendered no pages."}

    shown = _chosen_pages(rendered, pages)
    model = (settings.openai_decisions_model or DEFAULT_DECISIONS_MODEL).strip()
    url = decisions_url(settings.openai_base_url or "https://api.openai.com/v1")
    body = _vision_body(model, shown, len(rendered), questions, rubrics)
    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=VISION_TIMEOUT) as client:
            resp = await client.post(url, headers=headers, json=body)
            if resp.status_code >= 400:
                detail = _error_detail(resp)
                logger.warning(
                    "decisions failed status=%s detail=%s", resp.status_code, detail
                )
                return {"error": detail, "model": model}
            data = resp.json()
    except httpx.HTTPError as exc:
        logger.warning("decisions transport failed: %s", exc.__class__.__name__)
        return {"error": "Decisions request failed", "model": model}
    except ValueError:
        return {"error": "Decisions returned no JSON", "model": model}

    report: dict[str, Any] = {
        "model": model,
        # Which pages the answers are about, so an answer scoped to page 2 is
        # not read as an answer about the document.
        "pages_seen": [number for number, _ in shown],
        "answers": _vision_answers(data if isinstance(data, dict) else {}, questions),
    }
    if len(shown) < len(rendered):
        report["pages_skipped"] = len(rendered) - len(shown)
    usage = data.get("usage") if isinstance(data, dict) else None
    if isinstance(usage, dict) and usage.get("input_tokens") is not None:
        # Input tokens are the whole bill on this endpoint, so they are the only
        # usage number worth carrying back.
        report["input_tokens"] = usage["input_tokens"]
    return report


async def read_layout_report(typst_source: str, args: dict | None = None) -> dict:
    """The `read_layout` tool. Geometry always; vision only if asked."""
    args = args or {}
    targets = [
        value
        for value in (args.get("targets") or [])
        if isinstance(value, str) and value.strip()
    ][:MAX_TARGETS]
    questions = [
        value
        for value in (args.get("questions") or [])
        if isinstance(value, str) and value.strip()
    ][:MAX_QUESTIONS]
    pages = [value for value in (args.get("pages") or []) if isinstance(value, int)]
    seen: set[str] = set()
    rubrics = [
        value
        for value in (args.get("scores") or [])
        if isinstance(value, str)
        and value in SCORE_RUBRICS
        and not (value in seen or seen.add(value))
    ][:MAX_SCORES]

    report = await asyncio.to_thread(geometry_report, typst_source, targets)
    if "error" in report:
        return report
    if questions or rubrics:
        report["visual"] = await vision_report(
            typst_source, questions, pages, rubrics
        )
    return report
