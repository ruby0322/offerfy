"""add agent_api_keys

One row per credential a user mints for their own AI assistant (the MCP
surface). See app.models.AgentApiKey. Distinct from talent_api_keys, which an
operator issues by hand: no shared table, prefix or hash.

user_id is ondelete="CASCADE": a token is personal data, erased with the
account. key_hash is unique because lookup is one equality on it. revoked_at,
expires_at and last_used_at are nullable and have no server default: NULL means
"not revoked", "never expires" and "never used", and a default would make every
key look like it had been.

Plain create_table with no dialect guard, following 042_job_scores.py. No test
runs alembic; conftest builds the schema from the models with create_all, so
this file is checked by eye against the model, and against
`alembic upgrade head --sql`.
"""

from alembic import op
import sqlalchemy as sa

revision = "043_agent_api_keys"
down_revision = "042_job_scores"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_api_keys",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("key_hash", name="agent_api_keys_key_hash_key"),
    )
    op.create_index("ix_agent_api_keys_user_id", "agent_api_keys", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_api_keys_user_id", table_name="agent_api_keys")
    op.drop_table("agent_api_keys")
