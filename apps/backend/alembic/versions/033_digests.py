"""add digests and digest_items

The weekly digest, stored. digests is one row per (person, week);
digest_items is the postings in it, each with the score and prose it was
shown with. See app.models.Digest and app.models.DigestItem.

All three foreign keys are ondelete="CASCADE": a digest is personal data
erased with the account, an item is meaningless without its digest, and an
item is meaningless once its posting is gone.

digest_items' unique constraint carries track because one posting can
appear in both tracks of one digest.

status, track and tier have no CHECK constraint, matching
031_job_preferences.py and 032_applications.py.

Plain create_table with no dialect guard. No test runs alembic; conftest
builds the schema from the models with create_all, so this migration is
checked by eye against the models, and by a throwaway Postgres.
"""

from alembic import op
import sqlalchemy as sa

revision = "033_digests"
down_revision = "032_applications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "digests",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("built_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("candidate_count", sa.Integer(), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "user_id", "period_start", name="digests_user_id_period_start_key"
        ),
    )
    op.create_index("ix_digests_user_id", "digests", ["user_id"])

    op.create_table(
        "digest_items",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("digest_id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("track", sa.String(length=16), nullable=False),
        sa.Column("tier", sa.String(length=16), nullable=True),
        sa.Column("fit_score", sa.Float(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("gap", sa.Text(), nullable=True),
        sa.Column("requirements_met", sa.Float(), nullable=True),
        sa.Column("seniority_fit", sa.Float(), nullable=True),
        sa.Column("location_fit", sa.Float(), nullable=True),
        sa.Column("direction_fit", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["digest_id"], ["digests.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "digest_id", "job_id", "track", name="digest_items_digest_id_job_id_track_key"
        ),
    )
    op.create_index("ix_digest_items_digest_id", "digest_items", ["digest_id"])


def downgrade() -> None:
    op.drop_index("ix_digest_items_digest_id", table_name="digest_items")
    op.drop_table("digest_items")
    op.drop_index("ix_digests_user_id", table_name="digests")
    op.drop_table("digests")
