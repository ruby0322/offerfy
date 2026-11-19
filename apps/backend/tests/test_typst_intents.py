"""Intents: `set_arg` and `set_line` (value), then the five structural ones."""

import hashlib

import pytest

from app.schemas_editing import IntentIn, MarkOut
from app.services.emitters import deterministic_pairs, emitter_for
from app.services.typst_intents import (
    PLACEHOLDER, IntentError, apply_intent, normalize_blanks)
from app.services.typst_projection import line_unit, parse_projection, serialize_unit
from tests.fixtures.golden_content import GOLDEN as GOLDEN_CONTENT
from tests.test_typst_projection import read, unit_by_text


CUT = "Cut p99 latency of the ordering service from 900 ms to 240 ms."


def title_unit(p):
    return next(x for x in p.units.values() if x.key == "title")


def test_set_arg_replaces_one_span_and_nothing_else():
    src = read("synthetic-en"); p = parse_projection(src)
    u = title_unit(p)
    out = apply_intent(src, p, IntentIn(hash="", kind="set_arg", unit=u.id,
                                        expect=u.value, value="Staff Engineer"))
    assert out[:u.span[0]] == src[:u.span[0]]
    assert out[u.span[0]:u.span[0] + len("Staff Engineer")] == "Staff Engineer"
    assert out[u.span[0] + len("Staff Engineer"):] == src[u.span[1]:]
    assert out != src


def test_set_arg_escapes_quotes_and_backslashes():
    src = read("synthetic-en"); p = parse_projection(src)
    u = title_unit(p)
    out = apply_intent(src, p, IntentIn(hash="", kind="set_arg", unit=u.id,
                                        expect=u.value, value='A "B" \\ C'))
    assert 'A \\"B\\" \\\\ C' in out
    assert parse_projection(out).units[u.id].value == 'A "B" \\ C'


def test_set_arg_fills_an_empty_string_argument():
    src = read("synthetic-en"); p = parse_projection(src)
    u = title_unit(p)
    emptied = src[:u.span[0]] + src[u.span[1]:]
    p2 = parse_projection(emptied)
    e = p2.units[u.id]
    assert e.value == "" and e.span[0] == e.span[1]
    out = apply_intent(emptied, p2, IntentIn(hash="", kind="set_arg", unit=e.id,
                                             expect="", value="2020 - 2024"))
    assert out == src[:u.span[0]] + "2020 - 2024" + src[u.span[1]:]


def test_set_line_rebuilds_marks():
    src = read("synthetic-en"); p = parse_projection(src)
    u = unit_by_text(p, CUT)
    out = apply_intent(src, p, IntentIn(
        hash="", kind="set_line", unit=u.id, expect=u.value,
        text="Cut p99 to 180ms", marks=[MarkOut(kind="strong", start=4, end=7)]))
    assert "*p99*" in out and parse_projection(out).tier == "A"
    assert "Cut *p99* to 180ms" in out
    assert out.replace("Cut *p99* to 180ms", CUT) == src


def test_set_line_keeps_this_unit_s_escape_policy():
    # Two units, two spellings of `&`: the bullet already wrote `\\&`, the heading a bare
    # `&`. Each keeps its own after an edit.
    src = read("synthetic-marks"); p = parse_projection(src)
    u = unit_by_text(p, "Escapes in one line: R&D, ops@night, 5* rating, ~100 hosts, "
                        "$2M budget.")
    assert u.escapes == ("&",)
    out = apply_intent(src, p, IntentIn(
        hash="", kind="set_line", unit=u.id, expect=u.value,
        text="Escapes in one line: R&D II", marks=[]))
    assert "R\\&D II" in out and "R&D" not in out

    src = read("synthetic-en"); p = parse_projection(src)
    h = unit_by_text(p, "Skills & Awards")
    assert h.escapes == ()
    out = apply_intent(src, p, IntentIn(
        hash="", kind="set_line", unit=h.id, expect=h.value,
        text="Skills & Awards II", marks=[]))
    assert "== Skills & Awards II\n" in out and "\\&" not in out


def test_set_line_refuses_to_empty_a_line_and_names_delete():
    # An emptied `- ` is not projected, so every later positional unit id would shift.
    src = read("synthetic-en"); p = parse_projection(src)
    for role in ("bullet", "para", "heading"):
        u = next(x for x in p.units.values() if x.role == role and x.editable)
        for blank in ["", "   "]:
            with pytest.raises(IntentError) as e:
                apply_intent(src, p, IntentIn(hash="", kind="set_line", unit=u.id,
                                              expect=u.value, text=blank, marks=[]))
            assert e.value.code == "unsupported", (role, blank)
            assert "delete" in e.value.detail, (role, blank)


def test_a_value_intent_never_changes_the_unit_set():
    # The contract Task 6 and the client rely on: ids stay valid after a value edit.
    src = read("synthetic-en"); p = parse_projection(src)
    line = unit_by_text(p, CUT)
    out = apply_intent(src, p, IntentIn(hash="", kind="set_line", unit=line.id,
                                        expect=line.value, text="Cut p99 to 180ms"))
    q = parse_projection(out)
    assert out != src and len(p.units) > 10
    assert len(q.units) == len(p.units) and set(q.units) == set(p.units)
    assert q.units[line.id].value == "Cut p99 to 180ms"

    t = title_unit(p)
    out = apply_intent(src, p, IntentIn(hash="", kind="set_arg", unit=t.id,
                                        expect=t.value, value="Staff Engineer"))
    q = parse_projection(out)
    assert out != src
    assert len(q.units) == len(p.units) and set(q.units) == set(p.units)
    assert q.units[t.id].value == "Staff Engineer"


def test_expect_mismatch_is_refused():
    src = read("synthetic-en"); p = parse_projection(src)
    u = title_unit(p)
    with pytest.raises(IntentError) as e:
        apply_intent(src, p, IntentIn(hash="", kind="set_arg", unit=u.id,
                                      expect="something else", value="x"))
    assert e.value.code == "expect_mismatch"


def test_set_line_expect_mismatch_is_refused():
    src = read("synthetic-en"); p = parse_projection(src)
    u = unit_by_text(p, CUT)
    with pytest.raises(IntentError) as e:
        apply_intent(src, p, IntentIn(hash="", kind="set_line", unit=u.id,
                                      expect="stale", text="x", marks=[]))
    assert e.value.code == "expect_mismatch"


def _set_line(src, p, u, text):
    return apply_intent(src, p, IntentIn(hash="", kind="set_line", unit=u.id,
                                         expect=u.value, text=text, marks=[]))


def _unit_of_role(p, role):
    return next(x for x in p.units.values() if x.role == role and x.editable)


def test_a_value_that_would_reparse_as_structure_is_refused():
    # Typst has five structural line openers: `-` and `+` (list), `1.` (enum),
    # `/ ` (term list) and `=` (heading). Each must be refused for a paragraph...
    src = read("synthetic-en"); p = parse_projection(src)
    u = _unit_of_role(p, "para")
    for bad in ["- not a bullet", "+ not a bullet", "1. not an enum",
                "/ not a term: x", "= not a heading", "  - indented", "-", "12."]:
        with pytest.raises(IntentError) as e:
            _set_line(src, p, u, bad)
        assert e.value.code == "unsupported", bad
    # ...and all five markers for a bullet, where each nests a list, terms or heading.
    b = _unit_of_role(p, "bullet")
    for bad in ["- nested", "+ nested", "1. nested", "/ t: d", "= x", "== x"]:
        with pytest.raises(IntentError) as e:
            _set_line(src, p, b, bad)
        assert e.value.code == "unsupported", bad


