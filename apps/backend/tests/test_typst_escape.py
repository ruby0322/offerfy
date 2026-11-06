from pathlib import Path

import pytest
from app.services.typst_compile import compile_status, typst_available
from app.services.typst_escape import markup_value, sanitize_typst_source, typst_value

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


def test_typst_value_renders_nested_dicts_and_lists():
    out = typst_value({"a": [1, "x"], "b": {"c": True}, "d": None})
    assert out.replace("\n", "").replace(" ", "") == '("a":(1,"x",),"b":("c":true,),"d":none,)'


def test_typst_value_escapes_quotes_and_backslashes():
    assert typst_value('he said "hi"\\n') == '"he said \\"hi\\"\\\\n"'


def test_typst_value_renders_empty_dict_and_list():
    assert typst_value({}) == "(:)"
    assert typst_value([]) == "()"


# markup_value: the data-mode counterpart to escape_typst_markup, for raw
# text that was never pre-escaped (see its own docstring for why the two
# cannot share one implementation -- a `"` is not a string literal to
# protect in data, and a backslash is never already an intentional escape).


def test_markup_value_escapes_a_dollar_and_at_sign_inside_a_quoted_phrase():
    # escape_typst_markup copies a quoted span verbatim, assuming it is a
    # real Typst string literal -- correct for source, wrong here: a `"`
    # in raw data is just a character, so `$`/`@` right after it must
    # still be escaped. Verified against the real binary (see
    # tests/test_emitters_compile.py): without this, Typst raises
    # "unclosed delimiter" or treats the text after `@` as a label.
    assert markup_value('Set "$HOME @ 50%" in prod') == 'Set "\\$HOME \\@ 50%" in prod'


def test_markup_value_doubles_a_raw_backslash_so_it_survives_rendering():
    # escape_typst_markup passes a backslash plus the next character
    # through unchanged, assuming it is already an intentional escape --
    # correct for already-written source, wrong for raw data: Typst then
    # reads e.g. `\c` as an escape that renders a bare `c`, silently
    # dropping the backslash. Doubling first makes Typst see an escaped
    # backslash instead, which renders back to exactly one backslash.
    assert markup_value("C:\\configs\\prod") == "C:\\\\configs\\\\prod"


def test_markup_value_escapes_bare_dollar_at_and_unpaired_star():
    assert markup_value("Raised $100k with @ada") == r"Raised \$100k with \@ada"
    assert markup_value("Used *bold* and leftover *") == r"Used \*bold\* and leftover \*"
