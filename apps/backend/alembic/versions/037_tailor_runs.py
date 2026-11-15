"""tailor_runs: the server-driven first pass over a tailor

One table, no steps table. The pass is a single agent turn, so there is no
section plan to track -- see models.TailorRun for why, and why job_id lives
here rather than only in applications.

The partial unique index is the real guard against two running passes for one
resume; services.tailor_run.start_run's query in front of it is only a fast
path. Both Postgres and SQLite 3.8+ support partial indexes, so the
constraint is exercised by the test suite too, not just in production.

Revision ID: 037_tailor_runs
Revises: 036_application_replied_at
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "037_tailor_runs"
down_revision = "036_application_replied_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tailor_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("resume_id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("phase", sa.String(length=16), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["resume_id"], ["resumes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "status IN ('running', 'done', 'failed')",
            name="tailor_runs_status_values",
        ),
        sa.CheckConstraint(
            "phase IS NULL OR phase IN ('reading', 'assessing', 'writing')",
            name="tailor_runs_phase_values",
        ),
    )
    op.create_index("ix_tailor_runs_resume_id", "tailor_runs", ["resume_id"])
    op.create_index(
        "tailor_runs_one_running_per_resume",
        "tailor_runs",
        ["resume_id"],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
        sqlite_where=sa.text("status = 'running'"),
    )


def downgrade() -> None:
    op.drop_index("tailor_runs_one_running_per_resume", table_name="tailor_runs")
    op.drop_index("ix_tailor_runs_resume_id", table_name="tailor_runs")
    op.drop_table("tailor_runs")
