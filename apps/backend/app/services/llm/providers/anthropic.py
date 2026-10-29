"""Anthropic, through the Messages API.

Raw `httpx` rather than the `anthropic` SDK, and that is a decision worth
stating because it is the opposite of the usual advice:

  * The seam this adapter plugs into is an SSE stream read line by line. Every
    test in `tests/` drives the provider by replacing `httpx.AsyncClient` with
    a scripted stub (`tests/openai_script.py`, `tests/anthropic_script.py`),
    so an SDK client here would be the one provider the suite could not drive
    the same way.
  * It adds no runtime dependency. This host deploys by `pip install -r
    requirements.txt` and a restart (AGENTS.md section 3-4); a new package in
    the API's environment is a step that runbook does not have, on a service
    whose restart is the live site.

If the SDK is adopted later, this file is the only one that changes -- which
is the point of `providers.base`.

**Four shape differences from the Responses API** drive everything below, and
each one is a place a naive port breaks:

  1. The system prompt is a top-level `system` string, not `instructions`.
  2. `max_tokens` is **required**. There is no "as much as you need".
  3. Tool results are `tool_result` blocks inside a **user** message, paired to
     their call by `tool_use_id`. They are not top-level items.
  4. Tool arguments stream as `input_json_delta` fragments that have to be
     concatenated per content-block index; the terminal `message_stop` does not
     restate them. So unlike the OpenAI adapter, which reads everything off one
     terminal object, this one accumulates as it goes.
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator

import httpx
from fastapi import HTTPException

from app.config import get_settings
from app.services.llm.providers.base import (
    DELTA,
    REASONING,
    ROUND,
    Conversation,
    RoundResult,
    ToolCall,
    ToolResult,
    aiter_sse_payloads,
    empty_usage,
    parse_args,
    provider_error_detail,
    search_trace,
    token_count,
)

logger = logging.getLogger(__name__)

NAME = "anthropic"

# Pinned, not "latest". The Messages API is versioned by this header and a
# shape change arriving unannounced would break the stream reader below.
API_VERSION = "2023-06-01"

# Required on every request. Generous because this is a streaming request --
# timeouts are not the constraint streaming has -- and because a resume
# rewrite sends the whole document back. Too low is the expensive failure: a
# turn cut off at the cap has already been billed and has to be retried.
MAX_TOKENS = 16_000

# Anthropic's server-side web search, the counterpart of the Responses API's
# `{"type": "web_search"}`. The dated type is the dynamic-filtering version,
# which the current Sonnet and Opus generations support; it runs code
# server-side to filter results before they reach the context window, so the
# standalone code-execution tool must NOT also be declared -- a second
# execution environment confuses the model.
WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search"}

# Streaming events we act on. Everything else -- `ping`, `content_block_stop`,
# `message_start`'s own echo of the request -- is ignored, for the same reason
# the OpenAI adapter ignores what it does not know: the base URL may be a
# proxy.
MESSAGE_START = "message_start"
CONTENT_BLOCK_START = "content_block_start"
CONTENT_BLOCK_DELTA = "content_block_delta"
MESSAGE_DELTA = "message_delta"
MESSAGE_STOP = "message_stop"
ERROR_EVENT = "error"


def messages_url(base: str) -> str:
    """`/v1/messages` from whatever ANTHROPIC_BASE_URL was set to.

    Same derivation as the OpenAI adapter's, for the same reason: an operator
    pointing this at a gateway should not have to know which suffix we append.
    """
    base = base.rstrip("/")
    if base.endswith("/messages"):
        return base
    if base.endswith("/v1"):
        return base + "/messages"
    return base + "/v1/messages"


def tool_specs(tools: list[dict]) -> list[dict]:
    """Translate the neutral tool list into Messages-API tools.

    Two conversions, and nothing else:

      * `{"type": "function", "name", "description", "parameters"}` becomes
        `{"name", "description", "input_schema"}`. A tool with no `parameters`
        still needs an `input_schema`, because the field is required -- the
        no-argument tools (`read_typst`, `read_ats`) go through this path.
      * `{"type": "web_search"}` becomes the dated server-tool definition.

    Anything already carrying an `input_schema` is passed through untouched, so
    a caller that wants to hand this provider a native tool can.
    """
    out: list[dict] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        kind = tool.get("type")
        if kind == "web_search":
            out.append(dict(WEB_SEARCH_TOOL))
            continue
        if "input_schema" in tool:
            out.append(dict(tool))
            continue
        name = tool.get("name")
        if not isinstance(name, str) or not name:
            continue
        schema = tool.get("parameters")
        if not isinstance(schema, dict):
            schema = {"type": "object", "properties": {}}
        entry: dict[str, Any] = {"name": name, "input_schema": schema}
        description = tool.get("description")
        if isinstance(description, str) and description:
            entry["description"] = description
        out.append(entry)
    return out


def _usage_counts(start_usage: Any, delta_usage: Any) -> dict[str, int | None]:
    """Merge the two places the Messages API reports tokens.

    `message_start` carries the input side -- including both cache lines --
    because it is known before generation begins; `message_delta` carries the
    final `output_tokens`. Reading only one of them loses half the bill.

    The field names differ from OpenAI's and the mapping is the whole point of
    this function: `cache_read_input_tokens` is our `cached_tokens` and
    `cache_creation_input_tokens` is our `cache_write_tokens`. Note that
    Anthropic's `input_tokens` **excludes** both cache figures, where OpenAI's
    includes the cached slice -- so the total has to be added up here, or every
    cached read on this provider bills as if it had not happened.
    `app.services.usage` then subtracts the cached slice back out, and both
    providers arrive at the same arithmetic.

    There is no reasoning-token line on this API. Thinking is billed inside
    `output_tokens`, which is where OpenAI's reasoning tokens also sit -- so
    `reasoning_tokens` stays None and nothing is double-charged either way.
    """
    counts = empty_usage()
    fresh = token_count(start_usage, "input_tokens")
    cached = token_count(start_usage, "cache_read_input_tokens")
    written = token_count(start_usage, "cache_creation_input_tokens")
    if fresh is not None or cached is not None:
        counts["input_tokens"] = (fresh or 0) + (cached or 0)
        counts["cached_tokens"] = cached
    counts["cache_write_tokens"] = written
    # message_delta's count is the final one and supersedes any running total
    # message_start may have carried.
    output = token_count(delta_usage, "output_tokens")
    if output is None:
        output = token_count(start_usage, "output_tokens")
    counts["output_tokens"] = output
    return counts


def _search_traces(blocks: list[dict]) -> list[dict]:
    """Web searches the server ran, paired with their results.

    The API reports a search as two blocks: a `server_tool_use` carrying the
    query, and a later `web_search_tool_result` carrying the results, linked
    by `tool_use_id`. On error the result block's `content` is a single error
    object rather than a list, which is branched on here -- indexing it would
    raise on the one case worth reporting.
    """
    queries: dict[str, str] = {}
    for block in blocks:
        if block.get("type") == "server_tool_use" and block.get("name") == "web_search":
            args = block.get("input")
            query = args.get("query") if isinstance(args, dict) else None
            queries[str(block.get("id") or "")] = (
                query if isinstance(query, str) else ""
            )
    traces: list[dict] = []
    for block in blocks:
        if block.get("type") != "web_search_tool_result":
            continue
        query = queries.pop(str(block.get("tool_use_id") or ""), "")
        content = block.get("content")
        if isinstance(content, list):
            traces.append(search_trace(query, content))
        else:
            code = (
                content.get("error_code") if isinstance(content, dict) else None
            )
            traces.append(search_trace(query, [], str(code or "error")))
    # A query whose result block never arrived -- the turn was cut off between
    # the two -- is still a search the user watched happen.
    for query in queries.values():
        traces.append(search_trace(query, [], "incomplete"))
    return traces


class AnthropicConversation(Conversation):
    """A Messages-API `messages` array.

    Tool results go into a *user* message, which is the shape difference most
    likely to be got wrong: the API rejects a history whose assistant
    `tool_use` has no matching `tool_result`, and it has to be the next
    message, not a top-level sibling.

    All of a round's results go into **one** user message. Splitting parallel
    tool results across several messages is accepted but trains the model to
    stop making parallel calls, which would cost this product rounds -- and
    rounds are what the turn budget is spent on.
    """

    def __init__(self, spec: Any, messages: list[dict]):
        self.spec = spec
        self.messages: list[dict] = [
            {"role": row.get("role"), "content": row.get("content")}
            for row in messages
            if row.get("role") in {"user", "assistant"}
        ]

    def add_round(self, raw: Any) -> None:
        # Thinking blocks are echoed back unchanged, which is required to
        # continue a thinking turn on the same model: dropping or editing them
        # invalidates the turn. The whole block list goes back verbatim for
        # that reason -- never a reconstruction of it.
        if isinstance(raw, list) and raw:
            self.messages.append({"role": "assistant", "content": raw})

    def add_tool_results(self, results: list[ToolResult]) -> None:
        if not results:
            return
        self.messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": result.call.id,
                        "content": result.output,
                    }
                    for result in results
                ],
            }
        )

    def add_nudge(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def body(self, *, system: str, tools: list[dict], wrap_up: bool) -> dict:
        payload: dict[str, Any] = {
            "model": self.spec.id,
            "max_tokens": MAX_TOKENS,
            "system": system,
            "messages": self.messages,
            "stream": True,
            **(self.spec.options or {}),
        }
        if wrap_up:
            # The reserved final round. `{"type": "none"}` rather than
            # omitting `tools`: dropping the tool list mid-conversation
            # invalidates the cached prefix -- tools render at the very front
            # of it -- and would re-bill the whole history as fresh input on
            # the one round whose job is just to write a sentence.
            payload["tools"] = tool_specs(tools)
            payload["tool_choice"] = {"type": "none"}
        else:
            payload["tools"] = tool_specs(tools)
            payload["tool_choice"] = {"type": "auto"}
        return payload


class AnthropicProvider:
    name = NAME

    def configured(self) -> bool:
        return bool((get_settings().anthropic_api_key or "").strip())

    def url(self) -> str:
        base = (
            get_settings().anthropic_base_url or "https://api.anthropic.com/v1"
        ).strip()
        return messages_url(base)

    def headers(self) -> dict[str, str]:
        # `x-api-key`, not a bearer token: that is this API's scheme, and an
        # Authorization header is only correct for an OAuth access token.
        return {
            "x-api-key": get_settings().anthropic_api_key or "",
            "anthropic-version": API_VERSION,
            "Content-Type": "application/json",
        }

    def conversation(self, spec: Any, messages: list[dict]) -> AnthropicConversation:
        return AnthropicConversation(spec, messages)

    def tool_specs(self, tools: list[dict]) -> list[dict]:
        return tool_specs(tools)

    async def stream(
        self,
        client: httpx.AsyncClient,
        conversation: AnthropicConversation,
        *,
        system: str,
        tools: list[dict],
        wrap_up: bool,
    ) -> AsyncIterator[dict]:
        body = conversation.body(system=system, tools=tools, wrap_up=wrap_up)
        # Accumulated as the stream arrives, because this API does not restate
        # the message at the end. `blocks` is keyed by content-block index,
        # which is the only thing tying an `input_json_delta` to the
        # `tool_use` it belongs to.
        blocks: dict[int, dict] = {}
        partials: dict[int, list[str]] = {}
        start_usage: Any = None
        delta_usage: Any = None
        stopped = False
        try:
            async with client.stream(
                "POST", self.url(), headers=self.headers(), json=body
            ) as resp:
                if resp.status_code >= 400:
                    await resp.aread()
                    detail = provider_error_detail(resp)
                    logger.warning(
                        "anthropic chat failed status=%s detail=%s",
                        resp.status_code,
                        detail,
                    )
                    raise HTTPException(status_code=502, detail=detail)
                async for event in aiter_sse_payloads(resp):
                    etype = event.get("type")
                    if etype == MESSAGE_START:
                        message = event.get("message")
                        if isinstance(message, dict):
                            start_usage = message.get("usage")
                    elif etype == CONTENT_BLOCK_START:
                        index = event.get("index")
                        block = event.get("content_block")
                        if isinstance(index, int) and isinstance(block, dict):
                            blocks[index] = dict(block)
                            partials[index] = []
                    elif etype == CONTENT_BLOCK_DELTA:
                        index = event.get("index")
                        delta = event.get("delta")
                        if not isinstance(delta, dict):
                            continue
                        dtype = delta.get("type")
                        if dtype == "text_delta":
                            chunk = delta.get("text")
                            if isinstance(chunk, str) and chunk:
                                yield {"kind": DELTA, "text": chunk}
                                if isinstance(index, int) and index in blocks:
                                    blocks[index]["text"] = (
                                        str(blocks[index].get("text") or "") + chunk
                                    )
                        elif dtype == "thinking_delta":
                            chunk = delta.get("thinking")
                            if isinstance(chunk, str) and chunk:
                                yield {"kind": REASONING, "text": chunk}
                                if isinstance(index, int) and index in blocks:
                                    blocks[index]["thinking"] = (
                                        str(blocks[index].get("thinking") or "")
                                        + chunk
                                    )
                        elif dtype == "signature_delta":
                            # The thinking block's signature. Meaningless to
                            # us and never shown, but it has to survive into
                            # the echoed-back block or the next round rejects
                            # the history.
                            chunk = delta.get("signature")
                            if isinstance(chunk, str) and isinstance(index, int):
                                if index in blocks:
                                    blocks[index]["signature"] = (
                                        str(blocks[index].get("signature") or "")
                                        + chunk
                                    )
                        elif dtype == "input_json_delta":
                            chunk = delta.get("partial_json")
                            if isinstance(chunk, str) and isinstance(index, int):
                                partials.setdefault(index, []).append(chunk)
                    elif etype == MESSAGE_DELTA:
                        delta_usage = event.get("usage")
                    elif etype == MESSAGE_STOP:
                        stopped = True
                    elif etype == ERROR_EVENT:
                        error = event.get("error")
                        message = (
                            error.get("message") if isinstance(error, dict) else None
                        )
                        detail = (
                            message.strip()[:300]
                            if isinstance(message, str) and message.strip()
                            else "Chat request failed"
                        )
                        logger.warning("anthropic chat stream failed detail=%s", detail)
                        raise HTTPException(status_code=502, detail=detail)
        except httpx.HTTPError as exc:
            logger.warning(
                "anthropic chat transport failed: %s", exc.__class__.__name__
            )
            raise HTTPException(status_code=502, detail="Chat request failed") from exc
        if not stopped:
            # Same rule as the OpenAI adapter: a stream that ended without its
            # terminal event is a failed round, not an empty answer.
            logger.warning("anthropic chat stream ended without %s", MESSAGE_STOP)
            raise HTTPException(status_code=502, detail="Chat request failed")

        for index in sorted(blocks):
            block = blocks[index]
            if block.get("type") == "tool_use":
                raw = "".join(partials.get(index) or [])
                # `input` arrives as `{}` on the start event and is filled by
                # the fragments. An unparseable accumulation becomes `{}`,
                # which the tool rejects and the loop nudges a retry for --
                # the same path a malformed OpenAI argument string takes.
                block["input"] = parse_args(raw) if raw else (block.get("input") or {})
        ordered = [blocks[index] for index in sorted(blocks)]

        calls = [
            ToolCall(
                id=str(block.get("id") or "call"),
                name=str(block.get("name") or ""),
                arguments=block.get("input") or {},
            )
            for block in ordered
            if block.get("type") == "tool_use"
        ]
        text = "".join(
            str(block.get("text") or "")
            for block in ordered
            if block.get("type") == "text"
        )
        yield {
            "kind": ROUND,
            "result": RoundResult(
                text=text,
                calls=calls,
                raw=ordered,
                usage=_usage_counts(start_usage, delta_usage),
                searches=_search_traces(ordered),
            ),
        }
