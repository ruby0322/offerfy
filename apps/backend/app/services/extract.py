from io import BytesIO
from pathlib import Path

from pypdf import PdfReader

from pdfminer.high_level import extract_text as pdfminer_extract_text

ALLOWED_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".txt", ".md"}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def allowed_upload(filename: str) -> bool:
    suffix = Path(filename).suffix.lower()
    return suffix in ALLOWED_SUFFIXES


def extract_upload_text(filename: str, data: bytes) -> str | None:
    suffix = Path(filename).suffix.lower()
    if suffix in {".txt", ".md"}:
        return data.decode("utf-8", errors="replace")
    if suffix == ".pdf":
        return _extract_pdf(data)
    return None


def _extract_pdf(data: bytes) -> str | None:
    try:
        text = pdfminer_extract_text(BytesIO(data)) or ""
        if text.strip():
            return text
    except Exception:
        pass
    try:
        reader = PdfReader(BytesIO(data))
        parts = []
        for page in reader.pages:
            parts.append(page.extract_text() or "")
        joined = "\n".join(parts).strip()
        return joined or None
    except Exception:
        return None


def as_typst_comments(text: str, limit_lines: int = 400) -> str:
    lines = text.replace("\r\n", "\n").split("\n")[:limit_lines]
    return "\n".join("// " + line for line in lines)
