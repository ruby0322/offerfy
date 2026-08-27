from __future__ import annotations

import logging
import os
import re
import tarfile
import threading
import time
import tomllib
from pathlib import Path
from typing import Any

import httpx

from app.services.starter import resolve_package_path

logger = logging.getLogger(__name__)

INDEX_URL = "https://packages.typst.org/preview/index.json"
TARBALL_URL = "https://packages.typst.org/preview/{name}-{version}.tar.gz"
UNIVERSE_URL = "https://typst.app/universe/package/{name}/"
REGISTRY_THUMB_URL = (
    "https://raw.githubusercontent.com/typst/packages/main/packages/preview/"
    "{name}/{version}/{rel}"
)
PREFETCH_ENV = "OFFERFY_SKIP_TEMPLATE_PREFETCH"
INDEX_TTL_SEC = 3600
PACKAGE_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}$")
PREVIEW_SPEC_RE = re.compile(
    r"@preview/([a-zA-Z0-9][a-zA-Z0-9._-]{0,79}):([0-9]+(?:\.[0-9A-Za-z.+-]*)*)"
)
EXAMPLE_LIMIT = 12_000
IMAGE_SUFFIXES = {".png", ".webp", ".jpg", ".jpeg", ".gif"}
IMAGE_MEDIA = {
    ".png": "image/png",
    ".webp": "image/webp",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
}

_prefetch_started = False
_prefetch_lock = threading.Lock()
_index_lock = threading.Lock()
_cv_cache: tuple[float, list[dict[str, Any]]] | None = None


def package_root() -> Path:
    return Path(resolve_package_path())


def _version_key(version: str) -> tuple:
    parts: list[tuple[int, int | str]] = []
    for chunk in str(version).split("."):
        try:
            parts.append((0, int(chunk)))
        except ValueError:
            parts.append((1, chunk))
    return tuple(parts)


def latest_cv_packages(index: list[dict[str, Any]]) -> list[dict[str, Any]]:
    newest: dict[str, dict[str, Any]] = {}
    for row in index:
        if not isinstance(row, dict):
            continue
        name = row.get("name")
        version = row.get("version")
        cats = row.get("categories") or []
        if not isinstance(name, str) or not isinstance(version, str):
            continue
        if not isinstance(cats, list) or "cv" not in cats:
            continue
        prev = newest.get(name)
        if prev is None or _version_key(version) > _version_key(str(prev.get("version") or "")):
            newest[name] = row
    return sorted(newest.values(), key=lambda item: str(item.get("name") or ""))


def apply_prompt(name: str, version: str) -> str:
    spec = f"@preview/{name}:{version}"
    return (
        f"Please rewrite my current Typst resume to use the {name} template "
        f"({spec}). Keep my existing content (name, contacts, education, "
        "experience, projects, skills). Follow that package's API and typical "
        "usage; if it expects YAML/JSON, inline the data in the Typst source "
        "so the document compiles as a single file. Keep paper a4 if the "
        "template allows it. Call read_typst first, then apply_typst_edit with "
        "the full `source` (do not send search+replace)."
    )


def is_template_switch_message(message: str) -> bool:
    text = message or ""
    return (
        "rewrite my current Typst resume to use the" in text
        and "apply_typst_edit" in text
        and "@preview/" in text
    )


def parse_preview_spec(text: str) -> tuple[str, str] | None:
    match = PREVIEW_SPEC_RE.search(text or "")
    if not match:
        return None
    return match.group(1), match.group(2)


