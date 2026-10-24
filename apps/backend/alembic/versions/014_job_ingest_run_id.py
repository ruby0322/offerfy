"""add job_ingest_runs.run_id so a pass can be read as one thing"""

from alembic import op
import sqlalchemy as sa

revision = "014_job_ingest_run_id"
down_revision = "013_talent_disclosure"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {col["name"] for col in sa.inspect(bind).get_columns("job_ingest_runs")}
    # Nullable: rows written before this migration have no pass to belong to.
    # The admin view excludes them rather than inventing a bucket for them.
    if "run_id" not in columns:
        op.add_column("job_ingest_runs", sa.Column("run_id", sa.String(length=36), nullable=True))

    indexes = {idx["name"] for idx in sa.inspect(bind).get_indexes("job_ingest_runs")}
    # The table had no indexes and grows ~119 rows per pass, ~480/day. The admin
    # ingest view groups by run_id and orders passes by started_at; the per-pass
    # drill-down filters on run_id alone.
    if "ix_job_ingest_runs_run_id" not in indexes:
        op.create_index("ix_job_ingest_runs_run_id", "job_ingest_runs", ["run_id"])
    if "ix_job_ingest_runs_started_at" not in indexes:
        op.create_index("ix_job_ingest_runs_started_at", "job_ingest_runs", ["started_at"])


def downgrade() -> None:
    bind = op.get_bind()
    indexes = {idx["name"] for idx in sa.inspect(bind).get_indexes("job_ingest_runs")}
    if "ix_job_ingest_runs_started_at" in indexes:
        op.drop_index("ix_job_ingest_runs_started_at", table_name="job_ingest_runs")
    if "ix_job_ingest_runs_run_id" in indexes:
        op.drop_index("ix_job_ingest_runs_run_id", table_name="job_ingest_runs")
    columns = {col["name"] for col in sa.inspect(bind).get_columns("job_ingest_runs")}
    if "run_id" in columns:
        op.drop_column("job_ingest_runs", "run_id")
