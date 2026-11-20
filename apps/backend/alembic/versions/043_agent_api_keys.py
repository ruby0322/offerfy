"""add agent_api_keys and agent_tool_calls

This revision was amended before it was ever applied: it first created only
agent_api_keys, and agent_tool_calls was added here rather than as a 044.
042_job_scores.py's docstring sets the precedent for amending an unapplied
revision. One revision carries both tables because a half-applied pair has no
useful meaning: a key table with no audit trail, or an audit table whose
foreign key points at nothing, is not a state anyone wants to be in.

agent_tool_calls (see app.models.AgentToolCall) is the audit trail and the
hourly quota's counter. Both foreign keys are ondelete="CASCADE". resume_id is
a plain string, not a foreign key, so the record outlives the resume. created_at
has no server default: the writer sets it, and a default would make a
backfilled row look current. No arguments are stored; they carry resume content.

agent_api_keys: one row per credential a user mints for their own AI assistant (the MCP
surface). See app.models.AgentApiKey. Distinct from talent_api_keys, which an
operator issues by hand: no shared table, prefix or hash.

user_id is ondelete="CASCADE": a token is personal data, erased with the
account. key_hash is unique because lookup is one equality on it. revoked_at,
expires_at and last_used_at are nullable and have no server default: NULL means
"not revoked", "never expires" and "never used", and a default would make every
key look like it had been.

Plain create_table with no dialect guard, following 042_job_scores.py. No test
runs alembic; conftest builds the schema from the models with create_all, so
this file is checked by eye against the model, and by rendering
`alembic upgrade 042_job_scores:043_agent_api_keys --sql` (head dies offline on sqlite).
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

    op.create_table(
        "agent_tool_calls",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("key_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("tool", sa.String(length=64), nullable=False),
        sa.Column("resume_id", sa.String(length=36), nullable=True),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["key_id"], ["agent_api_keys.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_agent_tool_calls_user_id", "agent_tool_calls", ["user_id"])
    op.create_index("ix_agent_tool_calls_created_at", "agent_tool_calls", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_agent_tool_calls_created_at", table_name="agent_tool_calls")
    op.drop_index("ix_agent_tool_calls_user_id", table_name="agent_tool_calls")
    op.drop_table("agent_tool_calls")
    op.drop_index("ix_agent_api_keys_user_id", table_name="agent_api_keys")
    op.drop_table("agent_api_keys")
