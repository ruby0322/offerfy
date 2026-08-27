import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from fastapi import HTTPException

from app.services.starter import resolve_font_paths, resolve_package_path

_TYPST_BIN_CANDIDATES = (
    os.environ.get("TYPST_BIN"),
    str(Path(__file__).resolve().parents[2] / ".tools" / "typst"),
    "typst",
)

_RAW_AMP = re.compile(r"&(?!(?:#\d+|#x[0-9A-Fa-f]+|[A-Za-z][A-Za-z0-9]*);)")


def typst_binary() -> str | None:
    for candidate in _TYPST_BIN_CANDIDATES:
        if not candidate:
            continue
        if candidate != "typst" and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
        found = shutil.which(candidate)
        if found:
            return found
    return None


def typst_available() -> bool:
    return typst_binary() is not None


def _svg_page_index(path: Path) -> int:
    suffix = path.stem.rsplit("-", 1)[-1]
    return int(suffix) if suffix.isdigit() else 0


def sanitize_typst_svg(svg: str) -> str:
    """Make Typst SVG well-formed XML.

    Typst emits raw ``&`` in URLs (``?api=1&query=``). Browsers refuse to
    decode that as ``<img src>``, so the first preview page disappears.
    """
    return _RAW_AMP.sub("&amp;", svg)


def _svg_bytes(path: Path) -> bytes:
    return sanitize_typst_svg(path.read_text(encoding="utf-8")).encode("utf-8")


def compile_typst_pages(source: str, fmt: str, pages: str | None = None) -> list[bytes]:
    """Compile the document.

    Typst 0.15 writes one SVG/PNG file per page and requires `{p}` in the
    output path. PDF remains a single file. Never pass `--pages 1` as a
    substitute for a page template — that drops every page after the first.
    """
    if fmt not in {"svg", "pdf"}:
        raise HTTPException(status_code=400, detail="format must be svg or pdf")
    binary = typst_binary()
    if binary is None:
        raise HTTPException(status_code=503, detail="Typst is not available")

    package_path = resolve_package_path()
    font_paths = resolve_font_paths()

    with tempfile.TemporaryDirectory(prefix="offerfy-typst-") as tmp:
        tmp_path = Path(tmp)
        src_path = tmp_path / "resume.typ"
        src_path.write_text(source, encoding="utf-8")
        out_path = tmp_path / ("resume-{p}.svg" if fmt == "svg" else f"resume.{fmt}")
        cmd = [
            binary,
            "compile",
            f"--format={fmt}",
            "--package-path",
            package_path,
        ]
        if pages:
            cmd.extend(["--pages", pages])
        for font_path in font_paths:
            cmd.extend(["--font-path", font_path])
        cmd.extend([str(src_path), str(out_path)])
        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                check=False,
                timeout=30,
            )
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(status_code=504, detail="Typst compile timed out") from exc
        if completed.returncode != 0:
            err = (completed.stderr or completed.stdout or b"").decode("utf-8", errors="replace")
            error_lines = [
                line for line in err.splitlines() if "error:" in line.lower()
            ]
            snippet = "\n".join(error_lines)[:2000] if error_lines else err[-2000:]
            raise HTTPException(status_code=400, detail=f"Typst compile failed: {snippet}")
        if fmt == "pdf":
            pdf_path = tmp_path / "resume.pdf"
            if pdf_path.is_file():
                return [pdf_path.read_bytes()]
            raise HTTPException(status_code=500, detail="Typst produced no output")
        numbered = sorted(tmp_path.glob("resume-*.svg"), key=_svg_page_index)
        if numbered:
            return [_svg_bytes(path) for path in numbered]
        fallback = tmp_path / "resume.svg"
        if fallback.is_file():
            return [_svg_bytes(fallback)]
        raise HTTPException(status_code=500, detail="Typst produced no output")


def compile_status(source: str) -> dict:
    if not typst_available():
        return {"ok": True, "skipped": True}
    try:
        compile_typst(source, "pdf")
        return {"ok": True}
    except HTTPException as exc:
        return {"ok": False, "error": str(exc.detail)[:2000]}


def compile_typst(source: str, fmt: str, pages: str | None = None) -> bytes:
    """Single compile artifact. PDF is always one file; SVG callers that need
    every page must use compile_typst_pages."""
    blobs = compile_typst_pages(source, fmt, pages=pages)
    return blobs[0]
