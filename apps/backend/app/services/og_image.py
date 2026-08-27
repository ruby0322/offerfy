from __future__ import annotations

import hashlib
from collections import OrderedDict
from io import BytesIO
from threading import Lock

from PIL import Image

OG_WIDTH = 1200
OG_HEIGHT = 630
OG_PAD = 48
OG_BG_RGB = (0xF6, 0xF1, 0xE8)
OG_VERSION = "og-v1-1200x630-f6f1e8-pad48"
OG_CACHE_MAX = 32

_cache: OrderedDict[tuple[str, str], bytes] = OrderedDict()
_lock = Lock()


def og_etag(source: str) -> str:
    digest = hashlib.sha256(f"{OG_VERSION}\n{source}".encode()).hexdigest()
    return f'"{digest}"'


def compose_og_png(page_png: bytes) -> bytes:
    page = Image.open(BytesIO(page_png)).convert("RGBA")
    canvas = Image.new("RGB", (OG_WIDTH, OG_HEIGHT), OG_BG_RGB)
    inner_w = OG_WIDTH - 2 * OG_PAD
    inner_h = OG_HEIGHT - 2 * OG_PAD
    scale = min(inner_w / page.width, inner_h / page.height)
    new_w = max(1, round(page.width * scale))
    new_h = max(1, round(page.height * scale))
    fitted = page.resize((new_w, new_h), Image.Resampling.LANCZOS)
    rgb = Image.new("RGB", fitted.size, OG_BG_RGB)
    rgb.paste(fitted, mask=fitted.split()[3])
    x = (OG_WIDTH - rgb.width) // 2
    y = (OG_HEIGHT - rgb.height) // 2
    canvas.paste(rgb, (x, y))
    out = BytesIO()
    canvas.save(out, format="PNG")
    return out.getvalue()


def og_cache_get(token: str, etag: str) -> bytes | None:
    key = (token, etag)
    with _lock:
        val = _cache.get(key)
        if val is not None:
            _cache.move_to_end(key)
        return val


def og_cache_put(token: str, etag: str, body: bytes) -> None:
    key = (token, etag)
    with _lock:
        _cache[key] = body
        _cache.move_to_end(key)
        while len(_cache) > OG_CACHE_MAX:
            _cache.popitem(last=False)


def og_cache_clear() -> None:
    with _lock:
        _cache.clear()
