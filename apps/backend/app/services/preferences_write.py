"""Replace a person's job preferences. Shared by the PUT route and the agent tool.

This is the whole write path, extracted from the route so the two callers cannot
drift. It does not call a model and never touches resume facts.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import JobPreference, User
from app.schemas_jobs import JobPreferenceIn
from app.services.jobs.preferences import current_or_seeded


def _pipe(values: list[str]) -> str:
    return f"|{'|'.join(values)}|" if values else ""


def save(db: Session, user: User, body: JobPreferenceIn) -> tuple[JobPreference, bool]:
    """Replace the preferences wholesale; returns (row, first_save).

    Not a patch: a field omitted from `body` is cleared (activity returns to its
    default). Commits.
    """
    # Mutate the loaded row in place: updated_at's onupdate fires only on an
    # UPDATE, so delete-and-reinsert would reset it to creation time.
    row = current_or_seeded(db, user)
    # No row before this write: the client starts the first run on this flag.
    first_save = row not in db
    if first_save:
        db.add(row)
    row.target_functions = _pipe(body.target_functions)
    row.target_locations = _pipe(body.target_locations)
    row.target_seniority = body.target_seniority
    row.remote_required = body.remote_required
    row.activity = body.activity
    db.commit()
    db.refresh(row)
    return row, first_save
