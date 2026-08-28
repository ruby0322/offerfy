from __future__ import annotations

import hashlib
from collections import OrderedDict
from io import BytesIO
from pathlib import Path
from threading import Lock

from PIL import Image, ImageDraw, ImageFont

OG_WIDTH = 1200
OG_HEIGHT = 630
OG_PAD = 48
OG_INNER_W = OG_WIDTH - 2 * OG_PAD
OG_BG_RGB = (0xF6, 0xF1, 0xE8)
OG_CLAY_RGB = (0xA3, 0x5C, 0x3A)
OG_INK_RGB = (0x1C, 0x19, 0x14)
OG_VERSION = "og-v2-1200x630-f6f1e8-top-pad48-mark"
OG_CACHE_MAX = 32
OG_MARK_FONT = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")

_cache: OrderedDict[tuple[str, str], bytes] = OrderedDict()
_lock = Lock()


def og_etag(source: str) -> str:
    digest = hashlib.sha256(f"{OG_VERSION}\n{source}".encode()).hexdigest()
    return f'"{digest}"'


def _draw_mark(canvas: Image.Image) -> None:
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((48, 20, 55, 27), fill=OG_CLAY_RGB)
    if not OG_MARK_FONT.is_file():
        return
    font = ImageFont.truetype(str(OG_MARK_FONT), 20)
    text = "Offerfy"
    left, top, right, bottom = font.getbbox(text)
    text_h = bottom - top
    text_y = int(24 - text_h / 2 - top)
    text_y = max(0, min(text_y, OG_PAD - 1 - text_h))
    draw.text((48 + 8 + 8, text_y), text, fill=OG_INK_RGB, font=font)


def compose_og_png(page_png: bytes) -> bytes:
    page = Image.open(BytesIO(page_png)).convert("RGBA")
    canvas = Image.new("RGB", (OG_WIDTH, OG_HEIGHT), OG_BG_RGB)
    scale = OG_INNER_W / page.width
    new_w = OG_INNER_W
    new_h = max(1, round(page.height * scale))
    fitted = page.resize((new_w, new_h), Image.Resampling.LANCZOS)
    rgb = Image.new("RGB", fitted.size, OG_BG_RGB)
    rgb.paste(fitted, mask=fitted.split()[3])
    canvas.paste(rgb, (OG_PAD, OG_PAD))
    _draw_mark(canvas)
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
