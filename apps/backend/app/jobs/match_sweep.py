"""The deterministic per-user prefilter: hard filters only, no LLM, no scoring.

Two rulings, both defensible the other way, recorded so a later reader knows
which reading was chosen and why.

1. An empty axis is NO CONSTRAINT on that axis. A person who has told us
   nothing about location should still see matches rather than none. What
   never relaxes: is_active, the freshness cutoff, and the existence of a
   JobFacets row (a posting that was never enriched cannot be matched).

   remote_required is a tri-state on Job.remote. True keeps only remote=true
   (an unlabelled posting is not claimed remote) and makes target_locations
   inert, on purpose: a remote role's country is not where the person works.
   False keeps remote IS NOT true, because NULL means unknown and is the
   large majority of postings. Consequently remote_required=False alone is a
   real profile under constrains_nothing and matches most of the catalogue;
   that is intended.

2. A preference that constrains NOTHING matches NOTHING. No functions, no
   locations, no seniority and remote_required None is not "a filter that
   matches everything", it is the absence of a match profile. The preference
   seeder hands exactly that row to every user with no resume, and telling
   someone who has said nothing that they have a large match count is a lie dressed
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

import json

from sqlalchemy import func, or_
from sqlalchemy.orm import Query, Session

from app.models import Job, JobFacets, JobPreference
from app.services.jobs.enrich import default_taxonomy, taxonomy_path, valid_seniorities
from app.services.jobs.store import freshness_cutoff

# Low to high, read from data/job_taxonomy.json's seniority_ladder. The order
# cannot come from seniority_rules: that is ordered by match PRIORITY.
_taxonomy = default_taxonomy()
_rule_members = valid_seniorities(_taxonomy)

# seniority_rules lists "manager" and valid_seniorities() adds "unknown", but
# neither is a level. "unknown" means no rule matched; "manager" is a different
# axis (people management, not experience). They are deliberately absent from
# the ladder: left in, "unknown" would sit at index 0 and a target of intern
# would admit every unclassified posting. Never admitted by band arithmetic.
_OFF_LADDER = frozenset({"manager", "unknown"})

SENIORITY_LADDER: tuple[str, ...] = tuple(
    json.loads(taxonomy_path().read_text(encoding="utf-8"))["seniority_ladder"]
)

# Catches a rename (rung no longer a rule) and a new rung (member on neither
# the ladder nor the off-ladder list).
_bad = set(SENIORITY_LADDER) - _rule_members
_unclassified = _rule_members - set(SENIORITY_LADDER) - _OFF_LADDER
if _bad or _unclassified or set(SENIORITY_LADDER) & _OFF_LADDER:  # pragma: no cover
    raise RuntimeError(
        f"seniority_ladder out of step with seniority_rules: "
        f"not a rule={_bad}, unclassified={_unclassified}"
    )


def _band(target: str | None) -> list[str] | None:
    """The target rung and its neighbours, clamped at both ends.

    None means no seniority constraint; [] means a target we cannot place,
    which matches nothing rather than everything.
    """
    target = (target or "").strip()
    if not target:
        return None
    if target not in SENIORITY_LADDER:
        return []
    i = SENIORITY_LADDER.index(target)
    return list(SENIORITY_LADDER[max(0, i - 1) : i + 2])


def _tokens(delimited: str | None) -> list[str]:
    return [t for t in (delimited or "").lower().split("|") if t]


def _escape(t: str) -> str:
    return t.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _any_token(column, tokens: list[str]):
    # Whole-token match against the pipe-delimited value, never a substring:
    # '|tw|' must not match 'TWX' and '|ml|' must not match 'html'.
    padded = "|" + func.lower(column) + "|"
    # _ and % are LIKE wildcards ('north_america' is a real region): escape.
    return or_(*[padded.like(f"%|{_escape(t)}|%", escape="\\") for t in tokens])


def _exclusions(query: Query, pref: JobPreference) -> Query:
    """Seam for the applications and recent-digest exclusions (later slices)."""
    return query


def prefilter_query(db: Session, pref: JobPreference) -> Query:
    """The hard-filtered set. PRECONDITION: the caller must check
    constrains_nothing(pref) first; for such a preference this returns the whole
    active catalogue, which is not a match list. count_matches checks it."""
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
        # NULL means unknown, not "not remote" (most postings are NULL), so
        # on-site-only excludes only postings known to be remote.
        q = q.filter(Job.remote.is_not(True))

    # Deliberately inert for remote-only: a remote role's country is not where
    # the person works, so target_locations is skipped when remote is required.
    locations = _tokens(pref.target_locations)
    if locations and pref.remote_required is not True:
        q = q.filter(
            or_(
                *[
                    _any_token(col, locations)
                    for col in (JobFacets.country, JobFacets.region, JobFacets.city)
                ]
            )
        )

    return _exclusions(q, pref)


def constrains_nothing(pref: JobPreference) -> bool:
    return (
        not _tokens(pref.target_functions)
        and not _tokens(pref.target_locations)
        and not (pref.target_seniority or "").strip()
        and pref.remote_required is None
    )


def count_matches(db: Session, pref: JobPreference) -> int:
    if constrains_nothing(pref):
        return 0
    return prefilter_query(db, pref).count()
