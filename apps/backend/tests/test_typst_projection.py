"""Projection of the synthetic fixtures: shapes, string args, the report, the tier."""

import json
import re

import pytest

from app.services.revisions import source_hash
from app.services.typst_projection import (
    Mark, Unit, is_style_key, parse_projection, serialize_unit)
from tests.test_typst_fixtures import FIXTURES


def read(name: str) -> str:
    return (FIXTURES / f"{name}.typ").read_text(encoding="utf-8")


def line_offsets(src: str):
    """(offset, text) for every line, without its newline."""
    pos = 0
    for line in src.split("\n"):
        yield pos, line
        pos += len(line) + 1


def unit_by_text(p, text: str):
    (u,) = [u for u in p.units.values() if u.kind == "line" and u.value == text]
    return u


def arg_values(p) -> list[str]:
    return [u.value for u in p.units.values() if u.kind == "arg"]


def dump(u) -> dict:
    return {"id": u.id, "kind": u.kind, "key": u.key, "role": u.role,
            "span": list(u.span), "value": u.value, "editable": u.editable}


def _unesc(slice_: str) -> str:
    return slice_.replace('\\"', '"').replace("\\\\", "\\")


def test_recognises_shapes_not_names():
    p = parse_projection(read("synthetic-en"))
    names = {b.name for s in p.sections for b in s.blocks if b.kind == "comp"}
    assert {"work", "edu", "project"} <= names        # #project has no emitter


def test_let_indirection_one_hop():
    p = parse_projection(read("synthetic-en"))
    author = next(u for u in p.units.values() if u.key == "author")
    assert author.value == "Avery Quill"              # from `#let name = "..."`
    assert author.editable


def test_dead_let_is_linted():
    p = parse_projection(read("synthetic-en"))
    assert any("phone" in l for l in p.report.lints)
    assert len(p.report.lints) == 1                   # `mail` is used by `email: mail`


def test_style_keys_are_marked_not_hidden():
    p = parse_projection(read("synthetic-en"))
    accent = next(u for u in p.units.values() if u.key == "accent-color")
    assert accent.style and accent.editable
    assert not next(u for u in p.units.values() if u.key == "title").style


def test_style_key_rule():
    assert all(is_style_key(k) for k in
               ["font", "paper", "lang", "margin", "size", "leading",
                "accent-color", "author-position", "body-font"])
    assert not any(is_style_key(k) for k in ["title", "company", "start-date", "colors"])


def test_unparsed_lines_never_become_units():
    p = parse_projection(read("synthetic-en"))
    args = [u for u in p.units.values() if u.kind == "arg"]
    assert args and len(args) < len(p.units)
    for u in args:
        # a keyed argument's leading `#` may only be a hex colour
        assert u.key is not None
        hexcolour = re.fullmatch(r"#[0-9a-fA-F]{3,8}", u.value)
        assert not u.value.lstrip().startswith("#") or hexcolour
    assert p.report.structures["#pagebreak"] == 1
    # synthetic-en has no comment; the marks fixture has exactly one (a commented bullet).
    assert parse_projection(read("synthetic-marks")).report.structures["comment"] == 1


def test_nested_call_arguments_are_flattened_and_non_literals_reported():
    p = parse_projection(read("synthetic-en"))
    keys = [u.key for u in p.units.values()]
    assert keys.count("start-date") == 3 and keys.count("end-date") == 3
    assert p.report.skipped_args["font"] == 1         # a tuple, not a string literal


@pytest.mark.parametrize("name,tier", [
    ("synthetic-en", "A"), ("synthetic-zh", "A"), ("synthetic-marks", "A"),
    ("synthetic-tier-b", "B"),
    ("synthetic-tier-c", "C")])
def test_every_fixture_grades_the_tier_it_exists_for(name, tier):
    # Task 1 could only check these verdicts with the throwaway prototype parser, so
    # nothing in the suite held them. This is where they get pinned for real.
    assert parse_projection(read(name)).tier == tier


def test_a_non_reversible_argument_is_read_only_and_makes_tier_b():
    p = parse_projection('#work(title: "Tab\\there", company: "Acme")\n')
    title = next(u for u in p.units.values() if u.key == "title")
    assert title.value == "Tab\\there" and not title.editable
    assert next(u for u in p.units.values() if u.key == "company").editable
    assert p.tier == "B"


