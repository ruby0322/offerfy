"""The Anthropic adapter, through a full chat turn.

Driven end to end rather than unit-tested in pieces, because the point of the
provider split is that the *product* behaves the same on both vendors -- tool
dispatch, the edit being applied, the retry nudge, the telemetry row. A test
that only checked the request body would pass while the turn did nothing.

What is unit-tested on its own is the shape translation (`tool_specs`) and the
usage mapping, because both are silent when wrong: a mistranslated tool means
the model stops calling tools, and a mis-mapped usage field means a cached
read bills as fresh input.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.models import LlmCall
from app.services import model_catalog
from app.services.llm.providers import anthropic as adapter
from tests.anthropic_script import (
    ScriptedAnthropic,
    anthropic_message,
    anthropic_usage,
    server_tool_use_block,
    text_block,
    thinking_block,
    tool_use_block,
    web_search_result_block,
)
from tests.test_chat import post_chat

SONNET = "claude-sonnet-5-5"


@pytest.fixture
def on_sonnet(client: TestClient, anthropic_enabled, db_session):
    """A signed-in caller who has picked Sonnet.

    Signed in because `claude-sonnet-5-5` has `min_tier="registered"`: a guest
    may not pick it, which is itself asserted in test_model_catalog.
    """
    from tests.test_usage import _signed_in

    user = _signed_in(client, db_session, sub="sub-anthropic")
    assert client.put("/v1/models/selection", json={"model": SONNET}).status_code == 200
    return user


# --------------------------------------------------------------------------
# Shape translation
# --------------------------------------------------------------------------


def test_tools_translate_to_input_schema():
    from app.services.llm.tools import TOOLS

    translated = adapter.tool_specs(TOOLS)
    by_name = {tool.get("name"): tool for tool in translated}

    # Every function tool keeps its name and description and gains an
    # input_schema. `parameters` must not survive -- the API rejects it.
    read = by_name["read_typst"]
    assert "input_schema" in read and "parameters" not in read
    assert read["input_schema"]["type"] == "object"
    assert read["description"]

    edit = by_name["apply_typst_edit"]
    assert edit["input_schema"]["properties"]["search"] == {"type": "string"}

    # The neutral `{"type": "web_search"}` becomes the dated server tool.
    search = by_name["web_search"]
    assert search["type"] == "web_search_20260209"
    # And the standalone code-execution tool is NOT added alongside it: the
    # dated web-search version runs code itself, and a second execution
    # environment confuses the model.
    assert not any(
        str(tool.get("type") or "").startswith("code_execution") for tool in translated
    )


def test_a_tool_with_no_parameters_still_gets_a_schema():
    """`input_schema` is required, so a missing one cannot be passed through."""
    translated = adapter.tool_specs([{"type": "function", "name": "bare"}])
    assert translated == [{"name": "bare", "input_schema": {"type": "object", "properties": {}}}]


def test_a_native_tool_is_passed_through():
    native = {"name": "already", "input_schema": {"type": "object"}}
    assert adapter.tool_specs([native]) == [native]


@pytest.mark.parametrize(
    "base,expected",
    [
        ("https://api.anthropic.com/v1", "https://api.anthropic.com/v1/messages"),
        ("https://api.anthropic.com/v1/", "https://api.anthropic.com/v1/messages"),
        ("https://proxy.example", "https://proxy.example/v1/messages"),
        # Already complete: do not append a second /messages.
        ("https://proxy.example/v1/messages", "https://proxy.example/v1/messages"),
    ],
)
def test_the_endpoint_is_derived_not_assumed(base, expected):
    assert adapter.messages_url(base) == expected


# --------------------------------------------------------------------------
# Usage mapping
# --------------------------------------------------------------------------


def test_cached_reads_are_added_into_the_input_total():
    """The field that bills wrong if it is copied across verbatim.

    Anthropic's `input_tokens` excludes the cached slice; the Responses API's
    includes it. `app.services.usage` subtracts the cached slice back out of
    `input_tokens`, so the adapter has to hand it the inclusive total or every
    cached read on this provider is billed at the full input rate.
    """
    counts = adapter._usage_counts(
        anthropic_usage(
            input_tokens=2_000,
            cache_read_input_tokens=9_000,
            cache_creation_input_tokens=2_500,
        ),
        {"output_tokens": 140},
    )
    assert counts["input_tokens"] == 11_000
    assert counts["cached_tokens"] == 9_000
    # Not a slice of anything: an extra charge on top of the read.
    assert counts["cache_write_tokens"] == 2_500
    assert counts["output_tokens"] == 140
    # No reasoning line on this API; thinking bills inside output_tokens, so
    # recording it again would charge the thinking twice.
    assert counts["reasoning_tokens"] is None


def test_the_final_output_count_wins_over_the_running_one():
    counts = adapter._usage_counts(
        anthropic_usage(input_tokens=10, output_tokens=1), {"output_tokens": 310}
    )
    assert counts["output_tokens"] == 310


def test_usage_we_cannot_read_is_unknown_not_zero():
    """A proxy that drops the block must not make the turn free."""
    counts = adapter._usage_counts(None, None)
    assert counts == {
        "input_tokens": None,
        "cached_tokens": None,
        "cache_write_tokens": None,
        "output_tokens": None,
        "reasoning_tokens": None,
    }


# --------------------------------------------------------------------------
# A whole turn
# --------------------------------------------------------------------------


def test_a_turn_reads_edits_and_answers(client: TestClient, on_sonnet, monkeypatch):
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    assert "Your Name" in created["typst_source"]

    scripted = ScriptedAnthropic(
        [
            anthropic_message(
                blocks=[
                    thinking_block("Let me look at the document."),
                    tool_use_block("read_typst", {}, "toolu_read"),
                ],
                usage=anthropic_usage(input_tokens=1_800, cache_read_input_tokens=0),
                output_tokens=40,
            ),
            anthropic_message(
                blocks=[
                    tool_use_block(
                        "apply_typst_edit",
                        {"search": "Your Name", "replace": "Ada Lovelace"},
                        "toolu_edit",
                    )
                ],
                usage=anthropic_usage(input_tokens=600, cache_read_input_tokens=1_800),
                output_tokens=90,
            ),
            anthropic_message(
                blocks=[text_block("Renamed you to Ada Lovelace.")],
                usage=anthropic_usage(input_tokens=400, cache_read_input_tokens=2_400),
                output_tokens=20,
            ),
        ]
    )
    monkeypatch.setattr(
        "app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted
    )

    result = post_chat(client, created["id"], json={"message": "Rename me"})
    assert result["status"] == 200, result
    assert "Ada Lovelace" in result["json"]["typst_source"]
    assert result["json"]["applied"] is True
    assert "Ada Lovelace" in (result["json"]["message"] or {}).get("content", "")
    # The thinking was streamed as reasoning, not folded into the answer.
    reasoning = "".join(
        event.get("text") or ""
        for event in result["events"]
        if event.get("type") == "reasoning"
    )
    assert "look at the document" in reasoning

    # Three requests, and the model is the one that was picked.
    assert len(scripted.requests) == 3
    assert {body["model"] for body in scripted.requests} == {SONNET}


def test_the_request_carries_what_this_api_requires(
    client: TestClient, on_sonnet, monkeypatch
):
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    scripted = ScriptedAnthropic([anthropic_message(blocks=[text_block("done")])])
    monkeypatch.setattr(
        "app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted
    )
    post_chat(client, created["id"], json={"message": "hi"})

    body = scripted.requests[0]
    # The four shape differences from the Responses API, all in one place.
    assert body["max_tokens"] == adapter.MAX_TOKENS  # required, no default
    # `system` is a block list, not a string, and that is not cosmetic: it is
    # the only place the cache breakpoint can go. See the next test.
    assert "You edit a Typst resume" in body["system"][0]["text"]
    assert "instructions" not in body
    assert body["messages"][0]["role"] == "user"
    assert body["stream"] is True
    assert body["tool_choice"] == {"type": "auto"}
    # Thinking asked for explicitly, and visibly: the default is `omitted`,
    # which would stream empty thinking text and show the user a long pause.
    assert body["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert body["output_config"] == {"effort": "low"}

    headers = scripted.headers[0]
    # x-api-key, not a bearer token.
    assert headers["x-api-key"] == "sk-ant-test"
    assert "Authorization" not in headers
    assert headers["anthropic-version"] == adapter.API_VERSION
    assert scripted.urls[0].endswith("/v1/messages")


def test_tool_results_go_back_as_blocks_in_one_user_message(
    client: TestClient, on_sonnet, monkeypatch
):
    """The shape most likely to be got wrong, and the API rejects it outright.

    A `tool_use` with no matching `tool_result` in the next message is a 400,
    and splitting parallel results across several messages trains the model to
    stop making parallel calls -- which costs this product rounds.
    """
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    scripted = ScriptedAnthropic(
        [
            anthropic_message(
                blocks=[
                    tool_use_block("read_typst", {}, "toolu_a"),
                    tool_use_block("read_ats", {}, "toolu_b"),
                ]
            ),
            anthropic_message(blocks=[text_block("Looks fine.")]),
        ]
    )
    monkeypatch.setattr(
        "app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted
    )
    post_chat(client, created["id"], json={"message": "check it"})

    messages = scripted.requests[1]["messages"]
    # The assistant turn goes back verbatim, thinking signatures and all.
    assistant = messages[-2]
    assert assistant["role"] == "assistant"
    assert [block["type"] for block in assistant["content"]] == ["tool_use", "tool_use"]

    # Both results in ONE user message, each paired by tool_use_id.
    results = messages[-1]
    assert results["role"] == "user"
    assert [block["type"] for block in results["content"]] == [
        "tool_result",
        "tool_result",
    ]
    assert [block["tool_use_id"] for block in results["content"]] == [
        "toolu_a",
        "toolu_b",
    ]


def test_tool_arguments_are_reassembled_from_their_fragments(
    client: TestClient, on_sonnet, monkeypatch
):
    """`input_json_delta` is the only place a tool's arguments arrive.

    The terminal event does not restate them, so an adapter that read the
    finished message would call every tool with `{}`. The scripted stream
    chops the JSON into 7-character pieces to make sure that is exercised.
    """
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    long_replace = "Ada Lovelace, Countess of Lovelace, Analytical Engine"
    scripted = ScriptedAnthropic(
        [
            anthropic_message(
                blocks=[
                    tool_use_block(
                        "apply_typst_edit",
                        {"search": "Your Name", "replace": long_replace},
                        "toolu_edit",
                    )
                ]
            ),
            anthropic_message(blocks=[text_block("Done.")]),
        ]
    )
    monkeypatch.setattr(
        "app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted
    )
    result = post_chat(client, created["id"], json={"message": "rename"})
    assert long_replace in result["json"]["typst_source"]


def test_a_server_side_search_becomes_a_tool_card(
    client: TestClient, on_sonnet, monkeypatch
):
    """Web search is reported as two blocks linked by id, not as one call."""
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    scripted = ScriptedAnthropic(
        [
            anthropic_message(
                blocks=[
                    server_tool_use_block("Ada Lovelace employer", "srv_1"),
                    web_search_result_block(
                        [("https://example.com/ada", "Ada")], "srv_1"
                    ),
                    text_block("Found it."),
                ]
            )
        ]
    )
    monkeypatch.setattr(
        "app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted
    )
    result = post_chat(client, created["id"], json={"message": "look it up"})

    tools = [
        event["message"] for event in result["events"] if event.get("type") == "tool"
    ]
    import json as _json

    traces = [_json.loads(row["content"]) for row in tools]
    search = next(trace for trace in traces if trace["name"] == "web_search")
    assert search["arguments"]["query"] == "Ada Lovelace employer"
    assert search["result"]["sources"] == [
        {"url": "https://example.com/ada", "title": "Ada"}
    ]


def test_a_failed_search_is_reported_rather_than_raised(
    client: TestClient, on_sonnet, monkeypatch
):
    """The error shape is an object where a success is a list.

    Indexing it would raise on the one case worth reporting, and a search that
    hit its use limit is not a reason to fail the user's turn.
    """
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    scripted = ScriptedAnthropic(
        [
            anthropic_message(
                blocks=[
                    server_tool_use_block("anything", "srv_1"),
                    web_search_result_block([], "srv_1", error_code="max_uses_exceeded"),
                    text_block("Could not search."),
                ]
            )
        ]
    )
    monkeypatch.setattr(
        "app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted
    )
    result = post_chat(client, created["id"], json={"message": "look it up"})
    assert result["status"] == 200
    assert result["error"] is None
    import json as _json

    traces = [
        _json.loads(event["message"]["content"])
        for event in result["events"]
        if event.get("type") == "tool"
    ]
    search = next(trace for trace in traces if trace["name"] == "web_search")
    assert search["result"]["status"] == "max_uses_exceeded"
    assert search["result"]["sources"] == []


def test_each_round_is_billed_to_the_chosen_model(
    client: TestClient, on_sonnet, db_session, monkeypatch
):
    """Telemetry has to name the model that actually ran, or the cost report
    prices a Sonnet turn at the default model's rates."""
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    scripted = ScriptedAnthropic(
        [
            anthropic_message(
                blocks=[tool_use_block("read_typst", {}, "toolu_read")],
                usage=anthropic_usage(
                    input_tokens=2_000,
                    cache_read_input_tokens=9_000,
                    cache_creation_input_tokens=2_500,
                ),
                output_tokens=140,
            ),
            anthropic_message(
                blocks=[text_block("Looks good.")],
                usage=anthropic_usage(input_tokens=100, cache_read_input_tokens=11_000),
                output_tokens=20,
            ),
        ]
    )
    monkeypatch.setattr(
        "app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted
    )
    post_chat(client, created["id"], json={"message": "have a look"})

    rows = (
        db_session.query(LlmCall)
        .filter(LlmCall.purpose == "chat")
        .order_by(LlmCall.round_index.asc())
        .all()
    )
    assert [row.model for row in rows] == [SONNET, SONNET]
    first = rows[0]
    assert first.input_tokens == 11_000  # inclusive of the cached slice
    assert first.cached_tokens == 9_000
    assert first.cache_write_tokens == 2_500
    assert first.output_tokens == 140


