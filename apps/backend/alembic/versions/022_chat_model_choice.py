"""remember which model each caller picked

Model selection needs somewhere durable to put the choice. Both subjects get a
column, because both are real subjects here: `guest_sessions` already has a row
per cookie for the quota to count against, so a guest's pick survives a reload
without the server having to trust a model id the browser remembered.

Nullable with no default and no backfill, deliberately. NULL means "has not
chosen", which is what every existing row is, and
`services.model_choice.selected` reads that as the tier default. Writing
today's default into every row instead would freeze it: changing
`model_catalog.DEFAULT_MODEL` later would then move nobody, because everyone
would hold an explicit preference they never expressed.

64 characters is roomy for the ids in the catalogue (the longest is 17) and
matches `llm_calls.model` being 128 -- that column stores whatever a proxy
returned, this one only ever stores a catalogue id that
`model_choice.require_offered` validated.
"""

from alembic import op
import sqlalchemy as sa

revision = "022_chat_model_choice"
down_revision = "021_quota_grants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # batch_alter_table on both, for SQLite's sake: ADD COLUMN of a nullable
    # column is plain DDL on either dialect, but the surrounding migrations use
    # batch mode and a mix would be a trap for whoever edits this next.
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("chat_model", sa.String(length=64), nullable=True))
    with op.batch_alter_table("guest_sessions") as batch:
        batch.add_column(sa.Column("chat_model", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("guest_sessions") as batch:
        batch.drop_column("chat_model")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("chat_model")
