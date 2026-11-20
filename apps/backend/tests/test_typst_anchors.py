"""Where each line unit sits on the rendered page."""

from dataclasses import replace

import pytest

from app.services import typst_anchors
from app.services.text_layer import extract_text_layer
from app.services.typst_anchors import (
    Band, anchor_bands, anchor_positions, label_shadow)
from app.services.typst_compile import compile_typst, typst_available
from app.services.typst_anchors import _arg_bands, _assign, _squash
from app.services.typst_projection import Unit, call_end, parse_projection
from tests.test_typst_fixtures import FIXTURES

pytestmark = pytest.mark.skipif(not typst_available(), reason="typst binary not available")


def read(name):
    return (FIXTURES / f"{name}.typ").read_text(encoding="utf-8")


def _assert_same_layout(src):
    p = parse_projection(src)
    plain = extract_text_layer(compile_typst(src, "pdf"))
    shadow = extract_text_layer(compile_typst(label_shadow(src, p), "pdf"))
    assert [len(x["spans"]) for x in plain] == [len(x["spans"]) for x in shadow]
    assert plain  # the loop below must have something to compare
    for a, b in zip(plain, shadow):
        for sa, sb in zip(a["spans"], b["spans"]):
            assert abs(sa["y"] - sb["y"]) < 0.001 and sa["text"] == sb["text"]
            assert abs(sa["x"] - sb["x"]) < 0.001 and abs(sa["width"] - sb["width"]) < 0.001


@pytest.mark.parametrize("name", ["synthetic-en", "synthetic-zh", "synthetic-marks",
                                  "synthetic-long"])
def test_labels_do_not_move_the_layout(name):                   # Invariant 5
    src = read(name)
    assert label_shadow(src, parse_projection(src)) != src
    _assert_same_layout(src)


def _mid_paragraph_case():
    """A para unit cut short so its label lands between two words of one line."""
    src = read("synthetic-en")
    p = parse_projection(src)
    unit = next(u for u in p.units.values() if u.role == "para")
    cut = src.index("keep working.") + len("keep working.")
    assert unit.span[0] < cut < unit.span[1]
    short = replace(unit, span=(unit.span[0], cut))
    return src, replace(p, units={**p.units, unit.id: short}), short


def test_a_label_in_the_middle_of_flowing_text_does_not_move_it():
    src, p, unit = _mid_paragraph_case()
    shadow = label_shadow(src, p)
    assert f"keep working. <ofy-{unit.id}> Writes" in shadow
    def rows(text):
        return [[(s["text"], round(s["y"], 3), round(s["x"], 3)) for s in pg["spans"]]
                for pg in extract_text_layer(compile_typst(text, "pdf"))]
    assert rows(src) == rows(shadow)


def test_a_unit_whose_label_is_mid_line_still_gets_that_line():
    src, p, unit = _mid_paragraph_case()
    got = anchor_bands(src + "\n", p)[unit.id]      # the newline defeats the cache
    layer = extract_text_layer(compile_typst(src, "pdf"))[0]
    prose = next(s for s in layer["spans"] if s["text"].startswith("Backend engineer"))
    assert [(b.page, round(b.y, 2)) for b in got] == [(0, round(prose["y"], 2))]


def test_every_line_unit_gets_a_position():
    src = read("synthetic-en"); p = parse_projection(src)
    pos = anchor_positions(label_shadow(src, p))
    line_ids = {u.id for u in p.units.values() if u.role}
    assert line_ids and line_ids == set(pos)
    # In document order the anchors never run backwards.
    ordered = sorted(line_ids, key=lambda i: p.units[i].span[0])
    keys = [pos[i] for i in ordered]
    assert keys == sorted(keys)


def test_a_wrapped_line_yields_one_band_per_visual_line():     # Review Focus 5
    src = read("synthetic-long")
    p = parse_projection(src)
    bands = anchor_bands(src, p)
    long_unit = max((u for u in p.units.values() if u.role == "bullet"),
                    key=lambda u: len(u.value))
    assert len(long_unit.value) >= 1900
    got = bands[long_unit.id]
    assert len(got) >= 5
    assert all(isinstance(b, Band) and b.height > 0 for b in got)
    # Every band is its own visual line.
    assert len({(b.page, round(b.y, 2)) for b in got}) == len(got)
    # And a one-line unit next to it is not swollen into the neighbour's lines.
    short = next(u for u in p.units.values() if u.role == "bullet" and u.id != long_unit.id)
    assert len(bands[short.id]) == 1