def test_text_that_merely_resembles_structure_is_written():
    # Typst needs a space after the marker; none of these re-parses as structure.
    src = read("synthetic-en"); p = parse_projection(src)
    for role in ("para", "bullet", "heading"):
        u = _unit_of_role(p, role)
        for ok in ["=5% YoY", "-20% latency", "+15 points", "1.8s to 240ms",
                   "C/C++ and /dev/null", "a - b"]:
            out = _set_line(src, p, u, ok)
            assert out[u.span[0]:u.span[0] + len(ok)] == ok, (role, ok)
            q = parse_projection(out)
            assert q.units[u.id].value == ok and q.units[u.id].role == role


def test_a_heading_title_may_contain_a_marker_that_cannot_start_a_heading():
    src = read("synthetic-en"); p = parse_projection(src)
    h = _unit_of_role(p, "heading")
    for ok in ["= x", "- x", "1. x", "/ t: d", "== x"]:
        out = _set_line(src, p, h, ok)
        assert parse_projection(out).units[h.id].value == ok


def test_line_role_asks_the_parser():
    from app.services.typst_projection import line_role
    assert line_role("- x") == "bullet" and line_role("== x") == "heading"
    assert line_role("x") == "para" and line_role("=5% YoY") == "para"
    assert line_role("= x") is None and line_role("/ t: d") is None
    assert line_role("1. x") == "bullet" and line_role("-") is None


def test_a_read_only_unit_is_refused():
    src = read("synthetic-tier-b"); p = parse_projection(src)
    ro = [x for x in p.units.values() if not x.editable]
    assert ro
    u = ro[0]
    with pytest.raises(IntentError) as e:
        apply_intent(src, p, IntentIn(hash="", kind="set_line", unit=u.id,
                                      expect=u.value, text="x", marks=[]))
    assert e.value.code == "unsupported"
    assert "read-only" in e.value.detail   # not the splice guard's "does not match"


def test_a_missing_expect_is_a_bad_request_not_a_conflict():
    src = read("synthetic-en"); p = parse_projection(src)
    u = title_unit(p)
    line = unit_by_text(p, CUT)
    for intent in [
        IntentIn(hash="", kind="set_arg", unit=u.id, value="x"),
        IntentIn(hash="", kind="set_line", unit=line.id, text="x"),
    ]:
        with pytest.raises(IntentError) as e:
            apply_intent(src, p, intent)
        assert e.value.code == "unsupported"
        assert "expect" in e.value.detail


def test_an_unknown_unit_and_a_wrong_kind_are_refused():
    src = read("synthetic-en"); p = parse_projection(src)
    line = next(x for x in p.units.values() if x.kind == "line" and x.editable)
    t = title_unit(p)
    for intent, why in [
        (IntentIn(hash="", kind="set_line", unit="nope", expect="", text="x"),
         "no such unit"),
        (IntentIn(hash="", kind="set_arg", unit=line.id, expect=line.value, value="x"),
         "needs a arg unit"),
        (IntentIn(hash="", kind="set_line", unit=t.id, expect=t.value, text="x"),
         "needs a line unit"),
    ]:
        with pytest.raises(IntentError) as e:
            apply_intent(src, p, intent)
        assert e.value.code == "unsupported"
        assert why in e.value.detail   # the kind guard, not the splice guard


def test_a_newline_in_a_value_is_refused():
    src = read("synthetic-en"); p = parse_projection(src)
    u = title_unit(p)
    with pytest.raises(IntentError) as e:
        apply_intent(src, p, IntentIn(hash="", kind="set_arg", unit=u.id,
                                      expect=u.value, value="a\nb"))
    assert e.value.code == "unsupported"
    line = next(x for x in p.units.values() if x.kind == "line" and x.editable)
    with pytest.raises(IntentError) as e:
        apply_intent(src, p, IntentIn(hash="", kind="set_line", unit=line.id,
                                      expect=line.value, text="a\nb"))
    assert e.value.code == "unsupported"


def test_a_bad_mark_is_refused_not_written():
    src = read("synthetic-en"); p = parse_projection(src)
    u = unit_by_text(p, CUT)
    with pytest.raises(IntentError) as e:
        apply_intent(src, p, IntentIn(
            hash="", kind="set_line", unit=u.id, expect=u.value, text="abc",
            marks=[MarkOut(kind="strong", start=1, end=9)]))
    assert e.value.code == "unsupported"


def test_set_line_with_a_blank_link_url_is_unsupported():
    src = read("synthetic-en"); p = parse_projection(src)
    u = unit_by_text(p, CUT)
    for url in ("", "   "):
        with pytest.raises(IntentError) as e:
            apply_intent(src, p, IntentIn(
                hash="", kind="set_line", unit=u.id, expect=u.value, text=u.value,
                marks=[MarkOut(kind="link", start=0, end=3, url=url)]))
        assert e.value.code == "unsupported" and "url" in e.value.detail


# --- structural intents ---------------------------------------------------------------

PLACEHOLDER = "（待填）"


def h(src: str) -> str:
    return hashlib.sha256(src.encode("utf-8")).hexdigest()


def structural(src: str, kind: str, **fields) -> str:
    p = parse_projection(src)
    return apply_intent(src, p, IntentIn(hash=h(src), kind=kind, **fields))


def refused(src: str, kind: str, **fields) -> IntentError:
    with pytest.raises(IntentError) as exc:
        structural(src, kind, **fields)
    assert exc.value.code == "unsupported"
    return exc.value


def first_comp(p):
    """The first item that has bullets of its own (here the Northwind `#work`)."""
    return next(b for s in p.sections for b in s.blocks if b.kind == "comp" and b.bullets)


def section_named(p, title: str):
    return next(s for s in p.sections
                if s.title_unit and p.units[s.title_unit].value == title)


def only_child_section(p):
    """A headed section holding exactly one contentful block."""
    return next(s for s in p.sections if s.title_unit and len(s.blocks) == 1)


def titles(p) -> list[str]:
    return [p.units[s.title_unit].value for s in p.sections if s.title_unit]


def test_insert_bullet_lands_at_the_block_s_content_edge():
    src = read("synthetic-en"); p = parse_projection(src)
    b = first_comp(p)
    out = structural(src, "insert_bullet", target=b.id)
    assert len(first_comp(parse_projection(out)).bullets) == len(b.bullets) + 1
    assert out == src[:b.content_end] + f"- {PLACEHOLDER}\n" + src[b.content_end:]


def test_insert_item_copies_the_section_s_own_shape():
    src = read("synthetic-en"); p = parse_projection(src)
    sec = section_named(p, "Projects")
    out = structural(src, "insert_item", target=sec.id)
    new = parse_projection(out)
    comps = [b for b in section_named(new, "Projects").blocks if b.kind == "comp"]
    assert len(comps) == 2 and comps[-1].name == "project"  # not "work"
    args = [new.units[a] for a in comps[-1].args]
    assert args and all(u.value == PLACEHOLDER for u in args if not u.style)
    assert len(comps[-1].bullets) == 1
    assert new.units[comps[-1].bullets[0].unit].value == PLACEHOLDER


