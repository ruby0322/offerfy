import pytest

from app.services.edit_diff import compact_line_diff, edit_result_payload
from app.typst_edit import apply_typst_edit


def test_full_source_write_replaces_document():
    original = "#let name = \"Old\"\n== Education\n"
    updated = apply_typst_edit(original, {"source": "#let name = \"Ada Lovelace\"\n"})
    assert updated == "#let name = \"Ada Lovelace\"\n"


def test_search_replace_swaps_first_match():
    original = "aaa bbb aaa"
    updated = apply_typst_edit(original, {"search": "aaa", "replace": "ccc"})
    assert updated == "ccc bbb aaa"


def test_range_replacement_uses_char_offsets():
    original = "0123456789"
    updated = apply_typst_edit(
        original, {"start": 3, "end": 7, "replacement": "XYZ"}
    )
    assert updated == "012XYZ789"


def test_search_replace_raises_when_missing():
    with pytest.raises(ValueError):
        apply_typst_edit("hello", {"search": "zzz", "replace": "yyy"})


def test_range_raises_on_invalid_offsets():
    with pytest.raises(ValueError, match="invalid range"):
        apply_typst_edit("abcd", {"start": 3, "end": 1, "replacement": "x"})


def test_invalid_range_falls_back_to_full_source():
    original = '#let name = "Your Name"\n'
    filled = '#let name = "Ada Lovelace"\nMicrosoft intern\n'
    updated = apply_typst_edit(
        original,
        {
            "source": filled,
            "start": 0,
            "end": 8000,
            "replacement": "",
        },
    )
    assert updated == filled


def test_schema_zero_range_does_not_block_full_source():
    original = '#let name = "Your Name"\n'
    filled = '#let name = "Ada Lovelace"\nCAMP\n'
    updated = apply_typst_edit(
        original,
        {
            "source": filled,
            "search": "",
            "replace": "",
            "start": 0,
            "end": 0,
            "replacement": "",
        },
    )
    assert updated == filled


def test_noop_range_falls_back_to_full_source():
    original = "hello"
    filled = "hello from CAMP"
    updated = apply_typst_edit(
        original,
        {"source": filled, "start": 0, "end": 5, "replacement": "hello"},
    )
    assert updated == filled


def test_compact_line_diff_search_replace_style():
    rows = compact_line_diff("Your Name", "Ada Lovelace")
    assert rows == [
        {"op": "del", "text": "Your Name"},
        {"op": "add", "text": "Ada Lovelace"},
    ]


def test_compact_line_diff_multiline_changed_only():
    old = "keep\nremove me\nkeep too"
    new = "keep\nadd me\nkeep too"
    rows = compact_line_diff(old, new)
    assert rows == [
        {"op": "del", "text": "remove me"},
        {"op": "add", "text": "add me"},
    ]


def test_search_replace_wins_over_stale_full_source():
    original = '#let name = "Kuan-Cheng (James) Ku"\n'
    stale = '#let name = "Kuan-Cheng (James) Ku"\n#let extra = 1\n'
    updated = apply_typst_edit(
        original,
        {
            "source": stale,
            "search": "Kuan-Cheng (James) Ku",
            "replace": "Kuan-Cheng Ku",
        },
    )
    assert updated == '#let name = "Kuan-Cheng Ku"\n'
    assert "James" not in updated
    assert "extra" not in updated


def test_noop_search_falls_back_to_full_source():
    original = '#let name = "Your Name"\nkeep\n'
    filled = '#let name = "Ada Lovelace"\nCAMP intern\n'
    updated = apply_typst_edit(
        original,
        {
            "source": filled,
            "search": "Your Name",
            "replace": "Your Name",
        },
    )
    assert updated == filled


def test_missing_search_falls_back_to_full_source():
    original = '#let name = "Your Name"\n'
    filled = '#let name = "Ada Lovelace"\nMicrosoft\n'
    updated = apply_typst_edit(
        original,
        {
            "source": filled,
            "search": '#let name = "Ada Lovelace"',
            "replace": '#let name = "Ada Lovelace"',
        },
    )
    assert updated == filled


def test_identity_search_without_source_stays_noop():
    original = '#let name = "Ada"\n'
    updated = apply_typst_edit(original, {"search": "Ada", "replace": "Ada"})
    assert updated == original


def test_empty_search_does_not_block_full_source_write():
    original = '#let name = "Kuan-Cheng (James) Ku"\n'
    rewritten = '#let name = "Kuan-Cheng Ku"\n'
    updated = apply_typst_edit(
        original,
        {"source": rewritten, "search": "", "replace": ""},
    )
    assert updated == rewritten


def test_prefer_full_source_writes_document_even_with_unmatched_search():
    original = '#let name = "Your Name"\n'
    filled = '#import "@preview/basic-resume:0.2.9": *\n#let name = "Ada Lovelace"\n'
    updated = apply_typst_edit(
        original,
        {
            "source": filled,
            "search": '#let name = "Ada Lovelace"',
            "replace": '#let name = "Ada Lovelace"',
        },
        prefer_full_source=True,
    )
    assert updated == filled


def test_prefer_full_source_ignores_live_search_when_rewriting():
    original = '#let name = "Your Name"\nkeep\n'
    filled = '#import "@preview/basic-resume:0.2.9": *\n#let name = "Ada Lovelace"\n'
    updated = apply_typst_edit(
        original,
        {
            "source": filled,
            "search": "Your Name",
            "replace": "Ada Lovelace",
        },
        prefer_full_source=True,
    )
    assert updated == filled
    assert "keep" not in updated


def test_edit_result_payload_diffs_actual_source_not_empty_args():
    before = '#let name = "Kuan-Cheng (James) Ku"\n#let linkedin = "James Ku"\n'
    after = '#let name = "Kuan-Cheng Ku"\n#let linkedin = "Kuan-Cheng Ku"\n'
    payload = edit_result_payload(
        before, after, {"source": after, "search": "", "replace": ""}
    )
    assert payload["ok"] is True
    assert payload["changed"] is True
    assert payload["diff"] == [
        {"op": "del", "text": '#let name = "Kuan-Cheng (James) Ku"'},
        {"op": "add", "text": '#let name = "Kuan-Cheng Ku"'},
        {"op": "del", "text": '#let linkedin = "James Ku"'},
        {"op": "add", "text": '#let linkedin = "Kuan-Cheng Ku"'},
    ]
    assert payload["previous_source"] == before


def test_edit_result_payload_diffs_live_document_not_unused_search():
    before = '#let name = "Old"\nkeep\n'
    after = '#let name = "New"\nkeep\n'
    payload = edit_result_payload(
        before,
        after,
        {"source": after, "search": "unused", "replace": "also unused"},
    )
    assert payload["diff"] == [
        {"op": "del", "text": '#let name = "Old"'},
        {"op": "add", "text": '#let name = "New"'},
    ]


def test_edit_result_payload_marks_noop():
    source = '#let name = "Ada"\n'
    payload = edit_result_payload(source, source, {"source": source})
    assert payload["changed"] is False
    assert payload["diff"] == []
    assert "previous_source" not in payload
    hint = payload.get("hint") or ""
    assert "retry" in hint.lower() or "again" in hint.lower()
    assert "user" in hint.lower()


def test_compact_line_diff_trailing_newline_only():
    rows = compact_line_diff("foo", "foo\n")
    assert rows
    assert any(row["op"] == "add" for row in rows)