def test_escaped_quote_and_backslash_round_trip():
    p = parse_projection('#work(title: "Say \\"hi\\" \\\\ ok")\n')
    (u,) = p.units.values()
    assert u.value == 'Say "hi" \\ ok' and u.editable


def test_crlf_and_missing_final_newline_keep_spans_exact():   # Review Focus 4
    src = read("synthetic-en").replace("\n", "\r\n").rstrip("\r\n")
    p = parse_projection(src)
    assert p.units and p.tier == "A"
    for u in p.units.values():
        assert "\r" not in u.value and "\r" not in src[u.span[0]:u.span[1]]
        if u.kind == "arg":
            assert _unesc(src[u.span[0]:u.span[1]]) == u.value
        else:
            assert u.editable and serialize_unit(u) == src[u.span[0]:u.span[1]]
    ref = parse_projection(read("synthetic-en"))
    assert [u.value for u in p.units.values()] == [u.value for u in ref.units.values()]


@pytest.mark.parametrize("name", ["synthetic-en", "synthetic-zh", "synthetic-marks"])
def test_spans_slice_back_to_the_literal(name):
    # zh is the point: its offsets are code points, not UTF-8 bytes, and en cannot tell.
    src = read(name)
    p = parse_projection(src)
    args = [u for u in p.units.values() if u.kind == "arg"]
    assert args
    for u in args:
        assert _unesc(src[u.span[0]:u.span[1]]) == u.value


def test_zh_spans_are_code_points_not_bytes():
    src = read("synthetic-zh")
    assert len(src.encode("utf-8")) > len(src)
    author = next(u for u in parse_projection(src).units.values() if u.key == "author")
    assert author.value == "林小筑" and src[slice(*author.span)] == "林小筑"


def test_sections_and_blocks_tile_the_source():
    src = read("synthetic-en")
    p = parse_projection(src)
    heads = [s for s in p.sections if s.id != "s0"]
    assert len(heads) == 5
    for s in heads:
        assert src[s.outer[0]:].startswith("== ")
        for b in s.blocks:
            assert s.outer[0] <= b.outer[0] < b.content_end <= b.outer[1] <= s.outer[1]
    work = next(b for s in p.sections for b in s.blocks if b.name == "work")
    assert src[work.outer[0]:].startswith("#work(")
    # content_end sits after the last bullet line, before the blank line that follows it
    assert src[:work.content_end].endswith("migration.\n")


def test_a_comment_between_bullets_belongs_to_its_block():
    src = read("synthetic-marks")
    p = parse_projection(src)
    work = next(b for s in p.sections for b in s.blocks if b.name == "work")
    assert len(work.notes) == 1
    a, b = work.notes[0]
    assert src[a:b].startswith("// - Retired")


def test_continuation_lines_of_a_multi_line_construct_are_skipped_whole():
    src = (
        '#let data = (\n'
        '  name: "Drew Lark",\n'
        '  jobs: (\n'
        '    (role: "Engineer", note: "don\'t (stop"),\n'
        '  ),\n'
        ')\n'
        '== Experience\n'
        '#work(\n'
        '  title: "Engineer",\n'
        ')\n'
        '#for j in data.jobs {\n'
        '  [#j.role (#j.org]\n'
        '}\n'
    )
    p = parse_projection(src)
    assert arg_values(p) == ["Engineer"]
    assert [u.value for u in p.units.values() if u.role == "heading"] == ["Experience"]
    assert p.report.structures["#let"] == 1 and p.report.structures["#for"] == 1
    assert [s.id for s in p.sections] == ["s1"]       # an empty preamble is not a section


def test_hash_is_the_revisions_hash():
    src = read("synthetic-en")
    assert parse_projection(src).hash == source_hash(src)


def test_empty_source_is_tier_c():
    p = parse_projection("")
    assert p.tier == "C" and p.sections == () and p.units == {}


def test_an_empty_string_argument_is_still_an_editable_unit():
    p = parse_projection(read("synthetic-en"))
    empty = [u for u in p.units.values() if u.kind == "arg" and u.value == ""]
    assert empty and all(u.editable and u.span[0] == u.span[1] for u in empty)
    assert "dates" not in p.report.skipped_args


