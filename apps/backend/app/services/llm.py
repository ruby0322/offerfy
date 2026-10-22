from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Callable

import httpx
from fastapi import HTTPException

from app.config import get_settings
from app.services.edit_diff import edit_result_payload
from app.services.layout import compile_digest, read_layout_report
from app.typst_edit import apply_typst_edit
from app.services.typst_escape import sanitize_typst_source
from app.services.ats import read_ats_report

logger = logging.getLogger(__name__)

READ_TOOL = "read_typst"
EDIT_TOOL = "apply_typst_edit"
SEARCH_TOOL = "web_search"
ATS_TOOL = "read_ats"
LAYOUT_TOOL = "read_layout"
DEFAULT_MODEL = "gpt-5.6-terra"

SYSTEM_PROMPT = (
    "You edit a Typst resume. Before changing anything, call read_typst. "
    "For any content or layout change, call apply_typst_edit. "
    "Prefer search+replace on the live document. Do not send `source` "
    "together with search+replace. Use full `source` only when rewriting "
    "the whole file, including switching Typst Universe templates. "
    "Use web_search when you need current facts (companies, titles, dates, "
    "public profiles, job posts). "
    "Call only the tools you are given. "
    "The default starter uses @preview/basic-resume:0.2.9 with paper a4 and "
    'accent-color: "#26428b". Keep those unless the user asks to switch '
    "templates. "
    "Use YYYY-MM or Present (or 至今/现在) for dates. "
    "If the user asks about ATS, parseability, or failed checks, call read_ats "
    "on the current document, then apply_typst_edit to fix failed checks. "
    "For anything about the rendered page -- line breaks, page breaks, whether "
    "something fits on one line, how full a page is -- call read_layout instead "
    "of reasoning about it from the source. Quote the rendered text you care "
    "about in `targets`. Add `questions` only for what the geometry cannot "
    "answer, such as whether a page looks crowded or unbalanced. "
    "Every apply_typst_edit already returns a `layout` digest (page count, how "
    "full each page is, anything out of place); read it instead of calling "
    "read_layout again for the same facts. "
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
        "name": LAYOUT_TOOL,
        "description": (
            "Inspect the rendered resume: page count, how full each page is, "
            "text that wrapped onto another line, items the page break split, "
            "and content past the right margin. Compiles the current document. "
            "Use `targets` to ask about specific rendered text -- whether it "
            "wrapped, which page it is on, how much room is left on its last "
            "line. Use `questions` and `scores` only for what geometry cannot "
            "answer, such as how crowded, unbalanced or flat a page looks: a "
            "`question` is yes/no and comes back as a probability, a `score` is "
            "a 0-3 rubric and comes back graded."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "targets": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Up to 6 snippets of rendered text, quoted as the page "
                        "shows them."
                    ),
                },
                "questions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Up to 6 yes/no questions about how the rendered pages "
                        "look."
                    ),
                },
                "scores": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["density", "hierarchy"],
                    },
                    "description": (
                        "Graded visual verdicts about the rendered pages, 0 to "
                        "3, returned with the level labels so the number can be "
                        "read. `density` runs Airy to Cramped, so the middle is "
                        "good and both ends are faults. `hierarchy` runs Flat to "
                        "Strong, where higher is better. Prefer a score over a "
                        "yes/no question when "
                        "you want a degree, or when you want to compare the "
                        "same judgement before and after an edit."
                    ),
                },
                "pages": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": (
                        "Which pages the `questions` and `scores` are about, "
                        "1-based. One "
                        "request gives one answer per question about whatever "
                        "it was shown, so on a multi-page resume name the page "
                        "or the answer blends them. Defaults to all pages."
                    ),
                },
            },
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
    """Reasoning settings for the resume-editing loop.

    `summary: detailed` is what makes the thinking visible, which is the point.
    Over three runs each against the live API, `auto` surfaced a summary in only
    2/3 at low effort while `detailed` managed 3/3, at the same length (~430
    chars) and no extra latency -- so it buys reliability, not verbosity.

    Effort is `low` because nothing measured justified more. The tempting theory
    was that deliberation would pay for itself by avoiding the edit retries this
    module already handles. It does not: on a real two-change edit request both
    `low` and `medium` spent the entire tool-round budget (6/6 each), so effort
    bought no efficiency, and in two runs apiece `medium` once gave up with "I
    wasn't able to complete the update safely" where `low` finished both times.
    Same visible thinking, fewer reasoning tokens, so `low` wins on this
    evidence. Revisit with a larger sample before raising it.

    The previous setting was `effort: "none"`, on the grounds that GPT-5.6
    rejected custom tools otherwise. That no longer holds: `low` and `medium`
    both return function calls against this module's TOOLS without complaint.
    """
    slug = (model or "").lower()
    if slug.startswith("gpt-5.6"):
        return {"reasoning": {"effort": "low", "summary": "detailed"}}
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


# Responses API streaming. Only these two carry anything we act on; every other
# event type is ignored on purpose, because OPENAI_BASE_URL may be a proxy that
# adds or renames events we have never seen.
TEXT_DELTA_EVENT = "response.output_text.delta"
# The model's own summary of its thinking. Separate from answer text on purpose:
# it is not what the assistant said, so it never joins the answer bubble.
REASONING_DELTA_EVENT = "response.reasoning_summary_text.delta"
COMPLETED_EVENT = "response.completed"
# `incomplete` counts as a failure here on purpose: this turn applies edits to
# the user's document, and acting on a knowingly truncated response risks
# writing a half-rewritten resume. An error the user can retry is better.
STREAM_FAILURE_EVENTS = {"response.failed", "response.incomplete", "error"}