def test_insert_item_never_touches_a_shared_let():
    # `title: who` resolves to a `#let` outside the block; copying must not rewrite it
    src = ('#let who = "Ada"\n\n== Work\n\n#work(\n  title: who,\n  company: "X",\n)\n'
           '- did a thing\n')
    out = structural(src, "insert_item", target="s1")
    assert out.startswith('#let who = "Ada"\n') and out.count("Ada") == 1
    assert out.count(PLACEHOLDER) == 2  # the company and the bullet
    assert out.count("title: who,") == 2


def test_insert_item_into_a_bullet_list_adds_a_bullet():
    src = read("synthetic-en"); p = parse_projection(src)
    sec = section_named(p, "Skills & Awards")
    out = structural(src, "insert_item", target=sec.id)
    assert out == src + f"- {PLACEHOLDER}\n"


def test_insert_item_is_refused_where_there_is_nothing_to_copy():
    src = read("synthetic-tier-b"); p = parse_projection(src)
    empty = next(s for s in p.sections if s.title_unit and not s.blocks)
    refused(src, "insert_item", target=empty.id)
    refused(src, "insert_item", target="s0")        # the preamble holds only the show rule
    refused(src, "insert_item", target="nope")
    refused(src, "insert_item")


def test_insert_section_appends_a_heading_and_a_placeholder():
    src = read("synthetic-en")
    out = structural(src, "insert_section")
    assert out == src + f"\n== 新段落\n\n{PLACEHOLDER}\n\n"
    p = parse_projection(out)
    assert titles(p)[-1] == "新段落" and len(p.sections[-1].blocks) == 1


def test_delete_takes_the_outer_span_and_leaves_no_orphan():
    src = read("synthetic-en"); p = parse_projection(src)
    b = first_comp(p)
    out = structural(src, "delete", target=b.id)
    assert "\n\n\n" not in out and not out.lstrip().startswith("-")
    assert out == src[:b.outer[0]] + src[b.outer[1]:]
    assert "Northwind" not in out and "Contoso" in out


def test_delete_a_bullet_takes_only_that_line():
    src = read("synthetic-en"); p = parse_projection(src)
    bullet = first_comp(p).bullets[0]
    out = structural(src, "delete", target=bullet.id)
    assert out == src[:bullet.outer[0]] + src[bullet.outer[1]:]
    assert len(first_comp(parse_projection(out)).bullets) == 1


def test_deleting_the_last_item_takes_the_heading_too():
    src = read("synthetic-en"); p = parse_projection(src)
    sec = only_child_section(p)   # synthetic-en has three: Summary, Education, Projects
    title = p.units[sec.title_unit].value
    assert f"== {title}" in src
    out = structural(src, "delete", target=sec.blocks[0].id)
    assert f"== {title}" not in out
    assert out == src[:sec.outer[0]] + src[sec.outer[1]:]
    assert len(parse_projection(out).sections) == len(p.sections) - 1


def test_deleting_one_of_two_items_keeps_the_heading():
    src = read("synthetic-en"); p = parse_projection(src)
    sec = section_named(p, "Experience")
    out = structural(src, "delete", target=sec.blocks[0].id)
    assert "== Experience" in out
    assert len(parse_projection(out).sections) == len(p.sections)


def test_deleting_the_last_item_keeps_a_heading_that_still_has_other_content():
    # the `#pagebreak()` is not a projected block, but it is not nothing either
    src = '== A\n\n#work(title: "T")\n- x\n\n#pagebreak()\n\n== B\n\ntext\n'
    out = structural(src, "delete", target="s1i0")
    assert out == "== A\n\n#pagebreak()\n\n== B\n\ntext\n"


def test_deleting_the_last_block_of_the_file_leaves_one_trailing_newline():
    src = read("synthetic-en"); p = parse_projection(src)
    last = p.sections[-1].blocks[-1]
    out = structural(src, "delete", target=last.id)
    assert out.endswith("\n") and not out.endswith("\n\n")
    assert out == src[:last.outer[0]]


def test_a_show_rule_cannot_be_deleted_or_extended():
    src = read("synthetic-en")
    refused(src, "delete", target="s0i0")
    refused(src, "insert_bullet", target="s0i0")
    refused(src, "delete", target="s9i9")
    refused(src, "delete")


def test_insert_bullet_refuses_a_non_comp_block():
    src = read("synthetic-en"); p = parse_projection(src)
    skills = section_named(p, "Skills & Awards")
    refused(src, "insert_bullet", target=skills.blocks[0].id)
    refused(src, "insert_bullet", target=first_comp(p).bullets[0].id)


def test_a_stale_projection_is_refused():
    src = read("synthetic-en"); p = parse_projection(src)
    with pytest.raises(IntentError) as exc:
        apply_intent("// x\n" + src, p, IntentIn(hash="", kind="delete", target="s2i0"))
    assert exc.value.code == "unsupported"


def test_move_section_swaps_two_outer_spans():
    src = read("synthetic-en"); p = parse_projection(src)
    before = titles(p)
    out = structural(src, "move_section", target=p.sections[-1].id, dir="up")
    after = titles(parse_projection(out))
    assert after[-2:] == [before[-1], before[-2]] and after[:-2] == before[:-2]
    assert sorted(out) == sorted(src)          # nothing gained, nothing lost
    assert "\n\n\n" not in out and out.endswith("\n")


def test_move_down_then_up_is_the_identity_for_every_section():
    src = read("synthetic-en"); p = parse_projection(src)
    movable = [s for s in p.sections[1:-1]]
    assert len(movable) >= 3
    for sec in movable:
        down = structural(src, "move_section", target=sec.id, dir="down")
        assert down != src
        pd = parse_projection(down)
        moved = section_named(pd, p.units[sec.title_unit].value)
        assert structural(down, "move_section", target=moved.id, dir="up") == src


def test_moving_the_last_section_up_and_back_is_the_identity():
    src = read("synthetic-en"); p = parse_projection(src)
    up = structural(src, "move_section", target=p.sections[-1].id, dir="up")
    pu = parse_projection(up)
    assert structural(up, "move_section", target=pu.sections[-2].id, dir="down") == src


def test_move_section_refuses_the_edges_the_preamble_and_junk():
    src = read("synthetic-en"); p = parse_projection(src)
    refused(src, "move_section", target=p.sections[-1].id, dir="down")
    refused(src, "move_section", target=p.sections[1].id, dir="up")   # onto the preamble
    refused(src, "move_section", target="s0", dir="down")
    refused(src, "move_section", target=p.sections[2].id, dir="sideways")
    refused(src, "move_section", target=p.sections[2].id)
    refused(src, "move_section", target="s99", dir="up")


def test_five_add_delete_cycles_leave_the_file_byte_identical():     # Invariant 4
    src = read("synthetic-en")
    out = src
    for _ in range(5):
        p = parse_projection(out)
        out = apply_intent(out, p, IntentIn(kind="insert_bullet",
                                            target=first_comp(p).id, hash=h(out)))
        assert out != src
        p = parse_projection(out)
        out = apply_intent(out, p, IntentIn(kind="delete",
                                            target=first_comp(p).bullets[-1].id,
                                            hash=h(out)))
    assert out == src


