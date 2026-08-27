from __future__ import annotations

import base64
from pathlib import Path

from app.services.extract import extract_upload_text


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
EXTRACT_LIMIT = 20_000
ATTACH_HEADER = "Attached file: "


def stored_user_message(message: str, filename: str | None) -> str:
    text = (message or "").strip()
    if not filename:
        return text
    body = text or "Please use this file."
    return f"{ATTACH_HEADER}{filename}\n\n{body}"


def llm_content_for_upload(
    filename: str,
    data: bytes,
    *,
    instruction: str,
    extracted: str | None = None,
) -> str | list[dict]:
    if extracted is None:
        extracted = extract_upload_text(filename, data)
    suffix = Path(filename).suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        mime = MIME_BY_SUFFIX.get(suffix, "image/png")
        b64 = base64.b64encode(data).decode("ascii")
        text = instruction
        if extracted:
            text += "\n\nExtracted text:\n" + extracted[:EXTRACT_LIMIT]
        return [
            {"type": "input_text", "text": text},
            {"type": "input_image", "image_url": f"data:{mime};base64,{b64}"},
        ]
    text = instruction
    if extracted:
        text += "\n\n----- extracted -----\n" + extracted[:EXTRACT_LIMIT]
    else:
        text += (
            "\n\nThe upload had no extractable text "
            "(image-only PDF is not sent as an image)."
        )
    return text
