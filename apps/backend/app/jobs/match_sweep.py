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
   as a feature. The check lives INSIDE prefilter_query, which is the honest
   place for it: "no preference" translates to the empty set, not to the whole
   catalogue. Every reader of the prefilter gets the same answer, including
   the digest candidate selection of a later slice, which reads
   prefilter_query directly and would otherwise hand 20k-odd postings to every
   user who has told us nothing.

3. A target seniority OFF the ladder is not an error and not "everything".
   `manager` is matched EXACTLY (see _band): it is a different axis from the
   experience ladder, not a rung on it, and the exact match is the only route
   any preference has to a posting whose facet seniority is `manager`.
   `unknown` is refused at the API boundary instead (schemas_jobs.py): as a
   stated target it is meaningless, though it stays a valid /jobs browse
   filter, where it means "show me the unclassified".

count_matches takes a preference that may be TRANSIENT (current_or_seeded
returns an un-persisted row). Nothing here reads pref.id or touches the session
with it.

Scope: the applications exclusion is in place (`_exclusions`), and it is the
only one. The spec's second exclusion -- postings in the user's last four
digests -- was considered when the digest tables arrived and deliberately not
built: see `_exclusions` for why neither digest track wants it.
"""

from __future__ import annotations

from datetime import datetime, timezone

import json

from sqlalchemy import and_, exists, false, func, nulls_last, or_, select
from sqlalchemy.orm import Query, Session

from app.models import Application, Job, JobFacets, JobPreference, JobScore
from app.services.jobs.enrich import (
    default_taxonomy,
    taxonomy_path,
    valid_functions,
    valid_seniorities,
)
from app.services.jobs.store import freshness_cutoff

# Low to high, read from data/job_taxonomy.json's seniority_ladder. The order
# cannot come from seniority_rules: that is ordered by match PRIORITY.
_taxonomy = default_taxonomy()
_rule_members = valid_seniorities(_taxonomy)

# seniority_rules lists "manager" and valid_seniorities() adds "unknown", but
# neither is a level. "unknown" means no rule matched; "manager" is a different
# axis (people management, not experience). They are deliberately absent from
# the ladder: left in, "unknown" would sit at index 0 and a target of intern
# would admit every unclassified posting. Neither is ever admitted by band
# arithmetic -- a target of `senior` never reaches a `manager` posting.
#
# But off the ladder is not the same as unusable as a TARGET. `manager` is a
# real thing to want, enrich.py stamps it on real postings (Engineering
# Manager, 產品經理, Director of Engineering), and the preference seeder writes
# it from the person's own role title. _band therefore matches it EXACTLY:
# exact-match axis, not a band. Treating it as unplaceable gave a seeded
# manager a permanent, confident count of 0 and made that whole slice of the
# catalogue unreachable through preferences.
_EXACT_AXIS = frozenset({"manager"})
# Not a level and not a target either: schemas_jobs.py refuses it as a
# preference target, so it reaches _band only from a hand-built row.
_NOT_A_LEVEL = frozenset({"unknown"})
_OFF_LADDER = _EXACT_AXIS | _NOT_A_LEVEL

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

# A posting titled "Software Engineer" with no specialisation in its title
# carries function `software` -- the fallback tier's value, see
# enrich.function_of. 899 active postings when this was built, and the whole
# reason the tier exists is that they must be reachable: a target of `backend`
# sees 506 backend postings and would otherwise miss every generic one that
# suits it exactly as well.
#
# So the function axis is not exact, it is family-aware, and ASYMMETRICALLY:
#   a specialised target  -> that specialisation PLUS the generic postings
#   the generic target    -> the whole family
# A specialist wants their own field plus the unspecialised roles, not a
# frontend job because they picked backend. Someone who picks the generic
# target has named no specialisation, so narrowing them to only the 899
# generic postings would be worse than the unconstrained count they get
# today -- the family is what they mean.
#
# Functions outside the family (product, design, hardware, other) stay exact:
# a generic software posting does not suit a product manager.
_GENERIC_FUNCTION = "software"
_SOFTWARE_FAMILY = frozenset(
    {_GENERIC_FUNCTION, "backend", "frontend", "fullstack", "ml", "data", "infra", "security"}
)

# Catches a renamed or removed function in job_taxonomy.json: a family member
# that is no longer a real facet value would silently widen nothing.
_not_functions = _SOFTWARE_FAMILY - valid_functions(_taxonomy)
if _not_functions:  # pragma: no cover
    raise RuntimeError(
        f"_SOFTWARE_FAMILY names functions the taxonomy does not define: "
        f"{_not_functions}"
    )


def expand_functions(targets: list[str]) -> list[str]:
    """The facet values a set of target functions admits.

    Public because the digest candidate selection of a later slice reads the
    prefilter directly and must not re-derive this rule differently.
    """
    admitted: set[str] = set()
    for target in targets:
        if target == _GENERIC_FUNCTION:
            admitted |= _SOFTWARE_FAMILY
        elif target in _SOFTWARE_FAMILY:
            admitted |= {target, _GENERIC_FUNCTION}
        else:
            admitted.add(target)
    return sorted(admitted)


def _band(target: str | None) -> list[str] | None:
    """The facet seniorities a target admits.

    A target ON the ladder admits its own rung and its neighbours, clamped at
    both ends. `manager` is not on the ladder and is matched EXACTLY: it is a
    different axis (people management) rather than a rung, so "within one
    band" has no meaning for it, and because nothing else can emit it this is
    the only route a preference has to a `manager` posting.

    None means no seniority constraint. [] means a target we cannot place,
    which matches nothing rather than everything -- defence in depth only,
    since the API refuses every value that is neither a rung nor `manager`.
    """
    target = (target or "").strip()
    if not target:
        return None
    if target in _EXACT_AXIS:
        return [target]
    if target not in SENIORITY_LADDER:
        return []
    i = SENIORITY_LADDER.index(target)
    return list(SENIORITY_LADDER[max(0, i - 1) : i + 2])


def _tokens(delimited: str | None) -> list[str]:
    return [t for t in (delimited or "").lower().split("|") if t]


def _any_token(column, tokens: list[str]):
    # Whole-token match, never a substring: 'tw' must not match 'TWX' and 'ml'
    # must not match 'html'. Every column this is called with (JobFacets
    # .function, .country, .region, .city) holds a SINGLE token rather than a
    # pipe list, so an IN on lower(col) asks the same question the pipe-padded
    # LIKE did, without the wildcard escaping or the string concatenation. It is
    # NOT index-usable as written: the schema indexes only the raw columns, and
    # a plain btree cannot serve lower(col). A functional index on lower(col)
    # is what would make it so, and is where the remaining win is.
    return func.lower(column).in_(tokens)


def _exclusions(query: Query, pref: JobPreference) -> Query:
    """Drop every posting this person already has an application for.

    Any status counts: saved, tailored, applied, interviewing and closed all
    mean "acted on", so "N new matches" means "not yet acted on". It is a
    correlated NOT EXISTS on (user_id, job_id), not a fetched id set, because
    a later slice runs this per active user on a schedule.

    A posting the person deliberately skipped keeps counting: skipping is not
    recorded.

    The recent-digest exclusion this was left open for is NOT wanted, and the
    seam is closed rather than filled. Neither digest track asks for it.
    `strong_fit` repeats a posting deliberately until the person acts on it --
    excluding on "already digested" would make a strong match vanish because
    they were busy for a week, and acting on it is exactly what writes the
    applications row above. `new_this_week` is bounded by its own window
    (`first_seen_at` inside the period), so a posting leaves it by ageing, not
    by being excluded. Adding the exclusion here would also quietly change
    `count_matches`, which is the number the match-count card shows and has
    nothing to do with digests. See app/services/jobs/digest.py.
    """
    acted_on = (
        select(1)
        .where(Application.user_id == pref.user_id, Application.job_id == Job.id)
        .exists()
    )
    return query.filter(~acted_on)


def constrains_nothing(pref: JobPreference) -> bool:
    return (
        not _tokens(pref.target_functions)
        and not _tokens(pref.target_locations)
        and not (pref.target_seniority or "").strip()
        and pref.remote_required is None
    )


def prefilter_query(db: Session, pref: JobPreference) -> Query:
    """The hard-filtered set: every posting this preference could match.

    A preference that constrains nothing returns the EMPTY set, not the whole
    catalogue (ruling 2 in the module docstring). The guard lives here rather
    than in count_matches so that every reader of the prefilter -- including
    the digest candidate selection of a later slice, which reads this function
    directly -- gets the same answer.
    """
    q = (
        db.query(Job)
        .join(JobFacets, JobFacets.job_id == Job.id)
        .filter(Job.is_active.is_(True), Job.last_seen_at >= freshness_cutoff())
    )

    if constrains_nothing(pref):
        return _exclusions(q.filter(false()), pref)

    functions = _tokens(pref.target_functions)
    if functions:
        q = q.filter(_any_token(JobFacets.function, expand_functions(functions)))

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


def count_matches(db: Session, pref: JobPreference) -> int:
    """How many live postings this preference matches."""
    # Harmlessly redundant since the guard moved into prefilter_query; kept so
    # the cheap answer costs no round trip.
    if constrains_nothing(pref):
        return 0
    return prefilter_query(db, pref).count()


def match_preview(db: Session, pref: JobPreference, limit: int) -> list[Job]:
    """The newest few of the very same set count_matches counts.

    Ordered like the jobs page's own list -- newest posted first, postings
    with no posted_at last -- so the rows the dashboard shows are the rows
    /jobs would put at the top, not an arbitrary page of the match set.

    Reads through prefilter_query rather than re-deriving the filters, so a
    preview can never disagree with the count above it.
    """
    if limit <= 0 or constrains_nothing(pref):
        return []
    return (
        prefilter_query(db, pref)
        .order_by(nulls_last(Job.posted_at.desc()), Job.id.desc())
        .limit(limit)
        .all()
    )


def arrival_rows(
    db: Session,
    user_id: str,
    pref: JobPreference,
    since: datetime | None,
    limit: int,
) -> list[tuple[Job, JobScore | None]]:
    """What is new to this person: (posting, score-or-None) pairs, newest first.

    An arrival is one of two things, and they come from different places.

    1. A score row for this person whose scored_at is after `since`. No
       preference filter and no is_active filter: we scored it because it
       matched at the time, which is a fact about what happened to them, and
       editing the preference later does not un-happen it. The posting travels
       as it is now, so one that closed since being scored comes back with
       is_active False for the page to say so -- the inactive case is not an
       exception, it is just what the row says.
    2. A posting the CURRENT preference matches (the prefilter, the only place
       the current preference belongs) that this person has no score row for
       yet. A broad preference fills in over several sweep passes, so for up to
       a day the prefilter holds postings job_scores has not reached; hiding
       them would make match_count disagree with the list under it, and
       showing them unlabelled would read as "poor match" rather than "not
       looked at yet". They come back with score None. They are windowed on
       first_seen_at, so a posting already seen unscored is not "new" again
       until it has a score (when it becomes kind 1).

    `since=None` is a first visit: everything, up to `limit`.
    """
    scored = (
        db.query(Job, JobScore)
        .join(JobScore, and_(JobScore.job_id == Job.id, JobScore.user_id == user_id))
    )
    if since is not None:
        scored = scored.filter(JobScore.scored_at > since)
    rows: list[tuple[Job, JobScore | None]] = [
        (job, score)
        for job, score in scored.order_by(JobScore.scored_at.desc(), Job.id.desc())
        .limit(limit)
        .all()
    ]

    matching = prefilter_query(db, pref).with_entities(Job.id).subquery()
    waiting = db.query(Job).filter(
        Job.id.in_(select(matching.c.id)),
        ~exists().where(
            and_(JobScore.job_id == Job.id, JobScore.user_id == user_id)
        ),
    )
    if since is not None:
        waiting = waiting.filter(Job.first_seen_at > since)
    rows.extend(
        (job, None)
        for job in waiting.order_by(Job.first_seen_at.desc(), Job.id.desc())
        .limit(limit)
        .all()
    )

    def recency(pair):
        job, score = pair
        return score.scored_at if score is not None else job.first_seen_at

    def aware(dt):
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    rows.sort(key=lambda pair: (aware(recency(pair)), pair[0].id), reverse=True)
    return rows[:limit]
