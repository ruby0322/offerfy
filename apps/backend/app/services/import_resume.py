from __future__ import annotations

import base64
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db import get_session_factory
from app.models import ChatMessage, Resume
from app.services.llm import llm_configured, run_llm_turn
from app.services.rate_limit import try_consume_guest_rate
from app.services.typst_compile import compile_typst

PACKAGE = '#import "@preview/basic-resume:0.2.9"'
PAPER = 'paper: "a4"'
ACCENT = 'accent-color: "#f4be82"'
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
EXTRACT_LIMIT = 20_000

IMPORT_SYSTEM = (
    " Fill the existing basic-resume Typst template from the uploaded resume. "
    "Call read_typst first, then write a full Typst document via apply_typst_edit "
    "with `source`. Map name, email, education, work, projects, and skills. "
    "Preserve the upload's section order when those headings exist. "
    "Do not invent extra packages or columns."
)

DONE_LINE = {
    "en": "Filled the template from the upload.",
    "zh-TW": "已從上傳內容填入模板",
    "zh-CN": "已从上传内容填入模板",
}

FAILED_LINE = {
    "en": "Could not fill the template from the upload.",
    "zh-TW": "無法從上傳內容填入模板",
    "zh-CN": "无法从上传内容填入模板",
}

MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def has_template_invariants(source: str) -> bool:
    return PACKAGE in source and PAPER in source and ACCENT in source


def _done_line(locale: str) -> str:
    return DONE_LINE.get(locale, DONE_LINE["zh-TW"])


def _failed_line(locale: str, reason: str | None = None) -> str:
    base = FAILED_LINE.get(locale, FAILED_LINE["zh-TW"])
    if reason:
        return f"{base} ({reason})"
    return base


def _user_content(filename: str, data: bytes, extracted: str | None) -> str | list[dict]:
    suffix = Path(filename).suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        mime = MIME_BY_SUFFIX.get(suffix, "image/png")
        b64 = base64.b64encode(data).decode("ascii")
        text = (
            "Fill the basic-resume template from this uploaded resume image. "
            "Call read_typst, then apply_typst_edit with the full source."
        )
        if extracted:
            text += "\n\nExtracted text:\n" + extracted[:EXTRACT_LIMIT]
        return [
            {"type": "text", "text": text},
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
            },
        ]
    text = (
        "Fill the basic-resume template from this extracted resume text. "
        "Call read_typst, then apply_typst_edit with the full source."
    )
    if extracted:
        text += "\n\n----- extracted -----\n" + extracted[:EXTRACT_LIMIT]
    else:
        text += (
            "\n\nThe upload had no extractable text "
            "(image-only PDF is not sent as an image_url). Keep the starter structure."
        )
    return text


def _append_assistant(db: Session, resume: Resume, content: str) -> None:
    db.add(ChatMessage(resume_id=resume.id, role="assistant", content=content))


def _fail(db: Session, resume: Resume, original: str, reason: str) -> None:
    resume.typst_source = original
    resume.import_status = "failed"
    _append_assistant(db, resume, _failed_line(resume.locale, reason))


def import_uploaded_resume(
    resume_id: str,
    filename: str,
    data: bytes,
    extracted: str | None,
    guest_session_id: str | None,
) -> None:
    db = get_session_factory()()
    try:
        resume = db.get(Resume, resume_id)
        if resume is None:
            return
        original = resume.typst_source
        if not llm_configured():
            _fail(db, resume, original, "OpenAI is not configured")
            db.commit()
            return
        if guest_session_id and not try_consume_guest_rate(db, guest_session_id, "chat"):
            _fail(db, resume, original, "Hourly AI limit reached")
            db.commit()
            return
        user_content = _user_content(filename, data, extracted)
        try:
            _, new_source, _ = run_llm_turn(
                [{"role": "user", "content": user_content}],
                original,
                timeout=120.0,
                extra_system=IMPORT_SYSTEM,
                max_rounds=6,
            )
        except HTTPException as exc:
            _fail(db, resume, original, str(exc.detail))
            db.commit()
            return
        except Exception as exc:
            _fail(db, resume, original, str(exc) or "import failed")
            db.commit()
            return

        if new_source == original or not has_template_invariants(new_source):
            _fail(db, resume, original, "template invariants missing")
            db.commit()
            return
        try:
            compile_typst(new_source, "pdf")
        except HTTPException as exc:
            _fail(db, resume, original, str(exc.detail))
            db.commit()
            return

        resume.typst_source = new_source
        resume.import_status = "done"
        _append_assistant(db, resume, _done_line(resume.locale))
        db.commit()
    except Exception:
        db.rollback()
        try:
            resume = db.get(Resume, resume_id)
            if resume is not None and resume.import_status == "pending":
                resume.import_status = "failed"
                _append_assistant(db, resume, _failed_line(resume.locale))
                db.commit()
        except Exception:
            db.rollback()
    finally:
        db.close()
