from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

import httpx
from fastapi import HTTPException

from app.config import get_settings
from app.services.edit_diff import edit_result_payload
from app.typst_edit import apply_typst_edit
from app.services.typst_compile import compile_status
from app.services.typst_escape import sanitize_typst_source
from app.services.ats import read_ats_report

logger = logging.getLogger(__name__)

READ_TOOL = "read_typst"
EDIT_TOOL = "apply_typst_edit"
SEARCH_TOOL = "web_search"
ATS_TOOL = "read_ats"
DEFAULT_MODEL = "gpt-5.6-terra"

SYSTEM_PROMPT = (
    "You edit a Typst resume. Before changing anything, call read_typst. "
    "For any content or layout change, call apply_typst_edit. "
    "Prefer search+replace on the live document. Do not send `source` "
    "together with search+replace. Use full `source` only when rewriting "
    "the whole file, including switching Typst Universe templates. "
    "Use web_search when you need current facts (companies, titles, dates, "
    "public profiles, job posts). "
    "You may call only read_typst, apply_typst_edit, read_ats, and web_search. "
    "The default starter uses @preview/basic-resume:0.2.9 with paper a4 and "
    'accent-color: "#f4be82". Keep those unless the user asks to switch '
    "templates. "
    "Use YYYY-MM or Present (or 至今/现在) for dates. "
    "If the user asks about ATS, parseability, or failed checks, call read_ats "
    "on the current document, then apply_typst_edit to fix failed checks. "
    "If apply_typst_edit returns changed=false or an error, immediately "
    "call it again: copy an exact substring from the last read_typst, or "
    "write the full source. Do not ask the user to retry. "
    "Typst strings (\"...\"): escape \" as \\\" and \\ as \\\\. "
    "In markup (list items), write \\$ \\@ \\* so $, @, and unpaired * "
    "do not start math, labels, or emphasis. Put messy text in strings. "
    "Reply in the user's language."
)

EDIT_RETRY_NUDGE = (
    "The last apply_typst_edit did not change the document. "
    "Call apply_typst_edit again now. Copy an exact substring from the "
    "latest read_typst source, or write the full source. "
    "Do not ask the user to retry."
)

COMPILE_RETRY_NUDGE = (
    "Typst compile failed after the last apply_typst_edit. "
    "Call apply_typst_edit with the full source now. Escape special "
    "characters: in \"strings\" write \\\" and \\\\; in markup write "
    "\\$ \\@ \\* . Do not ask the user to retry."
)

