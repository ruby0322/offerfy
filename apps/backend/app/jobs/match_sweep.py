"""The deterministic per-user prefilter: hard filters only, no LLM, no scoring.

Two rulings, both defensible the other way, recorded so a later reader knows
which reading was chosen and why.

1. An empty axis is NO CONSTRAINT on that axis. A person who has told us
   nothing about location should still see matches rather than none. What
   never relaxes: is_active, the freshness cutoff, and the existence of a
   JobFacets row (a posting that was never enriched cannot be matched).

2. A preference that constrains NOTHING matches NOTHING. No functions, no
   locations, no seniority and remote_required None is not "a filter that
   matches everything", it is the absence of a match profile. The preference
   seeder hands exactly that row to every user with no resume, and telling
   someone who has said nothing that they have 21,773 matches is a lie dressed
   as a feature. The check lives in count_matches, not in the query, so
   prefilter_query stays a pure translation of the preference to SQL.

count_matches takes a preference that may be TRANSIENT (current_or_seeded
returns an un-persisted row). Nothing here reads pref.id or touches the session
with it.

Scope: the spec also excludes postings with an applications row and postings in
the user's last four digests. Those tables do not exist until later slices;
`_exclusions` is the one seam where both attach.
"""

from __future__ import annotations

from sqlalchemy import func, or_
from sqlalchemy.orm import Query, Session

from app.models import Job, JobFacets, JobPreference
from app.services.jobs.enrich import default_taxonomy, valid_seniorities
from app.services.jobs.store import freshness_cutoff

# Low to high. The taxonomy cannot supply this order: seniority_rules is
# ordered by match PRIORITY (staff_plus before senior, mid last as a catch-all),
# not by level. The membership is checked against the taxonomy below so a new
# rung added there fails loudly instead of silently dropping off the ladder.
_LEVELS = ("intern", "new_grad", "junior", "mid", "senior", "staff_plus")

# seniority_rules lists both of these and valid_seniorities() adds "unknown",
# but neither is a level. "unknown" means no rule matched; "manager" is a
# different axis (people management, not experience). Removing them from the
# ladder is deliberate: left in, "unknown" would sit at index 0 and a target of
# intern would admit every unclassified posting. They are never admitted by
# band arithmetic.
_OFF_LADDER = frozenset({"manager", "unknown"})

SENIORITY_LADDER: tuple[str, ...] = tuple(
    s for s in _LEVELS if s in valid_seniorities(default_taxonomy())
)

_unclassified = valid_seniorities(default_taxonomy()) - set(SENIORITY_LADDER) - _OFF_LADDER
if _unclassified:  # pragma: no cover - fires only when the taxonomy grows
    raise RuntimeError(f"seniority rungs neither on the ladder nor off it: {_unclassified}")


def _band(target: str | None) -> list[str] | None:
    """The target rung and its neighbours, clamped at both ends.

    None means no seniority constraint; [] means a target we cannot place,
    which matches nothing rather than everything.
    """
    if not target:
        return None
    if target not in SENIORITY_LADDER:
        return []
    i = SENIORITY_LADDER.index(target)
    return list(SENIORITY_LADDER[max(0, i - 1) : i + 2])


def _tokens(delimited: str | None) -> list[str]:
    return [t for t in (delimited or "").lower().split("|") if t]


def _any_token(column, tokens: list[str]):
    # Whole-token match against the pipe-delimited value, never a substring:
    # '|tw|' must not match 'TWX' and '|ml|' must not match 'html'.
    padded = "|" + func.lower(column) + "|"
    return or_(*[padded.like(f"%|{t}|%") for t in tokens])


def _exclusions(query: Query, pref: JobPreference) -> Query:
    """Seam for the applications and recent-digest exclusions (later slices)."""
    return query


def prefilter_query(db: Session, pref: JobPreference) -> Query:
    q = (
        db.query(Job)
        .join(JobFacets, JobFacets.job_id == Job.id)
        .filter(Job.is_active.is_(True), Job.last_seen_at >= freshness_cutoff())
    )

    functions = _tokens(pref.target_functions)
    if functions:
        q = q.filter(_any_token(JobFacets.function, functions))

    band = _band(pref.target_seniority)
    if band is not None:
        q = q.filter(JobFacets.seniority.in_(band))

    if pref.remote_required is True:
        q = q.filter(Job.remote.is_(True))
    elif pref.remote_required is False:
        q = q.filter(Job.remote.is_(False))

    locations = _tokens(pref.target_locations)
    if locations:
        in_place = or_(
            *[
                _any_token(col, locations)
                for col in (JobFacets.country, JobFacets.region, JobFacets.city)
            ]
        )
        # A remote posting satisfies a place filter it does not match, but only
        # for someone who asked for remote.
        if pref.remote_required is True:
            in_place = or_(in_place, Job.remote.is_(True))
        q = q.filter(in_place)

    return _exclusions(q, pref)


def _constrains_nothing(pref: JobPreference) -> bool:
    return (
        not _tokens(pref.target_functions)
        and not _tokens(pref.target_locations)
        and not pref.target_seniority
        and pref.remote_required is None
    )


def count_matches(db: Session, pref: JobPreference) -> int:
    if _constrains_nothing(pref):
        return 0
    return prefilter_query(db, pref).count()
