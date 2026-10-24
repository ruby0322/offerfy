"""add usage_counters, the quota's source of truth

Separate from llm_calls because of durability, not shape: the chat router's
usage insert is best-effort by design (telemetry may be lost, the work may
not), and a budget cannot read from a best-effort write -- a lost row would be
a free turn. llm_calls keeps its per-round detail and its try/except for admin
and cost analysis; this table carries the number the limit is enforced
against, and the two are reconciled.

See models.UsageCounter for why there is no foreign key and no surrogate id.
"""

from alembic import op
import sqlalchemy as sa

revision = "018_usage_counters"
down_revision = "017_llm_call_owner"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "usage_counters",
        sa.Column("subject_kind", sa.String(length=16), nullable=False),
        sa.Column("subject_id", sa.String(length=64), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("uusd", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("turns", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unmeasured", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        # The composite natural key is also the UPSERT conflict target, so it
        # has to be the primary key rather than a unique index beside a
        # surrogate one.
        sa.PrimaryKeyConstraint(
            "subject_kind",
            "subject_id",
            "window_start",
            name="usage_counters_pkey",
        ),
        sa.CheckConstraint(
            "subject_kind IN ('user', 'guest', 'network')",
            name="usage_counter_subject_kinds",
        ),
        sa.CheckConstraint(
            "uusd >= 0 AND turns >= 0 AND unmeasured >= 0",
            name="usage_counter_non_negative",
        ),
    )
    # The ceilings sum every subject for one window, so the window has to lead.
    # The primary key already serves the per-subject lookup.
    op.create_index(
        "ix_usage_counters_window", "usage_counters", ["window_start", "subject_kind"]
    )

    # No backfill. llm_calls rows predating this table stay where they are --
    # they are still the telemetry, and reconciliation reads them. Seeding
    # counters from them would import the very best-effort gaps this table
    # exists to stop depending on, and the current window is at most a week of
    # one user's spend.


def downgrade() -> None:
    op.drop_index("ix_usage_counters_window", table_name="usage_counters")
    op.drop_table("usage_counters")