def test_bands_cover_exactly_the_unit_text_in_the_english_fixture():
    src = read("synthetic-en"); p = parse_projection(src)
    layer = extract_text_layer(compile_typst(src, "pdf"))
    bands = anchor_bands(src, p)
    for u in p.units.values():
        if not u.role:
            continue
        got = bands[u.id]
        assert len(got) == 1, (u.id, u.value)
        b = got[0]
        texts = [s["text"] for s in layer[b.page]["spans"]
                 if abs(s["y"] - b.y) < 0.01 and u.value.replace(" ", "")[:12]
                 in s["text"].replace(" ", "")]
        assert texts, (u.id, u.value)


def test_a_unit_that_wraps_across_a_page_break_keeps_both_halves():
    src = read("synthetic-long")
    long = next(u for u in parse_projection(src).units.values()
                if u.role == "bullet" and len(u.value) > 1900)
    # Five times the text: starts on page 1 and runs over two page breaks.
    src = src.replace(long.value, " ".join([long.value] * 5))
    p = parse_projection(src)
    unit = next(u for u in p.units.values() if u.role == "bullet" and len(u.value) > 9000)
    bands = anchor_bands(src, p)
    got = bands[unit.id]
    assert {b.page for b in got} == {0, 1}          # it really does cross a page break
    assert len(got) == len({(b.page, round(b.y, 2)) for b in got})
    layers = extract_text_layer(compile_typst(src, "pdf"))
    # The tail on page 2 starts at the very top of that page: its first line is the
    # first line the compiler placed there, and it belongs to the bullet.
    top = min(layers[1]["spans"], key=lambda s: s["y"])
    assert (1, round(top["y"], 2)) in {(b.page, round(b.y, 2)) for b in got}
    assert top["text"].replace(" ", "") in unit.value.replace(" ", "")
    # The next bullet follows the last wrapped line and does not inherit any of them.
    follower = next(u for u in p.units.values() if u.role and u.value.startswith("Led a team"))
    assert len(bands[follower.id]) == 1
    last = max((b.page, b.y) for b in got)
    assert (bands[follower.id][0].page, bands[follower.id][0].y) > last
    # Exactly the lines the layer puts between the bullet's first line and the follower's:
    # nothing missing, nothing from the neighbours, no right-hand column.
    first = min((b.page, b.y) for b in got)
    follow = (bands[follower.id][0].page, bands[follower.id][0].y)
    expected = sorted((pg, round(sp["y"], 2)) for pg, layer in enumerate(layers)
                      for sp in layer["spans"]
                      if first <= (pg, sp["y"]) < follow and sp["x"] < 100)
    assert expected == sorted((b.page, round(b.y, 2)) for b in got)


def test_the_cache_returns_copies_so_a_caller_cannot_corrupt_it():
    src = read("synthetic-en"); p = parse_projection(src)
    first = anchor_bands(src, p)
    uid = next(iter(first))
    pristine = list(first[uid])
    first[uid].append(Band(9, 9.0, 9.0))
    first["zz"] = []
    again = anchor_bands(src, p)
    assert again is not first and again[uid] == pristine and "zz" not in again


@pytest.mark.parametrize("name", ["synthetic-en", "synthetic-zh", "synthetic-marks"])
def test_every_line_unit_has_one_band_holding_its_text_and_nothing_else(name):
    src = read(name); p = parse_projection(src)
    layers = extract_text_layer(compile_typst(src, "pdf"))
    bands = anchor_bands(src, p)
    for u in p.units.values():
        if not u.role:
            continue
        assert len(bands[u.id]) == 1, (u.id, u.value, bands[u.id])   # all fit on a line
        b = bands[u.id][0]
        line = next(s["text"] for s in layers[b.page]["spans"]
                    if abs(s["y"] - b.y) < 0.01 and _squash(s["text"]) in _squash(u.value))
        # Both directions: the line is a piece of the unit's text, and the unit's text
        # starts with the line (the unit being one line), ignoring bullet and punctuation.
        assert _squash(line) in _squash(u.value), (u.id, line, u.value)
        assert _squash(u.value).startswith(_squash(line)[:8]), (u.id, line, u.value)