def test_a_truncated_stream_is_not_an_empty_answer(
    client: TestClient, on_sonnet, monkeypatch
):
    """This turn applies edits to the user's document. Acting on a knowingly
    incomplete response risks writing a half-rewritten resume."""
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    scripted = ScriptedAnthropic(
        [anthropic_message(blocks=[text_block("partial")])], truncate=True
    )
    monkeypatch.setattr(
        "app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted
    )
    result = post_chat(client, created["id"], json={"message": "hi"})
    assert result["error"] is not None
    assert result["error"]["status"] == 502


def test_a_provider_error_is_relayed_not_swallowed(
    client: TestClient, on_sonnet, monkeypatch
):
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    scripted = ScriptedAnthropic([], status_code=429)
    monkeypatch.setattr(
        "app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted
    )
    result = post_chat(client, created["id"], json={"message": "hi"})
    assert result["error"]["status"] == 502
    assert "nope" in result["error"]["detail"]


def test_the_edit_retry_nudge_works_on_this_provider_too(
    client: TestClient, on_sonnet, monkeypatch
):
    """The product's own behaviour, shared by both adapters.

    A `changed=false` edit has to be nudged rather than reported to the user,
    and the nudge arrives as a user message after the tool results -- the one
    role either API allows there.
    """
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    scripted = ScriptedAnthropic(
        [
            anthropic_message(
                blocks=[
                    tool_use_block(
                        "apply_typst_edit",
                        {"search": "nothing like this exists", "replace": "x"},
                        "toolu_miss",
                    )
                ]
            ),
            anthropic_message(blocks=[text_block("I will try again.")]),
            anthropic_message(
                blocks=[
                    tool_use_block(
                        "apply_typst_edit",
                        {"search": "Your Name", "replace": "Ada"},
                        "toolu_hit",
                    )
                ]
            ),
            anthropic_message(blocks=[text_block("Renamed.")]),
        ]
    )
    monkeypatch.setattr(
        "app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted
    )
    result = post_chat(client, created["id"], json={"message": "rename"})
    assert "Ada" in result["json"]["typst_source"]

    nudges = [
        message
        for body in scripted.requests
        for message in body["messages"]
        if message["role"] == "user"
        and isinstance(message["content"], str)
        and "did not change the document" in message["content"]
    ]
    assert nudges, "the edit retry nudge never reached the model"


