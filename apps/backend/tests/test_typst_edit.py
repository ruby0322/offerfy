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
    with pytest.raises(ValueError):
        apply_typst_edit("abcd", {"start": 3, "end": 1, "replacement": "x"})


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


def test_empty_search_does_not_block_full_source_write():
    original = '#let name = "Kuan-Cheng (James) Ku"\n'
    rewritten = '#let name = "Kuan-Cheng Ku"\n'
    updated = apply_typst_edit(
        original,
        {"source": rewritten, "search": "", "replace": ""},
    )
    assert updated == rewritten


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


def test_compact_line_diff_trailing_newline_only():
    rows = compact_line_diff("foo", "foo\n")
    assert rows
    assert any(row["op"] == "add" for row in rows)
