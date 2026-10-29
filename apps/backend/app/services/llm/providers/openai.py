"""OpenAI, through the Responses API.

This is the adapter the product shipped on, lifted out of the old
`app.services.llm` unchanged in behaviour: same endpoint derivation, same
streaming event names, same usage reader, same transcript shape. The only new
thing is that it now satisfies `providers.base.Provider`, so the turn loop can
hold it and the Anthropic one in the same hand.

`OPENAI_BASE_URL` may point at a proxy, and that shapes several decisions
here: the endpoint is derived rather than assumed, only the two streaming
events we act on are recognised, and every field of the usage block is
optional.
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

NAME = "openai"

# Only these three carry anything we act on; every other event type is ignored
# on purpose, because the base URL may be a proxy that adds or renames events
# we have never seen.
TEXT_DELTA_EVENT = "response.output_text.delta"
# The model's own summary of its thinking. Separate from answer text on
# purpose: it is not what the assistant said, so it never joins the answer
# bubble.
REASONING_DELTA_EVENT = "response.reasoning_summary_text.delta"
COMPLETED_EVENT = "response.completed"
# `incomplete` counts as a failure here on purpose -- see Provider.stream.
STREAM_FAILURE_EVENTS = {"response.failed", "response.incomplete", "error"}


def responses_url(base: str) -> str:
    """`/v1/responses` from whatever OPENAI_BASE_URL was set to.

    Accepts a bare host, a `/v1`, a full `/responses`, and the
    `/chat/completions` an older config may still hold. Derived rather than
    assumed so that pointing this at a proxy does not require knowing which
    suffix convention it follows.
    """
    base = base.rstrip("/")
    if base.endswith("/responses"):
        return base
    if base.endswith("/chat/completions"):
        base = base[: -len("/chat/completions")]
    if base.endswith("/v1"):
        return base + "/responses"
    return base + "/v1/responses"


def _output_text(data: dict, output: list) -> str:
    """The answer text, from the convenience field or by walking the blocks.

    `output_text` is what the API provides and what is used when present; the
    walk is the fallback for a proxy that drops it.
    """
    text = data.get("output_text")
    if isinstance(text, str) and text.strip():
        return text
    parts: list[str] = []
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content")
        if isinstance(content, str) and content:
            parts.append(content)
            continue
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") in {"output_text", "text"} and isinstance(
                block.get("text"), str
            ):
                parts.append(block["text"])
    return "".join(parts)


def _citation_sources(output: list) -> list[dict[str, str]]:
    """URLs the model cited, for a search whose own `sources` came back empty.

    The annotations on the answer text are the second place the same links
    appear, and the only place they appear at all when `include` did not take
    effect -- a search card with no sources is worse than one built from the
    citations.
    """
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            for annotation in block.get("annotations") or []:
                if not isinstance(annotation, dict):
                    continue
                url = annotation.get("url")
                if not isinstance(url, str) or not url or url in seen:
                    continue
                seen.add(url)
                title = annotation.get("title")
                row = {"url": url}
                if isinstance(title, str) and title.strip():
                    row["title"] = title.strip()
                found.append(row)
    return found


def _usage_counts(data: dict) -> dict[str, int | None]:
    """Read the Responses-API usage block off the terminal response object.

    `total_tokens` is skipped because it is just the sum.
    `cache_write_tokens` nests beside `cached_tokens` with a
    `cache_write_tokens_details` TTL split we deliberately do not read -- 30m
    is the only TTL we have ever been billed, and taking the 30m rate for an
    unseen 12h write would under- rather than over-charge, which is the wrong
    direction to guess in.
    """
    empty = empty_usage()
    try:
        usage = data.get("usage")
        if not isinstance(usage, dict):
            return empty
        return {
            "input_tokens": token_count(usage, "input_tokens"),
            "cached_tokens": token_count(
                usage.get("input_tokens_details"), "cached_tokens"
            ),
            "cache_write_tokens": token_count(
                usage.get("input_tokens_details"), "cache_write_tokens"
            ),
            "output_tokens": token_count(usage, "output_tokens"),
            "reasoning_tokens": token_count(
                usage.get("output_tokens_details"), "reasoning_tokens"
            ),
        }
    except Exception:
        logger.warning("openai usage read failed", exc_info=True)
        return empty


def _web_search_trace(item: dict, extra_sources: list[dict[str, str]]) -> dict:
    action = item.get("action") if isinstance(item.get("action"), dict) else {}
    query = action.get("query") if isinstance(action.get("query"), str) else ""
    sources = action.get("sources") or item.get("sources") or extra_sources or []
    return search_trace(query, sources, str(item.get("status") or "completed"))


def _stream_failure_detail(event: dict) -> str:
    for key in ("error", "response"):
        section = event.get(key)
        if isinstance(section, dict):
            message = section.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()[:300]
            nested = section.get("error")
            if isinstance(nested, dict):
                message = nested.get("message")
                if isinstance(message, str) and message.strip():
                    return message.strip()[:300]
    return "Chat request failed"


class OpenAIConversation(Conversation):
    """A Responses-API `input` array.

    The assistant turn goes back in as the `output` array it arrived as, and
    tool results as sibling `function_call_output` items -- not nested in a
    message. That flatness is the Responses API's own shape; keeping it means
    reasoning items survive the round trip untouched, which is what lets the
    model carry its thinking across tool calls.
    """

    def __init__(self, spec: Any, messages: list[dict]):
        self.spec = spec
        self.items: list[dict] = list(messages)

    def add_round(self, raw: Any) -> None:
        if isinstance(raw, list):
            self.items.extend(raw)

    def add_tool_results(self, results: list[ToolResult]) -> None:
        for result in results:
            self.items.append(
                {
                    "type": "function_call_output",
                    "call_id": result.call.id or "call",
                    "output": result.output,
                }
            )

    def add_nudge(self, text: str) -> None:
        self.items.append({"role": "user", "content": text})

    def body(self, *, system: str, tools: list[dict], wrap_up: bool) -> dict:
        return {
            "model": self.spec.id,
            "instructions": system,
            "input": self.items,
            "tools": tools,
            "tool_choice": "none" if wrap_up else "auto",
            "include": ["web_search_call.action.sources"],
            "stream": True,
            **(self.spec.options or {}),
        }


class OpenAIProvider:
    name = NAME

    def configured(self) -> bool:
        return bool((get_settings().openai_api_key or "").strip())

    def url(self) -> str:
        base = (get_settings().openai_base_url or "https://api.openai.com/v1").strip()
        return responses_url(base)

    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {get_settings().openai_api_key}",
            "Content-Type": "application/json",
        }

    def conversation(self, spec: Any, messages: list[dict]) -> OpenAIConversation:
        return OpenAIConversation(spec, messages)

    def tool_specs(self, tools: list[dict]) -> list[dict]:
        """Nothing to translate: `tools.TOOLS` is already this API's shape."""
        return list(tools)

    async def stream(
        self,
        client: httpx.AsyncClient,
        conversation: OpenAIConversation,
        *,
        system: str,
        tools: list[dict],
        wrap_up: bool,
    ) -> AsyncIterator[dict]:
        body = conversation.body(
            system=system, tools=self.tool_specs(tools), wrap_up=wrap_up
        )
        # Text deltas are a side channel: every decision below still runs on
        # the terminal response.completed object, so the round logic does not
        # depend on having seen them.
        data: dict | None = None
        try:
            async with client.stream(
                "POST", self.url(), headers=self.headers(), json=body
            ) as resp:
                if resp.status_code >= 400:
                    # client.stream does not read the body for us, and
                    # provider_error_detail calls resp.json().
                    await resp.aread()
                    detail = provider_error_detail(resp)
                    logger.warning(
                        "openai chat failed status=%s detail=%s",
                        resp.status_code,
                        detail,
                    )
                    raise HTTPException(status_code=502, detail=detail)
                async for event in aiter_sse_payloads(resp):
                    etype = event.get("type")
                    if etype == TEXT_DELTA_EVENT:
                        chunk = event.get("delta")
                        if isinstance(chunk, str) and chunk:
                            yield {"kind": DELTA, "text": chunk}
                    elif etype == REASONING_DELTA_EVENT:
                        chunk = event.get("delta")
                        if isinstance(chunk, str) and chunk:
                            yield {"kind": REASONING, "text": chunk}
                    elif etype == COMPLETED_EVENT:
                        # Lifecycle events nest the response object under
                        # "response"; tolerate a flat payload too so a proxy
                        # that unwraps it does not break the turn.
                        payload = event.get("response")
                        if not isinstance(payload, dict):
                            payload = (
                                event if isinstance(event.get("output"), list) else None
                            )
                        if isinstance(payload, dict):
                            data = payload
                    elif etype in STREAM_FAILURE_EVENTS:
                        detail = _stream_failure_detail(event)
                        logger.warning("openai chat stream failed detail=%s", detail)
                        raise HTTPException(status_code=502, detail=detail)
        except httpx.HTTPError as exc:
            logger.warning(
                "openai chat transport failed: %s", exc.__class__.__name__
            )
            raise HTTPException(status_code=502, detail="Chat request failed") from exc
        if data is None:
            # A truncated stream must not read as an empty answer.
            logger.warning("openai chat stream ended without %s", COMPLETED_EVENT)
            raise HTTPException(status_code=502, detail="Chat request failed")

        output = data.get("output") if isinstance(data.get("output"), list) else []
        citations = _citation_sources(output)
        calls: list[ToolCall] = []
        searches: list[dict] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "web_search_call":
                searches.append(_web_search_trace(item, citations))
            elif item.get("type") == "function_call":
                calls.append(
                    ToolCall(
                        id=str(item.get("call_id") or item.get("id") or "call"),
                        name=str(item.get("name") or ""),
                        arguments=parse_args(item.get("arguments") or "{}"),
                    )
                )
        yield {
            "kind": ROUND,
            "result": RoundResult(
                text=_output_text(data, output),
                calls=calls,
                raw=output,
                usage=_usage_counts(data),
                searches=searches,
            ),
        }