def test_the_wrap_up_round_keeps_the_tool_list(
    client: TestClient, on_sonnet, monkeypatch
):
    """Tools stay declared and `tool_choice` goes to none.

    Dropping `tools` instead would invalidate the cached prefix -- tools
    render at the very front of it -- and re-bill the whole history as fresh
    input on the one round whose job is just to write a sentence.
    """
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    # Eight tool rounds exhausts the budget for a verify_compile turn, so the
    # ninth is the reserved wrap-up.
    scripted = ScriptedAnthropic(
        [
            anthropic_message(blocks=[tool_use_block("read_typst", {}, f"toolu_{i}")])
            for i in range(9)
        ]
        + [anthropic_message(blocks=[text_block("Had a look.")])]
    )
    monkeypatch.setattr(
        "app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted
    )
    post_chat(client, created["id"], json={"message": "look"})

    last = scripted.requests[-1]
    assert last["tool_choice"] == {"type": "none"}
    assert last["tools"], "the tool list was dropped on the wrap-up round"


def test_the_stable_prefix_is_marked_for_caching(
    client: TestClient, on_sonnet, monkeypatch
):
    """Without this the turn still works -- it just costs about 11x.

    This API does not cache automatically the way the Responses API does.
    Measured against the live endpoint on 2026-10-29: the same round billed
    7,714 uncached input tokens without the breakpoint and 7,620 cached reads
    with it, 19,262 vs 1,736 micro-USD. The whole published cost table assumes
    a cache-heavy mix, so losing this marker does not just cost money, it
    makes the table wrong.

    It sits at the end of `system` because the render order is
    tools -> system -> messages: one marker there covers the tool definitions
    and the prompt, which is the entire stable prefix.
    """
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    scripted = ScriptedAnthropic([anthropic_message(blocks=[text_block("ok")])])
    monkeypatch.setattr(
        "app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted
    )
    post_chat(client, created["id"], json={"message": "hi"})

    system = scripted.requests[0]["system"]
    assert isinstance(system, list) and len(system) == 1
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    # And the tools are still sent on the same request, or the breakpoint
    # covers a prefix that does not include them.
    assert scripted.requests[0]["tools"]


