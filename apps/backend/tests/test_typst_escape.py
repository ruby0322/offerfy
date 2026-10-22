from pathlib import Path

import pytest
from app.services.typst_compile import compile_status, typst_available
from app.services.typst_escape import sanitize_typst_source

TYPST = Path(__file__).resolve().parents[1] / "typst"
STARTER = TYPST / "starter.typ"
BASIC_RESUME = (
    TYPST / "packages" / "preview" / "basic-resume" / "0.2.9" / "template" / "main.typ"
)


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


def test_sanitize_is_noop_on_basic_resume_template():
    """The template's `/* ... */` block lists `name: ""` placeholders, and its
    commented-out examples are `//` lines. Sanitizing must not touch either."""
    source = BASIC_RESUME.read_text()
    assert sanitize_typst_source(source) == source


def test_sanitize_leaves_line_comments_alone():
    source = '// #work(company: "", dates: "", title: "")'
    assert sanitize_typst_source(source) == source


def test_sanitize_leaves_block_comments_alone():
    source = (
        "/*\n"
        '* #work(company: "", title: "")\n'
        "* nested /* inner */ is still a comment\n"
        "*/"
    )
    assert sanitize_typst_source(source) == source


def test_sanitize_keeps_trailing_comment_out_of_the_string():
    source = '  title: "Engineer",  // was "Intern"'
    assert sanitize_typst_source(source) == source


def test_sanitize_still_escapes_code_before_a_trailing_comment():
    assert (
        sanitize_typst_source('  title: "Wei "Eric" Chen",  // nickname')
        == r'  title: "Wei \"Eric\" Chen",  // nickname'
    )
    assert sanitize_typst_source("- Raised $1M // note") == r"- Raised \$1M // note"


def test_sanitize_does_not_mistake_a_url_for_a_comment():
    source = '#let personal-site = "https://stuxf.dev/a//b"\n- Raised $1M'
    fixed = sanitize_typst_source(source)
    assert fixed == '#let personal-site = "https://stuxf.dev/a//b"\n- Raised \\$1M'


def test_sanitize_resumes_escaping_after_a_block_comment_closes():
    source = '/*\n* #work(company: "", title: "")\n*/\n#let name = "Wei "Eric" Chen"'
    fixed = sanitize_typst_source(source)
    assert fixed.splitlines()[:3] == source.splitlines()[:3]
    assert fixed.splitlines()[3] == r'#let name = "Wei \"Eric\" Chen"'


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
