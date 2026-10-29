"""A scripted Messages-API stream, for driving the Anthropic adapter.

The twin of `tests/openai_script.py`, and deliberately shaped the same way:
both stand in for `httpx.AsyncClient`, both serialise a declarative payload
into the SSE frames the real API sends, and both record the request bodies so a
test can assert on what was sent.

The serialisation is the point. The adapter accumulates tool arguments from
`input_json_delta` fragments and reads usage from two different events, so a
stub that handed it one finished object would not exercise the half of it most
likely to be wrong. `sse_lines` therefore chops tool input into fragments the
way the API does.
"""

from __future__ import annotations

import json


def text_block(text: str) -> dict:
    return {"type": "text", "text": text}


def thinking_block(text: str, signature: str = "sig-1") -> dict:
    return {"type": "thinking", "thinking": text, "signature": signature}


def tool_use_block(name: str, arguments: dict, call_id: str = "toolu_1") -> dict:
    return {"type": "tool_use", "id": call_id, "name": name, "input": arguments}


def server_tool_use_block(query: str, call_id: str = "srvtoolu_1") -> dict:
    return {
        "type": "server_tool_use",
        "id": call_id,
        "name": "web_search",
        "input": {"query": query},
    }


def web_search_result_block(
    sources: list, call_id: str = "srvtoolu_1", error_code: str | None = None
) -> dict:
    return {
        "type": "web_search_tool_result",
        "tool_use_id": call_id,
        # The error shape is an object where a success is a list. Branching on
        # that is the adapter's job and this is how the API expresses it.
        "content": (
            {"type": "web_search_tool_result_error", "error_code": error_code}
            if error_code
            else [
                {"type": "web_search_result", "url": url, "title": title}
                for url, title in sources
            ]
        ),
    }


