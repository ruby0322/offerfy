from io import BytesIO

from PIL import Image

from app.services.og_image import (
    OG_BG_RGB,
    OG_CACHE_MAX,
    OG_HEIGHT,
    OG_WIDTH,
    compose_og_png,
    og_cache_clear,
    og_cache_get,
    og_cache_put,
    og_etag,
)


def _solid_png(w: int, h: int, color: tuple[int, int, int]) -> bytes:
    img = Image.new("RGB", (w, h), color)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_og_etag_changes_with_source():
    a = og_etag("#let x = 1")
    b = og_etag("#let x = 2")
    assert a.startswith('"') and a.endswith('"')
    assert a != b
    assert og_etag("#let x = 1") == a


def test_compose_og_png_is_1200x630_cream_letterbox():
    page = _solid_png(200, 400, (0, 0, 255))
    composed = compose_og_png(page)
    assert composed[:8] == b"\x89PNG\r\n\x1a\n"
    img = Image.open(BytesIO(composed)).convert("RGB")
    assert img.size == (OG_WIDTH, OG_HEIGHT)
    assert img.getpixel((0, 0)) == OG_BG_RGB
    assert img.getpixel((OG_WIDTH - 1, OG_HEIGHT - 1)) == OG_BG_RGB
    cx, cy = OG_WIDTH // 2, OG_HEIGHT // 2
    assert img.getpixel((cx, cy)) == (0, 0, 255)


def test_og_cache_lru_evicts_oldest():
    og_cache_clear()
    etag = og_etag("x")
    for i in range(OG_CACHE_MAX + 1):
        og_cache_put(f"tok-{i}", etag, f"body-{i}".encode())
    assert og_cache_get("tok-0", etag) is None
    assert og_cache_get(f"tok-{OG_CACHE_MAX}", etag) == f"body-{OG_CACHE_MAX}".encode()
