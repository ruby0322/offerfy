"""The provider registry. One lookup, keyed by `ModelSpec.provider`.

Adding a vendor is a module in this package plus one line in `_ADAPTERS`. The
turn loop never names a provider, and nothing outside this package imports an
adapter directly -- so a third one cannot be half-wired: either it is in the
table and every model pointing at it works, or it is not and
`tests/test_model_catalog.py::test_every_model_has_an_adapter` fails.
"""

from __future__ import annotations

from app.services.llm.providers import anthropic as anthropic_provider
from app.services.llm.providers import openai as openai_provider
from app.services.llm.providers.base import (
    DELTA,
    REASONING,
    ROUND,
    Conversation,
    Provider,
    RoundResult,
    ToolCall,
    ToolResult,
)

_ADAPTERS: dict[str, Provider] = {
    openai_provider.NAME: openai_provider.OpenAIProvider(),
    anthropic_provider.NAME: anthropic_provider.AnthropicProvider(),
}


def for_provider(name: str) -> Provider | None:
    return _ADAPTERS.get(name)


def for_spec(spec) -> Provider:
    """The adapter for a model.

    Raises rather than falling back. A model whose provider has no adapter is
    a catalogue bug, not a runtime condition to degrade through: silently
    running it on the other vendor's API would send Anthropic a Responses body
    or vice versa, and the failure would surface as an unexplained 502 on the
    user's turn instead of as the configuration error it is.
    """
    adapter = _ADAPTERS.get(spec.provider)
    if adapter is None:
        raise KeyError(f"no adapter for provider {spec.provider!r}")
    return adapter


def names() -> tuple[str, ...]:
    return tuple(_ADAPTERS)


__all__ = [
    "DELTA",
    "REASONING",
    "ROUND",
    "Conversation",
    "Provider",
    "RoundResult",
    "ToolCall",
    "ToolResult",
    "for_provider",
    "for_spec",
    "names",
]
