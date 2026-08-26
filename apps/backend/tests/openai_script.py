from __future__ import annotations

import json
from types import SimpleNamespace


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


class ScriptedOpenAI:
    def __init__(self, script: list[dict]):
        self.script = list(script)
        self.requests: list[dict] = []

    def __call__(self, *args, **kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def post(self, url, headers=None, json=None):
        self.requests.append(json or {})
        if not self.script:
            payload = llm_message(content="ok")
        else:
            payload = self.script.pop(0)
        return SimpleNamespace(status_code=200, json=lambda payload=payload: payload)