def test_a_bullet_added_to_any_item_is_deleted_back_to_the_same_bytes():
    src = read("synthetic-en"); p = parse_projection(src)
    comps = [b for s in p.sections for b in s.blocks if b.kind == "comp"]
    assert len(comps) == 5 and any(not b.bullets for b in comps)   # `align` has none
    for comp in comps:
        out = structural(src, "insert_bullet", target=comp.id)
        added = next(b for s in parse_projection(out).sections for b in s.blocks
                     if b.id == comp.id).bullets[-1]
        assert structural(out, "delete", target=added.id) == src, comp.id


def test_insert_and_delete_of_an_item_are_inverses_in_every_section():
    src = read("synthetic-en"); p = parse_projection(src)
    checked = 0
    for sec in p.sections:
        if not sec.title_unit or not sec.blocks:
            continue
        out = structural(src, "insert_item", target=sec.id)
        assert out != src
        added = next(s for s in parse_projection(out).sections if s.id == sec.id).blocks[-1]
        assert structural(out, "delete", target=added.id) == src, sec.id
        checked += 1
    assert checked == 5


def test_insert_and_delete_of_a_section_are_inverses():
    src = read("synthetic-en")
    out = structural(src, "insert_section")
    p = parse_projection(out)
    assert structural(out, "delete", target=p.sections[-1].blocks[0].id) == src


def test_delete_by_section_id_cuts_exactly_the_section_outer_span():
    src = read("synthetic-en"); p = parse_projection(src)
    for sec in (x for x in p.sections if x.title_unit):
        out = structural(src, "delete", target=sec.id)
        cut = src[:sec.outer[0]] + src[sec.outer[1]:]
        if sec.outer[1] >= len(src):
            cut = cut.rstrip("\n") + "\n"
        assert out == normalize_blanks(cut, at=sec.outer[0]), sec.id
        q = parse_projection(out)
        assert len(q.sections) == len(p.sections) - 1
        assert titles(q) == [t for t in titles(p) if t != p.units[sec.title_unit].value]
        assert "\n\n\n" not in out and out.endswith("\n")


def test_delete_by_section_id_is_the_inverse_of_insert_section():
    src = read("synthetic-en")
    out = structural(src, "insert_section")
    p = parse_projection(out)
    assert structural(out, "delete", target=p.sections[-1].id) == src


def test_delete_by_section_id_agrees_with_deleting_its_last_item():
    # the two routes to the same cut: one intent for the section, or the last item
    src = read("synthetic-en"); p = parse_projection(src)
    sec = only_child_section(p)
    assert (structural(src, "delete", target=sec.id)
            == structural(src, "delete", target=sec.blocks[0].id))


def test_the_two_delete_routes_agree_when_it_is_the_only_headed_section():
    # the case that used to differ: the block route reached it, the section route refused
    for src in ("#set page(margin: 1cm)\n\n== A\n\ntext\n",
                '#set page(margin: 1cm)\n\n== A\n\n#work(title: "T")\n- x\n'):
        sec = next(s for s in parse_projection(src).sections if s.title_unit)
        assert len(sec.blocks) == 1
        by_section = structural(src, "delete", target=sec.id)
        assert by_section == structural(src, "delete", target=sec.blocks[0].id)
        assert "==" not in by_section and by_section.strip()


def test_delete_by_section_id_refuses_the_preamble_and_an_empty_result():
    src = read("synthetic-en")
    refused(src, "delete", target="s0")
    # nothing but the section: deleting it would leave an empty document
    only = "== A\n\ntext\n"
    err = refused(only, "delete", target=parse_projection(only).sections[-1].id)
    assert "empty document" in err.detail
    # with two headed sections, deleting the second restores the one-section source exactly
    one = "#set page(margin: 1cm)\n\n== A\n\ntext\n"
    two = one + "\n== B\n\nmore\n"
    assert structural(two, "delete", target=parse_projection(two).sections[-1].id) == one


def test_a_section_delete_takes_what_the_form_does_not_show():
    # `outer` runs to the next heading: a comment, a `#let`, a `#pagebreak()` and a
    # level-1 heading inside it go too -- the reason the armed label carries a caveat.
    src = ('#set page(margin: 1cm)\n\n== A\n\nkeep\n\n== B\n\n// a note\n#let x = 1\n\n'
           '#work(title: "T")\n- one\n\n#pagebreak()\n\n= Part\n\n== C\n\ntail\n')
    p = parse_projection(src)
    b = section_named(p, "B")
    out = structural(src, "delete", target=b.id)
    for gone in ("// a note", "#let x", "#work", "- one", "#pagebreak()", "= Part"):
        assert gone not in out, gone
    assert out == src[:b.outer[0]] + src[b.outer[1]:]
    assert titles(parse_projection(out)) == ["A", "C"]
    assert "keep" in out and "tail" in out


def test_insert_section_works_on_a_document_with_no_heading():
    src = "#set page(margin: 1cm)\n\nplain text\n"
    out = structural(src, "insert_section")
    assert out == src + f"\n== 新段落\n\n{PLACEHOLDER}\n\n"
    assert titles(parse_projection(out)) == ["新段落"]


def test_insert_section_appends_whatever_other_headings_the_document_has():
    for src in ("#set page(margin: 1cm)\n\n= Name\n\ntext\n",
                "#set page(margin: 1cm)\n\n=== Sub\n\ntext\n"):
        assert parse_projection(src).report.structures.get("= heading") == 1
        out = structural(src, "insert_section")
        assert titles(parse_projection(out)) == ["新段落"]


def test_a_name_heading_plus_one_section_is_not_stranded_by_deleting_the_section():
    # The scenario that drove dropping the convention check: `= Name` above one `== `
    # section; delete the section and `= heading` counts 1 -- the same state as a document
    # that never had `==` sections. Adding one back must still work.
    src = "#set page(margin: 1cm)\n\n= Name\n\n== A\n\ntext\n"
    p = parse_projection(src)
    left = structural(src, "delete", target=p.sections[-1].id)
    q = parse_projection(left)
    assert titles(q) == [] and q.report.structures.get("= heading") == 1
    assert titles(parse_projection(structural(left, "insert_section"))) == ["新段落"]


def test_other_headings_do_not_block_insert_section_where_sections_already_exist():
    # a `= Name` title above `== ` sections is an ordinary résumé, not a mixed convention
    src = "= Name\n\n== A\n\ntext\n"
    assert titles(parse_projection(structural(src, "insert_section"))) == ["A", "新段落"]


def test_deleting_the_last_section_then_adding_one_is_possible():
    src = "#set page(margin: 1cm)\n\n== A\n\ntext\n"
    left = structural(src, "delete", target=parse_projection(src).sections[-1].id)
    assert titles(parse_projection(left)) == []
    assert titles(parse_projection(structural(left, "insert_section"))) == ["新段落"]


def test_a_comp_at_the_end_of_a_file_without_a_blank_line_round_trips():
    src = '== A\n\n#work(title: "T")\n- x\n'
    out = structural(src, "insert_item", target="s1")
    assert "\n\n#work(" in out
    assert [b.kind for b in parse_projection(out).sections[0].blocks] == ["comp", "comp"]
    assert structural(out, "delete", target="s1i1") == src


def test_a_file_without_a_final_newline_still_gets_a_bullet_on_its_own_line():
    src = '== A\n\n#work(title: "T")'
    out = structural(src, "insert_bullet", target="s1i0")
    assert out == f'== A\n\n#work(title: "T")\n- {PLACEHOLDER}\n'


