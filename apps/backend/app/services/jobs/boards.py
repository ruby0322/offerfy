from __future__ import annotations

import json
from pathlib import Path

from app.services.jobs.types import JOB_SOURCES, Board
from app.services.jobs.workday import WorkdayBoard

_BACKEND_ROOT = Path(__file__).resolve().parents[3]


def workday_boards_path() -> Path:
    return _BACKEND_ROOT / "data" / "workday_boards.json"


def load_workday_boards(path: Path | None = None) -> list[WorkdayBoard]:
    """Workday boards need host/tenant/site, so they live in their own file.

    A row is skipped rather than raising: one malformed entry should not take
    down the whole pass.
    """
    data = json.loads((path or workday_boards_path()).read_text(encoding="utf-8"))
    boards: list[WorkdayBoard] = []
    seen: set[tuple[str, str]] = set()
    for row in data.get("boards", []):
        host = str(row.get("host") or "").strip()
        tenant = str(row.get("tenant") or "").strip()
        site = str(row.get("site") or "").strip()
        company = str(row.get("company") or tenant).strip()
        if not host or not tenant or not site:
            continue
        key = (tenant, site)
        if key in seen:
            continue
        seen.add(key)
        boards.append(WorkdayBoard(host=host, tenant=tenant, site=site, company=company))
    return boards


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