def test_the_two_providers_send_the_same_system_prompt(
    client: TestClient, on_sonnet, monkeypatch
):
    """A prompt that drifted per provider would make the picker a behaviour
    switch rather than a cost-and-capability one."""
    from app.services.llm.prompts import SYSTEM_PROMPT

    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    scripted = ScriptedAnthropic([anthropic_message(blocks=[text_block("ok")])])
    monkeypatch.setattr(
        "app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted
    )
    post_chat(client, created["id"], json={"message": "hi"})
    assert scripted.requests[0]["system"][0]["text"].startswith(SYSTEM_PROMPT)


def test_an_unconfigured_provider_cannot_be_reached(
    client: TestClient, openai_enabled, db_session, monkeypatch
):
    """No Anthropic key means no Anthropic rows in the picker, so there is no
    way to select one -- which is better than offering a model that 502s.

    The catalogue default is itself an Anthropic row, so this also covers what
    such a deployment runs *instead*: the cheapest model it can actually
    reach, never the published default. Nothing here reads
    `model_catalog.DEFAULT_MODEL`, because in this configuration that is
    precisely the model that must not be chosen.
    """
    from tests.test_usage import _signed_in

    _signed_in(client, db_session, sub="sub-no-anthropic")
    table = client.get("/v1/models").json()
    assert {row["provider"] for row in table["models"]} == {"openai"}
    assert client.put("/v1/models/selection", json={"model": SONNET}).status_code == 400
    offered = {row["id"] for row in table["models"]}
    assert model_catalog.DEFAULT_MODEL not in offered
    assert table["selected"] in offered
    assert table["selected"] == min(
        (model_catalog.BY_ID[name] for name in offered),
        key=lambda spec: spec.prices.input,
    ).id