def test_the_structural_intents_do_not_mutate_the_projection():
    src = read("synthetic-en"); p = parse_projection(src)
    before = repr(p)
    for kind, fields in [("insert_bullet", {"target": first_comp(p).id}),
                         ("insert_item", {"target": "s2"}), ("insert_section", {}),
                         ("delete", {"target": first_comp(p).id}),
                         ("move_section", {"target": "s3", "dir": "up"})]:
        assert apply_intent(src, p, IntentIn(hash=h(src), kind=kind, **fields)) != src
    assert repr(p) == before


def test_normalize_blanks_collapses_runs_and_ends_with_a_newline():
    assert normalize_blanks("a\n\n\n\nb") == "a\n\nb\n"
    assert normalize_blanks("a\n\nb\n") == "a\n\nb\n"
    assert normalize_blanks("a\n\n\n\nb\n\n\n\nc", at=3) == "a\n\nb\n\n\n\nc\n"


def test_deleting_the_last_item_keeps_a_heading_with_unprojected_content_before_it():
    src = '== A\n\n#pagebreak()\n\n#work(title: "T")\n- x\n\n== B\n\ntext\n'
    out = structural(src, "delete", target="s1i0")
    assert out == "== A\n\n#pagebreak()\n\n== B\n\ntext\n"


LISTS = (
    "== A\n\n- a\n- b\n\n== B\n\ntext\n",        # tight, blank line after
    "== A\n\n- a\n\n== B\n\ntext\n",             # one item, blank line after
    "== A\n\n- a\n\n- b\n\n== B\n\ntext\n",      # already loose: stays loose
    "== A\n\n- a\n- b\n== B\n\ntext\n",          # tight, the heading follows directly
)


def test_insert_item_never_changes_how_the_siblings_are_spaced():
    # A blank line inside a tight list makes the whole list wide in Typst, so the
    # blank-line structure around every existing item must survive the insert. Each
    # sibling keeps exactly the blank lines it had; the new item copies its neighbour's.
    ph = f"- {PLACEHOLDER}\n"
    expected = (
        "== A\n\n- a\n- b\n" + ph + "\n== B\n\ntext\n",
        "== A\n\n- a\n" + ph + "\n== B\n\ntext\n",
        "== A\n\n- a\n\n- b\n\n" + ph + "\n== B\n\ntext\n",
        "== A\n\n- a\n- b\n" + ph + "== B\n\ntext\n",
    )
    for src, want in zip(LISTS, expected):
        assert structural(src, "insert_item", target="s1") == want


def test_insert_item_keeps_a_tight_list_tight():
    src = "== A\n\n- a\n- b\n\n== B\n\ntext\n"
    assert structural(src, "insert_item", target="s1") \
        == f"== A\n\n- a\n- b\n- {PLACEHOLDER}\n\n== B\n\ntext\n"


def test_an_item_added_to_a_list_deletes_back_to_the_same_bytes():
    for src in LISTS:
        out = structural(src, "insert_item", target="s1")
        added = parse_projection(out).sections[0].blocks[-1].id
        assert structural(out, "delete", target=added) == src


def test_insert_item_and_bullet_copy_the_sibling_s_list_marker():
    plus = '== A\n\n#work(title: "T")\n+ x\n+ y\n\n== B\n\ntext\n'
    out = structural(plus, "insert_item", target="s1")
    assert f'#work(title: "{PLACEHOLDER}")\n+ {PLACEHOLDER}\n' in out and "- " not in out
    out = structural(plus, "insert_bullet", target="s1i0")
    assert f"+ y\n+ {PLACEHOLDER}\n" in out and "- " not in out
    enum = '== A\n\n#work(title: "T")\n1. x\n\n'
    # deliberate change: an explicit `1.` is not copied (two items would both read "1.")
    assert f"+ {PLACEHOLDER}\n" in structural(enum, "insert_item", target="s1")
    assert f"1. {PLACEHOLDER}" not in structural(enum, "insert_item", target="s1")
    top = "== A\n\n+ a\n+ b\n"
    assert structural(top, "insert_item", target="s1") == top + f"+ {PLACEHOLDER}\n"
    indented = '== A\n\n#work(title: "T")\n  - x\n'
    assert f"  - {PLACEHOLDER}\n" in structural(indented, "insert_item", target="s1")


def test_the_gap_is_measured_between_adjacent_siblings_only():
    # `#pagebreak()` sits between the two items and is tight against the first, so the
    # first item's trailing blanks say nothing about how the last two are separated.
    src = ('== A\n\n#work(title: "T1")\n- x\n#pagebreak()\n\n#work(title: "T2")\n- y\n\n'
           '== B\n\ntext\n')
    out = structural(src, "insert_item", target="s1")
    assert out == ('== A\n\n#work(title: "T1")\n- x\n#pagebreak()\n\n#work(title: "T2")\n'
                   f'- y\n\n#work(title: "{PLACEHOLDER}")\n- {PLACEHOLDER}\n\n'
                   '== B\n\ntext\n')


def test_an_entry_with_an_unnamed_value_cannot_be_copied_blank():
    for call in ('#cvitem("Role at Acme", company: "Acme")', '#cvitem([Role], company: "Acme")'):
        src = f"== A\n\n{call}\n- x\n"
        err = refused(src, "insert_item", target="s1")
        assert "unnamed" in err.detail
    # the refusal is about the copied block only: a fully keyed one is copied
    keyed = '== A\n\n#cvitem(role: "Role at Acme", company: "Acme")\n- x\n'
    out = structural(keyed, "insert_item", target="s1")
    assert "Role at Acme" in out and out.count("Role at Acme") == 1
    # and bullets can still be added to the entry the item copy was refused for
    assert PLACEHOLDER in structural(
        '== A\n\n#cvitem("Role", company: "Acme")\n- x\n', "insert_bullet", target="s1i0")


def test_deleting_the_only_block_of_a_document_is_refused_not_compiled_empty():
    refused('#work(title: "T")\n- x\n', "delete", target="s0i0")


def test_three_newlines_collapse_to_two_so_the_cycle_is_exact_only_on_normal_seams():
    # Invariant 4 holds for seams that already hold at most one blank line. A seam with
    # two blank lines is the same paragraph break in Typst; the first structural edit
    # there normalizes it to one, and the document is stable after that.
    src = '== A\n\n#work(title: "T")\n- x\n\n\n== B\n\ntext\n'
    out = structural(src, "insert_item", target="s1")
    assert "\n\n\n" not in out
    added = parse_projection(out).sections[0].blocks[-1].id
    back = structural(out, "delete", target=added)
    assert back == src.replace("- x\n\n\n== B", "- x\n\n== B")
    assert len(back) == len(src) - 1
    again = structural(back, "insert_item", target="s1")
    assert structural(again, "delete",
                      target=parse_projection(again).sections[0].blocks[-1].id) == back


def test_delete_takes_the_comment_lines_attached_to_the_item_and_a_copy_does_not():
    src = ('== A\n\n#work(title: "T")\n- x\n// note to self\n\n#work(title: "U")\n- y\n')
    assert structural(src, "delete", target="s1i0") == '== A\n\n#work(title: "U")\n- y\n'
    out = structural('== A\n\n#work(title: "T")\n- x\n// note to self\n', "insert_item",
                     target="s1")
    assert out.count("// note to self") == 1


