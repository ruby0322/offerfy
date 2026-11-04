"""Per-turn tools for the resume import agent.

Mirrors `app/services/family_tools.py`'s shape: module-level flat spec
dicts, a `*_specs()` function, and a `*_handlers()` function returning
closures over `db` and the run. See that module's docstring for why the
shape is flat and provider-neutral -- `app/services/llm/tools.py`
translates it per provider.

The import agent gets exactly one tool here, `declare_import_plan`. The
other half of the plan/progress protocol -- saying which declared step a
given edit fulfils -- does not get a second tool: it rides on the existing
`apply_typst_edit` tool's new optional `step` argument (see
`app/services/llm/tools.py`'s `EDIT_TOOL` entry), because the import's
round budget cannot afford a dedicated "mark step done" call on top of
every edit.

`declare_import_plan`'s handler does no validation of its own beyond
defaulting a missing `steps` list to empty -- `import_run.declare_plan`
already sanitises and bounds whatever the model sends (truncating,
de-duplicating, capping at `MAX_STEPS`), so re-checking shapes here would
just duplicate that logic. The handler's return value is deliberately
tiny (`{"accepted": n}`): tool results are fed back into the model's own
context on the next round, so the full step list is never echoed back.
"""

from __future__ import annotations

import json
from typing import Callable

from sqlalchemy.orm import Session

from app.models import ResumeImport
from app.services.import_run import declare_plan

DECLARE_PLAN_TOOL = "declare_import_plan"

_DECLARE_PLAN = {
    "type": "function",
    "name": DECLARE_PLAN_TOOL,
    "description": (
        "Declare your plan for importing this resume, before making any "
        "edits. Look at the uploaded document and list the sections you "
        "actually found, in the order you intend to work through them. You "
        "decide how many sections there are and what to call each one -- "
        "nothing here is fixed: one resume might have publications, "
        "military service, or certifications, another might have no "
        "education section at all. Give each step a short, stable `key` "
        "you invent (e.g. 'work_history', 'skills', 'awards' -- these are "
        "only examples of the shape, not a checklist) and a human-readable "
        "`label` for a progress card (e.g. 'Work History'). "
        "After declaring the plan, every apply_typst_edit call you make "
        "while working on a given section must pass that same `key` back "
        "as apply_typst_edit's own `step` argument -- that is how you "
        "report progress on this plan; there is no separate tool for "
        "marking a step done. You may call this again later to add or "
        "rename steps if you discover more sections partway through; a "
        "step whose `key` survives keeps its progress."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "label": {"type": "string"},
                    },
                },
                "description": (
                    "The sections you found in the uploaded resume, in the "
                    "order you will edit them. You name them and decide how "
                    "many there are."
                ),
            },
        },
        "additionalProperties": False,
    },
}


def import_tool_specs() -> list[dict]:
    return [_DECLARE_PLAN]


def import_tool_handlers(db: Session, run: ResumeImport) -> dict[str, Callable[[dict], str]]:
    def declare(args: dict) -> str:
        steps = args.get("steps")
        if not isinstance(steps, list):
            steps = []
        accepted = declare_plan(db, run, steps)
        return json.dumps({"accepted": len(accepted)}, ensure_ascii=False)

    return {DECLARE_PLAN_TOOL: declare}
