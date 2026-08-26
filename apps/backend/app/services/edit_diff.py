from __future__ import annotations

import difflib
from typing import Any


DIFF_LINE_LIMIT = 80


def compact_line_diff(old: str, new: str, *, limit: int = DIFF_LINE_LIMIT) -> list[dict[str, str]]:
    """Changed-line diff for tool traces. No surrounding context."""
    old_lines = old.split("\n")
    new_lines = new.split("\n")
    rows: list[dict[str, str]] = []
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag == "replace":
            old_chunk = old_lines[i1:i2]
            new_chunk = new_lines[j1:j2]
            paired = min(len(old_chunk), len(new_chunk))
            for index in range(paired):
                rows.append({"op": "del", "text": old_chunk[index]})
                if len(rows) >= limit:
                    return rows
                rows.append({"op": "add", "text": new_chunk[index]})
                if len(rows) >= limit:
                    return rows
            for line in old_chunk[paired:]:
                rows.append({"op": "del", "text": line})
                if len(rows) >= limit:
                    return rows
            for line in new_chunk[paired:]:
                rows.append({"op": "add", "text": line})
                if len(rows) >= limit:
                    return rows
            continue
        if tag == "delete":
            for line in old_lines[i1:i2]:
                rows.append({"op": "del", "text": line})
                if len(rows) >= limit:
                    return rows
        elif tag == "insert":
            for line in new_lines[j1:j2]:
                rows.append({"op": "add", "text": line})
                if len(rows) >= limit:
                    return rows
    if not rows and old != new:
        return [
            {"op": "del", "text": old[-200:]},
            {"op": "add", "text": new[-200:]},
        ]
    return rows


def edit_result_payload(before: str, after: str, _args: dict) -> dict[str, Any]:
    changed = before != after
    return {
        "ok": True,
        "changed": changed,
        "source": after,
        "diff": compact_line_diff(before, after) if changed else [],
    }
