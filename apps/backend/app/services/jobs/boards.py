from __future__ import annotations

import json
from pathlib import Path

from app.services.jobs.types import Board, JOB_SOURCES

_BACKEND_ROOT = Path(__file__).resolve().parents[3]


def boards_path() -> Path:
    return _BACKEND_ROOT / "data" / "ats_boards.json"


def load_boards(path: Path | None = None) -> list[Board]:
    data = json.loads((path or boards_path()).read_text(encoding="utf-8"))
    boards: list[Board] = []
    seen: set[tuple[str, str]] = set()
    for row in data.get("boards", []):
        source = str(row.get("source") or "")
        slug = str(row.get("slug") or "").strip()
        company = str(row.get("company") or slug).strip()
        if source not in JOB_SOURCES or source == "taiwanjobs" or not slug:
            continue
        key = (source, slug)
        if key in seen:
            continue
        seen.add(key)
        boards.append(Board(source=source, slug=slug, company=company))
    return boards
