from __future__ import annotations

import json


def tool_call(name: str, arguments: dict | str, call_id: str = "call-1") -> dict:
    raw = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": raw},
    }


def web_search_call(
    query: str,
    call_id: str = "ws-1",
    sources: list | None = None,
) -> dict:
    action: dict = {"type": "search", "query": query}
    if sources is not None:
        action["sources"] = sources
    return {
        "type": "web_search_call",
        "id": call_id,
        "status": "completed",
        "action": action,
    }


def llm_message(
    *,
    content: str | None = None,
    tool_calls: list | None = None,
    web_search: dict | None = None,
) -> dict:
    output: list[dict] = []
    if web_search is not None:
        output.append(web_search)
    if tool_calls:
        for call in tool_calls:
            fn = call.get("function") or {}
            output.append(
                {
                    "type": "function_call",
                    "call_id": call.get("id", "call"),
                    "name": fn.get("name"),
                    "arguments": fn.get("arguments") or "{}",
                }
            )
    if content is not None:
        output.append(
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": content}],
            }
        )
    return {"output": output, "output_text": content or ""}


def _payload_text(payload: dict) -> str:
    text = payload.get("output_text")
    if isinstance(text, str) and text:
        return text
    parts: list[str] = []
    for item in payload.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for block in item.get("content") or []:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
    return "".join(parts)


def sse_lines(payload: dict, chunk: int = 7) -> list[str]:
    """Serialize a scripted response the way the Responses API streams it."""
    lines: list[str] = []
    text = _payload_text(payload)
    for start in range(0, len(text), chunk):
        frame = {"type": "response.output_text.delta", "delta": text[start : start + chunk]}
        lines.extend([f"data: {json.dumps(frame)}", ""])
    lines.extend([f"data: {json.dumps({'type': 'response.completed', 'response': payload})}", ""])
    lines.extend(["data: [DONE]", ""])
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


class ScriptedOpenAI:
    def __init__(self, script: list[dict], truncate: bool = False):
        self.script = list(script)
        self.requests: list[dict] = []
        # truncate=True drops the completed event, to exercise the 502 path.
        self.truncate = truncate

    def __call__(self, *args, **kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def _next_payload(self, body) -> dict:
        self.requests.append(body or {})
        if not self.script:
            return llm_message(content="ok")
        return self.script.pop(0)

    def stream(self, method, url, headers=None, json=None, **kwargs):
        payload = self._next_payload(json)
        if self.truncate:
            # Deltas, then the connection drops before response.completed.
            lines = [line for line in sse_lines(payload) if "response.completed" not in line]
            return ScriptedStream(payload, lines=lines)
        return ScriptedStream(payload)
