"""Employer slugs: a URL name for a company we only know as a string.

`Job.company` is the whole of an employer's identity here -- there is no
employers table, and that same string is the join to the frontend's mark set
(components/employer-marks.ts). So a slug is *derived*, never stored: whatever
the boards spell today is what `/jobs/employers/<slug>` resolves against, and
renaming a board renames its page the next time ingest runs. That is the
honest behaviour for a catalog whose employer list is a GROUP BY, but it does
mean an employer page is not a permalink the way a posting is.

The derivation folds away case, punctuation and spacing, so it is lossy and
two different companies genuinely can claim one slug -- "Wise" and "WISE"
would, and so would "Scale AI" and "Scale-AI" if both were ever boarded.
`resolve_slug` therefore never trusts a slug on its own: it folds every
company currently in scope and answers only when exactly one claims it. An
ambiguous slug is a 404, not a coin flip between two employers' job lists.

A name with no ASCII alphanumerics at all (a Chinese-only company name, which
the retired taiwanjobs source could still have left behind) folds to the empty
string. Those employers get no slug and no page rather than a row of escaped
percent signs; they are still reachable by `/jobs?company=<name>`.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable

_NON_SLUG = re.compile(r"[^a-z0-9]+")


def employer_slug(company: str) -> str:
    """The URL name for one company, or "" when the name cannot make one."""
    return _NON_SLUG.sub("-", company.strip().casefold()).strip("-")


def slug_index(companies: Iterable[str]) -> dict[str, str]:
    """slug -> company, for the companies whose slug nothing else claims.

    Both halves of a collision are dropped: with no way to tell which
    employer the visitor meant, serving either one's postings under the
    shared slug would be a wrong answer rather than a missing one.
    """
    claimed: dict[str, list[str]] = defaultdict(list)
    for company in companies:
        slug = employer_slug(company)
        if slug:
            claimed[slug].append(company)
    return {slug: names[0] for slug, names in claimed.items() if len(names) == 1}


def resolve_slug(slug: str, companies: Iterable[str]) -> str | None:
    """The company a slug names, or None if no company or several claim it."""
    wanted = employer_slug(slug)
    if not wanted:
        return None
    return slug_index(companies).get(wanted)
