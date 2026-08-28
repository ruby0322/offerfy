from io import BytesIO

from PIL import Image

from app.services.og_image import (
    OG_BG_RGB,
    OG_CACHE_MAX,
    OG_HEIGHT,
    OG_VERSION,
    OG_WIDTH,
    compose_og_png,
    og_cache_clear,
    og_cache_get,
    og_cache_put,
    og_etag,
)

OG_CLAY = (0xA3, 0x5C, 0x3A)


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


def test_og_version_is_v2_topcrop():
    assert OG_VERSION == "og-v2-1200x630-f6f1e8-top-pad48-mark"


def test_compose_og_png_width_fit_top_crop():
    page = _solid_png(200, 400, (0, 0, 255))
    composed = compose_og_png(page)
    img = Image.open(BytesIO(composed)).convert("RGB")
    assert img.size == (OG_WIDTH, OG_HEIGHT)
    assert img.getpixel((0, 0)) == OG_BG_RGB
    assert img.getpixel((OG_WIDTH - 1, 0)) == OG_BG_RGB
    assert img.getpixel((0, 47)) == OG_BG_RGB
    assert img.getpixel((0, 100)) == OG_BG_RGB
    assert img.getpixel((OG_WIDTH // 2, OG_HEIGHT - 1)) == (0, 0, 255)
    assert img.getpixel((48, 50)) == (0, 0, 255)
    assert img.getpixel((48, 20)) == OG_CLAY
    assert img.getpixel((51, 23)) == OG_CLAY


def test_compose_og_png_landscape_leaves_cream_below():
    page = _solid_png(400, 100, (0, 0, 255))
    img = Image.open(BytesIO(compose_og_png(page))).convert("RGB")
    assert img.getpixel((OG_WIDTH // 2, OG_HEIGHT - 1)) == OG_BG_RGB


def test_og_cache_lru_evicts_oldest():
    og_cache_clear()
    etag = og_etag("x")
    for i in range(OG_CACHE_MAX + 1):
        og_cache_put(f"tok-{i}", etag, f"body-{i}".encode())
    assert og_cache_get("tok-0", etag) is None
    assert og_cache_get(f"tok-{OG_CACHE_MAX}", etag) == f"body-{OG_CACHE_MAX}".encode()