@pytest.mark.parametrize("name,old,new", [
    ("synthetic-en", "Led a team of four through a zero-downtime database migration.",
     "Led a team of four through a Contoso Sample Ltd. migration in Springfield "
     "during 2020 and 2021."),
    ("synthetic-zh", "帶領四人團隊完成零停機的資料庫遷移。",
     "帶領四人團隊與範例樣本有限公司完成零停機的資料庫遷移，團隊位於臺北。"),
])
def test_a_unit_does_not_claim_the_next_jobs_lines_when_its_text_recurs(name, old, new):
    src = read(name)
    assert old in src
    src = src.replace(old, new)
    p = parse_projection(src)
    unit = next(u for u in p.units.values() if u.role == "bullet" and u.value == new)
    layers = extract_text_layer(compile_typst(src, "pdf"))
    got = anchor_bands(src, p)[unit.id]
    assert len(got) == 1, got
    line = next(s for s in layers[got[0].page]["spans"] if abs(s["y"] - got[0].y) < 0.01)
    assert _squash(line["text"]).startswith(_squash(new)[:6])
    # The recurring strings do render further down: that is what must not be claimed.
    rows = [s["text"] for layer in layers for s in layer["spans"]]
    assert any(t.startswith(("Contoso", "範例樣本")) for t in rows)


def test_a_user_label_with_a_generated_name_does_not_move_an_anchor():
    plain = read("synthetic-long")
    mine = plain.replace("teaching assistant for Algorithms.",
                         "teaching assistant <u9> for Algorithms.")
    assert mine != plain
    p_plain, p_mine = parse_projection(plain), parse_projection(mine)
    assert anchor_positions(label_shadow(mine, p_mine))["u9"] == \
        anchor_positions(label_shadow(plain, p_plain))["u9"]
    assert anchor_bands(mine, p_mine)["u9"] == anchor_bands(plain, p_plain)["u9"]
    # A user label already in our namespace pushes ours somewhere else.
    clash = plain.replace("teaching assistant for Algorithms.",
                          "teaching assistant <ofy-u9> for Algorithms.")
    shadow = label_shadow(clash, parse_projection(clash))
    assert "<ofyx-u9>" in shadow and "<ofy-u9> for" in shadow
    assert anchor_positions(shadow)["u9"] == \
        anchor_positions(label_shadow(plain, p_plain))["u9"]


def test_a_trailing_comment_does_not_cost_the_unit_its_label():
    src = read("synthetic-en").replace(
        "teaching assistant for Algorithms.", "teaching assistant for Algorithms. // todo")
    p = parse_projection(src)
    unit = next(u for u in p.units.values() if u.role == "bullet" and "Algorithms" in u.value)
    shadow = label_shadow(src, p)
    assert f"Algorithms. <ofy-{unit.id}> // todo" in shadow
    assert unit.id in anchor_positions(shadow)
    got = anchor_bands(src, p)[unit.id]
    layers = extract_text_layer(compile_typst(src, "pdf"))
    assert len(got) == 1
    assert "Graduated with honors" in next(
        s["text"] for s in layers[got[0].page]["spans"] if abs(s["y"] - got[0].y) < 0.01)


def test_the_first_line_is_the_one_nearest_the_anchor_without_a_tuned_constant():
    layer = {"spans": [{"text": "above", "x": 1, "y": 10, "height": 10},
                       {"text": "below", "x": 1, "y": 60, "height": 10}]}
    unit = Unit(id="u1", kind="line", span=(0, 1), value="zzz", editable=True, role="bullet")
    got = _assign([unit], {"u1": (0, 52.0)}, [layer])   # outside both, nearer the lower
    assert [(b.page, b.y) for b in got["u1"]] == [(0, 60)]


def test_the_shadow_must_paginate_like_the_document(monkeypatch):
    src = read("synthetic-en"); p = parse_projection(src)
    real = typst_anchors._eval_anchors
    typst_anchors._cache.clear()
    assert anchor_bands(src, p)                       # control: the real pages agree
    typst_anchors._cache.clear()
    monkeypatch.setattr(typst_anchors, "_eval_anchors",
                        lambda *a, **k: (lambda r: (r[0], r[1] + 1))(real(*a, **k)))
    assert anchor_bands(src, p) == {}