def template_example_source(
    name: str, version: str, *, root: Path | None = None
) -> str | None:
    if not PACKAGE_NAME_RE.fullmatch(name):
        return None
    dest = (root or package_root()) / "preview" / name / version
    candidates: list[Path] = []
    toml_path = dest / "typst.toml"
    if toml_path.is_file():
        try:
            data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        template = data.get("template") if isinstance(data, dict) else None
        if isinstance(template, dict):
            rel_dir = str(template.get("path") or "template")
            entry = str(template.get("entrypoint") or "main.typ")
            candidates.append(dest / rel_dir / entry)
    candidates.extend(
        [
            dest / "template" / "main.typ",
            dest / "template" / "cv.typ",
            dest / "template" / "resume.typ",
        ]
    )
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key in seen or not path.is_file():
            continue
        seen.add(key)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if text.strip():
            return text[:EXAMPLE_LIMIT]
    return None


def template_api_block(name: str, version: str, *, root: Path | None = None) -> str:
    example = template_example_source(name, version, root=root)
    extra = (
        " Do not invent helpers like #section or #entry. Copy the example's "
        "function names, #show/resume.with arguments, and structure. Replace "
        "only the personal content. Inline any YAML/JSON/#include data so the "
        "file compiles alone. Missing fonts are warnings; unknown variables "
        "are errors. If apply_typst_edit returns compile.ok=false, rewrite "
        "the full source again."
    )
    if example:
        extra += (
            f" Official example for @preview/{name}:{version}:\n```typst\n"
            f"{example}\n```"
        )
    return extra


def package_cached(root: Path, name: str, version: str) -> bool:
    dest = root / "preview" / name / version
    return (dest / "typst.toml").is_file()


def reset_template_index_cache() -> None:
    global _cv_cache
    with _index_lock:
        _cv_cache = None


def thumbnail_relpath(row: dict[str, Any]) -> str | None:
    template = row.get("template")
    if isinstance(template, dict):
        thumb = template.get("thumbnail")
        if isinstance(thumb, str) and thumb.strip():
            return thumb.strip()
    thumb = row.get("thumbnail")
    if isinstance(thumb, str) and thumb.strip():
        return thumb.strip()
    return None


def _safe_under(dest: Path, rel: str) -> Path | None:
    dest = dest.resolve()
    candidate = (dest / rel).resolve()
    try:
        candidate.relative_to(dest)
    except ValueError:
        return None
    return candidate


def _safe_file(dest: Path, rel: str) -> Path | None:
    candidate = _safe_under(dest, rel)
    return candidate if candidate is not None and candidate.is_file() else None


def resolve_thumbnail(dest: Path, declared: str | None) -> Path | None:
    if declared:
        found = _safe_file(dest, declared)
        if found is not None:
            return found
        found = _safe_file(dest, f"template/{declared}")
        if found is not None:
            return found
    for name in ("thumbnail.png", "thumbnail.webp", "thumbnail.jpg", "thumbnail.jpeg"):
        found = _safe_file(dest, name)
        if found is not None:
            return found
    for folder in (dest, dest / "assets", dest / "template", dest / "examples"):
        if not folder.is_dir():
            continue
        matches = sorted(
            (
                item
                for item in folder.iterdir()
                if item.is_file()
                and item.suffix.lower() in IMAGE_SUFFIXES
                and "thumb" in item.name.lower()
            ),
            key=lambda item: item.name,
        )
        if matches:
            return matches[0]
    return None


def cached_latest_cv_packages() -> list[dict[str, Any]]:
    global _cv_cache
    now = time.monotonic()
    with _index_lock:
        if _cv_cache is not None and now - _cv_cache[0] < INDEX_TTL_SEC:
            return _cv_cache[1]
    try:
        rows = latest_cv_packages(fetch_index())
    except Exception:
        logger.warning("template list index fetch failed")
        rows = []
    with _index_lock:
        _cv_cache = (now, rows)
    return rows


def declared_thumbnail_file(dest: Path, declared: str | None) -> Path | None:
    if not declared:
        return None
    found = _safe_file(dest, declared)
    if found is not None:
        return found
    return _safe_file(dest, f"template/{declared}")


def registry_thumb_cache_path(root: Path, name: str, version: str, rel: str) -> Path | None:
    return _safe_under(root / ".universe-thumbs" / name / version, Path(rel).name)