TEMPLATE_SWITCH_EXTRA = (
    " The user asked to rewrite the resume into a different Typst Universe "
    "template. You MUST change the #import and rewrite the full document to "
    "that package's API. Do not keep @preview/basic-resume unless that is "
    "the requested package. Call read_typst, then apply_typst_edit with only "
    "the full `source` field (no search+replace). Keep the user's content "
    "(name, contacts, education, experience, projects, skills). Prefer "
    "paper a4 if the template allows it. Do not invent helpers like #section "
    "or #entry."
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
        "name": ATS_TOOL,
        "description": (
            "Run the ATS parseability scan on the current Typst resume "
            "(compiles PDF, then returns pass/fail checks). Call this when "
            "the user asks about ATS status or to fix failed checks."
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


def _tool_output_for_model(result: str) -> str:
    """Drop restore checkpoints from the model context; keep them in traces."""
    try:
        payload = json.loads(result)
    except json.JSONDecodeError:
        return result
    if isinstance(payload, dict) and "previous_source" in payload:
        payload = dict(payload)
        payload.pop("previous_source", None)
        return json.dumps(payload, ensure_ascii=False)
    return result


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


def _edit_needs_retry(name: str | None, parsed: Any) -> bool:
    if name != EDIT_TOOL or not isinstance(parsed, dict):
        return False
    if parsed.get("error"):
        return True
    return parsed.get("changed") is False


async def aiter_llm_turn(
    messages: list[dict],
    typst_source: str,
    *,
    timeout: float = 90.0,
    extra_system: str = "",
    max_rounds: int = 6,
    prefer_full_source: bool = False,
    verify_compile: bool = False,
):
    """Yield tool/source/assistant events. OpenAI waits on the event loop."""
    if not llm_configured():
        raise HTTPException(status_code=503, detail="Chat is not configured")
    settings = get_settings()
    base = (settings.openai_base_url or "https://api.openai.com/v1").strip()
    url = _responses_url(base)
    model = normalize_openai_model(settings.openai_model)

    input_items: list[dict] = _history_input(messages)
    source = typst_source
    assistant_text = ""
    retry_nudge: str | None = None

    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
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
                resp = await client.post(url, headers=headers, json=body)
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
                    yield {"kind": "tool", "trace": _web_search_trace(item, citations)}
                elif item.get("type") == "function_call":
                    function_calls.append(item)
            if function_calls:
                input_items.extend(output)
                for call in function_calls:
                    name = call.get("name")
                    args = _parse_args(call.get("arguments") or "{}")
                    if name == READ_TOOL:
                        result = json.dumps({"source": source})
                    elif name == ATS_TOOL:
                        report = await asyncio.to_thread(read_ats_report, source)
                        result = json.dumps(report, ensure_ascii=False)
                    elif name == EDIT_TOOL:
                        try:
                            before = source
                            source = apply_typst_edit(
                                source, args, prefer_full_source=prefer_full_source
                            )
                            source = sanitize_typst_source(source)
                            result = json.dumps(
                                edit_result_payload(before, source, args),
                                ensure_ascii=False,
                            )
                            if verify_compile and source != before:
                                payload = json.loads(result)
                                payload["compile"] = await asyncio.to_thread(
                                    compile_status, source
                                )
                                if payload["compile"].get("ok") is False:
                                    payload["hint"] = (
                                        "Typst compile failed. Escape special "
                                        "characters (\\\" \\$ \\@ \\* in the right "
                                        "context) and call apply_typst_edit with "
                                        "only full source."
                                    )
                                result = json.dumps(payload, ensure_ascii=False)
                        except ValueError as exc:
                            result = json.dumps(
                                {
                                    "error": str(exc),
                                    "hint": (
                                        "Copy an exact substring from the latest "
                                        "read_typst source, or write the full source. "
                                        "Do not ask the user to retry."
                                    ),
                                }
                            )
                    else:
                        result = json.dumps(
                            {
                                "error": (
                                    "only read_typst, apply_typst_edit, read_ats, "
                                    "and web_search are allowed"
                                )
                            }
                        )
                    trace = _tool_trace(name or "unknown", args, result)
                    yield {"kind": "tool", "trace": trace}
                    parsed = trace.get("result")
                    if name == EDIT_TOOL and isinstance(parsed, dict) and parsed.get("changed"):
                        yield {"kind": "source", "typst_source": source}
                        compile_info = parsed.get("compile")
                        if isinstance(compile_info, dict) and compile_info.get("ok") is False:
                            retry_nudge = COMPILE_RETRY_NUDGE
                        else:
                            retry_nudge = None
                    elif _edit_needs_retry(name, parsed):
                        retry_nudge = EDIT_RETRY_NUDGE
                    input_items.append(
                        {
                            "type": "function_call_output",
                            "call_id": call.get("call_id") or call.get("id") or "call",
                            "output": _tool_output_for_model(result),
                        }
                    )
                assistant_text = _output_text(data, output) or assistant_text
                continue
            assistant_text = _output_text(data, output)
            if retry_nudge:
                input_items.extend(output)
                input_items.append({"role": "user", "content": retry_nudge})
                retry_nudge = None
                continue
            break
        else:
            if not assistant_text:
                assistant_text = "Updated the Typst source."

    yield {"kind": "assistant", "content": assistant_text or "Updated the Typst source."}


async def aiter_chat_edit(
    messages: list[dict],
    typst_source: str,
    *,
    prefer_full_source: bool = False,
    extra_system: str = "",
    verify_compile: bool = False,
):
    extra = (
        " Extracted upload text may appear as Typst comments. "
        "If the user attached a file, treat it as source material "
        "(resume, job post, or notes) and call apply_typst_edit when the "
        "resume should change. "
        "Always call read_typst for the current source; never assume a prior snapshot. "
        "Call read_ats when asked about ATS checks or to fix failed ones. "
        "Escape Typst special characters in strings and markup."
        + extra_system
    )
    async for event in aiter_llm_turn(
        messages,
        typst_source,
        timeout=120.0 if prefer_full_source else 90.0,
        extra_system=extra,
        max_rounds=8 if (prefer_full_source or verify_compile) else 6,
        prefer_full_source=prefer_full_source,
        verify_compile=verify_compile,
    ):
        yield event
