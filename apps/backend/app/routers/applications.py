"""A person's applications: the routes behind Save, Apply and the dashboard rows.

Thin by design. Every status transition lives in services.jobs.applications;
a handler looks the row up by (user, job), calls the service, and projects it.
The lookup is always by (user_id, job_id), so another person's row is
indistinguishable from a missing one: 404, never 403.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_user
from app.models import Application, Job, User
from app.schemas_jobs import (
    ApplicationConfirm,
    ApplicationCreate,
    ApplicationOut,
    ApplicationPatch,
)
from app.services.jobs import applications as service

router = APIRouter()


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _out(db: Session, row: Application) -> ApplicationOut:
    job = db.get(Job, row.job_id)
    return ApplicationOut(
        job_id=row.job_id,
        job_title=job.title,
        company=job.company,
        apply_url=job.apply_url,
        job_is_active=job.is_active,
        status=row.status,
        source=row.source,
        tailor_resume_id=row.tailor_resume_id,
        applied_at=_iso(row.applied_at),
        applied_confirmed_at=_iso(row.applied_confirmed_at),
        last_status_at=row.last_status_at.isoformat(),
        notes=row.notes,
    )


def _find(db: Session, user: User, job_id: str) -> Application | None:
    return db.scalar(
        select(Application).where(
            Application.user_id == user.id, Application.job_id == job_id
        )
    )


def _owned(db: Session, user: User, job_id: str) -> Application:
    row = _find(db, user, job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Application not found")
    return row


@router.post("/v1/me/applications", response_model=ApplicationOut, status_code=201)
def save_application(
    body: ApplicationCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    """Save a posting. Idempotent: a second save returns the row untouched."""
    # 422 for a reference carried in the body, 404 for one carried in the
    # path (see mark_applied): the collection URL exists, the payload is bad.
    if db.get(Job, body.job_id) is None:
        raise HTTPException(status_code=422, detail="Job not found")
    row = service.save(db, user=user, job_id=body.job_id, source=body.source)
    return _out(db, row)


@router.get("/v1/me/applications/{job_id}", response_model=ApplicationOut)
def get_application(
    job_id: str, db: Session = Depends(get_db), user: User = Depends(require_user)
):
    """The person's row for one posting, for the public job page's Save button."""
    return _out(db, _owned(db, user, job_id))


@router.delete("/v1/me/applications/{job_id}", status_code=204)
def unsave_application(
    job_id: str, db: Session = Depends(get_db), user: User = Depends(require_user)
):
    row = _owned(db, user, job_id)
    if row.status != "saved":
        raise HTTPException(
            status_code=409,
            detail=f"Application is already {row.status}; only a saved posting can be removed",
        )
    if not service.unsave(db, user=user, job_id=job_id):
        # Moved past saved between the lookup and the delete.
        raise HTTPException(
            status_code=409, detail="Application has moved past saved"
        )
    return Response(status_code=204)


@router.post("/v1/me/applications/{job_id}/applied", response_model=ApplicationOut)
def mark_applied(
    job_id: str, db: Session = Depends(get_db), user: User = Depends(require_user)
):
    # The id is in the path, so a missing posting is a missing resource: 404.
    if db.get(Job, job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return _out(db, service.mark_applied(db, user=user, job_id=job_id))


@router.post("/v1/me/applications/{job_id}/confirm", response_model=ApplicationOut)
def confirm_application(
    job_id: str,
    body: ApplicationConfirm,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    row = _owned(db, user, job_id)
    if body.applied:
        row = service.confirm_applied(db, application=row)
    else:
        row = service.revert_applied(db, application=row)
    return _out(db, row)


@router.patch("/v1/me/applications/{job_id}", response_model=ApplicationOut)
def patch_application(
    job_id: str,
    body: ApplicationPatch,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    row = _owned(db, user, job_id)
    if "notes" in body.model_fields_set:
        # Written here, not in the service: notes carry no transition, and
        # last_status_at has no onupdate, so this leaves "days quiet" alone.
        # Written before set_status so that service's commit is the request's
        # only one; with no status, commit here.
        row.notes = body.notes
    if body.status is not None:
        row = service.set_status(db, application=row, status=body.status)
    else:
        db.commit()
    return _out(db, row)
