"""What every provider adapter has to look like, and the parts they share.

The turn loop in `app.services.llm.turn` knows nothing about any provider.
It asks an adapter for a `Conversation`, streams a round out of it, runs
whatever tools the round called, hands the results back to the conversation and
goes round again. Everything provider-shaped lives behind these two protocols:

  * `Conversation` owns the transcript, because its shape is the thing that
    differs most. OpenAI's Responses API takes the assistant turn back as the
    `output` array it was handed and tool results as `function_call_output`
    items at the top level; Anthropic's Messages API takes an assistant message
    whose `content` is a block list and tool results as `tool_result` blocks
    inside a *user* message. A loop that tried to hold one neutral transcript
    and convert at the boundary would have to round-trip reasoning blocks and
    tool-call ids through a lossy middle shape; letting each adapter keep its
    own native transcript avoids that entirely.

  * `Provider` is the request: where it goes, what headers it carries, whether
    it is configured at all, and how to read one streamed round out of it.

Both providers stream Server-Sent Events, so the framing reader and the error
reader are here rather than duplicated -- those are the two places where a
proxy in front of either API tends to misbehave, and there should be one of
each to fix.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Protocol

import httpx

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# What a round produces
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolCall:
    """One function call the model asked for, provider-independent.

    `id` is whatever the provider calls it -- an OpenAI `call_id`, an
    Anthropic `toolu_...` -- and is opaque to the loop. It goes back out
    through `Conversation.add_tool_results` so the adapter can pair a result
    with its call, which both APIs require and neither does positionally.
    """

    id: str
    name: str
    arguments: dict


@dataclass(frozen=True)
class ToolResult:
    call: ToolCall
    # The JSON string the tool returned, already stripped of anything that
    # should not enter the model's context. See turn._tool_output_for_model.
    output: str


@dataclass
class RoundResult:
    """Everything one model round yielded that the loop acts on.

    `raw` is the assistant turn in the provider's own shape, handed straight
    back to `Conversation.add_round`. The loop never looks inside it.
    """

    text: str = ""
    calls: list[ToolCall] = field(default_factory=list)
    raw: Any = None
    # Normalised token counts; see `empty_usage` for the keys and why every
    # one of them is optional.
    usage: dict[str, int | None] = field(default_factory=dict)
    # Server-side web searches the provider ran on our behalf, already in the
    # chat transcript's tool-trace shape.
    searches: list[dict] = field(default_factory=list)


# The event dicts an adapter yields while streaming. `round` is terminal and
# arrives exactly once per successful round; the rest are display-only and the
# loop relays them untouched.
DELTA = "delta"
REASONING = "reasoning"
ROUND = "round"


def empty_usage() -> dict[str, int | None]:
    """The usage shape, with nothing known.

    Every field is optional on purpose, and this is the value a provider
    returns when it reported no usage at all. A base URL may point at a proxy
    that omits or renames parts of the usage block, and telemetry must never
    be the reason a turn fails -- a shape we do not recognise reads as
    "unknown", not as an error. `app.services.usage` prices an unknown round
    at FALLBACK_ROUND_UUSD rather than at zero.

    `cache_write_tokens` is its own charge, not a subset of anything else
    here: cached and reasoning counts are slices of input and output
    respectively, but a cache write bills on top of the read at 1.25x the
    uncached input rate.
    """
    return {
        "input_tokens": None,
        "cached_tokens": None,
        "cache_write_tokens": None,
        "output_tokens": None,
        "reasoning_tokens": None,
    }


def token_count(section: Any, key: str) -> int | None:
    value = section.get(key) if isinstance(section, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


# --------------------------------------------------------------------------
# The protocols
# --------------------------------------------------------------------------


class Conversation(Protocol):
    """One turn's transcript, in the provider's own shape."""

    def add_round(self, raw: Any) -> None:
        """Record the assistant turn exactly as the provider returned it."""

    def add_tool_results(self, results: list[ToolResult]) -> None:
        """Record what the tools the model called returned."""

    def add_nudge(self, text: str) -> None:
        """Add an instruction from us, as though the user had said it.

        Used by the retry nudges, which are the one thing this loop says to a
        model that the user did not. A real user role rather than a system
        message because it has to arrive *after* the tool results it is
        reacting to, and only the user role may do that on both APIs.
        """

    def body(self, *, system: str, tools: list[dict], wrap_up: bool) -> dict:
        """The request payload for the next round.

        `wrap_up` withholds the tools, which is how the loop reserves a final
        round for an answer.
        """


class Provider(Protocol):
    """One vendor's chat API."""

    name: str

    def configured(self) -> bool:
        """Whether there is a credential for this provider at all."""

    def url(self) -> str: ...

    def headers(self) -> dict[str, str]: ...

    def conversation(self, spec: Any, messages: list[dict]) -> Conversation:
        """Start a transcript from the bounded chat history."""

    def stream(
        self,
        client: httpx.AsyncClient,
        conversation: Conversation,
        *,
        system: str,
        tools: list[dict],
        wrap_up: bool,
    ) -> AsyncIterator[dict]:
        """One round. Yields display events, then exactly one ROUND event.

        Raises HTTPException(502) for anything that means the round did not
        happen -- a provider error, a transport failure, or a stream that
        ended without its terminal event. A truncated stream must not read as
        an empty answer: this turn applies edits to the user's document, and
        acting on a knowingly incomplete response risks writing a
        half-rewritten resume.
        """


