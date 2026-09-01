from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import httpx

from app.services.jobs.http import get_bytes
from app.services.jobs.sanitize import sanitize_html
from app.services.jobs.types import NormalizedJob

FEED_URL = "https://free.taiwanjobs.gov.tw/webservice_taipei/Webservice.ashx"
_ILLEGAL_TAG = re.compile(r"[（(][^）)]*[）)]")
_BACKEND_ROOT = Path(__file__).resolve().parents[3]


def queries_path() -> Path:
    return _BACKEND_ROOT / "data" / "taiwanjobs_queries.json"


def load_queries(path: Path | None = None) -> list[dict[str, str | int]]:
    data = json.loads((path or queries_path()).read_text(encoding="utf-8"))
    rows = data.get("queries") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return [{"count": 200}]
    out: list[dict[str, str | int]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        item: dict[str, str | int] = {"count": int(row.get("count") or 200)}
        if row.get("zipno"):
            item["zipno"] = str(row["zipno"])
        if row.get("jobno"):
            item["jobno"] = str(row["jobno"])
        out.append(item)
    return out or [{"count": 200}]


def fetch_taiwanjobs(
    client: httpx.Client,
    queries: list[dict[str, str | int]] | None = None,
) -> list[NormalizedJob]:
    seen: dict[str, NormalizedJob] = {}
    for query in queries if queries is not None else load_queries():
        url = _query_url(query)
        try:
            response = get_bytes(client, url)
            if response.status_code == 404:
                continue
            response.raise_for_status()
        except httpx.HTTPError:
            continue
        for job in parse_taiwanjobs(response.text):
            seen[job.source_id] = job
    return list(seen.values())


def parse_taiwanjobs(raw: str) -> list[NormalizedJob]:
    text = raw.strip()
    if not text:
        return []
    if text.startswith("{") or text.startswith("["):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = None
        if payload is not None:
            return _from_json(payload)
    cleaned = _ILLEGAL_TAG.sub("", text)
    try:
        root = ET.fromstring(cleaned)
    except ET.ParseError:
        return []
    rows = _xml_rows(root)
    out: list[NormalizedJob] = []
    for row in rows:
        job = _from_row(row)
        if job is not None:
            out.append(job)
    return out


def _query_url(query: dict[str, str | int]) -> str:
    params = []
    count = int(query.get("count") or 200)
    if count < 1:
        count = 1
    if count > 1000:
        count = 1000
    params.append(f"count={count}")
    if query.get("zipno"):
        params.append(f"zipno={query['zipno']}")
    if query.get("jobno"):
        params.append(f"jobno={query['jobno']}")
    return f"{FEED_URL}?{'&'.join(params)}"


def _xml_rows(root: ET.Element) -> list[dict[str, str]]:
    # ASP.NET DataSet typically wraps each listing in Table / 職缺.
    candidates: list[ET.Element] = []
    for tag in ("Table", "table", "JOB", "job"):
        found = root.findall(f".//{tag}")
        if found:
            candidates = found
            break
    if not candidates:
        # Fall back to immediate children that look like records.
        candidates = [child for child in list(root) if len(list(child))]
    rows: list[dict[str, str]] = []
    for node in candidates:
        row: dict[str, str] = {}
        for child in list(node):
            key = _local(child.tag)
            row[key] = (child.text or "").strip()
        if row:
            rows.append(row)
    return rows


def _local(tag: str) -> str:
    if "}" in tag:
        tag = tag.rsplit("}", 1)[-1]
    return tag.strip()


def _from_json(payload: Any) -> list[NormalizedJob]:
    rows: list[Any]
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        for key in ("data", "records", "jobs", "Table"):
            if isinstance(payload.get(key), list):
                rows = payload[key]
                break
        else:
            rows = [payload]
    else:
        return []
    out: list[NormalizedJob] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        job = _from_row({str(k): "" if v is None else str(v) for k, v in row.items()})
        if job is not None:
            out.append(job)
    return out


def _from_row(row: dict[str, str]) -> NormalizedJob | None:
    title = _first(row, "OCCU_DESC", "職務名稱", "title", "JOB_TITLE")
    company = _first(row, "COMPNAME", "公司名稱", "company", "COMP_NAME")
    if not title or not company:
        return None
    detail = _first(row, "JOB_DETAIL", "工作內容", "description", "CONTENT")
    city = _first(row, "CITYNAME", "City name", "縣市", "location", "WK_ADDR")
    url = _first(row, "URL_QUERY", "url", "LINK", "JOB_URL")
    source_id = _first(row, "JOBNO", "jobno", "ID") or _hash_id(company, title, url or city)
    if not url:
        url = f"https://www.taiwanjobs.gov.tw/"
    html, text = sanitize_html(f"<p>{detail}</p>" if detail else "")
    if not text:
        text = title
    posted = _parse_date(_first(row, "TRANDATE", "STOP_DATE", "posted_at"))
    return NormalizedJob(
        source="taiwanjobs",
        source_id=source_id[:255],
        company=company[:255],
        title=title[:512],
        location=city[:512] if city else None,
        remote=_remote(city, detail),
        apply_url=url[:2048],
        source_url=url[:2048],
        description_html=html,
        description_text=text,
        posted_at=posted,
    )


def _first(row: dict[str, str], *keys: str) -> str:
    lower = {k.lower(): v for k, v in row.items()}
    for key in keys:
        value = row.get(key) or lower.get(key.lower())
        if value and value.strip():
            return value.strip()
    return ""


def _hash_id(company: str, title: str, extra: str | None) -> str:
    blob = f"{company}|{title}|{extra or ''}".encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:32]


def _remote(location: str | None, detail: str) -> bool | None:
    blob = f"{location or ''} {detail}".lower()
    if "remote" in blob or "遠端" in blob or "居家" in blob:
        return True
    return None


def _parse_date(value: str) -> datetime | None:
    if not value:
        return None
    text = value.strip().replace("/", "-")
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y%m%d"):
        try:
            dt = datetime.strptime(text[:19], fmt).replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None
