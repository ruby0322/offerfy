from __future__ import annotations

import time
from typing import Any

import httpx

from app.services.jobs.types import USER_AGENT

DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


def new_client() -> httpx.Client:
    return httpx.Client(
        timeout=DEFAULT_TIMEOUT,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json, application/xml, text/xml, */*"},
        follow_redirects=True,
    )


def get_bytes(client: httpx.Client, url: str, *, attempts: int = 3) -> httpx.Response:
    last: httpx.Response | None = None
    for i in range(attempts):
        response = client.get(url)
        last = response
        if response.status_code in RETRY_STATUSES and i + 1 < attempts:
            time.sleep(0.4 * (i + 1))
            continue
        return response
    assert last is not None
    return last


def get_json(client: httpx.Client, url: str) -> Any:
    response = get_bytes(client, url)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()