async def _aiter_sse_payloads(resp: httpx.Response):
    """Yield decoded JSON objects from a text/event-stream response body."""
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
        # event:, id: and retry: carry nothing we need; the payload has its own type.
    event = flush()
    if event is not None:
        yield event


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
    extra_tools: list[dict] | None = None,
    tool_handlers: dict[str, Callable[[dict], str]] | None = None,
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
    emitted_assistant = False

    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        # One round past the tool budget is reserved for the answer, with tools
        # withheld. Without it, a turn that spends every round on tool calls ends
        # on the canned fallback and the user is told nothing about what changed.
        # Reasoning makes that more likely, because deliberating costs rounds.
        for attempt in range(max_rounds + 1):
            wrap_up = attempt == max_rounds
            body = {
                "model": model,
                "instructions": SYSTEM_PROMPT + extra_system,
                "input": input_items,
                "tools": TOOLS + list(extra_tools or []),
                "tool_choice": "none" if wrap_up else "auto",
                "include": ["web_search_call.action.sources"],
                "stream": True,
                **completions_tool_extras(model),
            }
            # Text deltas are a side channel: every decision below still runs on the
            # terminal response.completed object, so the round logic is unchanged.
            # The client's `read` timeout becomes a per-chunk budget once we stream,
            # which is why `timeout` is still passed through as a plain float.
            data: dict | None = None
            try:
                async with client.stream("POST", url, headers=headers, json=body) as resp:
                    if resp.status_code >= 400:
                        # client.stream does not read the body for us, and
                        # _provider_error_detail calls resp.json().
                        await resp.aread()
                        detail = _provider_error_detail(resp)
                        logger.warning(
                            "openai chat failed status=%s detail=%s", resp.status_code, detail
                        )
                        raise HTTPException(status_code=502, detail=detail)
                    async for event in _aiter_sse_payloads(resp):
                        etype = event.get("type")
                        if etype == TEXT_DELTA_EVENT:
                            chunk = event.get("delta")
                            if isinstance(chunk, str) and chunk:
                                yield {"kind": "delta", "text": chunk}
                        elif etype == REASONING_DELTA_EVENT:
                            chunk = event.get("delta")
                            if isinstance(chunk, str) and chunk:
                                yield {"kind": "reasoning", "text": chunk}
                        elif etype == COMPLETED_EVENT:
                            # Lifecycle events nest the response object under
                            # "response"; tolerate a flat payload too so a
                            # proxy that unwraps it does not break the turn.
                            payload = event.get("response")
                            if not isinstance(payload, dict):
                                payload = event if isinstance(event.get("output"), list) else None
                            if isinstance(payload, dict):
                                data = payload
                        elif etype in STREAM_FAILURE_EVENTS:
                            detail = _stream_failure_detail(event)
                            logger.warning("openai chat stream failed detail=%s", detail)
                            raise HTTPException(status_code=502, detail=detail)
            except httpx.HTTPError as exc:
                logger.warning("openai chat transport failed: %s", exc.__class__.__name__)
                raise HTTPException(status_code=502, detail="Chat request failed") from exc
            if data is None:
                # A truncated stream must not read as an empty answer.
                logger.warning("openai chat stream ended without %s", COMPLETED_EVENT)
                raise HTTPException(status_code=502, detail="Chat request failed")
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
                # This round's words would otherwise be overwritten by a later
                # round. Promote them to a real message, above their own tool
                # cards, instead of letting the user watch them vanish.
                round_text = _output_text(data, output)
                if round_text.strip():
                    emitted_assistant = True
                    yield {"kind": "assistant", "content": round_text}
                for call in function_calls:
                    name = call.get("name")
                    args = _parse_args(call.get("arguments") or "{}")
                    if name == READ_TOOL:
                        result = json.dumps({"source": source})
                    elif name == ATS_TOOL:
                        report = await asyncio.to_thread(read_ats_report, source)
                        result = json.dumps(report, ensure_ascii=False)
                    elif name == LAYOUT_TOOL:
                        report = await read_layout_report(source, args)
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
                                # One compile answers both questions. The layout
                                # digest rides home on the edit the agent just
                                # made, so seeing what it did to the page costs
                                # no extra round and no extra compile.
                                digest = await asyncio.to_thread(
                                    compile_digest, source
                                )
                                payload["compile"] = digest["compile"]
                                if "layout" in digest:
                                    payload["layout"] = digest["layout"]
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
                    elif tool_handlers and name in tool_handlers:
                        result = tool_handlers[name](args)
                    else:
                        result = json.dumps({"error": f"unknown tool {name}"})
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
                continue
            assistant_text = _output_text(data, output)
            # The reserved round is always terminal: it exists to produce the
            # answer, so a leftover nudge must not send it round again.
            if retry_nudge and not wrap_up:
                input_items.extend(output)
                # Same reasoning as the tool branch: the nudge abandons this
                # round, so its words are emitted now rather than dropped.
                if assistant_text.strip():
                    emitted_assistant = True
                    yield {"kind": "assistant", "content": assistant_text}
                    assistant_text = ""
                input_items.append({"role": "user", "content": retry_nudge})
                retry_nudge = None
                continue
            break

    if assistant_text.strip():
        yield {"kind": "assistant", "content": assistant_text}
    elif not emitted_assistant:
        yield {"kind": "assistant", "content": "Updated the Typst source."}


async def aiter_chat_edit(
    messages: list[dict],
    typst_source: str,
    *,
    prefer_full_source: bool = False,
    extra_system: str = "",
    verify_compile: bool = False,
    extra_tools: list[dict] | None = None,
    tool_handlers: dict[str, Callable[[dict], str]] | None = None,
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
        extra_tools=extra_tools,
        tool_handlers=tool_handlers,
    ):
        yield event
