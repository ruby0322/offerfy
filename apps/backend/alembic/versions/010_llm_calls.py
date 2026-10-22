"""add llm_calls token telemetry"""

from alembic import op
import sqlalchemy as sa

revision = "010_llm_calls"
down_revision = "009_resume_family_memory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_calls",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("resume_id", sa.String(length=36), nullable=True),
        sa.Column("purpose", sa.String(length=16), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("round_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "prefer_full_source",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("cached_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("reasoning_tokens", sa.Integer(), nullable=True),
        sa.Column("source_chars", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["resume_id"], ["resumes.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "purpose IN ('chat', 'repair', 'vision')",
            name="llm_call_purpose_values",
        ),
    )
    # The admin overview reads this by day, so the date is the only index worth
    # carrying; resume_id is for ad-hoc drilldowns, which scan fine.
    op.create_index("ix_llm_calls_created_at", "llm_calls", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_llm_calls_created_at", table_name="llm_calls")
    op.drop_table("llm_calls")
