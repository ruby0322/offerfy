"""A read-through cache over `score.score_candidates`: a score is bought once.

Before this module, every weekly digest build paid a provider for every
(resume, posting) pair, including the pairs it scored the week before, and
`strong_fit` deliberately re-shows a posting until the person acts on it, so
repeat scoring is the normal case. `JobScore` holds one judgement per
(user, posting); `cached_scores` answers from it and buys only what it lacks.
That is what makes scoring a new posting four times a day affordable later:
a pass over an unchanged pool costs nothing. See section 2 of
docs/superpowers/specs/2026-11-19-onboarding-first-run-design.md.

WHAT "STALE" COMPARES TO. This is the one thing in the module a reader will be
tempted to "simplify", so it is spelled out.

A cached row is stale when `JobScore.facts_input_hash` differs from the
`input_hash` on the person's STORED `ResumeFacts` row (the `facts` argument),
or when `JobScore.description_hash` differs from the posting's current
`JobFacets.description_hash`. It is NEVER compared against
`resume_facts.input_hash(...)` recomputed from the live resume.
`resume_facts.facts_are_stale()` answers a different question -- "has the
resume moved since the facts were extracted?" -- and it is the natural thing to
reach for here. Do not. For anyone who has edited their resume since the last
extraction, the live hash differs from the stored row's, so every pass would
see every pair as stale, rescore it against the unchanged stored facts, write
that same stored hash back, and see it as stale again six hours later: 30 pairs
x 4 passes a day, forever, buying the same answer. The two questions are kept
apart on purpose. A later task repairs the facts row first, and only then is a
score's hash compared against a row that has stopped moving. A row scored
against the old facts is then stale exactly once.

ERROR IS A FAULT, NOT AN ANSWER. `ScoreRun.error` is set only when a miss could
not be bought: the provider was unreachable, answered with something
unparseable, or `score_candidates` gave up after a fault. A refusal is an
answer. `score_candidates` files a declined pair under `refused` and leaves
`error` None; this module preserves that, because setting `error` for a refusal
would make one permanently refused posting degrade a person's digest every week
forever. A refusal is stored (`outcome="refused"`, no tier) and not retried
while both hashes hold; a changed resume or a rewritten posting makes it a new
question.

`cap` bounds PURCHASES, not the pool returned. Cached rows come back whatever
the cap is, and they come back after a provider fault too: 29 real judgements
plus one posting that could not be bought is a pool the digest can still rank.
A PARTIAL PURCHASE IS KEPT. `score_candidates` hands back the pairs it did get
answered along with the fault, and they are written here, because a row is per
(person, posting) and a fault that stopped a pass early is the same thing to
this cache as a cap that ended it early -- `stale_pairs` finds the remainder
next pass either way. Discarding them was the old rule at both levels, and it
meant a provider having a bad minute produced nothing at all: on 2026-11-27 a
pass got ten answers and wrote none of them.

TRANSACTIONS. Nothing in this module commits. `write_back`, `record_prose` and
`cached_scores` all write into the caller's session and leave the commit to the
caller, who owns the transaction (a digest build rolls back a user's whole
pass on a crash; an HTTP caller decides when its request is done). The cost is
that a caller must commit after `cached_scores` for the purchase to be kept; a
caller that rolls back loses what it paid for, which is its decision to make.

One writer. `write_back` and `record_prose` are the only code that writes
`job_scores`, so the rules below live in one place: an upsert on
(user_id, job_id) that survives two runs racing for the same pair, and a
rescore that clears the prose, since a sentence about the old score describes
a judgement that no longer stands.

TWO DATES, TWO QUESTIONS. `scored_at` is when the answer was given and moves
on every write. `created_at` is when the pair was first judged, is written
once by `write_back`'s insert, and never moves. The dashboard's arrivals
window reads `created_at`, so a rescore -- which is what a resume edit causes
for up to 30 pairs on the next sweep pass -- cannot announce a match set the
reader has already seen as new. `scored_at` keeps its own jobs: the
longest-unswept ordering in `score_sweep.sweep_order`, and "when was this
judged" on the page.

A posting with no `JobFacets` row. Staleness needs the posting's hash and
enrichment may not have reached it yet. Its hash is recorded as "" so it can be
scored and cached now; when enrichment arrives the hash differs and the pair is
rescored once, which is the correct answer (the text really was not the text
that was scored).
"""

