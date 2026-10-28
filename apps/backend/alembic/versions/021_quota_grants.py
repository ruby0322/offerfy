"""add quota_grants, so one person's allowance can move without a deploy

The weekly ceiling lived only in services.usage.TIERS. That is right for a
*published* allowance -- /v1/usage/tiers serves those numbers, so the public
page cannot drift from the enforced one -- and wrong for a *per-person* one:
raising one person's credits meant editing TIERS and redeploying, which moves
everyone in that tier.

Grants are additive and recurring: an active grant raises the ceiling in every
weekly window it covers, and can never lower or set one. See
models.QuotaGrant for why there is no one-off pool, no turns column, and no
consumption recorded here.
"""

from alembic import op
import sqlalchemy as sa

revision = "021_quota_grants"
down_revision = "020_user_profile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Named FKs and named checks: ensure_schema() builds this table from the
    # models for SQLite in the tests, so the names have to match models.py
    # character for character or the two schemas diverge invisibly.
    op.create_table(
        "quota_grants",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("credits", sa.Integer(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("detail", sa.String(length=255), nullable=True),
        sa.Column("issued_by_user_id", sa.String(length=36), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_quota_grants_user_id",
            ondelete="CASCADE",
        ),
        # SET NULL, not CASCADE: deleting the admin who issued a grant must not
        # delete the recipient's allowance.
        sa.ForeignKeyConstraint(
            ["issued_by_user_id"],
            ["users.id"],
            name="fk_quota_grants_issued_by",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint("credits > 0", name="quota_grant_credits_positive"),
        sa.CheckConstraint(
            "expires_at IS NULL OR expires_at > starts_at",
            name="quota_grant_interval",
        ),
        sa.CheckConstraint(
            "reason IN ('manual', 'compensation', 'beta', 'staff', 'promotion')",
            name="quota_grant_reasons",
        ),
    )
    # The only query shape is "this user's grants" -- on every cost pre-flight
    # for the active sum, and on the admin detail view for the full history. A
    # plain index rather than one partial on revoked_at, because ensure_schema()
    # has to build the same thing on SQLite.
    op.create_index("ix_quota_grants_user", "quota_grants", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_quota_grants_user", table_name="quota_grants")
    op.drop_table("quota_grants")
