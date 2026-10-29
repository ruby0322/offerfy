"""The resume-editing agent, and the provider layer underneath it.

What lives where, and why it is split this way:

    prompts.py          Every word said to a model. Provider-neutral.
    tools.py            The tool surface, in one shape, translated per vendor.
    turn.py             The agent loop: rounds, tool dispatch, retries.
    providers/base.py   What an adapter has to be, plus the shared SSE reader.
    providers/openai.py The Responses API.
    providers/anthropic.py  The Messages API.
    providers/__init__.py   The registry, keyed by ModelSpec.provider.

The catalogue of models itself is `app.services.model_catalog`, and which one
a given person gets is `app.services.model_choice`. Neither is imported from
here, so the loop cannot accidentally decide a model: it is handed one.

This was a single 848-line module before the picker existed, with the
Responses API's streaming shape, transcript shape and usage field names
interleaved with the product's own retry and compile-verify behaviour. Adding
a second vendor to that would have meant a second copy of the behaviour. The
import surface below is what the rest of the application used then and uses
now, so nothing outside this package had to change.

`import httpx` is load-bearing, not leftover: the test suite drives every
provider by replacing `httpx.AsyncClient`, and several tests reach it through
this module's name.
"""

from __future__ import annotations

import httpx  # noqa: F401  -- see the module docstring

from app.services.llm import providers
from app.services.llm.prompts import (
    ATS_TOOL,
    COMPILE_RETRY_NUDGE,
    EDIT_RETRY_NUDGE,
    EDIT_TOOL,
    LAYOUT_TOOL,
    READ_TOOL,
    SEARCH_TOOL,
    SYSTEM_PROMPT,
    TEMPLATE_SWITCH_EXTRA,
)
from app.services.llm.providers.openai import (
    _output_text,
    responses_url as _responses_url,
)
from app.services.llm.tools import TOOLS
from app.services.llm.turn import (
    aiter_chat_edit,
    aiter_llm_turn,
    llm_configured,
    provider_configured,
)

__all__ = [
    "ATS_TOOL",
    "COMPILE_RETRY_NUDGE",
    "EDIT_RETRY_NUDGE",
    "EDIT_TOOL",
    "LAYOUT_TOOL",
    "READ_TOOL",
    "SEARCH_TOOL",
    "SYSTEM_PROMPT",
    "TEMPLATE_SWITCH_EXTRA",
    "TOOLS",
    "_output_text",
    "_responses_url",
    "aiter_chat_edit",
    "aiter_llm_turn",
    "llm_configured",
    "provider_configured",
    "providers",
]