def anthropic_usage(
    *,
    input_tokens: int | None = None,
    cache_read_input_tokens: int | None = None,
    cache_creation_input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> dict:
    """A `message_start` usage block, with this API's own field names.

    Note what the names mean, because it is where a port goes wrong:
    `input_tokens` here is the *uncached* count and excludes both cache lines,
    the opposite of the Responses API where the cached slice is included.
    """
    usage: dict = {}
    if input_tokens is not None:
        usage["input_tokens"] = input_tokens
    if cache_read_input_tokens is not None:
        usage["cache_read_input_tokens"] = cache_read_input_tokens
    if cache_creation_input_tokens is not None:
        usage["cache_creation_input_tokens"] = cache_creation_input_tokens
    if output_tokens is not None:
        usage["output_tokens"] = output_tokens
    return usage


def anthropic_message(
    *,
    blocks: list[dict] | None = None,
    usage: dict | None = None,
    output_tokens: int | None = None,
    stop_reason: str | None = None,
) -> dict:
    """One scripted assistant turn. `output_tokens` lands on `message_delta`."""
    return {
        "blocks": list(blocks or []),
        "usage": usage or {},
        "output_tokens": output_tokens,
        "stop_reason": stop_reason
        or ("tool_use" if any(b.get("type") == "tool_use" for b in (blocks or [])) else "end_turn"),
    }


def sse_lines(payload: dict, chunk: int = 7) -> list[str]:
    """Serialise a scripted turn as the Messages API streams it."""
    lines: list[str] = []

    def frame(obj: dict) -> None:
        lines.extend([f"data: {json.dumps(obj)}", ""])

    frame(
        {
            "type": "message_start",
            "message": {
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "content": [],
                "usage": payload.get("usage") or {},
            },
        }
    )
    for index, block in enumerate(payload.get("blocks") or []):
        kind = block.get("type")
        if kind == "text":
            frame(
                {
                    "type": "content_block_start",
                    "index": index,
                    "content_block": {"type": "text", "text": ""},
                }
            )
            body = str(block.get("text") or "")
            for start in range(0, len(body), chunk):
                frame(
                    {
                        "type": "content_block_delta",
                        "index": index,
                        "delta": {
                            "type": "text_delta",
                            "text": body[start : start + chunk],
                        },
                    }
                )
        elif kind == "thinking":
            frame(
                {
                    "type": "content_block_start",
                    "index": index,
                    "content_block": {"type": "thinking", "thinking": ""},
                }
            )
            body = str(block.get("thinking") or "")
            for start in range(0, len(body), chunk):
                frame(
                    {
                        "type": "content_block_delta",
                        "index": index,
                        "delta": {
                            "type": "thinking_delta",
                            "thinking": body[start : start + chunk],
                        },
                    }
                )
            frame(
                {
                    "type": "content_block_delta",
                    "index": index,
                    "delta": {
                        "type": "signature_delta",
                        "signature": str(block.get("signature") or ""),
                    },
                }
            )
        elif kind == "tool_use":
            frame(
                {
                    "type": "content_block_start",
                    "index": index,
                    # `input` really is empty here; the fragments carry it.
                    "content_block": {
                        "type": "tool_use",
                        "id": block.get("id"),
                        "name": block.get("name"),
                        "input": {},
                    },
                }
            )
            raw = json.dumps(block.get("input") or {})
            for start in range(0, len(raw), chunk):
                frame(
                    {
                        "type": "content_block_delta",
                        "index": index,
                        "delta": {
                            "type": "input_json_delta",
                            "partial_json": raw[start : start + chunk],
                        },
                    }
                )
        else:
            # server_tool_use / web_search_tool_result arrive whole.
            frame(
                {
                    "type": "content_block_start",
                    "index": index,
                    "content_block": block,
                }
            )
        frame({"type": "content_block_stop", "index": index})
    delta: dict = {"type": "message_delta", "delta": {"stop_reason": payload.get("stop_reason")}}
    if payload.get("output_tokens") is not None:
        delta["usage"] = {"output_tokens": payload["output_tokens"]}
    frame(delta)
    frame({"type": "message_stop"})
    return lines


class ScriptedStream:
    """Stands in for the object httpx.AsyncClient.stream yields."""

    def __init__(self, payload: dict, status_code: int = 200, lines: list[str] | None = None):
        self.status_code = status_code
        self._payload = payload
        self._lines = sse_lines(payload) if lines is None else lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def json(self):
        return self._payload

    @property
    def text(self) -> str:
        return json.dumps(self._payload)


class ScriptedAnthropic:
    def __init__(
        self,
        script: list[dict],
        truncate: bool = False,
        status_code: int = 200,
        error: dict | None = None,
    ):
        self.script = list(script)
        self.requests: list[dict] = []
        self.headers: list[dict] = []
        self.urls: list[str] = []
        # truncate=True drops message_stop, to exercise the 502 path.
        self.truncate = truncate
        self.status_code = status_code
        # An `error` SSE event mid-stream, which is a different failure from a
        # non-200: the response started fine and then gave up.
        self.error = error
        self.decision_requests: list[dict] = []

    def __call__(self, *args, **kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, headers=None, json=None, **kwargs):
        # read_layout's vision questions POST to /v1/decisions; patching
        # AsyncClient patches httpx itself, so one stub answers both.
        self.decision_requests.append({"url": url, "body": json or {}})
        return ScriptedStream({"answers": []})

    def stream(self, method, url, headers=None, json=None, **kwargs):
        self.requests.append(json or {})
        self.headers.append(headers or {})
        self.urls.append(url)
        payload = self.script.pop(0) if self.script else anthropic_message(
            blocks=[text_block("ok")]
        )
        if self.status_code >= 400:
            return ScriptedStream(
                {"type": "error", "error": {"message": "nope"}},
                status_code=self.status_code,
                lines=[],
            )
        if self.error is not None:
            return ScriptedStream(
                payload,
                lines=[f"data: {json_dumps({'type': 'error', 'error': self.error})}", ""],
            )
        lines = sse_lines(payload)
        if self.truncate:
            lines = [line for line in lines if "message_stop" not in line]
        return ScriptedStream(payload, lines=lines)


def json_dumps(obj) -> str:
    return json.dumps(obj)