def test_a_paren_in_markup_is_prose_and_the_next_section_survives():
    # `#stars (` has a space, so it is prose plus a paren, not a call. If it were read
    # as a call the `]` would pop the wrong frame and `== B` would be swallowed.
    src = ('== A\n#align(left)[Rated #stars (4 of 5]\n'
           '== B\n#work(title: "Second")\n')
    p = parse_projection(src)
    assert [s.id for s in p.sections] == ["s1", "s2"]
    assert arg_values(p) == ["Second"]
    assert "unterminated" not in p.report.structures


def test_an_unclosed_construct_is_reported_not_silent():
    src = ('== A\n#align(left)[Rated #stars(4 of 5]\n'
           '== B\n#work(title: "Second")\n')
    p = parse_projection(src)
    assert p.report.structures["unterminated"] == 1
    assert arg_values(p) == []                # nothing harvested from inside it


def test_an_unclosed_call_does_not_harvest_later_literals():
    p = parse_projection('#work(title: "A"\n== B\n#edu(school: "C")\n')
    assert arg_values(p) == [] and p.report.structures["unterminated"] == 1


def test_argument_recursion_stops_after_one_level():
    p = parse_projection('#work(dates: outer(inner(deep: "x"), start: "s"))\n')
    keys = [u.key for u in p.units.values()]
    assert "deep" not in keys and "start" in keys


# --- line units (Task 3) ---------------------------------------------------------------


def _slice(src, u):
    return src[u.span[0]:u.span[1]]


def test_bullet_marks_use_plain_text_offsets():
    p = parse_projection(read("synthetic-marks"))
    u = unit_by_text(p, "Shipped zero-downtime deploys and kept the error budget intact.")
    strong = [m for m in u.marks if m.kind == "strong"][0]
    emph = [m for m in u.marks if m.kind == "emph"][0]
    assert u.value[strong.start:strong.end] == "zero-downtime"
    assert u.value[emph.start:emph.end] == "error budget"


def test_link_inside_bold_round_trips():
    src = read("synthetic-marks")
    p = parse_projection(src)
    u = unit_by_text(p, "Maintained the public docs as a single source of truth.")
    assert {m.kind for m in u.marks} == {"strong", "link"}
    link = [m for m in u.marks if m.kind == "link"][0]
    assert link.url == "https://example.org/docs"
    assert u.value[link.start:link.end] == "the public docs"
    assert u.editable and serialize_unit(u) == _slice(src, u)


def test_heading_with_markup_is_a_line_unit():                 # Review Focus 1
    src = "== Skills & *Tools* and _more_\n\n- one\n"
    p = parse_projection(src)
    title = p.units[[s for s in p.sections if s.title_unit][0].title_unit]
    assert title.role == "heading" and title.marks
    assert title.value == "Skills & Tools and more"
    assert title.editable and serialize_unit(title) == _slice(src, title)


def test_every_heading_title_is_a_heading_unit():
    for name in ("synthetic-en", "synthetic-zh", "synthetic-marks"):
        src = read(name)
        p = parse_projection(src)
        titles = [p.units[s.title_unit] for s in p.sections if s.title_unit]
        assert len(titles) == len([s for s in p.sections if s.id != "s0"]) > 0
        for t in titles:
            assert t.role == "heading" and t.kind == "line"
            assert src[t.span[0] - 3:t.span[0]] == "== "


def test_escape_policy_follows_the_source():
    en, marks = read("synthetic-en"), read("synthetic-marks")
    raw = unit_by_text(parse_projection(en), "Skills & Awards")     # a bare & (heading)
    pm = parse_projection(marks)
    esc = unit_by_text(
        pm, "Escapes in one line: R&D, ops@night, 5* rating, ~100 hosts, $2M budget.")
    assert raw.role == "heading" and "&" not in raw.escapes
    assert esc.escapes == ("&",)               # the canonical ones are not recorded
    assert serialize_unit(raw) == _slice(en, raw)
    assert serialize_unit(esc) == _slice(marks, esc)
    assert "R\\&D" in serialize_unit(esc) and "ops\\@night" in serialize_unit(esc)


def test_commented_bullet_does_not_split_the_run():
    p = parse_projection(read("synthetic-marks"))
    comp = [b for s in p.sections for b in s.blocks if b.kind == "comp"][0]
    assert len(comp.bullets) == 5 and len(comp.notes) == 1
    after = [p.units[b.unit].value for b in comp.bullets][-2:]
    assert after[0].startswith("Owned the nested content block")
    assert after[1].startswith("Escapes in one line")


