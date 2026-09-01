"""official jobs catalog tables"""

from alembic import op
import sqlalchemy as sa

revision = "006_jobs_catalog"
down_revision = "005_resume_shares"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=255), nullable=False),
        sa.Column("company", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("location", sa.String(length=512), nullable=True),
        sa.Column("remote", sa.Boolean(), nullable=True),
        sa.Column("apply_url", sa.String(length=2048), nullable=False),
        sa.Column("source_url", sa.String(length=2048), nullable=False),
        sa.Column("description_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("description_html", sa.Text(), nullable=False, server_default=""),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("spotlight_score", sa.Float(), nullable=False, server_default="0"),
        sa.CheckConstraint(
            "source IN ('greenhouse', 'lever', 'ashby', 'taiwanjobs')",
            name="job_source_values",
        ),
        sa.UniqueConstraint("source", "source_id", name="jobs_source_source_id_key"),
    )
    op.create_index("ix_jobs_active_seen", "jobs", ["is_active", "last_seen_at"])
    op.create_index("ix_jobs_active_spotlight", "jobs", ["is_active", "spotlight_score"])
    op.create_index("ix_jobs_source", "jobs", ["source"])
    op.create_table(
        "job_ingest_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("board_slug", sa.String(length=255), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ok_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("upserted_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expired_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_snippet", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ok"),
    )


def downgrade() -> None:
    op.drop_table("job_ingest_runs")
    op.drop_index("ix_jobs_source", table_name="jobs")
    op.drop_index("ix_jobs_active_spotlight", table_name="jobs")
    op.drop_index("ix_jobs_active_seen", table_name="jobs")
    op.drop_table("jobs")
