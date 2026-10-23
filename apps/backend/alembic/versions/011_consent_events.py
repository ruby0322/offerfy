"""add consent_events ledger"""

from alembic import op
import sqlalchemy as sa

revision = "011_consent_events"
down_revision = "010_llm_calls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "consent_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), nullable=True),
        sa.Column("guest_session_id", sa.String(length=36), nullable=True),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("policy_version", sa.String(length=32), nullable=False),
        sa.Column("locale", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("detail", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        # SET NULL, not CASCADE: erasing an account must not erase the proof
        # that it consented. A row with no subject is the erased case.
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["guest_session_id"], ["guest_sessions.id"], ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            "purpose IN ('service', 'talent_introduction')",
            name="consent_purpose_values",
        ),
        sa.CheckConstraint(
            "action IN ('granted', 'withdrawn')",
            name="consent_action_values",
        ),
        sa.CheckConstraint(
            "NOT (user_id IS NOT NULL AND guest_session_id IS NOT NULL)",
            name="consent_subject_not_both",
        ),
    )
    # Current state is the latest row per (subject, purpose), so both reads are
    # by user_id; created_at carries the ledger's chronological views.
    op.create_index("ix_consent_events_user_id", "consent_events", ["user_id"])
    op.create_index("ix_consent_events_created_at", "consent_events", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_consent_events_created_at", table_name="consent_events")
    op.drop_index("ix_consent_events_user_id", table_name="consent_events")
    op.drop_table("consent_events")