def test_structural_edits_on_the_chinese_fixture_are_exact_in_code_points():
    src = read("synthetic-zh"); p = parse_projection(src)
    assert len(src.encode("utf-8")) > len(src)          # offsets must be code points
    comps = [b for s in p.sections for b in s.blocks if b.kind == "comp"]
    for comp in comps:
        out = structural(src, "insert_bullet", target=comp.id)
        added = next(b for s in parse_projection(out).sections for b in s.blocks
                     if b.id == comp.id).bullets[-1]
        assert structural(out, "delete", target=added.id) == src, comp.id
    for sec in p.sections:
        if sec.title_unit and sec.blocks:
            out = structural(src, "insert_item", target=sec.id)
            added = next(s for s in parse_projection(out).sections
                         if s.id == sec.id).blocks[-1]
            assert structural(out, "delete", target=added.id) == src, sec.id
    out = structural(src, "insert_item", target=section_named(p, "專案").id)
    new = parse_projection(out)
    project = [b for b in section_named(new, "專案").blocks if b.kind == "comp"][-1]
    assert project.name == "project" and "tinyqueue" in src and out.count("tinyqueue") == src.count("tinyqueue")
    last = p.sections[-1]
    up = structural(src, "move_section", target=last.id, dir="up")
    assert titles(parse_projection(up))[-2:] == [titles(p)[-1], titles(p)[-2]]
    assert structural(up, "move_section", target=parse_projection(up).sections[-2].id,
                      dir="down") == src
    only = section_named(p, "學歷")
    gone = structural(src, "delete", target=only.blocks[0].id)
    assert "學歷" not in gone and gone == src[:only.outer[0]] + src[only.outer[1]:]


# -- an empty section: the template's own emitter, or nothing --------------------------

PAIRS = sorted(deterministic_pairs())
# Parametrising over an emptied registry would collect nothing and pass by vacuity.
assert PAIRS, "deterministic_pairs() is empty -- every gate below would collect nothing"


def _without(src: str, start: str, stop: str) -> str:
    a = src.index(start)
    return src[:a] + src[src.index(stop, a):]


def test_insert_item_into_an_empty_section_uses_the_emitter():
    # synthetic-en is basic-resume 0.2.9, whose own emitter dates a work entry with
    # `dates-helper(...)`; other templates name that call differently (see below).
    src = _without(read("synthetic-en"), "#work(", "#pagebreak()")
    p = parse_projection(src)
    assert not section_named(p, "Experience").blocks
    out = structural(src, "insert_item", target=section_named(p, "Experience").id)
    assert "dates-helper(" in out and out.count(PLACEHOLDER) >= 4
    new = section_named(parse_projection(out), "Experience")
    assert [b.name for b in new.blocks if b.kind == "comp"] == ["work"]
    assert out.endswith(src[src.index("#pagebreak()"):])


def test_insert_item_into_an_empty_section_without_an_emitter_is_refused():
    src = _without(read("synthetic-en"), "#work(", "#pagebreak()")
    for pinned in ("not-a-template:1.0.0", "basic-resume:0.0.1"):
        other = src.replace("basic-resume:0.2.9", pinned)
        assert emitter_for(*pinned.split(":")) is None
        refused(other, "insert_item", target=section_named(
            parse_projection(other), "Experience").id)


def test_insert_item_does_not_fill_a_section_the_projection_cannot_see_into():
    # `Skills` here holds a #grid: not empty, merely unprojected. Nothing is appended.
    src = read("synthetic-tier-b")
    p = parse_projection(src)
    refused(src, "insert_item", target=section_named(p, "Skills").id)


def test_emit_parse_round_trips_for_every_pinned_emitter():            # Invariant 2
    assert PAIRS
    opaque_seen = 0
    for name, version in PAIRS:
        source = emitter_for(name, version).emit(GOLDEN_CONTENT).source
        p = parse_projection(source)
        assert p.units, name
        out = source
        for u in sorted(p.units.values(), key=lambda x: -x.span[0]):
            if u.opaque:
                # By design (typst_projection's docstring) a run the parser cannot
                # rebuild makes its unit read-only; it must never be claimed editable.
                assert not u.editable, (name, u.value)
                opaque_seen += 1
                continue
            out = out[:u.span[0]] + serialize_unit(u) + out[u.span[1]:]
        assert out == source, name
    # ats-friendly-resume's `[#"url"]` link label is the one such unit in the corpus
    assert opaque_seen == 1


def test_an_escape_the_range_already_has_is_written_back_as_it_was():
    for text in (r"12M\/yr and \/x", r"x\/\/y", r"a\&b"):
        u = line_unit(text)
        assert u is not None and u.editable, text
    # a range that mixes `\/` and a bare `/` cannot be rebuilt: read-only, never rewritten
    assert not line_unit(r"a\/ b/c").editable
    # `#"text"` is code, not text: an opaque run, so read-only rather than escaped to `\#`
    u = line_unit('a #"b/c" d')
    assert u.opaque == ('#"b/c"',) and not u.editable
    assert serialize_unit(line_unit(r"12M\/yr")) == r"12M\/yr"
    assert serialize_unit(line_unit("a // b")) == "a /\\/ b"      # canonical stays canonical


def test_insert_item_refuses_for_an_emitter_whose_entries_are_not_markup():
    # clean-print-cv keeps its entries in a data dict: there is no entry to write as text
    name, version = next(pair for pair in PAIRS if pair[0] == "clean-print-cv")
    for title in ("Experience", "Skills"):         # an item, and a free-form bullet
        refused(f'#import "@preview/{name}:{version}": *\n\n== {title}\n\n',
                "insert_item", target="s1")


def test_the_first_item_of_an_emitter_has_one_placeholder_per_field():
    for name, version in PAIRS:
        src = (f'#import "@preview/{name}:{version}": *\n\n== Projects\n\n')
        if name == "clean-print-cv":
            continue
        out = structural(src, "insert_item", target="s1")
        assert f"{PLACEHOLDER}, {PLACEHOLDER}" not in out, name
        assert f"{PLACEHOLDER}{PLACEHOLDER}" not in out, name


def test_a_project_gets_a_role_exactly_where_the_template_has_a_slot_for_it():
    def first_project(name, version):
        src = f'#import "@preview/{name}:{version}": *\n\n== Projects\n\n'
        return structural(src, "insert_item", target="s1")
    assert "role: " in first_project("basic-resume", "0.2.9")
    assert "role: " not in first_project("acorn-resume", "0.1.0")
    assert "role: " not in first_project("ats-friendly-resume", "0.1.1")


def test_a_slash_after_an_escaped_backslash_is_bare_not_escaped():
    u = line_unit(r"a\\/\/b")                 # value `a\//b`: `\\`, bare `/`, `\/`
    assert u.editable and u.value == "a\\//b"
    # a value with a backslash before `//` is written, and what is written reads back
    from dataclasses import replace
    out = serialize_unit(replace(line_unit("plain"), value="x\\//y"))
    assert out == r"x\\/\/y"
    assert line_unit(out).editable and line_unit(out).value == "x\\//y"


def test_a_slash_the_serializer_would_escape_beside_a_star_stays_canonical():
    u = line_unit(r"a/b\/*c*")
    assert u.editable and "/" not in u.escapes
    assert serialize_unit(u) == r"a/b\/*c*"


