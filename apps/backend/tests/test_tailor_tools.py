"""The requirement table and the action list, and the validation that makes
the action list's one-click apply honest.

These are pure functions of their arguments plus the draft, so most of this
file needs no database at all. The two handlers read `resume.typst_source` and
nothing else -- `db` is in their signature for symmetry with the other tool
modules -- so they are exercised with a stand-in resume rather than a row,
which keeps the per-turn cap test independent of the ORM.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from app.services.revisions import source_hash
from app.services.tailor_tools import (
    BEFORE_MAX_CHARS,
    MAP_REQUIREMENTS_TOOL,
    MAX_ACTIONS,
    MAX_REQUIREMENTS,
    PROPOSE_ACTIONS_TOOL,
    STATUSES,
    map_requirements,
    tailor_tool_handlers,
    tailor_tool_specs,
)

# A draft with the things a real one has and a validator trips over: CJK,
# indentation, and one line that repeats.
SOURCE = """= 王小明

== 工作經歷

*Stripe* — 後端工程師 (2022–2025)
  - 以 Docker 打包服務並部署至雲端
  - 與美國團隊以英文協作
  - 維護請款與對帳服務

*Acme* — 實習生 (2021)
  - 維護請款與對帳服務
"""


def _resume(source: str = SOURCE):
    return SimpleNamespace(typst_source=source, user_id="u1", kind="tailor")


# --- specs ------------------------------------------------------------------


def test_both_tools_are_offered_to_every_resume():
    names = [spec["name"] for spec in tailor_tool_specs(_resume())]
    assert names == [MAP_REQUIREMENTS_TOOL, PROPOSE_ACTIONS_TOOL]


def test_the_status_enum_is_the_vocabulary_the_validator_enforces():
    """One list, so the schema cannot drift from what `map_requirements`
    accepts -- a model told a value is legal and then told it is unknown has
    no way to recover."""
    spec = tailor_tool_specs(_resume())[0]
    props = spec["parameters"]["properties"]["requirements"]["items"]["properties"]
    assert props["status"]["enum"] == list(STATUSES)


def test_the_map_tool_tells_the_model_not_to_score():
    """The whole point of the table: it describes what the resume says. The
    description is the only place a model reads this."""
    description = tailor_tool_specs(_resume())[0]["description"].lower()
    assert "not a score" in description
    assert "never" in description


def test_the_actions_tool_states_the_verbatim_and_unique_rule():
    """`before` is applied by a string search in the browser, so these two
    properties are the whole contract."""
    description = tailor_tool_specs(_resume())[1]["description"].lower()
    assert "character for character" in description
    assert "exactly once" in description


# --- the requirement table --------------------------------------------------


def test_a_clean_table_counts_by_status():
    out = map_requirements(
        SOURCE,
        [
            {"requirement": "三年以上後端經驗", "status": "in_resume"},
            {"requirement": "Kubernetes", "status": "partly_in_resume"},
            {"requirement": "Go", "status": "not_in_resume"},
            {"requirement": "溝通能力強", "status": "unclear"},
        ],
    )
    assert out["total"] == 4
    assert out["counts"] == {
        "in_resume": 1,
        "partly_in_resume": 1,
        "not_in_resume": 1,
        "unclear": 1,
    }
    assert out["dropped"] == []
    assert out["source_hash"] == source_hash(SOURCE)


def test_a_quote_that_is_in_the_resume_is_marked_found():
    out = map_requirements(
        SOURCE,
        [
            {
                "requirement": "英文書面溝通",
                "status": "in_resume",
                "resume_quote": "與美國團隊以英文協作",
            }
        ],
    )
    assert out["items"][0]["resume_quote_found"] is True


def test_a_quote_the_resume_does_not_contain_is_reported_not_dropped():
    """The row is a claim about the posting; the quote is supporting detail.
    Dropping real rows over an inexact quote is the mistake
    `app.services.jobs.reasons` made on its first production run."""
    out = map_requirements(
        SOURCE,
        [
            {
                "requirement": "Kubernetes",
                "status": "partly_in_resume",
                "resume_quote": "managed a Kubernetes fleet",
            }
        ],
    )
    assert out["total"] == 1
    assert out["items"][0]["resume_quote_found"] is False


def test_a_row_with_no_quote_reports_neither_found_nor_missing():
    out = map_requirements(SOURCE, [{"requirement": "Go", "status": "not_in_resume"}])
    assert out["items"][0]["resume_quote"] is None
    assert out["items"][0]["resume_quote_found"] is None


def test_structurally_broken_rows_are_dropped_and_said_so():
    out = map_requirements(
        SOURCE,
        [
            {"requirement": "ok", "status": "in_resume"},
            {"status": "in_resume"},
            {"requirement": "bad status", "status": "excellent"},
            "not an object",
        ],
    )
    assert out["total"] == 1
    assert [d["reason"] for d in out["dropped"]] == [
        "missing_requirement",
        "unknown_status",
        "not_an_object",
    ]


def test_prose_fields_are_clamped_rather_than_rejected():
    out = map_requirements(
        SOURCE,
        [{"requirement": "x" * 500, "status": "in_resume", "note": "y" * 500}],
    )
    row = out["items"][0]
    assert len(row["requirement"]) < 500
    assert len(row["note"]) < 500


def test_the_table_is_capped_and_says_when_it_truncated():
    rows = [{"requirement": f"r{i}", "status": "in_resume"} for i in range(MAX_REQUIREMENTS + 4)]
    out = map_requirements(SOURCE, rows)
    assert out["total"] == MAX_REQUIREMENTS
    assert out["truncated"] is True


def test_a_non_list_is_an_error_the_model_can_correct():
    assert "error" in map_requirements(SOURCE, {"requirement": "x"})


# --- the action list --------------------------------------------------------


def _actions(items, source: str = SOURCE):
    handlers = tailor_tool_handlers(None, _resume(source))
    return json.loads(handlers[PROPOSE_ACTIONS_TOOL]({"actions": items}))


def test_a_before_found_once_is_appliable():
    out = _actions(
        [
            {
                "title": "寫出 Kubernetes 的規模",
                "before": "  - 以 Docker 打包服務並部署至雲端",
                "after": "  - 以 Docker 打包服務，透過 Kubernetes 管理 40+ 個服務",
            }
        ]
    )
    row = out["items"][0]
    assert row["occurrences"] == 1
    assert row["appliable"] is True
    assert row["reason"] is None
    assert out["appliable"] == 1
    assert "hint" not in out


def test_a_before_the_resume_repeats_is_ambiguous_not_appliable():
    """Two matches and the browser cannot know which one the model meant, so
    the card must not offer a button for it."""
    out = _actions(
        [{"title": "x", "before": "  - 維護請款與對帳服務", "after": "  - 維護請款服務"}]
    )
    row = out["items"][0]
    assert row["occurrences"] == 2
    assert row["appliable"] is False
    assert row["reason"] == "before_ambiguous"
    assert "hint" in out


def test_a_before_that_is_not_there_is_missing():
    out = _actions([{"title": "x", "before": "nowhere in the draft", "after": "y"}])
    assert out["items"][0]["reason"] == "before_missing"
    assert out["items"][0]["occurrences"] == 0


def test_an_unchanged_pair_is_a_noop():
    out = _actions([{"title": "x", "before": "= 王小明", "after": "= 王小明"}])
    assert out["items"][0]["reason"] == "no_change"


def test_half_a_pair_is_dropped_with_a_reason():
    """A `before` with nothing to put in its place cannot be applied, and an
    `after` with no anchor cannot be placed. Neither is an open suggestion, so
    guessing which the model meant would be inventing one."""
    out = _actions(
        [
            {"title": "x", "before": "", "after": "y"},
            {"title": "y", "before": "= 王小明"},
        ]
    )
    assert out["items"] == []
    assert [d["reason"] for d in out["dropped"]] == [
        "after_without_before",
        "before_without_after",
    ]


def test_an_item_with_no_title_is_dropped_not_silently_skipped():
    out = _actions([{"before": "= 王小明", "after": "= 王大明"}])
    assert out["items"] == []
    assert out["dropped"][0]["reason"] == "missing_title"


def test_an_open_suggestion_is_kept_with_no_apply_button():
    """The shape that exists because the suggestions must answer every gap the
    table named -- and a gap is exactly the case with no `before` to copy."""
    out = _actions(
        [
            {
                "title": "技能區要不要補上 Go",
                "why": "履歷沒有任何 Go 的痕跡",
                "asks": ["我有用過 Go", "沒有 Go 經驗，跳過"],
            }
        ]
    )
    row = out["items"][0]
    assert row["kind"] == "open"
    assert row["appliable"] is False
    assert row["reason"] == "no_preview"
    assert row["before"] is None and row["after"] is None
    assert row["asks"] == ["我有用過 Go", "沒有 Go 經驗，跳過"]


def test_an_open_suggestion_does_not_trigger_the_fix_your_spans_hint():
    """Telling the model to go correct an open suggestion would push it to
    invent a `before`, which is the one thing it must not do for a gap."""
    out = _actions([{"title": "ask them about Go"}])
    assert out["items"][0]["kind"] == "open"
    assert "hint" not in out


def test_a_concrete_rewrite_needs_no_asks():
    """They are optional on purpose: an apply button is already the way in."""
    out = _actions(
        [{"title": "x", "before": "= 王小明", "after": "= 王大明"}]
    )
    assert out["items"][0]["kind"] == "edit"
    assert out["items"][0]["asks"] == []


def test_asks_are_clamped_capped_and_deduped():
    out = _actions(
        [
            {
                "title": "open",
                "asks": ["a" * 200, "keep", "keep", "b", "c", 7, ""],
            }
        ]
    )
    asks = out["items"][0]["asks"]
    assert len(asks) <= 3
    assert len(asks[0]) < 200
    assert asks.count("keep") == 1


def test_an_over_long_before_is_rejected_never_truncated():
    """`before` is a key, not prose: a truncated key is a guaranteed
    mismatch, so this is the one field the module refuses to clamp."""
    long = "a" * (BEFORE_MAX_CHARS + 1)
    out = _actions([{"title": "x", "before": long, "after": "y"}])
    row = out["items"][0]
    assert row["reason"] == "before_too_long"
    assert len(row["before"]) == len(long)


def test_the_cap_leaves_room_for_every_gap_the_table_can_name():
    """The table holds up to 12 rows and each gap needs an answer, so a cap of
    6 would have silently decided which gaps go unanswered."""
    assert MAX_ACTIONS >= 10


def test_unappliable_actions_are_kept_so_the_person_can_still_read_them():
    out = _actions(
        [
            {"title": "good", "before": "  - 與美國團隊以英文協作", "after": "  - 英文協作"},
            {"title": "bad", "before": "absent", "after": "y"},
        ]
    )
    assert out["total"] == 2
    assert out["appliable"] == 1
    assert [row["title"] for row in out["items"]] == ["good", "bad"]


def test_actions_carry_stable_ids_and_a_source_hash():
    out = _actions(
        [
            {"title": "a", "before": "= 王小明", "after": "= 王大明"},
            {"title": "b", "before": "absent", "after": "y"},
        ]
    )
    assert [row["id"] for row in out["items"]] == ["a1", "a2"]
    assert out["source_hash"] == source_hash(SOURCE)


def test_the_action_list_is_capped():
    items = [{"title": f"t{i}", "before": f"b{i}", "after": f"a{i}"} for i in range(MAX_ACTIONS + 2)]
    out = _actions(items)
    assert out["total"] == MAX_ACTIONS
    assert out["truncated"] is True


def test_cjk_survives_the_json_round_trip():
    """`ensure_ascii=False` throughout: a model handed \\u escapes of its own
    output has to decode them before it can reason about them."""
    handlers = tailor_tool_handlers(None, _resume())
    raw = handlers[PROPOSE_ACTIONS_TOOL](
        {"actions": [{"title": "改寫摘要", "before": "= 王小明", "after": "= 王大明"}]}
    )
    assert "改寫摘要" in raw


# --- the per-turn cap -------------------------------------------------------


def test_a_second_requirement_table_in_one_turn_is_refused():
    """Two tables in one thread reads as a bug, so the cap is real -- and it
    is threaded, because the chat router rebuilds handlers per tool call."""
    calls: list[str] = []
    resume = _resume()
    first = json.loads(
        tailor_tool_handlers(None, resume, mapped=calls)[MAP_REQUIREMENTS_TOOL](
            {"requirements": [{"requirement": "a", "status": "in_resume"}]}
        )
    )
    assert first["ok"] is True
    # A fresh build, the same list: this is exactly what chat.py does.
    second = json.loads(
        tailor_tool_handlers(None, resume, mapped=calls)[MAP_REQUIREMENTS_TOOL](
            {"requirements": [{"requirement": "b", "status": "in_resume"}]}
        )
    )
    assert "error" in second


def test_a_rejected_table_does_not_burn_the_turn_s_one_call():
    """A model whose first attempt was malformed must be able to try again."""
    calls: list[str] = []
    resume = _resume()
    bad = json.loads(
        tailor_tool_handlers(None, resume, mapped=calls)[MAP_REQUIREMENTS_TOOL](
            {"requirements": "not a list"}
        )
    )
    assert "error" in bad
    good = json.loads(
        tailor_tool_handlers(None, resume, mapped=calls)[MAP_REQUIREMENTS_TOOL](
            {"requirements": [{"requirement": "a", "status": "in_resume"}]}
        )
    )
    assert good["ok"] is True


def test_the_action_tool_is_not_capped():
    """The reply's `hint` asks a model with unmatched spans to read the source
    and call again -- a cap would make that instruction a lie."""
    resume = _resume()
    handlers = tailor_tool_handlers(None, resume)
    one = json.loads(handlers[PROPOSE_ACTIONS_TOOL]({"actions": [{"title": "a", "before": "absent", "after": "b"}]}))
    two = json.loads(handlers[PROPOSE_ACTIONS_TOOL]({"actions": [{"title": "a", "before": "= 王小明", "after": "= 王大明"}]}))
    assert one["ok"] is True
    assert two["ok"] is True
    assert two["appliable"] == 1