def test_unserialisable_line_is_read_only():
    src = read("synthetic-tier-b")
    p = parse_projection(src)
    bad = [u for u in p.units.values() if not u.editable]
    assert len(bad) == 1 and bad[0].kind == "line"
    assert bad[0].opaque == ("#footnote[Quarterly review, 2022.]",)
    assert serialize_unit(bad[0]) != _slice(src, bad[0])
    assert p.tier == "B"


@pytest.mark.parametrize("name", ["synthetic-en", "synthetic-zh", "synthetic-marks",
                                  "synthetic-tier-b", "synthetic-tier-c"])
def test_every_line_is_a_unit_a_block_or_a_counted_structure(name):
    # The design promises the report accounts for everything the form does not project
    # ("沒有被偷偷忽略"). After Task 2 a plain text line outside a block was in neither
    # place; this is the invariant that keeps that from becoming a permanent blind spot.
    src = read(name)
    p = parse_projection(src)
    spans = [u.span for u in p.units.values()]
    outers = [b.outer for s in p.sections for b in s.blocks]
    unaccounted = []
    for start, line in line_offsets(src):          # (offset, text) per line
        if not line.strip():
            continue
        end = start + len(line)
        if any(a < end and start < b for a, b in spans + outers):
            continue
        unaccounted.append(line.strip())
    # The expectation is a golden, read by a person: every line in it is something a form
    # should not show (#import, #set, #let, a comment, a #grid...), never résumé content.
    golden = json.loads((FIXTURES / f"{name}.unprojected.json").read_text("utf-8"))
    assert unaccounted == golden
    if unaccounted:
        assert sum(p.report.structures.values()) > 0


@pytest.mark.parametrize("name", ["synthetic-en", "synthetic-zh", "synthetic-marks"])
def test_rewriting_every_unit_changes_nothing(name):           # Invariant 1
    src = read(name)
    p = parse_projection(src)
    assert any(u.kind == "line" for u in p.units.values())
    out = src
    for u in sorted(p.units.values(), key=lambda x: -x.span[0]):
        out = out[:u.span[0]] + serialize_unit(u) + out[u.span[1]:]
    assert out == src


def test_a_loose_paragraph_and_bullets_are_units_with_exact_spans():
    src = "== A\nJust a line of prose.\n  - indented *bold* bullet  \n+ numbered\n"
    p = parse_projection(src)
    assert [(u.role, u.value) for u in p.units.values() if u.role != "heading"] == [
        ("para", "Just a line of prose."), ("bullet", "indented bold bullet"),
        ("bullet", "numbered")]
    for u in p.units.values():
        assert u.editable and serialize_unit(u) == _slice(src, u)


@pytest.mark.parametrize("line,opaque", [
    ("see $x^2$ here", ("$x^2$",)),
    ("run `ls -la` now", ("`ls -la`",)),
    ("tag <lbl> and @ref", ("<lbl>", "@ref")),
    ("end // trailing comment", ("// trailing comment",)),
    ("a #h(1fr) b", ("#h(1fr)",)),
    ("a#name b", ("#name",)),
    ("hard\\ break", ("\\ ",)),
])
def test_opaque_runs_make_the_unit_read_only(line, opaque):
    src = f"- {line}\n"
    (u,) = parse_projection(src).units.values()
    assert u.opaque == opaque and not u.editable


@pytest.mark.parametrize("line", [
    "snake_case word", "unclosed *bold", "stray ] bracket", "a # b", "http://x.org/y",
    "mixed R\\&D and R&D", "empty ** pair"])
def test_ambiguous_lines_are_read_only_never_rewritten(line):
    src = f"- {line}\n"
    (u,) = parse_projection(src).units.values()
    assert not u.editable


def test_double_slash_is_emitted_escaped_and_survives_a_round_trip():
    src = "- a /\\/ b\n"
    (u,) = parse_projection(src).units.values()
    assert u.value == "a // b" and u.editable and serialize_unit(u) == "a /\\/ b"


def test_a_nested_mark_pair_with_equal_ranges_keeps_its_order():
    for line in ("*_both_*", "_*both*_"):
        src = f"- {line}\n"
        (u,) = parse_projection(src).units.values()
        assert [m.kind for m in u.marks] == (
            ["strong", "emph"] if line[0] == "*" else ["emph", "strong"])
        assert u.editable and serialize_unit(u) == line


