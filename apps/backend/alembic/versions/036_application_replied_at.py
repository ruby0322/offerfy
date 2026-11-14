"""add applications.replied_at

When the employer answered, whatever the answer was. See
app.models.Application.replied_at for why the status cannot stand in for it:
closing a row overwrites the status, so after the fact a rejection and a
silence are both `closed` and nothing distinguishes them. Reply rate and the
pipeline diagram read this column.

Nullable with no default and no backfill, deliberately. Every existing row
predates the column, and there is no honest value to give it: a `closed` row
written before today may or may not have had a reply, and guessing either way
would invent history that then shows up as a number on someone's dashboard.
NULL means "we do not know", which is the truth, and reply rate counts only
rows it can account for.

`interviewing` rows are the one case where a backfill would be defensible --
you cannot interview without having heard back -- but it would have to invent
a timestamp, and `last_status_at` is the wrong one (it moves on every later
transition). Left NULL for the same reason as the rest.

Plain add_column with no dialect guard, matching 032_applications.py. No test
runs alembic; conftest builds the schema from the models with create_all, so
this migration is checked by eye against app.models.Application and against a
throwaway Postgres, not by the suite.
"""

from alembic import op
import sqlalchemy as sa

revision = "036_application_replied_at"
down_revision = "035_match_digest_purposes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "applications",
        sa.Column("replied_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("applications", "replied_at")