def test_an_eval_that_finds_nothing_gives_no_bands_rather_than_empty_lists(monkeypatch):
    src = read("synthetic-en"); p = parse_projection(src)
    typst_anchors._cache.clear()
    monkeypatch.setattr(typst_anchors, "_eval_anchors", lambda *a, **k: ({}, 2))
    assert anchor_bands(src, p) == {}


def test_a_bullet_with_an_opaque_footnote_falls_back_to_its_anchor_line():
    # The rendered line reads "...finance1 every quarter." (the footnote is a superscript
    # and its text moves to the page foot) while the unit's value still carries the
    # footnote source, so the text check finds nothing and the fallback decides.
    src = read("synthetic-tier-b"); p = parse_projection(src)
    unit = next(u for u in p.units.values() if "#footnote[" in u.value)
    layers = extract_text_layer(compile_typst(src, "pdf"))
    assert not any("Presentedtheresults" in s["text"].replace(" ", "")
                   and s["text"].replace(" ", "").replace("\u2022", "")
                   .replace("1", "") in unit.value.replace(" ", "")
                   for s in layers[0]["spans"])      # really is the no-match case
    bands = anchor_bands(src, p)
    page, y = anchor_positions(label_shadow(src, p))[unit.id]
    got = bands[unit.id]
    assert len(got) == 1 and got[0].page == page == 0
    assert got[0].y <= y <= got[0].y + got[0].height     # the anchor's own line
    line = next(s for s in layers[0]["spans"] if abs(s["y"] - got[0].y) < 0.01)
    assert line["text"].startswith("\u2022 Presented the results to finance")
    # Not the neighbours' lines, and the footnote text at the page foot is not claimed.
    others = {(b.page, round(b.y, 2)) for uid, rows in bands.items() if uid != unit.id
              for b in rows}
    assert (got[0].page, round(got[0].y, 2)) not in others


# -- string arguments: no label of their own, so they fall back to their block's rows ---


def _row_texts(layers, band):
    """What the text layer prints on the row a band covers."""
    return [s["text"] for s in layers[band.page]["spans"]
            if band.y - 0.5 <= s["y"] <= band.y + band.height]


def test_a_string_argument_gets_the_rows_of_the_block_it_belongs_to():
    """A `title` / `company` / date argument is not a line and carries no label, so the
    design gives it "the rows of this block", bracketed by the line units around it.
    Without it the highlight was missing from 24 of this fixture's 37 editable units, and
    an armed delete on an entry with no bullets drew no band at all."""
    src = read("synthetic-en"); p = parse_projection(src)
    layers = extract_text_layer(compile_typst(src, "pdf"))
    bands = anchor_bands(src, p, layers=layers)
    content = [u for u in p.units.values() if u.kind == "arg" and not u.style]
    assert len(content) == 24                        # every string field on the form
    assert all(bands.get(u.id) for u in content), [
        u.key for u in content if not bands.get(u.id)]

    # The first `#work`: its two rendered rows, and not its bullets'.
    work = next(b for s in p.sections for b in s.blocks if b.name == "work")
    rows = bands[work.args[0]]
    assert [_row_texts(layers, b) for b in rows] == [
        ["Senior Engineer", "Jan 2022 — Present"],
        ["Northwind Example Co.", "Remote"]]
    # Every argument of the block gets the same band: it is the block's, not the field's.
    for uid in work.args:
        if not p.units[uid].style:
            assert bands[uid] == rows
    # The bullets print below, on rows this band does not reach.
    bullet = next(p.units[b.unit] for b in work.bullets)
    assert bands[bullet.id] and all(
        (b.page, b.y) not in {(r.page, r.y) for r in rows} for b in bands[bullet.id])

    # The preamble's fields print above the first heading: the name and the contact line.
    author = next(u for u in p.units.values() if u.key == "author")
    assert [_row_texts(layers, b) for b in bands[author.id]][0] == ["Avery Quill"]
    assert "avery.quill@example.com" in "".join(_row_texts(layers, bands[author.id][-1]))
    heading = next(p.units[s.title_unit] for s in p.sections
                   if s.title_unit and p.units[s.title_unit].value == "Summary")
    assert all(b.y + b.height <= bands[heading.id][0].y for b in bands[author.id])


