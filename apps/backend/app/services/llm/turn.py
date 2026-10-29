"""The agent loop: rounds, tools, retries. One copy, every provider.

Nothing in this module names a vendor. It asks `providers.for_spec` for an
adapter, streams a round, runs whatever tools the round called, hands the
results back to the adapter's transcript and goes again. Everything that
differs between OpenAI and Anthropic -- endpoint, headers, request body,
streaming events, transcript shape, usage field names -- is behind
`providers.base`.

That split is what makes the model picker safe. The retry nudges, the
compile-verify step, the reserved wrap-up round and the edit-applied bookkeeping
are the product's actual behaviour, and they are identical whichever model a
person chose. Before this module existed they were interleaved with the
Responses API's own shape, and adding a second provider would have meant a
second copy of them.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable

import httpx
from fastapi import HTTPException

from app.services import model_catalog
from app.services.ats import read_ats_report
from app.services.chat_window import window_history
from app.services.edit_diff import edit_result_payload
from app.services.layout import compile_digest, read_layout_report
from app.services.llm import providers
from app.services.llm.prompts import (
    ATS_TOOL,
    COMPILE_RETRY_NUDGE,
    EDIT_RETRY_NUDGE,
    EDIT_TOOL,
    LAYOUT_TOOL,
    READ_TOOL,
    SYSTEM_PROMPT,
)
from app.services.llm.tools import TOOLS
from app.services.typst_escape import sanitize_typst_source
from app.typst_edit import apply_typst_edit

logger = logging.getLogger(__name__)


def llm_configured() -> bool:
    """Whether chat can run at all.

    True if *any* provider has a credential. A model whose own provider is
    unconfigured is filtered out of the picker (see services.model_choice), so
    an account with only one key set gets a working product with a shorter
    list rather than a picker full of models that 502.
    """
    return any(
        adapter.configured() for adapter in map(providers.for_provider, providers.names())
        if adapter is not None
    )


def provider_configured(spec) -> bool:
    adapter = providers.for_provider(spec.provider)
    return bool(adapter and adapter.configured())


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


def _history_input(messages: list[dict]) -> list[dict]:
    """The chat history as a provider sees it, bounded.

    This is the one funnel: the main turn and the repair loop both arrive here,
    so neither can re-send an unbounded transcript. `window_history` keeps the
    user/assistant filter this used to do inline -- `tool` rows have never been
    sent and still are not -- and drops the middle of a long conversation. See
    `app.services.chat_window` for why the cut moves in steps rather than
    sliding, and what stops a shorter history from being touched at all.

    The log line is per turn, not per round, and only when something was
    actually dropped: it is how you tell from the host whether the bound is
    engaging, without a schema change. `input_tokens` on `llm_calls` and
    `llm_cache_hit_rate` on /v1/admin/overview are what measure the effect.
    """
    window = window_history(messages)
    if window.folded:
        logger.info(
            "chat history folded dropped=%d kept=%d sent_chars=%d total_chars=%d",
            window.dropped,
            len(window.items),
            window.sent_chars,
            window.total_chars,
        )
    return window.items


def _vision_usage_event(
    report: Any,
    model: str,
    round_index: int,
    prefer_full_source: bool,
    source_chars: int,
) -> dict | None:
    """Lift the `read_layout` vision call's own usage onto a usage event.

    That call bills separately from the round that made it, and
    `vision_report` already parses its usage into the report -- so the round
    loop can hand it on without a second HTTP read or a Session.

    A `visual` block that came back from the endpoint is the signal that the
    call happened, so the token count does not decide whether there is an
    event: unreadable or absent, it reads as "unknown", exactly like a chat
    round whose response carried no `usage` at all. None means no call was
    billed -- a geometry-only `read_layout`, or a `vision_report` that gave up
    before reading a response (`skipped`, a render failure, a provider error),
    which is the same silence a chat round keeps when its own request fails.

    The model recorded is the vision endpoint's own, never the chat model the
    person picked: that call always runs on the decisions model, and billing it
    to the chat model would price it at the wrong rate.
    """
    visual = report.get("visual") if isinstance(report, dict) else None
    if not isinstance(visual, dict):
        return None
    if visual.get("skipped") is not None or visual.get("error") is not None:
        return None
    return {
        "kind": "usage",
        "purpose": "vision",
        "model": str(visual.get("model") or model),
        "round_index": round_index,
        "prefer_full_source": prefer_full_source,
        "source_chars": source_chars,
        "input_tokens": visual.get("input_tokens")
        if isinstance(visual.get("input_tokens"), int)
        else None,
        # Input tokens are the whole bill on the decisions endpoint: it drops
        # output charges and has no cache line at all, which is most of why
        # read_layout's vision half rides on it.
        "cached_tokens": None,
        "cache_write_tokens": None,
        "output_tokens": None,
        "reasoning_tokens": None,
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
    model: str | None = None,
    timeout: float = 90.0,
    extra_system: str = "",
    max_rounds: int = 6,
    prefer_full_source: bool = False,
    verify_compile: bool = False,
    extra_tools: list[dict] | None = None,
    tool_handlers: dict[str, Callable[[dict], str]] | None = None,
):
    """Yield tool/source/assistant events for one turn, on one model.

    `model` is whoever's choice reached here -- resolved and tier-checked by
    `app.services.model_choice` before this is called, never read from a
    request in here. An unknown name is still honoured as a passthrough,
    because the base URL may be a proxy serving its own ids; it is priced as
    the dearest model in the catalogue, so a proxy cannot be a cheap tier.
    """
    spec = model_catalog.spec_or_passthrough(model)
    adapter = providers.for_spec(spec)
    if not adapter.configured():
        raise HTTPException(status_code=503, detail="Chat is not configured")

    conversation = adapter.conversation(spec, _history_input(messages))
    system = SYSTEM_PROMPT + extra_system
    tools = TOOLS + list(extra_tools or [])
    source = typst_source
    assistant_text = ""
    retry_nudge: str | None = None
    emitted_assistant = False

    async with httpx.AsyncClient(timeout=timeout) as client:
        # One round past the tool budget is reserved for the answer, with tools
        # withheld. Without it, a turn that spends every round on tool calls
        # ends on the canned fallback and the user is told nothing about what
        # changed. Reasoning makes that more likely, because deliberating
        # costs rounds.
        for attempt in range(max_rounds + 1):
            wrap_up = attempt == max_rounds
            result: providers.RoundResult | None = None
            # The client's `read` timeout becomes a per-chunk budget once we
            # stream, which is why `timeout` is still passed through as a
            # plain float.
            async for event in adapter.stream(
                client, conversation, system=system, tools=tools, wrap_up=wrap_up
            ):
                kind = event.get("kind")
                if kind == providers.ROUND:
                    result = event["result"]
                elif kind in (providers.DELTA, providers.REASONING):
                    yield {"kind": kind, "text": event["text"]}
            if result is None:  # pragma: no cover -- adapters raise instead
                raise HTTPException(status_code=502, detail="Chat request failed")

            # Telemetry, not an answer: the router persists this and relays no
            # frame for it. `source` is what entered this round, so the size it
            # records is the size that was billed.
            yield {
                "kind": "usage",
                "model": spec.id,
                "round_index": attempt,
                "prefer_full_source": prefer_full_source,
                "source_chars": len(source),
                **result.usage,
            }
            for trace in result.searches:
                yield {"kind": "tool", "trace": trace}

            if result.calls:
                conversation.add_round(result.raw)
                # This round's words would otherwise be overwritten by a later
                # round. Promote them to a real message, above their own tool
                # cards, instead of letting the user watch them vanish.
                if result.text.strip():
                    emitted_assistant = True
                    yield {"kind": "assistant", "content": result.text}
                outputs: list[providers.ToolResult] = []
                for call in result.calls:
                    name = call.name
                    args = call.arguments
                    if name == READ_TOOL:
                        payload = json.dumps({"source": source})
                    elif name == ATS_TOOL:
                        report = await asyncio.to_thread(read_ats_report, source)
                        payload = json.dumps(report, ensure_ascii=False)
                    elif name == LAYOUT_TOOL:
                        report = await read_layout_report(source, args)
                        payload = json.dumps(report, ensure_ascii=False)
                        vision_usage = _vision_usage_event(
                            report, spec.id, attempt, prefer_full_source, len(source)
                        )
                        if vision_usage is not None:
                            yield vision_usage
                    elif name == EDIT_TOOL:
                        try:
                            before = source
                            source = apply_typst_edit(
                                source, args, prefer_full_source=prefer_full_source
                            )
                            source = sanitize_typst_source(source)
                            payload = json.dumps(
                                edit_result_payload(before, source, args),
                                ensure_ascii=False,
                            )
                            if verify_compile and source != before:
                                parsed = json.loads(payload)
                                # One compile answers both questions. The
                                # layout digest rides home on the edit the
                                # agent just made, so seeing what it did to the
                                # page costs no extra round and no extra
                                # compile.
                                digest = await asyncio.to_thread(compile_digest, source)
                                parsed["compile"] = digest["compile"]
                                if "layout" in digest:
                                    parsed["layout"] = digest["layout"]
                                if parsed["compile"].get("ok") is False:
                                    parsed["hint"] = (
                                        "Typst compile failed. Escape special "
                                        "characters (\\\" \\$ \\@ \\* in the right "
                                        "context) and call apply_typst_edit with "
                                        "only full source."
                                    )
                                payload = json.dumps(parsed, ensure_ascii=False)
                        except ValueError as exc:
                            payload = json.dumps(
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
                        payload = tool_handlers[name](args)
                    else:
                        payload = json.dumps({"error": f"unknown tool {name}"})
                    trace = _tool_trace(name or "unknown", args, payload)
                    yield {"kind": "tool", "trace": trace}
                    parsed_result = trace.get("result")
                    if (
                        name == EDIT_TOOL
                        and isinstance(parsed_result, dict)
                        and parsed_result.get("changed")
                    ):
                        yield {"kind": "source", "typst_source": source}
                        compile_info = parsed_result.get("compile")
                        if (
                            isinstance(compile_info, dict)
                            and compile_info.get("ok") is False
                        ):
                            retry_nudge = COMPILE_RETRY_NUDGE
                        else:
                            retry_nudge = None
                    elif _edit_needs_retry(name, parsed_result):
                        retry_nudge = EDIT_RETRY_NUDGE
                    outputs.append(
                        providers.ToolResult(
                            call=call, output=_tool_output_for_model(payload)
                        )
                    )
                conversation.add_tool_results(outputs)
                continue

            assistant_text = result.text
            # The reserved round is always terminal: it exists to produce the
            # answer, so a leftover nudge must not send it round again.
            if retry_nudge and not wrap_up:
                conversation.add_round(result.raw)
                # Same reasoning as the tool branch: the nudge abandons this
                # round, so its words are emitted now rather than dropped.
                if assistant_text.strip():
                    emitted_assistant = True
                    yield {"kind": "assistant", "content": assistant_text}
                    assistant_text = ""
                conversation.add_nudge(retry_nudge)
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
    model: str | None = None,
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
        model=model,
        timeout=120.0 if prefer_full_source else 90.0,
        extra_system=extra,
        max_rounds=8 if (prefer_full_source or verify_compile) else 6,
        prefer_full_source=prefer_full_source,
        verify_compile=verify_compile,
        extra_tools=extra_tools,
        tool_handlers=tool_handlers,
    ):
        yield event
