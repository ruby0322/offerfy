from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx
from fastapi import HTTPException

from app.config import get_settings
from app.services.edit_diff import edit_result_payload
from app.typst_edit import apply_typst_edit

logger = logging.getLogger(__name__)

READ_TOOL = "read_typst"
EDIT_TOOL = "apply_typst_edit"
SEARCH_TOOL = "web_search"
DEFAULT_MODEL = "gpt-5.6-terra"

SYSTEM_PROMPT = (
    "You edit a Typst resume. Before changing anything, call read_typst. "
    "For any content or layout change, call apply_typst_edit. "
    "Prefer search+replace on the live document. Do not send `source` "
    "together with search+replace. Use full `source` only when rewriting "
    "the whole file. "
    "Use web_search when you need current facts (companies, titles, dates, "
    "public profiles, job posts). "
    "You may call only read_typst, apply_typst_edit, and web_search. "
    'Keep #import "@preview/basic-resume:0.2.9", paper: "a4", and '
    'accent-color: "#f4be82". '
    "Use YYYY-MM or Present (or 至今/现在) for dates. "
    "Reply in the user's language."
)

TOOLS = [
    {"type": "web_search"},
    {
        "type": "function",
        "name": READ_TOOL,
        "description": (
            "Return the current Typst resume source. Call this before any edit "
            "so you see the latest document."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": EDIT_TOOL,
        "description": (
            "Edit the current Typst resume. Prefer `search`+`replace` on the "
            "live document. Do not send `source` together with a patch. Use "
            "full `source` only when rewriting the whole file, or character "
            "offsets `start`+`end`+`replacement`."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "search": {"type": "string"},
                "replace": {"type": "string"},
                "start": {"type": "integer"},
                "end": {"type": "integer"},
                "replacement": {"type": "string"},
            },
        },
    },
]

# Product / ChatGPT labels → API model IDs.
_MODEL_ALIASES = {
    "gpt-5.6 terra": "gpt-5.6-terra",
    "gpt-5.6-terra": "gpt-5.6-terra",
    "terra": "gpt-5.6-terra",
    "gpt-5.6 sol": "gpt-5.6-sol",
    "gpt-5.6-sol": "gpt-5.6-sol",
    "sol": "gpt-5.6-sol",
    "gpt-5.6": "gpt-5.6-sol",
    "gpt-5.6 luna": "gpt-5.6-luna",
    "gpt-5.6-luna": "gpt-5.6-luna",
    "luna": "gpt-5.6-luna",
}


def normalize_openai_model(name: str | None) -> str:
    raw = (name or "").strip()
    if not raw:
        return DEFAULT_MODEL
    key = re.sub(r"[\s_]+", " ", raw.lower()).strip()
    compact = key.replace(" ", "-")
    return _MODEL_ALIASES.get(key) or _MODEL_ALIASES.get(compact) or raw


def completions_tool_extras(model: str) -> dict:
    """GPT-5.6 rejects custom tools unless reasoning is off."""
    slug = (model or "").lower()
    if slug.startswith("gpt-5.6"):
        return {"reasoning": {"effort": "none"}}
    return {}


def llm_configured() -> bool:
    return bool((get_settings().openai_api_key or "").strip())


def _responses_url(base: str) -> str:
    base = base.rstrip("/")
    if base.endswith("/responses"):
        return base
    if base.endswith("/chat/completions"):
        base = base[: -len("/chat/completions")]
    if base.endswith("/v1"):
        return base + "/responses"
    return base + "/v1/responses"


def _tool_trace(name: str, arguments: dict, result: str) -> dict[str, Any]:
    try:
        parsed_result: Any = json.loads(result)
    except json.JSONDecodeError:
        parsed_result = result
    return {"name": name, "arguments": arguments, "result": parsed_result}


def _parse_args(raw_args: Any) -> dict:
    if isinstance(raw_args, dict):
        return raw_args
    if isinstance(raw_args, str):
        try:
            parsed = json.loads(raw_args)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _provider_error_detail(resp: httpx.Response) -> str:
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


def _history_input(messages: list[dict]) -> list[dict]:
    items: list[dict] = []
    for message in messages:
        role = message.get("role")
        if role not in {"user", "assistant"}:
            continue
        content = message.get("content") or ""
        if not isinstance(content, str):
            content = str(content)
        items.append({"role": role, "content": content})
    return items


def _output_text(data: dict, output: list) -> str:
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
            if block.get("type") in {"output_text", "text"} and isinstance(block.get("text"), str):
                parts.append(block["text"])
    return "".join(parts)


def _citation_sources(output: list) -> list[dict[str, str]]:
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