from __future__ import annotations

from datetime import datetime, timezone
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import JobFacets, JobScore
from app.services.jobs.score import Scored, ScoreRun, score_candidates

COMPONENTS = ("requirements_met", "seniority_fit", "location_fit", "direction_fit")
# JobScore.model and .prose_model are String(64), as Digest.model is, and the
# model id comes from configuration rather than from this code. Digest.model is
# deliberately stored as `model[:64]`; these were not, so a configured id over
# 64 characters would raise on every write, for every user, forever -- the
# scoring would be bought and then thrown away by the insert. Truncated at the
# writer, in the one module that writes these columns.
MODEL_COLUMN = 64
# What ScoreRun.model reads when the rows it carries were scored by more than one
# model. Digest.model uses the same literal for the same reason.
MIXED = "mixed"
NO_FACETS_HASH = ""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _description_hashes(db, jobs: list[Any]) -> dict[str, str]:
    ids = [str(j.id) for j in jobs]
    if not ids:
        return {}
    rows = db.execute(
        select(JobFacets.job_id, JobFacets.description_hash).where(JobFacets.job_id.in_(ids))
    ).all()
    return {job_id: digest for job_id, digest in rows}


def _existing(db, user_id: str, job_ids: list[str]) -> dict[str, JobScore]:
    if not job_ids:
        return {}
    rows = db.scalars(
        select(JobScore).where(JobScore.user_id == user_id, JobScore.job_id.in_(job_ids))
    ).all()
    return {r.job_id: r for r in rows}


def _usable(row: JobScore) -> bool:
    """A row we can answer from. A scored row that lost its number is not one."""
    if row.outcome == "refused":
        return True
    return row.tier is not None and row.fit_score is not None


def _is_fresh(row: JobScore | None, *, facts_hash: str, description_hash: str) -> bool:
    return (
        row is not None
        and row.facts_input_hash == facts_hash
        and row.description_hash == description_hash
        and _usable(row)
    )


def _distinct(jobs: list[Any]) -> list[Any]:
    """Drop a job without an id, or a repeat of one, as score_candidates does."""
    seen: set[str] = set()
    out = []
    for job in jobs:
        job_id = getattr(job, "id", None)
        if not job_id or job_id in seen:
            continue
        seen.add(job_id)
        out.append(job)
    return out


def _hash_of(hashes: dict[str, str], job_id: str) -> str:
    """The one place a posting with no JobFacets row becomes NO_FACETS_HASH."""
    return hashes.get(job_id, NO_FACETS_HASH)


def _classify(
    db, *, user, facts, jobs
) -> tuple[list[Any], dict[str, JobScore], dict[str, str]]:
    """(stale jobs in the caller's order, fresh rows by job id, hashes by job id)."""
    hashes = _description_hashes(db, jobs)
    rows = _existing(db, user.id, [str(j.id) for j in jobs])
    stale = []
    fresh: dict[str, JobScore] = {}
    for job in jobs:
        job_id = str(job.id)
        row = rows.get(job_id)
        current = _hash_of(hashes, job_id)
        if _is_fresh(row, facts_hash=facts.input_hash, description_hash=current):
            fresh[job_id] = row
        else:
            stale.append(job)
    return stale, fresh, hashes


def stale_pairs(db, *, user, facts, jobs) -> list[Any]:
    """The jobs that would have to be bought: no row, a stale row, or a lost number.

    The sweep counts a purchase with this, so it and `cached_scores` agree
    about what one is -- they call the same classifier. Order is the caller's.

    `routers.matches.get_progress` does NOT, and the claim that it did was
    false: it counts rows (`_scored_count`), which answers "how many
    judgements exist", not "how many are still to buy". That is deliberate on
    its side -- a progress read must not re-derive staleness, or a resume
    edited mid-run would make the denominator move under the person -- so the
    two are not meant to agree and this function must not be described as
    though they were.
    """
    return _classify(db, user=user, facts=facts, jobs=_distinct(list(jobs)))[0]


def _find(db, user_id: str, job_id: str) -> JobScore | None:
    return db.scalars(
        select(JobScore).where(JobScore.user_id == user_id, JobScore.job_id == job_id)
    ).first()


