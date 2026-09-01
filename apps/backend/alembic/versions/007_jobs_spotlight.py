"""add jobs.spotlight_score for dest tables created before 006"""

from alembic import op
import sqlalchemy as sa

revision = "007_jobs_spotlight"
down_revision = "006_jobs_catalog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {col["name"] for col in sa.inspect(bind).get_columns("jobs")}
    if "spotlight_score" not in columns:
        op.add_column(
            "jobs",
            sa.Column("spotlight_score", sa.Float(), nullable=False, server_default="0"),
        )
    indexes = {idx["name"] for idx in sa.inspect(bind).get_indexes("jobs")}
    if "ix_jobs_active_spotlight" not in indexes:
        op.create_index("ix_jobs_active_spotlight", "jobs", ["is_active", "spotlight_score"])


def downgrade() -> None:
    bind = op.get_bind()
    indexes = {idx["name"] for idx in sa.inspect(bind).get_indexes("jobs")}
    if "ix_jobs_active_spotlight" in indexes:
        op.drop_index("ix_jobs_active_spotlight", table_name="jobs")
    columns = {col["name"] for col in sa.inspect(bind).get_columns("jobs")}
    if "spotlight_score" in columns:
        op.drop_column("jobs", "spotlight_score")
