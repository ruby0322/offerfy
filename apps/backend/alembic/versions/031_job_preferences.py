"""add job_preferences

What a person wants next -- the intent a resume cannot supply, and the other
half of a match profile beside resume_facts. See app.models.JobPreference.

A row exists only once the person has saved their own preferences; before
that the API serves a transient seed computed from the facts on every read
and stores nothing. Once the row exists it is written only by the user, and a
rebuild of resume_facts must never overwrite it; that rule is the whole reason
it is a separate table rather than more columns on resume_facts.

user_id is unique, one row per person, with ondelete="CASCADE": erasing the
account erases it. target_functions and target_locations are pipe-delimited
Text in the '|a|b|' idiom, so LIKE '%|ml|%' cannot match 'html'.
remote_required is a nullable Boolean, a real tri-state matching jobs.remote
(True remote-only, False on-site only, NULL no preference).

activity has no CHECK constraint, matching 028_job_facets.py: the vocabulary
may grow and one writer owns it, so a constraint would just be a migration tax
on every future addition.

Plain create_table with no dialect guard, matching 028_job_facets.py -- it
runs the same on SQLite and Postgres. No test runs alembic either way;
conftest builds the schema from the models with create_all, so this
migration is checked by eye against app.models.JobPreference, and by a
throwaway Postgres, not by the suite.
"""

from alembic import op
import sqlalchemy as sa

revision = "031_job_preferences"
down_revision = "030_resume_facts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_preferences",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("target_functions", sa.Text(), nullable=False),
        sa.Column("target_locations", sa.Text(), nullable=False),
        sa.Column("target_seniority", sa.String(length=16), nullable=True),
        sa.Column("remote_required", sa.Boolean(), nullable=True),
        sa.Column("activity", sa.String(length=16), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", name="job_preferences_user_id_key"),
    )
    op.create_index("ix_job_preferences_user_id", "job_preferences", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_job_preferences_user_id", table_name="job_preferences")
    op.drop_table("job_preferences")