def _fill(
    row: JobScore, *, facts, description_hash: str, model: str, scored: Scored | None
) -> None:
    """Write this answer onto a row. Never touches `created_at`.

    `created_at` dates the pair, not the answer: it says when this posting
    became new to this person, and the arrivals window reads it. A rescore is
    a new answer about an old pair, so it moves `scored_at` and leaves
    `created_at` where it was. Set it here and a resume edit would announce
    the reader's whole match set as new again.
    """
    row.facts_input_hash = facts.input_hash
    row.description_hash = description_hash
    row.model = model[:MODEL_COLUMN]
    row.scored_at = _now()
    # Prose written about the previous judgement does not describe this one.
    row.reason = None
    row.gap = None
    row.prose_model = None
    if scored is None:
        row.outcome = "refused"
        row.tier = None
        row.fit_score = None
        for name in COMPONENTS:
            setattr(row, name, None)
    else:
        row.outcome = "scored"
        row.tier = scored.tier
        row.fit_score = scored.fit_score
        for name in COMPONENTS:
            setattr(row, name, scored.components.get(name))


def write_back(
    db,
    *,
    user,
    facts,
    job,
    model: str,
    scored: Scored | None = None,
    refused: bool = False,
    description_hash: str | None = None,
) -> JobScore:
    """Upsert the answer for one (user, posting). Flushes; never commits.

    `scored` records a score; `refused=True` records a refusal (a null tier, so
    nobody can read a number off it). Exactly one must be given. `model` is the
    model that gave the answer, which `Scored` does not carry.

    `description_hash` must be the hash the answer was computed against, i.e.
    the one read before the provider was called. Re-reading it here would race
    `enrich_jobs`: a posting rewritten during the call would get its NEW hash
    stamped on a judgement of the OLD text and read as fresh forever. Only a
    caller with no earlier read in hand omits it, and gets a lookup.

    Two runs racing for the same pair is not an error. The insert is wrapped in
    a savepoint, and if the unique constraint fires the winner's row is updated
    with this run's answer. Both ran the same question against the same hashes,
    so either answer is a fair one to keep; one row remains.
    """
    if (scored is None) == (not refused):
        raise ValueError("write_back takes exactly one of scored= or refused=True")
    job_id = str(job.id)
    if description_hash is None:
        facets = db.get(JobFacets, job_id)
        description_hash = _hash_of(
            {job_id: facets.description_hash} if facets is not None else {}, job_id
        )
    fields = dict(facts=facts, description_hash=description_hash, model=model, scored=scored)
    row = _find(db, user.id, job_id)
    if row is None:
        row = JobScore(user_id=user.id, job_id=job_id)
        _fill(row, **fields)
        # The one place created_at is written: this pair is new to this person
        # as of its first judgement. The conflict branch below deliberately
        # does not set it -- the winner's row keeps the date it was created,
        # which is the date the reader's window should compare against.
        row.created_at = row.scored_at
        try:
            with db.begin_nested():
                db.add(row)
        except IntegrityError:
            # The savepoint rollback already dropped the pending row.
            row = _find(db, user.id, job_id)
            if row is None:  # the winner vanished between the two statements
                raise
            _fill(row, **fields)
    else:
        _fill(row, **fields)
    db.flush()
    return row


def record_prose(
    db,
    *,
    user,
    job,
    reason: str | None = None,
    gap: str | None = None,
    model: str | None = None,
    rejected: bool = False,
) -> JobScore:
    """Record bought prose, or the fact that its validator rejected it.

    The score stands either way. `rejected=True` marks the row
    `prose_rejected` and leaves no sentence, so the prose is not re-bought on
    every pass (the same bargain a refusal makes one level down). Otherwise
    `reason`, `gap` and the model that wrote them are stored and the outcome
    stays "scored". `scored_at` is untouched: it dates the score, not the
    sentence. Flushes; never commits.

    The row must already exist and hold a score. Prose without a judgement to
    be about, or about a refusal, is a caller bug, so it raises rather than
    quietly inventing a row.
    """
    if rejected and (reason is not None or gap is not None):
        raise ValueError("rejected prose carries no reason or gap")
    if not rejected and reason is None and gap is None:
        raise ValueError("record_prose needs a reason, a gap, or rejected=True")
    row = _find(db, user.id, str(job.id))
    if row is None:
        raise LookupError("no job_scores row to attach prose to")
    if row.outcome == "refused":
        raise ValueError("a refused pair has no score to write prose about")
    if rejected:
        row.outcome = "prose_rejected"
        row.reason = None
        row.gap = None
    else:
        row.outcome = "scored"
        row.reason = reason
        row.gap = gap
    # Truncated like `model` above, and for the same reason: prose_model is
    # String(64) and the id is configuration, not code.
    row.prose_model = None if model is None else model[:MODEL_COLUMN]
    db.flush()
    return row