def fetch_registry_thumbnail(name: str, version: str, rel: str, *, root: Path) -> Path | None:
    cache = registry_thumb_cache_path(root, name, version, rel)
    if cache is None:
        return None
    if cache.is_file():
        return cache
    url = REGISTRY_THUMB_URL.format(name=name, version=version, rel=rel.replace("\\", "/"))
    try:
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
            content_type = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
            if not content_type.startswith("image/"):
                return None
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(resp.content)
    except Exception:
        logger.warning("template thumbnail fetch failed name=%s version=%s", name, version)
        return None
    return cache if cache.is_file() else None


def find_thumbnail(name: str, *, root: Path | None = None) -> Path | None:
    if not PACKAGE_NAME_RE.fullmatch(name):
        return None
    packages = cached_latest_cv_packages()
    row = next((item for item in packages if str(item.get("name") or "") == name), None)
    if row is None:
        return None
    version = str(row.get("version") or "")
    if not version:
        return None
    base = root or package_root()
    dest = base / "preview" / name / version
    declared = thumbnail_relpath(row)
    found = declared_thumbnail_file(dest, declared)
    if found is not None:
        return found
    if declared:
        fetched = fetch_registry_thumbnail(name, version, declared, root=base)
        if fetched is not None:
            return fetched
    return resolve_thumbnail(dest, None)


def thumbnail_media_type(path: Path) -> str:
    return IMAGE_MEDIA.get(path.suffix.lower(), "application/octet-stream")


def fetch_index() -> list[dict[str, Any]]:
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(INDEX_URL)
        resp.raise_for_status()
        data = resp.json()
    if not isinstance(data, list):
        return []
    return [row for row in data if isinstance(row, dict)]


def _safe_extract(archive: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tar:
        tar.extractall(dest, filter="data")


def prefetch_one(name: str, version: str, *, root: Path | None = None) -> bool:
    base = root or package_root()
    dest = base / "preview" / name / version
    if package_cached(base, name, version):
        return True
    url = TARBALL_URL.format(name=name, version=version)
    dest.mkdir(parents=True, exist_ok=True)
    archive = dest / ".pkg.tar.gz"
    try:
        with httpx.Client(timeout=60.0, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
            archive.write_bytes(resp.content)
        _safe_extract(archive, dest)
    except Exception:
        logger.warning("template prefetch failed name=%s version=%s", name, version)
        return False
    finally:
        if archive.exists():
            archive.unlink(missing_ok=True)
    return package_cached(base, name, version)


def prefetch_cv_packages() -> None:
    try:
        packages = latest_cv_packages(fetch_index())
    except Exception:
        logger.warning("template index fetch failed")
        return
    root = package_root()
    for row in packages:
        name = str(row.get("name") or "")
        version = str(row.get("version") or "")
        if name and version:
            prefetch_one(name, version, root=root)


def start_template_prefetch() -> None:
    global _prefetch_started
    if os.environ.get(PREFETCH_ENV, "").strip() in {"1", "true", "yes"}:
        return
    with _prefetch_lock:
        if _prefetch_started:
            return
        _prefetch_started = True
    thread = threading.Thread(target=prefetch_cv_packages, name="cv-template-prefetch", daemon=True)
    thread.start()


def list_templates() -> list[dict[str, str | bool]]:
    rows = cached_latest_cv_packages()
    root = package_root()
    out: list[dict[str, str | bool]] = []
    for row in rows:
        name = str(row.get("name") or "")
        version = str(row.get("version") or "")
        if not name or not version:
            continue
        description = str(row.get("description") or "")
        out.append(
            {
                "name": name,
                "version": version,
                "description": description,
                "universe_url": UNIVERSE_URL.format(name=name),
                "import_line": f'#import "@preview/{name}:{version}": *',
                "apply_prompt": apply_prompt(name, version),
                "cached": package_cached(root, name, version),
            }
        )
    return out