def test_crlf_line_units_exclude_the_carriage_return():
    src = "== A\r\n- one *two*\r\n- three\r\n"
    p = parse_projection(src)
    for u in p.units.values():
        assert u.editable and serialize_unit(u) == _slice(src, u)
    assert [u.value for u in p.units.values()] == ["A", "one two", "three"]


def test_en_projection_matches_the_golden_dump():
    got = [dump(u) for u in sorted(parse_projection(read("synthetic-en")).units.values(),
                                   key=lambda u: u.span[0])]
    assert got == json.loads((FIXTURES / "synthetic-en.units.json").read_text("utf-8"))


def test_a_content_body_projects_its_lines():
    src = read("synthetic-en")
    p = parse_projection(src)
    align = next(b for s in p.sections for b in s.blocks if b.name == "align")
    assert align.body and align.body_units
    u = p.units[align.body_units[0]]
    assert u.role == "para" and u.editable and u.value.startswith("Backend engineer")
    assert _slice(src, u) == u.value and serialize_unit(u) == _slice(src, u)
    assert align.body[0] <= u.span[0] < u.span[1] <= align.body[1]
    assert "#align" not in p.report.structures


def test_a_multi_line_content_body_is_one_unit_per_line():
    src = "== A\n#align(left)[\n  first *line*\n\n  second line\n]\n"
    p = parse_projection(src)
    (b,) = p.sections[0].blocks
    assert [p.units[i].value for i in b.body_units] == ["first line", "second line"]
    for i in b.body_units:
        assert p.units[i].editable and serialize_unit(p.units[i]) == _slice(src, p.units[i])


def test_marks_fixture_heading_carries_bold_and_a_link():
    src = read("synthetic-marks")
    p = parse_projection(src)
    title = next(p.units[s.title_unit] for s in p.sections
                 if s.title_unit and p.units[s.title_unit].marks)
    assert title.value == "Core Skills and stack"
    assert [m.kind for m in title.marks] == ["strong", "link"]
    assert title.editable and serialize_unit(title) == _slice(src, title)


def test_unprojected_content_inside_a_component_is_not_tier_a():
    p = parse_projection(read("synthetic-tier-b"))
    assert p.report.skipped_args.get("(positional)") == 3   # two grid cells and a tag
    assert p.tier == "B"


def test_positional_strings_and_blocks_are_counted_but_identifiers_are_not():
    src = '== A\n#cvitem("Role", "Company")\n#align(left)[Hi]\n#box(1pt, [cell])\n'
    p = parse_projection(src)
    assert p.report.skipped_args["(positional)"] == 3   # 2 strings + 1 block, not `left`
    assert p.tier == "B"
    only_ident = parse_projection('== A\n#align(left)[Hi]\n')
    assert "(positional)" not in only_ident.report.skipped_args
    assert only_ident.tier == "A"


def test_a_positional_string_beside_keyed_fields_makes_a_block_tier_b():
    p = parse_projection('== A\n#work("Extra", title: "T")\n')
    assert p.report.skipped_args["(positional)"] == 1 and p.tier == "B"


@pytest.mark.parametrize("name", ["synthetic-en", "synthetic-zh", "synthetic-marks",
                                  "synthetic-tier-c"])
def test_the_other_fixtures_hide_no_positional_content(name):
    assert "(positional)" not in parse_projection(read(name)).report.skipped_args


def test_a_marker_only_line_is_never_a_unit_covering_the_marker():
    for src in ("== A\n- real one\n-   \n", "== A\n-\n", "== A\n#work(title: \"T\")\n"
                "- real one\n-   \n+\n"):
        p = parse_projection(src)
        lines = [u for u in p.units.values() if u.role != "heading" and u.kind == "line"]
        assert all(u.value not in ("-", "+") and src[u.span[0]] not in "-+"
                   for u in lines)
        assert p.report.structures["empty list item"] >= 1


def test_enumerations_are_bullets_whose_span_excludes_the_number():
    src = "== A\n1. first item\n  23. second\n3.5 million\n"
    p = parse_projection(src)
    got = [(u.role, u.value, src[u.span[0]:u.span[1]])
           for u in p.units.values() if u.role != "heading"]
    assert got == [("bullet", "first item", "first item"),
                   ("bullet", "second", "second"),
                   ("para", "3.5 million", "3.5 million")]