def _web_search_trace(item: dict, extra_sources: list[dict[str, str]] | None = None) -> dict[str, Any]:
    action = item.get("action") if isinstance(item.get("action"), dict) else {}
    query = action.get("query") if isinstance(action.get("query"), str) else ""
    raw_sources = action.get("sources") or item.get("sources") or extra_sources or []
    sources: list[dict[str, str]] = []
    if isinstance(raw_sources, list):
        for entry in raw_sources:
            if isinstance(entry, str) and entry:
                sources.append({"url": entry})
            elif isinstance(entry, dict):
                url = entry.get("url")
                if isinstance(url, str) and url:
                    row = {"url": url}
                    title = entry.get("title")
                    if isinstance(title, str) and title.strip():
                        row["title"] = title.strip()
                    sources.append(row)
    return {
        "name": SEARCH_TOOL,
        "arguments": {"query": query, "type": action.get("type") or "search"},
        "result": {"status": item.get("status") or "completed", "sources": sources},
    }


def run_llm_turn(
    messages: list[dict],
    typst_source: str,
    *,
    timeout: float = 90.0,
    extra_system: str = "",
    max_rounds: int = 6,
) -> tuple[str, str, list[dict[str, Any]]]:
    """Run tool-calling turns. Returns (assistant_text, source, tool_traces)."""
    if not llm_configured():
        raise HTTPException(status_code=503, detail="Chat is not configured")
    settings = get_settings()
    base = (settings.openai_base_url or "https://api.openai.com/v1").strip()
    url = _responses_url(base)
    model = normalize_openai_model(settings.openai_model)

    input_items: list[dict] = _history_input(messages)
    source = typst_source
    assistant_text = ""
    traces: list[dict[str, Any]] = []

    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }

    with httpx.Client(timeout=timeout) as client:
        for _ in range(max_rounds):
            body = {
                "model": model,
                "instructions": SYSTEM_PROMPT + extra_system,
                "input": input_items,
                "tools": TOOLS,
                "tool_choice": "auto",
                "include": ["web_search_call.action.sources"],
                **completions_tool_extras(model),
            }
            try:
                resp = client.post(url, headers=headers, json=body)
            except httpx.HTTPError as exc:
                logger.warning("openai chat transport failed: %s", exc.__class__.__name__)
                raise HTTPException(status_code=502, detail="Chat request failed") from exc
            if resp.status_code >= 400:
                detail = _provider_error_detail(resp)
                logger.warning("openai chat failed status=%s detail=%s", resp.status_code, detail)
                raise HTTPException(status_code=502, detail=detail)
            data = resp.json()
            if not isinstance(data, dict):
                data = {}
            output = data.get("output") if isinstance(data.get("output"), list) else []
            citations = _citation_sources(output)
            function_calls: list[dict] = []
            for item in output:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "web_search_call":
                    traces.append(_web_search_trace(item, citations))
                elif item.get("type") == "function_call":
                    function_calls.append(item)
            if function_calls:
                input_items.extend(output)
                for call in function_calls:
                    name = call.get("name")
                    args = _parse_args(call.get("arguments") or "{}")
                    if name == READ_TOOL:
                        result = json.dumps({"source": source})
                    elif name == EDIT_TOOL:
                        try:
                            before = source
                            source = apply_typst_edit(source, args)
                            result = json.dumps(
                                edit_result_payload(before, source, args),
                                ensure_ascii=False,
                            )
                        except ValueError as exc:
                            result = json.dumps({"error": str(exc)})
                    else:
                        result = json.dumps(
                            {
                                "error": (
                                    "only read_typst, apply_typst_edit, and web_search "
                                    "are allowed"
                                )
                            }
                        )
                    traces.append(_tool_trace(name or "unknown", args, result))
                    input_items.append(
                        {
                            "type": "function_call_output",
                            "call_id": call.get("call_id") or call.get("id") or "call",
                            "output": result,
                        }
                    )
                assistant_text = _output_text(data, output) or assistant_text
                continue
            assistant_text = _output_text(data, output)
            break
        else:
            if not assistant_text:
                assistant_text = "Updated the Typst source."

    return assistant_text or "Updated the Typst source.", source, traces


def chat_edit(
    messages: list[dict],
    typst_source: str,
) -> tuple[str, str, list[dict[str, Any]]]:
    """Run one chat turn. Returns (assistant_text, updated_typst_source, tool_traces)."""
    extra = (
        " Extracted upload text may appear as Typst comments. "
        "Always call read_typst for the current source; never assume a prior snapshot."
    )
    return run_llm_turn(
        messages,
        typst_source,
        timeout=90.0,
        extra_system=extra,
        max_rounds=6,
    )
