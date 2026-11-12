"""The one writer of Application.status.

Every transition on an application goes through this module; routes and the
tailor-creation path call it, and nothing else may assign Application.status.
Keeping the vocabulary and the rules in one place is the point: STATUSES and
SOURCES live here, not in the model, because they may grow and the table has
no CHECK constraint on them (matching JobPreference.activity).

Two rulings live here.

1. Advancement is monotonic, and save never moves a row backwards. A person
   can tailor a posting they never saved, then click Save on the same card;
   the unique (user, job) constraint makes a naive insert a 500, and a naive
   "set status = saved" would silently demote a tailored or applied row. So
   save returns an existing row untouched. The only ways down are the explicit
   ones: revert_applied (the person said "I did not apply") and set_status
   (the person moving their own row).

2. pipeline does not filter Job.is_active. This is deliberately the opposite
   of match_sweep.prefilter_query, which always filters it: matching decides
   what to offer, and a dead posting should not be offered. The pipeline is
   the person's own history; an application whose posting has since been
   taken down is still a fact about what they did, and must not vanish.

The confirmation, in the spec's words: "The outbound click sets status =
applied and leaves it NULL; the dashboard asks those rows 'did you apply?',
and an answer either stamps it or reverts the row to tailored." A confirmed
row is never asked again.

last_status_at is written only by this module, explicitly, at each status
change; the column has no onupdate. An onupdate would fire on any UPDATE (a
notes edit, a tailor re-link, the confirmation stamp) and reset the "days
quiet" clock the dashboard shows. The trade this accepts: anything that
changes status outside this module leaves the column stale.
"""

from __future__ import annotations

from sqlalchemy import case, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.models import Application, User, _utcnow

STATUSES: tuple[str, ...] = ("saved", "tailored", "applied", "interviewing", "closed")
SOURCES: tuple[str, ...] = ("digest", "search")

# The statuses a person may set on their own row via set_status. Everything
# else is reached only by the action that implies it.
SELF_REPORTED: tuple[str, ...] = ("interviewing", "closed")

# Dashboard order, most advanced first. "closed" is not in it: it is an exit,
# not a step, so it sorts after every group (see pipeline).
GROUP_ORDER: tuple[str, ...] = ("interviewing", "applied", "tailored", "saved")


def _rank(status: str) -> int:
    """Position on the advancement ladder; higher is further along.

    Only defined for statuses in GROUP_ORDER; _advance guards closed.
    """
    return len(GROUP_ORDER) - 1 - GROUP_ORDER.index(status)


def _advance(application: Application, status: str) -> bool:
    """Move the row forward to status; never backwards, never out of closed.

    The single place the monotonic rule lives. Returns True when it moved.
    """
    if application.status == "closed" or status not in GROUP_ORDER:
        return False
    if _rank(application.status) >= _rank(status):
        return False
    application.status = status
    application.last_status_at = _utcnow()
    return True


def _check_source(source: str) -> None:
    if source not in SOURCES:
        raise ValueError(f"unknown application source: {source!r}")


def _get(db: Session, user: User, job_id: str) -> Application | None:
    return db.scalar(
        select(Application).where(
            Application.user_id == user.id, Application.job_id == job_id
        )
    )


def _get_or_create(db: Session, user: User, job_id: str, source: str) -> Application:
    existing = _get(db, user, job_id)
    if existing is not None:
        return existing
    application = Application(
        user_id=user.id,
        job_id=job_id,
        status="saved",
        source=source,
        last_status_at=_utcnow(),
    )
    try:
        with db.begin_nested():
            db.add(application)
    except IntegrityError:
        # A concurrent request (double click) inserted the row first.
        existing = _get(db, user, job_id)
        if existing is None:
            raise
        return existing
    return application


def save(db: Session, *, user: User, job_id: str, source: str = "search") -> Application:
    """Create the row at saved, or return the existing one untouched."""
    _check_source(source)
    application = _get_or_create(db, user, job_id, source)
    db.commit()
    return application


def unsave(db: Session, *, user: User, job_id: str) -> bool:
    """Delete the row only while it is still saved. True when it deleted."""
    result = db.execute(
        delete(Application).where(
            Application.user_id == user.id,
            Application.job_id == job_id,
            Application.status == "saved",
        )
    )
    db.commit()
    return result.rowcount > 0


def link_tailor(
    db: Session,
    *,
    user: User,
    job_id: str,
    tailor_resume_id: str,
    source: str = "search",
) -> Application:
    """Upsert, advance to tailored, and point at the tailored resume."""
    _check_source(source)
    application = _get_or_create(db, user, job_id, source)
    _advance(application, "tailored")
    application.tailor_resume_id = tailor_resume_id
    db.commit()
    return application


def mark_applied(db: Session, *, user: User, job_id: str) -> Application:
    """The outbound click: applied, stamped, confirmation still owed."""
    application = _get_or_create(db, user, job_id, "search")
    if _advance(application, "applied") and application.applied_at is None:
        application.applied_at = _utcnow()
    db.commit()
    return application


def confirm_applied(db: Session, *, application: Application) -> Application:
    """The person answered 'yes, I applied'. Idempotent.

    Only an applied row can be confirmed; a stale tab answering 'yes' for a
    row that is not at applied changes nothing.
    """
    if application.status != "applied":
        return application
    if application.applied_confirmed_at is None:
        application.applied_confirmed_at = _utcnow()
    db.commit()
    return application


def revert_applied(db: Session, *, application: Application) -> Application:
    """The person answered 'no, I did not apply': back to where they were.

    The destination depends on the tailor. `applied` is reachable without one
    (the outbound click on /jobs/[id] creates a saved row and advances it
    straight to applied), and sending that row to `tailored` would invent a
    "ready to send" entry with no tailored resume behind it, undeletable
    because only `saved` rows delete. So: `tailored` when a tailor is linked,
    otherwise `saved`.

    Only an applied row can be un-applied; a stale tab answering 'no' for a
    row that has since moved on (interviewing, closed) changes nothing.
    """
    if application.status != "applied":
        return application
    application.status = "tailored" if application.tailor_resume_id is not None else "saved"
    application.applied_at = None
    application.applied_confirmed_at = None
    application.last_status_at = _utcnow()
    db.commit()
    return application


def set_status(db: Session, *, application: Application, status: str) -> Application:
    """The person moving their own row: interviewing or closed only."""
    if status not in SELF_REPORTED:
        raise ValueError(f"status {status!r} is not self-reported")
    application.status = status
    application.last_status_at = _utcnow()
    db.commit()
    return application


def pipeline(db: Session, *, user: User) -> list[Application]:
    """Every row for the user: GROUP_ORDER, then most recently changed first.

    Does NOT filter Job.is_active; see the module docstring. closed sorts last.
    """
    group = case(
        {status: i for i, status in enumerate(GROUP_ORDER)},
        value=Application.status,
        else_=len(GROUP_ORDER),
    )
    return list(
        db.scalars(
            select(Application)
            .where(Application.user_id == user.id)
            .options(joinedload(Application.job))
            .order_by(group, Application.last_status_at.desc(), Application.id)
        )
    )