def test_term_list_lines_are_counted_not_projected():
    p = parse_projection("== A\n/ Term: the definition\n- item\n")
    assert p.report.structures["term list item"] == 1
    assert [u.value for u in p.units.values() if u.role != "heading"] == ["item"]


def test_a_value_slash_next_to_a_star_is_escaped_so_no_block_comment_opens():
    opener = Unit("u1", "line", (0, 0), "a/b", True, marks=(Mark("strong", 2, 3),))
    closer = Unit("u1", "line", (0, 0), "*x/", True, marks=(Mark("strong", 1, 3),))
    assert serialize_unit(opener) == "a\\/*b*"
    assert serialize_unit(closer) == "\\**x\\/*"


@pytest.mark.parametrize("marks", [
    (Mark("strong", 1, 99),),                       # runs past the value
    (Mark("strong", 2, 2),),                        # empty
    (Mark("strong", -1, 3),),
    (Mark("underline", 0, 2),),                     # unknown kind
    (Mark("strong", 0, 3), Mark("emph", 2, 5)),     # overlap without nesting
    (Mark("link", 0, 2, None),),                    # a link without a url
    (Mark("link", 0, 2, "a\nb"),),
    (Mark("link", 0, 2, ""),),                      # a blank url: typst rejects #link("")
    (Mark("link", 0, 2, "  \t"),),
])
def test_serialize_refuses_marks_that_would_emit_unbalanced_markup(marks):
    with pytest.raises(ValueError):
        serialize_unit(Unit("u1", "line", (0, 0), "hello", True, marks=marks))


def test_serialize_accepts_nested_and_adjacent_marks():
    u = Unit("u1", "line", (0, 0), "abcd", True,
             marks=(Mark("strong", 0, 4), Mark("emph", 0, 2), Mark("group", 2, 4)))
    assert serialize_unit(u) == "*_ab_[cd]*"


def test_list_syntax_inside_a_content_body_keeps_its_marker_outside_the_unit():
    src = ("== A\n#align(left)[\n  plain line\n  - a bullet\n  1. an enumeration\n"
           "  -   \n  / Term: text\n]\n")
    p = parse_projection(src)
    (b,) = p.sections[0].blocks
    got = [(p.units[i].role, p.units[i].value, src[slice(*p.units[i].span)])
           for i in b.body_units]
    assert got == [("para", "plain line", "plain line"),
                   ("bullet", "a bullet", "a bullet"),
                   ("bullet", "an enumeration", "an enumeration")]
    assert p.report.structures["empty list item"] == 1
    assert p.report.structures["term list item"] == 1
    for i in b.body_units:
        assert p.units[i].editable and serialize_unit(p.units[i]) == _slice(src, p.units[i])


def test_an_indented_heading_is_a_structure_and_never_an_editable_paragraph():
    """A heading indented inside a content body is normal markup, and reading it as
    prose put `== Nested heading` in a form field -- where one value edit demoted the
    heading to body text with nothing refused. Both line-classifier call sites (the
    content body and the top level) have to agree that indentation is not content."""
    body = "== A\n#align(left)[\n  == Nested heading\n  some prose\n]\n"
    p = parse_projection(body)
    (b,) = p.sections[0].blocks
    assert [p.units[i].value for i in b.body_units] == ["some prose"]
    assert p.report.structures["= heading"] == 1
    assert not [u for u in p.units.values() if "==" in u.value]

    top = "= Name\n\n  == Indented top level\n\nplain prose\n"
    q = parse_projection(top)
    assert [u.value for u in q.units.values()] == ["plain prose"]
    assert q.report.structures["= heading"] == 2  # `= Name` and the indented one


def test_serialize_names_the_missing_url():
    u = Unit("u1", "line", (0, 0), "hello", True, marks=(Mark("link", 0, 2, ""),))
    with pytest.raises(ValueError, match="url"):
        serialize_unit(u)


def test_a_stored_empty_link_makes_its_unit_read_only_and_does_not_break_the_parse():
    src = read("synthetic-en").replace(
        "Cut p99 latency", '#link("")[Cut] p99 latency', 1)
    assert '#link("")[Cut]' in src
    p = parse_projection(src)  # must not raise
    hit = [u for u in p.units.values()
           if any(m.kind == "link" and m.url == "" for m in u.marks)]
    assert len(hit) == 1 and hit[0].editable is False