def _as_scored(row: JobScore) -> Scored:
    components = {n: getattr(row, n) for n in COMPONENTS if getattr(row, n) is not None}
    return Scored(
        job_id=row.job_id, tier=row.tier, fit_score=row.fit_score, components=components
    )


def _models(rows: list[JobScore], bought: str) -> str:
    # Scoped to the rows it is handed, whatever they are: a refusal is never
    # shown (spec section 2), so it never counts. The cache cannot know what a
    # digest will end up showing; a caller that needs "the shown items' model"
    # asks `models_for` with the ids it shows.
    names = {r.model for r in rows if r.outcome != "refused"}
    if bought:
        names.add(bought)
    if len(names) > 1:
        return MIXED
    return next(iter(names), "")


def models_for(db, *, user, job_ids: Iterable[str]) -> str:
    """The one model behind these postings' scores, or "mixed", or "".

    Reads the cache rows for exactly `job_ids` (flushed rows included), ignoring
    refusals. This is the shown-scoped answer: the single-or-mixed rule lives
    here, in one module, and the caller supplies the scope.
    """
    ids = [str(i) for i in job_ids]
    return _models(list(_existing(db, user.id, ids).values()), "")


async def cached_scores(db, *, user, facts, jobs, cap: int) -> ScoreRun:
    """Fresh cache rows plus whatever had to be bought, as one `ScoreRun`.

    At most `cap` of the stale pairs, in the caller's order, are bought; the
    rest are simply absent from the result, to be picked up by a later pass.
    `usage` is the purchase's alone, since cached rows cost nothing.
    `model` is the one model behind every non-refused row in the pool this
    returns, or "mixed" -- pool-scoped, NOT scoped to what a caller later shows.
    A caller that records "the model behind what the person sees" must ask
    `models_for` about the postings it shows. Writes are
    flushed, not committed: the caller commits (see TRANSACTIONS above).
    """
    jobs = _distinct(list(jobs))
    stale, fresh, hashes = _classify(db, user=user, facts=facts, jobs=jobs)
    to_buy = stale[: max(0, cap)]

    run = ScoreRun()
    bought_model = ""
    bought_scored: list[Scored] = []
    bought_refused: list[str] = []
    if to_buy:
        bought = await score_candidates(db, user=user, facts=facts, jobs=to_buy)
        run.usage = bought.usage
        run.error = bought.error
        bought_model = bought.model
        # Written whether or not `error` is set: what came back was answered
        # and paid for, and a pair that was never reached is simply absent
        # here and still stale, which is the same state a cap leaves it in.
        # This used to be gated on `bought.error is None`, the second of two
        # places that threw a partial purchase away.
        by_id = {str(j.id): j for j in to_buy}
        for item in bought.scored:
            write_back(
                db, user=user, facts=facts, job=by_id[item.job_id],
                model=bought.model, scored=item,
                description_hash=_hash_of(hashes, item.job_id),
            )
            bought_scored.append(item)
        for job_id in bought.refused:
            write_back(
                db, user=user, facts=facts, job=by_id[job_id],
                model=bought.model, refused=True,
                description_hash=_hash_of(hashes, job_id),
            )
            bought_refused.append(job_id)

    rows = [fresh[str(j.id)] for j in jobs if str(j.id) in fresh]
    run.scored = [_as_scored(r) for r in rows if r.outcome != "refused"] + bought_scored
    run.refused = [r.job_id for r in rows if r.outcome == "refused"] + bought_refused
    # A fault with nothing to show keeps the model that was unreachable, so a
    # degraded digest and an empty one stay distinguishable; "every shown item
    # agrees" is vacuous when nothing is shown.
    kept = bought_model if (bought_scored or bought_refused or not rows) else ""
    run.model = _models(rows, kept)
    return run
