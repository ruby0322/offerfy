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


def test_markup_value_escapes_bare_dollar_and_at():
    assert markup_value("Raised $100k with @ada") == r"Raised \$100k with \@ada"


def test_markup_value_escapes_every_star_regardless_of_pairing():
    # Unlike escape_typst_markup, which only escapes an *unpaired* `*`
    # (assuming a matched pair is intentional bold formatting an editor
    # wrote on purpose): a matched pair in raw data is not an intentional
    # formatting request either, it is just two characters next to each
    # other (`C# and F#`, `Rated *5/5*`), so every `*` is escaped the same
    # way regardless of how many there are.
    assert markup_value("Used *bold* and leftover *") == r"Used \*bold\* and leftover \*"


def test_markup_value_escapes_hash_tilde_underscore_backtick_angles_and_brackets():
    # Review finding (Critical): `#` opens Typst code mode and was the
    # one character the first version of this function missed entirely --
    # `C# and F#` failed to compile, and `Ranked #1 of 40 reps` compiled
    # but silently dropped the `#` (`dropped == ()` was a lie). `<...>`
    # can open a label (`Owned the <draft> spec handoff` silently lost
    # `<draft>`); `~` is the non-breaking-space shorthand; `_`/`` ` ``
    # toggle emphasis/raw the same way `*` does; `[`/`]` can open a
    # content block. All verified against the real binary to round-trip
    # to the literal character once escaped (see
    # tests/test_emitters_compile.py for the end-to-end proof through a
    # registered emitter).
    hostile = "C# #tag <lbl> [idx] `raw` a~b x_y s*b @ref $1k"
    escaped = markup_value(hostile)
    assert escaped == (
        r"C\# \#tag \<lbl\> \[idx\] \`raw\` a\~b x\_y s\*b \@ref \$1k"
    )


def test_markup_value_escapes_dash_plus_slash_and_equals_everywhere_not_just_at_index_0():
    # Critical (second round): a prior version of this function escaped
    # `-`/`+`/`/`/`=` only as the first character of the whole *string*,
    # reasoning that a bullet/heading's own fixed prefix already occupies
    # line-start for every caller except a bare paragraph (summary).
    # That reasoning broke on leading whitespace (still line-initial once
    # rendered, but not index 0) and on an embedded newline (makes
    # whatever follows line-initial too, invisible to a single `out[0]`
    # check) -- verified against the real binary to fabricate a bullet/
    # heading or hard-fail the compile (`/` at true line start: "expected
    # colon", term-list syntax). Escaping every occurrence -- mid-string
    # too, not only "when a check decides it matters" -- has no position
    # left to get wrong.
    assert markup_value("a - b + c / d = e") == "a \\- b \\+ c \\/ d \\= e"
    for marker in "-+/=":
        assert markup_value(f"{marker} leads a line") == f"\\{marker} leads a line"
        assert markup_value(f"  {marker} indented") == f"  \\{marker} indented"


def test_markup_value_prevents_a_double_slash_comment_anywhere_on_the_line():
    # Critical (second round): `//` opens a Typst line comment -- not
    # only at the start of a line, anywhere a `/` is immediately followed
    # by another `/`. Escaping only a line-initial `/` left this wide
    # open: "Designer // Developer at Acme" rendered as just "Designer",
    # and a `//` inside one `SkillGroup` item swallowed every item after
    # it on that group's single source line. Escaping *every* `/`
    # unconditionally means a raw `//` can never reach the document at
    # all -- there are always two separate `\/` escapes, never one raw
    # comment-opening token.
    assert markup_value("Designer // Developer at Acme") == "Designer \\/\\/ Developer at Acme"
    assert markup_value("Vue//Nuxt") == "Vue\\/\\/Nuxt"


def test_markup_value_prevents_a_block_comment_from_opening_or_closing():
    # `/*` opens a block comment that runs to end-of-file if never closed
    # (the exact bug app.services.import_qa's module docstring documents
    # for LLM-written source) and `*/` would close one if it were ever
    # open. Both `/` and `*` are escaped unconditionally, so neither
    # digraph can form.
    assert markup_value("a /* b */ c") == "a \\/\\* b \\*\\/ c"


def test_markup_value_normalises_an_embedded_newline_to_a_space():
    # ResumeContent's fields are plain str with nothing upstream
    # stripping a newline out of an LLM extraction -- a multi-line value
    # is representable even though every emitter treats each one as a
    # single logical line. A raw newline passed through would split that
    # line in the emitted Typst source; for `_skill_line`, which builds
    # one whole skill group as a single source line, that silently drops
    # every item after the newline from the group.
    assert markup_value("line one\nline two") == "line one line two"
    assert markup_value("line one\r\nline two") == "line one line two"
    # And critically, normalising first means the text after a former
    # newline is no longer line-initial at all, so it needs no special
    # handling of its own -- the unconditional escaping above already
    # covers it.
    assert markup_value("line one\n- line two") == "line one \\- line two"


def test_markup_value_normalises_every_typst_line_break_character():
    # Typst's own lexer treats more than ASCII \n/\r as a line break --
    # vertical tab, form feed, NEL, and the Unicode line/paragraph
    # separators all split a line the same way. None of these are
    # stripped out of an LLM extraction either, so each gets the same
    # normalisation as \n for the same reason.
    for ch in ("\x0b", "\x0c", "\x85", " ", " "):
        assert markup_value(f"line one{ch}line two") == "line one line two"


def test_markup_value_leaves_a_dash_run_or_ellipsis_alone():
    # Settled, not a gap: a *run* of two or more `-` (Typst's en/em dash
    # shorthand) and three `.` (ellipsis) are intact typography this
    # feature must not suppress -- the same substitution the starter and
    # LLM paths already produce, not content loss (no word, number or
    # token disappears; the IR remains authoritative). `.` was never in
    # the escaped set; a lone `-` still is -- escaping every `-`
    # unconditionally does mean a `--`/`---` run renders as literal
    # hyphens rather than a dash once it passes through this function,
    # a disclosed trade-off (see the module docstring), not tested here
    # as a requirement either way. What this test pins down is only that
    # `.` is never touched, so "..." is untouched by this function and
    # free to become an ellipsis downstream.
    assert markup_value("Loading... done") == "Loading... done"