def test_a_layout_argument_gets_no_band_because_it_prints_nowhere():
    src = read("synthetic-en"); p = parse_projection(src)
    bands = anchor_bands(src, p)
    style = [u for u in p.units.values() if u.kind == "arg" and u.style]
    assert [u.key for u in style] == ["accent-color", "paper", "lang"]
    assert not any(u.id in bands for u in style)


def test_two_bulletless_entries_in_one_section_each_get_their_own_rows():
    """Two sibling entries with no bullets between them share one bracket, so bracketing
    gave neither a band -- an education section written without bullets had no highlight
    at all, which is what a person reported. Each block carries its own label now, so the
    anchors bound each other and each entry gets its own rows. Anti-goal 3 still holds:
    the bands must not be shared, and must not be swapped."""
    src = read("synthetic-en")
    for bullet in ("- Cut p99 latency of the ordering service from 900 ms to 240 ms.\n",
                   "- Led a team of four through a zero-downtime database migration.\n",
                   "- Built the internal deploy dashboard used by 30 engineers.\n"):
        assert bullet in src
        src = src.replace(bullet, "")
    p = parse_projection(src)
    layers = extract_text_layer(compile_typst(src, "pdf"))
    bands = anchor_bands(src, p, layers=layers)
    in_experience = [uid for s in p.sections if s.title_unit
                     and p.units[s.title_unit].value == "Experience"
                     for b in s.blocks for uid in b.args]
    assert len(in_experience) == 9
    assert all(bands.get(uid) for uid in in_experience), [
        p.units[uid].key for uid in in_experience if not bands.get(uid)]

    first, second = [b for s in p.sections for b in s.blocks if b.name == "work"]
    rows_a, rows_b = bands[first.args[0]], bands[second.args[0]]
    # Each entry's own rows, in the order they render, and not its sibling's.
    assert [_row_texts(layers, b) for b in rows_a] == [
        ["Senior Engineer", "Jan 2022 — Present"],
        ["Northwind Example Co.", "Remote"]]
    assert [_row_texts(layers, b) for b in rows_b] == [
        ["Software Engineer"], ["Contoso Sample Ltd.", "Springfield"]]
    assert {(b.page, b.y) for b in rows_a}.isdisjoint({(b.page, b.y) for b in rows_b})


def test_sibling_entries_whose_text_is_identical_still_get_their_own_rows():
    """The reported shape: two degrees at the same university, so `institution` and
    `location` are byte-identical between the entries and only the degree and the dates
    differ. Matching rows by text cannot separate these; the anchors can, because each
    block's label bounds the next one's."""
    src = ('#import "@preview/basic-resume:0.2.9": *\n\n'
           '#show: resume.with(author: "Ruby Example", email: "r@example.com")\n\n'
           '== Education\n\n'
           '#edu(\n'
           '  institution: "National Taiwan University",\n'
           '  location: "Taipei, Taiwan",\n'
           '  dates: dates-helper(start-date: "2026-02", end-date: "2028-01"),\n'
           '  degree: "Master’s in Information Management",\n'
           ')\n\n'
           '#edu(\n'
           '  institution: "National Taiwan University",\n'
           '  location: "Taipei, Taiwan",\n'
           '  dates: dates-helper(start-date: "2021-09", end-date: "2025-06"),\n'
           '  degree: "Bachelor’s in Information Management",\n'
           ')\n')
    p = parse_projection(src)
    layers = extract_text_layer(compile_typst(src, "pdf"))
    bands = anchor_bands(src, p, layers=layers)

    masters, bachelors = [b for s in p.sections for b in s.blocks if b.name == "edu"]
    assert [p.units[i].value for i in masters.args] == [
        "National Taiwan University", "Taipei, Taiwan", "2026-02", "2028-01",
        "Master’s in Information Management"]
    # Every field of both entries is highlightable -- none was, before the block label.
    for block in (masters, bachelors):
        assert all(bands.get(uid) for uid in block.args), [
            p.units[uid].key for uid in block.args if not bands.get(uid)]

    rows_a = [_row_texts(layers, b) for b in bands[masters.args[0]]]
    rows_b = [_row_texts(layers, b) for b in bands[bachelors.args[0]]]
    # The shared rows land once each, and the degree that differs picks the right entry.
    assert rows_a == [["National Taiwan University", "Taipei, Taiwan"],
                      ["Master’s in Information Management", "2026-02 — 2028-01"]]
    assert rows_b == [["National Taiwan University", "Taipei, Taiwan"],
                      ["Bachelor’s in Information Management", "2021-09 — 2025-06"]]
    assert {(b.page, b.y) for b in bands[masters.args[0]]}.isdisjoint(
        {(b.page, b.y) for b in bands[bachelors.args[0]]})


