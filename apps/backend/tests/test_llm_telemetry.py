"""Per-round token telemetry for the chat agent.

A turn makes up to nine model calls and the repair loop can run the whole turn
four times over, so these tests pin down what gets recorded, what purpose it is
recorded under, and -- most importantly -- that a missing or broken metric
never costs the user their answer.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.deps import SESSION_COOKIE, _sign
from app.models import LlmCall, User
from app.services.typst_compile import typst_available
from tests.openai_script import ScriptedOpenAI, llm_message, llm_usage, tool_call
from tests.test_chat import post_chat


def _calls(db_session) -> list[LlmCall]:
    """Every recorded call, in a deterministic order.

    Not ordered by created_at: several rows land inside the same clock tick, so
    the timestamps tie. (purpose, round_index) is what the assertions are about
    anyway.
    """
    db_session.rollback()
    rows = db_session.query(LlmCall).all()
    return sorted(rows, key=lambda row: (row.purpose, row.round_index))


def _skip_real_compiles(monkeypatch) -> None:
    """Keep `verify_compile` cheap and identical with or without the Typst CLI."""
    monkeypatch.setattr(
        "app.services.llm.compile_digest", lambda source: {"compile": {"ok": True}}
    )


def test_each_round_records_its_own_token_counts(
    client: TestClient, db_session, openai_enabled, monkeypatch
):
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    original = created["typst_source"]
    _skip_real_compiles(monkeypatch)
    scripted = ScriptedOpenAI(
        [
            llm_message(
                tool_calls=[tool_call("read_typst", {}, "read-1")],
                usage=llm_usage(input_tokens=1850, cached_tokens=0, output_tokens=40),
            ),
            llm_message(
                tool_calls=[
                    tool_call(
                        "apply_typst_edit",
                        {"search": "Your Name", "replace": "Ada Lovelace"},
                        "edit-1",
                    )
                ],
                usage=llm_usage(
                    input_tokens=5200,
                    cached_tokens=1792,
                    output_tokens=310,
                    reasoning_tokens=256,
                    total_tokens=5510,
                ),
            ),
            llm_message(
                content="Renamed you.",
                usage=llm_usage(input_tokens=6100, cached_tokens=5120, output_tokens=25),
            ),
        ]
    )
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted)

    result = post_chat(client, created["id"], json={"message": "Rename me to Ada Lovelace"})
    assert result["status"] == 200
    assert "Ada Lovelace" in result["json"]["typst_source"]

    rows = _calls(db_session)
    assert [row.purpose for row in rows] == ["chat", "chat", "chat"]
    assert [row.round_index for row in rows] == [0, 1, 2]
    assert [row.resume_id for row in rows] == [created["id"]] * 3
    assert [row.model for row in rows] == ["gpt-5.6-terra"] * 3
    assert [row.prefer_full_source for row in rows] == [False, False, False]
    assert [row.input_tokens for row in rows] == [1850, 5200, 6100]
    assert [row.cached_tokens for row in rows] == [0, 1792, 5120]
    assert [row.output_tokens for row in rows] == [40, 310, 25]
    # Only the middle round reported reasoning tokens; the others stay unknown
    # rather than being recorded as zero.
    assert [row.reasoning_tokens for row in rows] == [None, 256, None]
    # The edit lands between round 1 and round 2, so the last round is billed
    # for the longer document. That correlation is the point of the column.
    assert [row.source_chars for row in rows] == [
        len(original),
        len(original),
        len(original) + len("Ada Lovelace") - len("Your Name"),
    ]
    assert rows[-1].created_at is not None


def test_usage_is_persisted_without_an_sse_frame(
    client: TestClient, db_session, openai_enabled, monkeypatch
):
    """The client never learns about this event type."""
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    scripted = ScriptedOpenAI(
        [llm_message(content="Nothing to change.", usage=llm_usage(input_tokens=1900))]
    )
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted)

    result = post_chat(client, created["id"], json={"message": "Looks good?"})
    assert result["status"] == 200
    assert "usage" not in {event.get("type") for event in result["events"]}
    assert result["kinds"] == ["assistant", "done"]
    blob = json.dumps(result["events"])
    assert "1900" not in blob
    assert len(_calls(db_session)) == 1


def test_repair_rounds_record_as_repair(
    client: TestClient, db_session, openai_enabled, monkeypatch
):
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    _skip_real_compiles(monkeypatch)
    rewritten = created["typst_source"].replace("Your Name", "Ada Lovelace", 1)
    repaired = rewritten + "\n// repaired\n"
    # First check fails, which is what sends the whole turn round again; the
    # second passes, which ends the loop.
    verdicts = [{"ok": False, "error": "error: unknown variable"}, {"ok": True}]
    monkeypatch.setattr(
        "app.routers.chat.compile_status", lambda source: verdicts.pop(0)
    )
    scripted = ScriptedOpenAI(
        [
            llm_message(
                tool_calls=[tool_call("read_typst", {}, "read-1")],
                usage=llm_usage(input_tokens=1850, cached_tokens=0),
            ),
            llm_message(
                tool_calls=[
                    tool_call(
                        "apply_typst_edit",
                        {"source": rewritten, "search": "", "replace": ""},
                        "edit-1",
                    )
                ],
                usage=llm_usage(input_tokens=5200, cached_tokens=1792),
            ),
            llm_message(
                content="Renamed you.", usage=llm_usage(input_tokens=6100, cached_tokens=5120)
            ),
            # The repair turn starts over from round 0.
            llm_message(
                tool_calls=[
                    tool_call(
                        "apply_typst_edit",
                        {"source": repaired, "search": "", "replace": ""},
                        "edit-2",
                    )
                ],
                usage=llm_usage(input_tokens=7400, cached_tokens=0, output_tokens=900),
            ),
            llm_message(
                content="Fixed the compile error.",
                usage=llm_usage(input_tokens=8100, cached_tokens=6400),
            ),
        ]
    )
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted)

    result = post_chat(client, created["id"], json={"message": "Rename me to Ada Lovelace"})
    assert result["status"] == 200
    assert "// repaired" in result["json"]["typst_source"]
    assert verdicts == []

    rows = _calls(db_session)
    assert [(row.purpose, row.round_index) for row in rows] == [
        ("chat", 0),
        ("chat", 1),
        ("chat", 2),
        ("repair", 0),
        ("repair", 1),
    ]
    # The repair loop always takes the expensive path, and that is exactly what
    # makes it the suspected cost tail -- so the flag has to be on its rows.
    assert [row.prefer_full_source for row in rows] == [False, False, False, True, True]
    assert [row.input_tokens for row in rows] == [1850, 5200, 6100, 7400, 8100]
    repair_rows = [row for row in rows if row.purpose == "repair"]
    assert sum(row.input_tokens for row in repair_rows) == 15500
    # The repair rounds see the rewritten document, not the starter.
    assert all(row.source_chars >= len(rewritten) for row in repair_rows)


def test_a_response_without_usage_still_records_the_call(
    client: TestClient, db_session, openai_enabled, monkeypatch
):
    """A proxy that reports nothing must still leave a countable row."""
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    scripted = ScriptedOpenAI([llm_message(content="Done.")])
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted)

    result = post_chat(client, created["id"], json={"message": "Anything to fix?"})
    assert result["status"] == 200
    assert result["json"]["message"]["content"] == "Done."

    rows = _calls(db_session)
    assert len(rows) == 1
    assert rows[0].purpose == "chat"
    assert rows[0].input_tokens is None
    assert rows[0].cached_tokens is None
    assert rows[0].output_tokens is None
    assert rows[0].reasoning_tokens is None


@pytest.mark.parametrize(
    "usage",
    [
        # A proxy that reports the totals but not the details.
        {"input_tokens": 4000, "output_tokens": 120},
        # ...or reports them as strings, or as something else entirely.
        {"input_tokens": "4000", "input_tokens_details": "cached", "output_tokens": None},
        {"input_tokens": 4000, "input_tokens_details": {"cached_tokens": True}},
    ],
)
def test_an_unexpected_usage_shape_is_read_for_what_it_has(
    client: TestClient, db_session, openai_enabled, monkeypatch, usage
):
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    scripted = ScriptedOpenAI([llm_message(content="Done.", usage=usage)])
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted)

    result = post_chat(client, created["id"], json={"message": "Anything to fix?"})
    assert result["status"] == 200
    assert result["json"]["message"]["content"] == "Done."

    rows = _calls(db_session)
    assert len(rows) == 1
    # Whatever came back, nothing was invented: an unreadable field is None,
    # never a guess and never an exception.
    assert rows[0].cached_tokens is None
    assert rows[0].input_tokens in {4000, None}


def _explode_on_construction(**kwargs):
    """Telemetry that cannot even be built: the Session never sees the row."""
    raise RuntimeError("llm_calls is on fire")


def _explode_on_commit(**kwargs):
    """Telemetry the database refuses once it is flushed.

    The constructor and `db.add()` both succeed, so the failure lands inside
    the transaction -- which is the case that leaves SQLAlchemy wanting a
    rollback and would otherwise poison every write the rest of the turn makes
    through the same Session. `purpose` is only checked by the CHECK
    constraint, so this is the smallest insert the schema rejects.
    """
    return LlmCall(**{**kwargs, "purpose": "not-a-purpose"})


@pytest.mark.parametrize("broken", [_explode_on_construction, _explode_on_commit])
def test_a_broken_telemetry_insert_does_not_break_the_turn(
    client: TestClient, db_session, openai_enabled, monkeypatch, broken
):
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    _skip_real_compiles(monkeypatch)

    monkeypatch.setattr("app.routers.chat.LlmCall", broken)
    scripted = ScriptedOpenAI(
        [
            llm_message(
                tool_calls=[tool_call("read_typst", {}, "read-1")],
                usage=llm_usage(input_tokens=1850),
            ),
            llm_message(
                tool_calls=[
                    tool_call(
                        "apply_typst_edit",
                        {"search": "Your Name", "replace": "Ada Lovelace"},
                        "edit-1",
                    )
                ],
                usage=llm_usage(input_tokens=5200),
            ),
            llm_message(content="Renamed you.", usage=llm_usage(input_tokens=6100)),
        ]
    )
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted)

    result = post_chat(client, created["id"], json={"message": "Rename me to Ada Lovelace"})
    # A lost metric, not a lost answer: the edit, the trace rows and the
    # assistant message all still land on the same Session that just failed.
    assert result["status"] == 200
    assert result["error"] is None
    assert result["kinds"] == ["tool", "tool", "source", "assistant", "done"]
    assert "Ada Lovelace" in result["json"]["typst_source"]
    assert result["json"]["applied"] is True
    assert result["json"]["message"]["content"] == "Renamed you."
    assert _calls(db_session) == []


def test_a_vision_answer_is_billed_separately_from_its_round(
    client: TestClient, db_session, openai_enabled, monkeypatch
):
    """The same wiring as the test below, with the compile stubbed out.

    `read_layout` needs the Typst CLI to render the pages it asks about, so
    this stands in for that test wherever the binary is missing.
    """
    created = client.post("/v1/resumes", json={"locale": "en"}).json()

    async def stubbed_report(source, args):
        return {
            "pages": 1,
            "visual": {
                "model": "gpt-6-luna",
                "pages_seen": [1],
                "answers": [{"question": "Crowded?", "probability": 0.12}],
                "input_tokens": 2521,
            },
        }

    monkeypatch.setattr("app.services.llm.read_layout_report", stubbed_report)
    scripted = ScriptedOpenAI(
        [
            llm_message(
                tool_calls=[
                    tool_call("read_layout", {"questions": ["Crowded?"]}, "layout-1")
                ],
                usage=llm_usage(input_tokens=2000),
            ),
            llm_message(content="It does not.", usage=llm_usage(input_tokens=4000)),
        ]
    )
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted)

    result = post_chat(client, created["id"], json={"message": "Does this look crowded?"})
    assert result["status"] == 200

    rows = _calls(db_session)
    assert [(row.purpose, row.round_index) for row in rows] == [
        ("chat", 0),
        ("chat", 1),
        ("vision", 0),
    ]
    vision = rows[-1]
    assert vision.input_tokens == 2521
    assert vision.model == "gpt-6-luna"
    # Input tokens are the whole bill on the decisions endpoint; the rest is
    # unknown rather than zero.
    assert vision.output_tokens is None
    assert vision.cached_tokens is None
    # The round that asked still records its own spend, unchanged.
    assert rows[0].input_tokens == 2000


@pytest.mark.parametrize(
    "visual, expected_tokens",
    [
        # The endpoint answered but reported no usage at all.
        ({"model": "gpt-6-luna", "pages_seen": [1], "answers": []}, None),
        # ...or reported it as a string, which the reader refuses to guess at.
        (
            {"model": "gpt-6-luna", "pages_seen": [1], "answers": [], "input_tokens": "2521"},
            None,
        ),
        # ...or reported it readably, the ordinary case.
        (
            {"model": "gpt-6-luna", "pages_seen": [1], "answers": [], "input_tokens": 2521},
            2521,
        ),
    ],
)
def test_a_vision_call_that_reported_nothing_still_records(
    client: TestClient, db_session, openai_enabled, monkeypatch, visual, expected_tokens
):
    """The vision call ran, so it is countable even when its bill is unknown.

    This is the same promise the chat rounds keep: a response that carried no
    readable usage is a lost metric, not a lost call. A row with an unknown
    input count is what tells the dashboard the spend is unmeasurable, instead
    of quietly reporting the turn as one call cheaper than it was.
    """
    created = client.post("/v1/resumes", json={"locale": "en"}).json()

    async def stubbed_report(source, args):
        return {"pages": 1, "visual": visual}

    monkeypatch.setattr("app.services.llm.read_layout_report", stubbed_report)
    scripted = ScriptedOpenAI(
        [
            llm_message(
                tool_calls=[tool_call("read_layout", {"questions": ["Crowded?"]}, "layout-1")],
                usage=llm_usage(input_tokens=2000),
            ),
            llm_message(content="It does not.", usage=llm_usage(input_tokens=4000)),
        ]
    )
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted)

    result = post_chat(client, created["id"], json={"message": "Does this look crowded?"})
    assert result["status"] == 200

    rows = _calls(db_session)
    assert [(row.purpose, row.round_index) for row in rows] == [
        ("chat", 0),
        ("chat", 1),
        ("vision", 0),
    ]
    vision = rows[-1]
    assert vision.input_tokens == expected_tokens
    assert vision.model == "gpt-6-luna"


@pytest.mark.parametrize(
    "report",
    [
        # read_layout answered from geometry alone: no vision call was made.
        {"pages": 1},
        {"pages": 1, "visual": None},
        # vision_report gave up before it had a response to read, so nothing
        # was billed -- the same silence a failed chat request leaves behind.
        {"pages": 1, "visual": {"skipped": "llm_not_configured"}},
        {"pages": 1, "visual": {"error": "The resume rendered no pages."}},
        {"pages": 1, "visual": {"error": "Decisions request failed", "model": "gpt-6-luna"}},
    ],
)
def test_a_vision_call_that_never_happened_records_nothing(
    client: TestClient, db_session, openai_enabled, monkeypatch, report
):
    created = client.post("/v1/resumes", json={"locale": "en"}).json()

    async def stubbed_report(source, args):
        return report

    monkeypatch.setattr("app.services.llm.read_layout_report", stubbed_report)
    scripted = ScriptedOpenAI(
        [
            llm_message(
                tool_calls=[tool_call("read_layout", {"questions": ["Crowded?"]}, "layout-1")],
                usage=llm_usage(input_tokens=2000),
            ),
            llm_message(content="It does not.", usage=llm_usage(input_tokens=4000)),
        ]
    )
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted)

    result = post_chat(client, created["id"], json={"message": "Does this look crowded?"})
    assert result["status"] == 200
    assert [row.purpose for row in _calls(db_session)] == ["chat", "chat"]


@pytest.mark.skipif(not typst_available(), reason="Typst CLI is required")
def test_the_read_layout_vision_call_records_as_vision(
    client: TestClient, db_session, openai_enabled, monkeypatch
):
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    scripted = ScriptedOpenAI(
        [
            llm_message(
                tool_calls=[
                    tool_call(
                        "read_layout",
                        {"questions": ["Does the page look crowded?"]},
                        "layout-1",
                    )
                ],
                usage=llm_usage(input_tokens=2000),
            ),
            llm_message(content="It does not.", usage=llm_usage(input_tokens=4000)),
        ],
        decisions={
            "answers": [{"type": "predicate", "name": "q1", "probability": 0.12}],
            "usage": {"input_tokens": 2521},
        },
    )
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted)

    result = post_chat(client, created["id"], json={"message": "Does this look crowded?"})
    assert result["status"] == 200

    rows = _calls(db_session)
    assert [row.purpose for row in rows] == ["chat", "chat", "vision"]
    vision = rows[-1]
    assert vision.input_tokens == 2521
    # The decisions endpoint bills input tokens only, and it is a different
    # model from the one running the turn.
    assert vision.output_tokens is None
    assert vision.model == "gpt-6-luna"
    assert vision.round_index == 0


def test_admin_overview_reports_daily_tokens_and_the_cache_hit_rate(
    client: TestClient, db_session, monkeypatch
):
    monkeypatch.setenv("ADMIN_EMAILS", "ops@example.com")
    get_settings.cache_clear()
    ops = User(google_sub="sub-ops", email="ops@example.com", locale="en")
    db_session.add(ops)
    db_session.commit()
    now = datetime.now(timezone.utc)
    db_session.add_all(
        [
            LlmCall(
                purpose="chat",
                model="gpt-5.6-terra",
                round_index=0,
                input_tokens=1000,
                cached_tokens=250,
                output_tokens=100,
                reasoning_tokens=60,
                source_chars=5900,
                created_at=now,
            ),
            LlmCall(
                purpose="repair",
                model="gpt-5.6-terra",
                round_index=1,
                prefer_full_source=True,
                input_tokens=3000,
                cached_tokens=750,
                output_tokens=400,
                source_chars=6100,
                created_at=now,
            ),
            # Reported nothing. It counts as a call and adds nothing to the
            # token totals, so it cannot drag the hit rate down either.
            LlmCall(purpose="chat", model="proxy", round_index=2, created_at=now),
            # A proxy that bills input but never says how much of it was
            # cached. Its tokens belong in the totals; counting them as a 0%
            # hit would report broken caching off a number nobody measured.
            LlmCall(
                purpose="chat",
                model="proxy",
                round_index=3,
                input_tokens=8000,
                output_tokens=200,
                created_at=now,
            ),
            # Same story for every vision call: the decisions endpoint reports
            # input tokens and nothing else, by design.
            LlmCall(
                purpose="vision",
                model="gpt-6-luna",
                round_index=0,
                input_tokens=2521,
                created_at=now,
            ),
            # Yesterday, so it must not land in today's bucket.
            LlmCall(
                purpose="chat",
                model="gpt-5.6-terra",
                round_index=0,
                input_tokens=7000,
                cached_tokens=7000,
                created_at=now - timedelta(days=1),
            ),
        ]
    )
    db_session.commit()

    res = client.get(
        "/v1/admin/overview",
        cookies={SESSION_COOKIE: _sign(ops.id, get_settings().auth_token_secret)},
    )
    assert res.status_code == 200
    series = res.json()["series"]
    today = series[-1]
    assert today["llm_calls"] == 5
    assert today["llm_input_tokens"] == 14521
    assert today["llm_cached_tokens"] == 1000
    assert today["llm_output_tokens"] == 700
    assert today["llm_reasoning_tokens"] == 60
    # 1000 / 4000: only the two rows that reported both halves of the rate are
    # in it. Over every row that billed input it would read 0.0689 -- a figure
    # that says "caching is broken" about calls whose cache status was never
    # reported.
    assert today["llm_cache_hit_rate"] == 0.25

    yesterday = series[-2]
    assert yesterday["llm_calls"] == 1
    assert yesterday["llm_cache_hit_rate"] == 1.0

    quiet = series[0]
    assert quiet["llm_calls"] == 0
    assert quiet["llm_input_tokens"] == 0
    # No input tokens is no data, not a 0% hit rate.
    assert quiet["llm_cache_hit_rate"] is None


def test_a_day_that_reported_no_cached_tokens_has_no_hit_rate(
    client: TestClient, db_session, monkeypatch
):
    """Input billed, cache status never reported: still "no data", not 0%.

    A day of vision-only traffic, or of traffic through a proxy that drops
    `input_tokens_details`, is exactly the case the hit rate must stay silent
    about -- 0% reads as "caching is broken".
    """
    monkeypatch.setenv("ADMIN_EMAILS", "ops@example.com")
    get_settings.cache_clear()
    ops = User(google_sub="sub-ops", email="ops@example.com", locale="en")
    db_session.add(ops)
    db_session.commit()
    now = datetime.now(timezone.utc)
    db_session.add_all(
        [
            LlmCall(
                purpose="vision",
                model="gpt-6-luna",
                round_index=0,
                input_tokens=2521,
                created_at=now,
            ),
            LlmCall(
                purpose="chat",
                model="proxy",
                round_index=0,
                input_tokens=4000,
                output_tokens=120,
                created_at=now,
            ),
        ]
    )
    db_session.commit()

    res = client.get(
        "/v1/admin/overview",
        cookies={SESSION_COOKIE: _sign(ops.id, get_settings().auth_token_secret)},
    )
    assert res.status_code == 200
    today = res.json()["series"][-1]
    assert today["llm_calls"] == 2
    assert today["llm_input_tokens"] == 6521
    assert today["llm_cached_tokens"] == 0
    assert today["llm_cache_hit_rate"] is None
