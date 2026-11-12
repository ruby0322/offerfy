"""add applications

One row per (person, posting): the record that someone saved, tailored for,
applied to or closed out a job. The spine of the dashboard. See
app.models.Application.

user_id and job_id are ondelete="CASCADE": the row is personal data erased
with the account, and meaningless once its posting is gone. tailor_resume_id
is ondelete="SET NULL" on purpose: deleting the tailored resume must not
erase the record that you applied.

status and source have no CHECK constraint, matching 031_job_preferences.py:
the vocabularies may grow and one writer owns them, so a constraint would
just be a migration tax on every future addition.

Plain create_table with no dialect guard, matching 031_job_preferences.py.
No test runs alembic; conftest builds the schema from the models with
create_all, so this migration is checked by eye against
app.models.Application, and by a throwaway Postgres, not by the suite.
"""

from alembic import op
import sqlalchemy as sa

revision = "032_applications"
down_revision = "031_job_preferences"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "applications",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("tailor_resume_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tailor_resume_id"], ["resumes.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint("user_id", "job_id", name="applications_user_id_job_id_key"),
    )
    op.create_index("ix_applications_user_id", "applications", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_applications_user_id", table_name="applications")
    op.drop_table("applications")