def test_a_block_with_a_body_takes_no_label_so_the_shadow_still_compiles():
    """`#figure(caption: "...")[body]` has a keyed string argument, so it is a block with
    content -- but a label after its closing paren lands between the arguments and the
    body and takes the body away from the call. Verified: the shadow then fails to
    compile with "missing argument: body", and since the bands come from one `typst
    eval` that is a 400 for the whole request, not a missing highlight. So a block with
    a body keeps the bracket, and the caption still gets a band from it."""
    src = ('#import "@preview/basic-resume:0.2.9": *\n\n'
           '#show: resume.with(author: "Ruby Example", email: "r@example.com")\n\n'
           '== Experience\n\n'
           '#figure(caption: "A caption that is content")[A figured body]\n\n'
           '#work(title: "Analyst", company: "Initech")\n'
           '- Did the thing.\n')
    p = parse_projection(src)
    figure = next(b for s in p.sections for b in s.blocks if b.name == "figure")
    assert figure.body is not None and figure.args          # content, and a body
    assert f"b{figure.id}" not in label_shadow(src, p)      # no label of its own
    # The `#show:` block is excluded too, by having no `#name(` head to measure from.
    show = next(b for s in p.sections for b in s.blocks if b.kind == "show")
    assert call_end(src, show) is None
    assert f"b{show.id}" not in label_shadow(src, p)

    _assert_same_layout(src)                                # and so it still compiles
    bands = anchor_bands(src, p)
    # The sibling that CAN take a label still does, and the request still answers.
    work = next(b for s in p.sections for b in s.blocks if b.name == "work")
    assert f"b{work.id}" in label_shadow(src, p)
    assert all(bands.get(uid) for uid in work.args)
    # The caption itself stays unhighlighted, as it was before blocks carried labels: a
    # figure prints its caption BELOW its body, so the bracket between the heading above
    # and the body unit holds no rows. Checked against the previous commit, so this is a
    # standing limit of the bracket and not something the block label took away.
    caption = next(u for u in p.units.values() if u.key == "caption")
    assert caption.id not in bands


def test_a_document_with_no_line_unit_gets_no_band_at_all():
    """Nothing to label means nothing to bracket with, so the whole map is empty rather
    than a page-sized band under every field."""
    src = ('#import "@preview/basic-resume:0.2.9": *\n\n'
           '#show: resume.with(author: "Avery Quill", email: "a@example.com")\n')
    p = parse_projection(src)
    assert not [u for u in p.units.values() if u.role]   # no line unit to bracket with
    assert [u.key for u in p.units.values()] == ["author", "email"]
    assert anchor_bands(src, p) == {}


def test_an_argument_with_nothing_placed_on_either_side_gets_no_band():
    """The bracket needs one end at least. A line unit whose anchor fell off the page
    keeps no band of its own, and with none placed above a block and none below it there
    is nothing to bracket with -- where a band would run the length of the page."""
    src = ('#import "@preview/basic-resume:0.2.9": *\n\n== Experience\n\n'
           '#work(title: "Analyst", company: "Initech")\n')
    p = parse_projection(src)
    layers = extract_text_layer(compile_typst(src, "pdf"))
    (heading,) = [u for u in p.units.values() if u.role]
    (block,) = [b for s in p.sections for b in s.blocks if b.args]
    assert _arg_bands(src, p, {heading.id: []}, layers) == {}
    # The heading's own band above it is enough, so this really is about the bracket.
    placed = anchor_bands(src, p, layers=layers)
    got = _arg_bands(src, p, {heading.id: placed[heading.id]}, layers)
    assert [_row_texts(layers, b) for b in got[block.args[0]]] == [["Analyst"],
                                                                  ["Initech"]]
