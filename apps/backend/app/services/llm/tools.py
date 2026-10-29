"""The tool surface, written once in a provider-neutral shape.

The shape is OpenAI's Responses API flat function spec, because that is what
this module has always held and rewriting it would have churned every
description for no gain. `providers.anthropic` translates it -- a flat
`{name, description, parameters}` becomes `{name, description, input_schema}`,
and the `{"type": "web_search"}` entry becomes Anthropic's own server-side
web-search tool. `providers.openai` passes it through untouched.

That translation is one function in one place (`providers.anthropic.tool_specs`)
precisely so that adding a tool stays a single edit here. A caller may pass
`extra_tools` in the same shape -- services/family_tools.py does -- and it
goes through the same translation.
"""

from __future__ import annotations

from app.services.llm.prompts import (
    ATS_TOOL,
    EDIT_TOOL,
    LAYOUT_TOOL,
    READ_TOOL,
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
            "Inspect the rendered resume. Compiles the current document and "
            "returns page count, how full each page is, anything out of place, "
            "and `items`: every rendered item in order, each with the number of "
            "rows it occupies, the points of room left on its last line, and a "
            "`head` of its rendered text. Find the item you mean by its head "
            "and read the numbers -- `rows` above 1 means it wrapped, and "
            "`spare_pt` is how much room is left before it would. "
            "Use `questions` and `scores` for what those numbers cannot "
            "answer, such as how crowded, unbalanced or flat a page looks: a "
            "`question` is yes/no and comes back as a probability, a `score` is "
            "a 0-3 rubric and comes back graded. The geometry reads the page as "
            "one column running top to bottom; where that does not hold it says "
            "`geometry: unreliable`, lists no items, and `questions` is then "
            "the only way to get an answer."
        ),
        "parameters": {
            "type": "object",
            "properties": {
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
