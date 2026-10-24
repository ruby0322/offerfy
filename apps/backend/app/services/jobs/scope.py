"""Which postings Offerfy carries.

Offerfy sells resume craft to technical candidates chasing competitive roles, so
a Finance Manager at a great engineering company is out of scope even though the
company is in scope. The board list decides *which employers*; this decides
*which of their postings*.

Deny needles come in two tiers, which a single list cannot express:

* `deny_always` -- the role itself is non-technical whatever domain it serves.
  "Technical Recruiter, Hardware" is a recruiting job, and leaked through a
  single-tier list because "hardware" rescued it.
* `deny_context` -- a domain rather than a role, so a keep needle overrides it.
  "Compliance Engineer" and "Engineering Manager, Billing" are engineers.

Both tiers were tuned against the live catalogue, not from imagination.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


_BACKEND_ROOT = Path(__file__).resolve().parents[3]


def scope_path() -> Path:
    return _BACKEND_ROOT / "data" / "job_scope.json"


@dataclass(frozen=True)
class ScopeRules:
    keep_title: tuple[str, ...]
    deny_always: tuple[str, ...]
    deny_context: tuple[str, ...]
    keep_function_ids: frozenset[str]


def load_scope(path: Path | None = None) -> ScopeRules:
    data = json.loads((path or scope_path()).read_text(encoding="utf-8"))
    return ScopeRules(
        keep_title=tuple(n.casefold() for n in data.get("keep_title", []) if n),
        deny_always=tuple(n.casefold() for n in data.get("deny_always", []) if n),
        deny_context=tuple(n.casefold() for n in data.get("deny_context", []) if n),
        keep_function_ids=frozenset(
            str(f).casefold() for f in data.get("keep_function_ids", []) if f
        ),
    )


@lru_cache(maxsize=1)
def default_scope() -> ScopeRules:
    return load_scope()


def title_in_scope(title: str, rules: ScopeRules | None = None) -> bool:
    """Whether a posting's title belongs in the catalogue.

    Unknown titles are kept. A false negative silently loses a good job and
    nobody notices; a false positive is visible on the jobs page and can be
    fixed by adding a needle.
    """
    config = rules or default_scope()
    folded = f" {title.casefold()} "
    if any(needle in folded for needle in config.deny_always):
        return False
    if not any(needle in folded for needle in config.deny_context):
        return True
    return any(needle in folded for needle in config.keep_title)


def function_in_scope(function_id: str | None, rules: ScopeRules | None = None) -> bool:
    """For sources that publish a structured role taxonomy (SmartRecruiters).

    Checked before fetching a posting's description, which costs one request
    each. A missing function is kept -- see title_in_scope on why we lean that
    way -- and the title rule still gets its say afterwards.
    """
    if not function_id:
        return True
    config = rules or default_scope()
    return function_id.casefold() in config.keep_function_ids