def test_section_kind_is_by_word_not_by_substring():
    from app.services.typst_intents import _section_kind
    assert [_section_kind(t) for t in ("Coursework", "Teamwork", "Networking")] == ["bullet"] * 3
    assert [_section_kind(t) for t in ("Work Experience", "工作經歷", "Projects", "Education")] \
        == ["work", "work", "project", "education"]


def test_no_emitter_says_so():
    src = _without(read("synthetic-en"), "#work(", "#pagebreak()").replace(
        "basic-resume:0.2.9", "not-a-template:1.0.0")
    err = refused(src, "insert_item", target=section_named(parse_projection(src),
                                                           "Experience").id)
    assert "no emitter" in str(err)


def test_every_placeholder_of_a_first_item_lands_in_a_field_the_form_can_reach():
    from app.services.typst_intents import _emitter_snippet, _unreachable
    got = {}
    for name, version in PAIRS:
        for kind in ("work", "education", "project", "bullet"):
            try:
                body = _emitter_snippet(
                    f'#import "@preview/{name}:{version}": *\n', kind, PLACEHOLDER)
            except IntentError:
                assert name == "clean-print-cv", (name, kind)
                continue
            assert PLACEHOLDER in body, (name, kind)
            assert _unreachable(body, PLACEHOLDER) == 0, (name, kind)
            got[name, kind] = body.count(PLACEHOLDER)
    # What each template really reaches. Reachable-or-nothing is satisfied by emitting
    # nothing, so the count of fields kept is pinned too: giving up a reachable field
    # (url, bullets, location) changes one of these.
    assert got == {
        ("basic-resume", "work"): 6, ("basic-resume", "education"): 6,
        ("basic-resume", "project"): 6, ("basic-resume", "bullet"): 1,
        ("ats-friendly-resume", "work"): 6, ("ats-friendly-resume", "education"): 6,
        ("ats-friendly-resume", "project"): 5, ("ats-friendly-resume", "bullet"): 1,
        ("acorn-resume", "work"): 5, ("acorn-resume", "education"): 6,
        ("acorn-resume", "project"): 1, ("acorn-resume", "bullet"): 1,
    }


def test_an_escaped_backslash_run_at_the_start_of_the_range_is_counted():
    # the backslash at the range's first character is part of the run (kills `j > lo`)
    for text in (r"\\/\/b", r"- \\/\/b", r"== \\/\/b"):
        u = line_unit(text)
        assert u is not None and u.editable and u.value.endswith("//b"), text


def test_a_slash_before_the_range_is_not_the_one_the_escape_follows():
    # `/` sits just outside [lo, end): the first `\/` is the range's own escape, so it is
    # recorded, not mistaken for the second of `//` (kills dropping `i > lo`)
    from app.services.typst_projection import _line_unit
    src = r"/\/a"
    u = _line_unit(src, "u1", 1, len(src), "para")
    assert u.editable and u.escapes == ("/",)


def test_an_intent_may_bring_the_placeholder_in_its_own_language():
    """The placeholder and the new heading are *content*, not Typst, so the locale with
    the person's words owns them. An English-locale résumé was getting 「（待填）」 in its
    `url:` and date arguments, where it is easy to miss before export."""
    src = read("synthetic-en"); p = parse_projection(src)
    skills = section_named(p, "Skills & Awards")
    assert structural(src, "insert_item", target=skills.id,
                      placeholder="(to fill in)") == src + "- (to fill in)\n"
    assert PLACEHOLDER not in structural(src, "insert_item", target=skills.id,
                                         placeholder="(to fill in)")

    bullets = structural(src, "insert_bullet", target=first_comp(p).id,
                         placeholder="(to fill in)")
    assert "- (to fill in)\n" in bullets and PLACEHOLDER not in bullets

    out = structural(src, "insert_section", placeholder="(to fill in)",
                     title="New section")
    assert out == src + "\n== New section\n\n(to fill in)\n\n"
    assert titles(parse_projection(out))[-1] == "New section"

    # Copying an entry's shape blanks every reachable field with the word it was given.
    work = structural(src, "insert_item", target=section_named(p, "Experience").id,
                      placeholder="(to fill in)")
    assert work.count("(to fill in)") >= 4 and PLACEHOLDER not in work

    # The emitter fallback for an empty section writes it too.
    empty = _without(src, "#work(", "#pagebreak()")
    first = structural(empty, "insert_item", placeholder="(to fill in)",
                       target=section_named(parse_projection(empty), "Experience").id)
    assert "dates-helper(" in first and first.count("(to fill in)") >= 4
    assert PLACEHOLDER not in first


def test_a_placeholder_is_checked_like_any_other_value():
    src = read("synthetic-en"); p = parse_projection(src)
    skills = section_named(p, "Skills & Awards").id
    for bad in ("", "   ", "two\nlines", "two\rlines"):
        refused(src, "insert_item", target=skills, placeholder=bad)
        refused(src, "insert_section", title=bad)
    # Refused whichever structural intent carried it, before anything is written.
    refused(src, "delete", target=first_comp(p).id, placeholder="two\nlines")
    # And the server's own default still stands in when nothing is brought.
    assert structural(src, "insert_item", target=skills) == src + f"- {PLACEHOLDER}\n"


def test_each_insert_item_refusal_names_which_one_it_was():
    """`unsupported` covers four quite different things here, and the panel cannot word
    them in the person's language off one English sentence -- it was showing the
    positional case's sentence ("entries hold text the form cannot blank out") for all
    four, including the one that is now the common path: an empty section whose template
    has no emitter."""
    src = read("synthetic-en")
    p = parse_projection(src)

    # 1. an entry carrying a positional string, which a copy cannot blank
    # the *last* entry of the section is the one a copy would take its shape from
    ph = '#work(\n  title: "Software Engineer",'
    positional = src.replace(ph, ph.replace("title: ", '"Software Engineer",\n  title: '))
    assert positional != src
    err = refused(positional, "insert_item",
                  target=section_named(parse_projection(positional), "Experience").id)
    assert err.reason == "positional", err.detail

    # 2. an empty section whose template has no emitter -- the common path, now that the
    #    button is offered on an empty section at all
    empty = _without(src, "#work(", "#pagebreak()")
    none = empty.replace("basic-resume:0.2.9", "not-a-template:1.0.0")
    err = refused(none, "insert_item",
                  target=section_named(parse_projection(none), "Experience").id)
    assert err.reason == "no_emitter", err.detail

    # 3. a template whose own entries put fields where the form cannot reach them
    odd = empty.replace("basic-resume:0.2.9", "clean-print-cv:0.1.0")
    err = refused(odd, "insert_item",
                  target=section_named(parse_projection(odd), "Experience").id)
    assert err.reason == "template_shape", err.detail

    # 4. a section holding content the projection cannot read, so nothing to copy
    tier_b = read("synthetic-tier-b")
    grid = next(s for s in parse_projection(tier_b).sections
                if s.title_unit and not s.blocks)
    err = refused(tier_b, "insert_item", target=grid.id)
    assert err.reason == "nothing_to_copy", err.detail

    # A refusal with nothing to distinguish carries no reason, and the panel then shows
    # the generic sentence with the server's own detail rather than guessing.
    assert refused(src, "insert_bullet", target="nope").reason == ""
    assert refused(src, "insert_item", target="nope").reason == ""


