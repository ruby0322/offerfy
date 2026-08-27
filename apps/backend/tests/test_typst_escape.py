from pathlib import Path

import pytest
from app.services.typst_compile import compile_status, typst_available
from app.services.typst_escape import sanitize_typst_source

STARTER = Path(__file__).resolve().parents[1] / "typst" / "starter.typ"


def test_sanitize_is_noop_on_starter():
    source = STARTER.read_text()
    assert sanitize_typst_source(source) == source


def test_sanitize_escapes_quotes_inside_let_string():
    source = STARTER.read_text().replace("Your Name", 'Wei "Eric" Chen', 1)
    fixed = sanitize_typst_source(source)
    assert r'Wei \"Eric\" Chen' in fixed
    assert '#let name = "Wei "Eric" Chen"' not in fixed


def test_sanitize_keeps_already_escaped_quotes():
    source = STARTER.read_text().replace("Your Name", r"Wei \"Eric\" Chen", 1)
    assert sanitize_typst_source(source) == source


def test_sanitize_escapes_dollar_and_at_in_bullets():
    source = STARTER.read_text().replace(
        "- Replace this bullet with an achievement",
        "- Raised $100k with @ada",
        1,
    )
    fixed = sanitize_typst_source(source)
    assert r"\$100k" in fixed
    assert r"\@ada" in fixed


def test_sanitize_escapes_unmatched_star_in_bullets():
    source = STARTER.read_text().replace(
        "- Replace this bullet with an achievement",
        "- Used *bold* and leftover *",
        1,
    )
    fixed = sanitize_typst_source(source)
    assert "leftover *" not in fixed or r"\*" in fixed


@pytest.mark.skipif(not typst_available(), reason="Typst CLI is required")
def test_sanitize_makes_common_fill_artifacts_compile():
    source = STARTER.read_text()
    messy = (
        source.replace("Your Name", 'Wei "Eric" Chen', 1)
        .replace(
            "- Replace this bullet with an achievement",
            "- Raised $100k; ping @ada",
            1,
        )
    )
    assert compile_status(messy).get("ok") is False
    fixed = sanitize_typst_source(messy)
    status = compile_status(fixed)
    assert status.get("ok") is True, status
