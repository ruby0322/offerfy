"""add usage_refusals, so the tiers can be tuned from data

Nothing was written when a request was refused, which left "how often does a
guest reach the wall" and -- more importantly -- "does a signed-in person reach
20 credits" unanswerable. The second decides whether a promise in the Terms is
true: the matching bonus is lawfully a thank-you only while the signed-in tier
is independently sufficient.

See models.UsageRefusal for what is deliberately not recorded.
"""

from alembic import op
import sqlalchemy as sa

revision = "019_usage_refusals"
down_revision = "018_usage_counters"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "usage_refusals",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("subject_kind", sa.String(length=16), nullable=False),
        sa.Column("subject_id", sa.String(length=64), nullable=False),
        sa.Column("tier", sa.String(length=16), nullable=False),
        sa.Column("limit_kind", sa.String(length=32), nullable=False),
        sa.Column("purpose", sa.String(length=16), nullable=False),
        sa.Column("used_uusd", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("turns_used", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint(
            "subject_kind IN ('user', 'guest', 'network')",
            name="usage_refusal_subject_kinds",
        ),
        sa.CheckConstraint(
            "limit_kind IN ('weekly_turns', 'weekly_credits', 'burst', "
            "'global_ceiling', 'guest_pool')",
            name="usage_refusal_limit_kinds",
        ),
    )
    # The only query shape is "refusals over a period, grouped by tier and
    # limit", for the admin dashboard and for tuning.
    op.create_index("ix_usage_refusals_created_at", "usage_refusals", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_usage_refusals_created_at", table_name="usage_refusals")
    op.drop_table("usage_refusals")
