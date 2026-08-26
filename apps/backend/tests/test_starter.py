import re
from pathlib import Path

from app.services.starter import generate_starter

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def test_en_starter_matches_default_file():
    from_file = (BACKEND_ROOT / "typst" / "starter.typ").read_text(encoding="utf-8")
    generated = generate_starter("en")
    assert generated == from_file


def test_en_starter_uses_basic_resume_a4_and_accent():
    src = generate_starter("en")
    assert "@preview/basic-resume:0.2.9" in src
    assert 'paper: "a4"' in src
    assert 'accent-color: "#f4be82"' in src
    assert "New Computer Modern" in src
    assert 'lang: "en"' in src


def test_zh_tw_starter_uses_noto_tc():
    src = generate_starter("zh-TW")
    assert "Noto Serif CJK TC" in src
    assert 'lang: "zh"' in src
    assert 'paper: "a4"' in src
    assert 'accent-color: "#f4be82"' in src
    assert "學歷" in src or "教育" in src
    assert "工作經歷" in src


def test_zh_cn_starter_uses_noto_sc():
    src = generate_starter("zh-CN")
    assert "Noto Serif CJK SC" in src
    assert 'lang: "zh"' in src
    assert "教育" in src or "教育经历" in src
    assert "工作经历" in src


def test_starter_dates_are_yyyy_mm_or_present():
    for locale in ("en", "zh-TW", "zh-CN"):
        src = generate_starter(locale)
        assert re.search(r"\d{4}-\d{2}", src)
        assert "Aug 2023" not in src
        assert "Present" in src or "至今" in src or "现在" in src


def test_starter_has_placeholders():
    src = generate_starter("en")
    assert "Your Name" in src or "name" in src.lower()
    assert "@" in src
    assert "== Education" in src
    assert "== Experience" in src