# --- the fix-forward batch: five defects cursor[bot] found after the merge --------------

# Every character Typst's lexer breaks a line on. Spelled out here rather than imported
# so the test fails if `has_line_break`'s set is ever narrowed to match the code again.
LINE_BREAKS = ("\n", "\r", "\u2028", "\u2029", "\x85", "\x0b", "\x0c")


def test_set_line_refuses_a_link_url_whose_scheme_a_viewer_may_execute():
    """A `javascript:` url compiles into a live URI in a shared PDF, so the refusal has
    to be on the server: `link-url.ts`'s allow-list is the client's and is bypassable."""
    src = read("synthetic-en"); p = parse_projection(src)
    u = unit_by_text(p, CUT)

    def attempt(url: str):
        return apply_intent(src, p, IntentIn(
            hash="", kind="set_line", unit=u.id, expect=u.value, text=u.value,
            marks=[MarkOut(kind="link", start=0, end=3, url=url)]))

    # The four executable schemes, and the spellings that hide them: a reader strips
    # controls, spaces and zero-width characters before it decides what the scheme is.
    for url in ("javascript:alert(1)", "JavaScript:alert(1)", "  javascript:alert(1)",
                "java\tscript:alert(1)", "java\u200bscript:alert(1)", "JAVASCRIPT:x",
                "data:text/html;base64,PHNjcmlwdD4=", "vbscript:msgbox(1)",
                "file:///etc/passwd"):
        with pytest.raises(IntentError) as e:
            attempt(url)
        assert e.value.code == "unsupported" and e.value.reason == "link_scheme", url

    # A DENYLIST on purpose, not the client's allow-list: a line someone is editing may
    # legitimately carry a scheme no viewer executes, and an allow-list here would leave
    # them unable to edit that line at all.
    for url in ("https://example.com/a", "http://example.com", "mailto:a@b.co",
                "tel:+886912345678", "ftp://host/file", "geo:25.03,121.56",
                "example.com:8080/x"):
        assert attempt(url) != src, url


def test_a_url_already_in_a_document_does_not_become_read_only():
    """Why the refusal is on the write path and not in `_check_marks`: that one is also
    reached from `_line_unit`, which decides `editable`. A refusal there would strand the
    person -- the line holding the bad url would be the one line they could not fix."""
    src = ('#import "@preview/basic-resume:0.2.10": *\n\n= A\n\n== S\n\n'
           'Visit #link("javascript:alert(1)")[here] today\n')
    p = parse_projection(src)
    unit = next(u for u in p.units.values() if u.kind == "line" and "Visit" in u.value)
    assert unit.editable, "an existing bad url must stay editable, not become read-only"
    # And it can be edited into something harmless, marks dropped.
    out = apply_intent(src, p, IntentIn(hash="", kind="set_line", unit=unit.id,
                                        expect=unit.value, text="Visit here today",
                                        marks=[]))
    assert "javascript:" not in out


KEYED_BLOCK_SRC = '''#import "@preview/basic-resume:0.2.10": *

= Ada Lovelace

== Experience

#work(
  title: "Engineer",
  company: "Northwind",
  details: [
    - Shipped the thing
  ],
)

#work(
  title: "Intern",
  company: "Acme",
  details: [
    - Learned the thing
  ],
)
'''


def test_a_keyed_content_block_stops_an_item_copy_like_a_positional_one():
    """`details: [...]` is content the projection makes no unit of. The guard tested
    `key == POSITIONAL`, and a keyed block has a key -- so `insert_item` copied the
    previous entry's bullets into the new entry, where no field could clear them."""
    p = parse_projection(KEYED_BLOCK_SRC)
    # The premise: the block's bullets really are unreachable.
    assert p.report.skipped_args.get("details") == 2
    assert all(len(b.bullets) == 0 for s in p.sections for b in s.blocks)

    e = refused(KEYED_BLOCK_SRC, "insert_item",
                target=section_named(p, "Experience").id)
    # The reason code stays `positional`: the frontend words its sentence from it
    # (`StructuredPanel.tsx`), and this batch does not touch the frontend.
    assert e.reason == "positional"


def test_every_line_break_typst_knows_is_refused_by_every_intent_string():
    """`\\n`/`\\r` were the only two checked, so a paste carrying U+2028 wrote a break
    into the source that the re-parse check never saw."""
    src = read("synthetic-en"); p = parse_projection(src)
    arg = title_unit(p)
    line = unit_by_text(p, CUT)
    skills = section_named(p, "Skills & Awards").id

    for ch in LINE_BREAKS:
        name = f"U+{ord(ch):04X}"
        with pytest.raises(IntentError) as e:
            apply_intent(src, p, IntentIn(hash="", kind="set_arg", unit=arg.id,
                                           expect=arg.value, value=f"a{ch}b"))
        assert e.value.code == "unsupported", name
        with pytest.raises(IntentError) as e:
            apply_intent(src, p, IntentIn(hash="", kind="set_line", unit=line.id,
                                           expect=line.value, text=f"a{ch}b", marks=[]))
        assert e.value.code == "unsupported", name
        refused(src, "insert_item", target=skills, placeholder=f"a{ch}b")
        refused(src, "insert_section", title=f"a{ch}b")


def test_a_written_word_is_escaped_for_the_context_it_lands_in():
    """`_word` validated the placeholder and nothing escaped it, so it reached the source
    raw -- the one `IntentIn` string that did. Two contexts, two escapes."""
    src = read("synthetic-en"); p = parse_projection(src)

    # 1. MARKUP: a heading and a paragraph of its own.
    out = structural(src, "insert_section",
                     title='#eval("1+1")', placeholder="- *bold* and == heading")
    added = out[len(src):]
    assert "\\#eval" in added and "\\- \\*bold\\*" in added
    p2 = parse_projection(out)
    fresh = [s for s in p2.sections if s.id not in {x.id for x in p.sections}]
    assert len(fresh) == 1, "the placeholder must not fabricate a second section"
    sec = fresh[0]
    # It round-trips: the person sees their own text in the form and can clear it.
    assert p2.units[sec.title_unit].value == '#eval("1+1")'
    bodies = [p2.units[b.unit] for b in sec.blocks if b.unit]
    assert len(bodies) == 1 and bodies[0].role == "para"
    assert bodies[0].value == "- *bold* and == heading"

    # 2. MARKUP, bullet line: `- x` must not become a nested list.
    skills = section_named(p, "Skills & Awards")
    out = structural(src, "insert_item", target=skills.id, placeholder="- x")
    p3 = parse_projection(out)
    added_bullets = [b for b in section_named(p3, "Skills & Awards").blocks]
    assert len(added_bullets) == len(skills.blocks) + 1
    assert any(p3.units[b.unit].value == "- x" for b in added_bullets if b.unit)

    # 3. STRING LITERAL: an arg unit's span is inside `"..."`.
    exp = section_named(p, "Experience")
    out = structural(src, "insert_item", target=exp.id, placeholder='a "b" \\ c')
    assert 'a \\"b\\" \\\\ c' in out
    p4 = parse_projection(out)
    vals = [u.value for u in p4.units.values() if u.kind == "arg"]
    assert 'a "b" \\ c' in vals, "the placeholder must round-trip out of the literal"
