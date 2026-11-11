"""Deterministic facets derived from a posting's title (and, for function,
its description).

Offerfy's catalogue gives every posting only free-text title/company/location.
Two of the planned filters -- seniority and function -- can be read off the
title alone most of the time, the way a human skims a job board: the title
names a level ("Staff", "Senior", "Intern") and/or a role ("Backend Engineer",
"Product Designer"). Both derivations are ordered-list, first-match-wins
scans, the same shape as scope.py's title_in_scope, for the same reason: it
keeps the vocabulary in data (job_taxonomy.json) instead of in branching
code, so tuning a rule never touches this module.

Unknown is kept, not guessed into the nearest bucket -- same bias as
scope.py. A title that names no seniority level at all, including the
generic "engineer"/"developer"/... rule, is seniority "unknown" rather than
a silent "mid"; "mid" is just the last, most generic rule in the taxonomy
file, not special-cased here. A title (and description) that names no
function at all is function "other" rather than a silent guess, because
"other" is still a real, useful bucket on a filter UI whereas a wrong guess
erodes trust in all of them.

`function_of` checks title needles across every rule before it checks any
rule's description needles -- a title is the employer's deliberate choice of
what the role is; a description is boilerplate that happens to mention
adjacent technologies, so a title hit always outranks a description hit,
regardless of rule order.

`load_taxonomy` also loads the location vocabulary (`location_countries`,
`location_regions`, `remote_needles`) even though nothing in this module
consumes it yet -- that is for the location-facet derivation landing in the
next task, which should not need to touch this loader or the shape of
job_taxonomy.json.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


_BACKEND_ROOT = Path(__file__).resolve().parents[3]


def taxonomy_path() -> Path:
    return _BACKEND_ROOT / "data" / "job_taxonomy.json"


@dataclass(frozen=True)
class SeniorityRule:
    seniority: str
    needles: tuple[str, ...]


@dataclass(frozen=True)
class FunctionRule:
    function: str
    title_needles: tuple[str, ...]
    description_needles: tuple[str, ...]


@dataclass(frozen=True)
class Taxonomy:
    seniority_rules: tuple[SeniorityRule, ...]
    function_rules: tuple[FunctionRule, ...]
    location_countries: dict[str, str]
    location_regions: dict[str, str]
    remote_needles: tuple[str, ...]


def load_taxonomy(path: Path | None = None) -> Taxonomy:
    data = json.loads((path or taxonomy_path()).read_text(encoding="utf-8"))
    seniority_rules = tuple(
        SeniorityRule(
            seniority=rule["seniority"],
            needles=tuple(n.casefold() for n in rule.get("any", []) if n),
        )
        for rule in data.get("seniority_rules", [])
    )
    function_rules = tuple(
        FunctionRule(
            function=rule["function"],
            title_needles=tuple(
                n.casefold() for n in rule.get("title_any", []) if n
            ),
            description_needles=tuple(
                n.casefold() for n in rule.get("description_any", []) if n
            ),
        )
        for rule in data.get("function_rules", [])
    )
    location_countries = {
        needle.casefold(): value
        for needle, value in data.get("location_countries", {}).items()
        if needle
    }
    location_regions = {
        needle.casefold(): value
        for needle, value in data.get("location_regions", {}).items()
        if needle
    }
    remote_needles = tuple(
        n.casefold() for n in data.get("remote_needles", []) if n
    )
    return Taxonomy(
        seniority_rules=seniority_rules,
        function_rules=function_rules,
        location_countries=location_countries,
        location_regions=location_regions,
        remote_needles=remote_needles,
    )


@lru_cache(maxsize=1)
def default_taxonomy() -> Taxonomy:
    return load_taxonomy()


def seniority_of(title: str, taxonomy: Taxonomy | None = None) -> str:
    """First seniority rule whose needle appears in the padded title wins.

    No needle is hardcoded here -- see the module docstring on why "mid" is
    just data, the last and most generic rule in job_taxonomy.json. A title
    matching no rule at all, not even that one, is "unknown".
    """
    config = taxonomy or default_taxonomy()
    folded = f" {title.casefold()} "
    for rule in config.seniority_rules:
        if any(needle in folded for needle in rule.needles):
            return rule.seniority
    return "unknown"


def function_of(
    title: str, description_text: str, taxonomy: Taxonomy | None = None
) -> str:
    """Title needles, across every rule, win over any rule's description
    needles. See the module docstring for why.
    """
    config = taxonomy or default_taxonomy()
    folded_title = f" {title.casefold()} "
    for rule in config.function_rules:
        if any(needle in folded_title for needle in rule.title_needles):
            return rule.function
    folded_description = f" {description_text.casefold()} "
    for rule in config.function_rules:
        if any(needle in folded_description for needle in rule.description_needles):
            return rule.function
    return "other"