# --------------------------------------------------------------------------
# Shared SSE plumbing
# --------------------------------------------------------------------------


async def aiter_sse_payloads(resp: httpx.Response) -> AsyncIterator[dict]:
    """Yield decoded JSON objects from a text/event-stream response body.

    Both providers frame their streams the same way, and both put the payload
    type inside the payload -- so `event:`, `id:` and `retry:` lines carry
    nothing either adapter needs and are skipped. A `data:` payload that is
    not JSON is dropped rather than raised on, because a proxy may inject
    keepalives or comments we have never seen.
    """
    data_lines: list[str] = []

    def flush() -> dict | None:
        nonlocal data_lines
        if not data_lines:
            return None
        payload = "\n".join(data_lines)
        data_lines = []
        if payload.strip() in {"", "[DONE]"}:
            return None
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    async for raw in resp.aiter_lines():
        line = raw.rstrip("\r\n")
        if not line:
            event = flush()
            if event is not None:
                yield event
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    event = flush()
    if event is not None:
        yield event


def provider_error_detail(resp: httpx.Response) -> str:
    """The message out of an error body, however the provider nests it.

    OpenAI puts it at `error.message`; Anthropic at `error.message` too, but
    its top-level `type` is `"error"` rather than the error being nested under
    a status. A proxy may flatten either. Falls back to the status code, and
    truncates, because this string is relayed to the user.
    """
    try:
        data = resp.json()
    except ValueError:
        data = None
    message = None
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict):
            message = err.get("message")
        elif isinstance(err, str):
            message = err
        if not message:
            message = data.get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()[:300]
    return f"Chat request failed ({resp.status_code})"


def parse_args(raw_args: Any) -> dict:
    """A tool call's arguments as a dict, whatever arrived.

    Both providers can deliver arguments as a JSON string that the model
    generated token by token, so malformed JSON is a normal outcome rather
    than an exception -- an empty dict lets the tool reject it and the loop
    nudge a retry, which is what the retry nudges exist for.
    """
    if isinstance(raw_args, dict):
        return raw_args
    if isinstance(raw_args, str):
        try:
            parsed = json.loads(raw_args)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def search_trace(query: str, sources: Any, status: str = "completed") -> dict:
    """One web search, in the shape the chat transcript stores tool calls in.

    Shared because both providers run web search server-side and both report
    it as something that happened rather than as a call we made, but they
    report the results differently. Normalising here means the frontend's
    tool card renders a search the same way whichever model ran it.
    """
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    if isinstance(sources, list):
        for entry in sources:
            url = entry if isinstance(entry, str) else None
            title = None
            if isinstance(entry, dict):
                url = entry.get("url")
                raw_title = entry.get("title")
                if isinstance(raw_title, str) and raw_title.strip():
                    title = raw_title.strip()
            if not isinstance(url, str) or not url or url in seen:
                continue
            seen.add(url)
            row = {"url": url}
            if title:
                row["title"] = title
            rows.append(row)
    return {
        "name": "web_search",
        "arguments": {"query": query, "type": "search"},
        "result": {"status": status, "sources": rows},
    }
