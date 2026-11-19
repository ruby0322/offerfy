"""add job_scores and users.matches_seen_at

job_scores caches one judgement per (person, posting) so a new account sees
scored matches in its first session instead of waiting for a weekly batch.
See app.models.JobScore, and section 1 of
docs/superpowers/specs/2026-11-19-onboarding-first-run-design.md.

users.matches_seen_at is the server-side "last looked at matches" marker; NULL
means never looked, so it deliberately has no server default.

users.first_run_started_at is the claim that makes the second of two
concurrent first runs a no-op instead of a duplicate that races on the
job_scores unique key. NULL means no run has been claimed, same reasoning: a
server default would make every account look like it had already had one.

users.facts_build_failed_at is the score sweep's failure memory for the resume
facts build, so a persistently failing extraction is not re-bought every six
hours forever. NULL means no failure to remember.

One revision for the pair, because they land in the same release and a
half-applied pair has no useful meaning.

Both foreign keys are ondelete="CASCADE": a score is personal data erased with
the account, and meaningless once its posting is gone.

tier and fit_score are nullable because a refusal is stored as a row with no
score. outcome has no CHECK constraint, matching 031_job_preferences.py,
032_applications.py and 033_digests.py: the vocabulary lives in the model.

scored_at and created_at are both NOT NULL and are different facts: scored_at
is when the judgement was made and moves on every rescore, created_at is when
the pair was first judged and never moves. The arrivals window reads
created_at, so a resume edit cannot re-announce a match set the reader has
already seen. Both are written by the application (score_cache.write_back) and
so have no server default; the model carries a Python-side default for
created_at so no writer can leave it NULL.

created_at was added to this revision rather than a 043 because 042 has not
been applied anywhere: amending an unapplied revision is the whole point of
checking before it ships.

Plain create_table with no dialect guard. No test runs alembic; conftest
builds the schema from the models with create_all, so this migration is
checked by eye against the models, and against alembic's offline --sql output.
"""

from alembic import op
import sqlalchemy as sa

revision = "042_job_scores"
down_revision = "041_chat_messages_resume_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_scores",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("facts_input_hash", sa.String(length=64), nullable=False),
        sa.Column("description_hash", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("tier", sa.String(length=16), nullable=True),
        sa.Column("fit_score", sa.Float(), nullable=True),
        sa.Column("requirements_met", sa.Float(), nullable=True),
        sa.Column("seniority_fit", sa.Float(), nullable=True),
        sa.Column("location_fit", sa.Float(), nullable=True),
        sa.Column("direction_fit", sa.Float(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("gap", sa.Text(), nullable=True),
        sa.Column("prose_model", sa.String(length=64), nullable=True),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("scored_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "job_id", name="job_scores_user_id_job_id_key"),
    )
    op.create_index("ix_job_scores_user_id", "job_scores", ["user_id"])
    op.create_index("ix_job_scores_job_id", "job_scores", ["job_id"])

    op.add_column(
        "users",
        sa.Column("matches_seen_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("first_run_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("facts_build_failed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "facts_build_failed_at")
    op.drop_column("users", "first_run_started_at")
    op.drop_column("users", "matches_seen_at")
    op.drop_index("ix_job_scores_job_id", table_name="job_scores")
    op.drop_index("ix_job_scores_user_id", table_name="job_scores")
    op.drop_table("job_scores")
