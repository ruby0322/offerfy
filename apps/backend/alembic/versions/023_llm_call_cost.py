"""record what each model round cost, at the price in force when it ran

`llm_calls` stored token counts and nothing else, so every reader priced them
live through `usage.round_cost_uusd`. That made the cost of history move
whenever a price did -- and it does: correcting claude-opus-5-5 from a seeded
$5/$25 to the published $4/$20 moved one round from 34,653 to 25,848 uUSD.

`usage_counters` do not move, because they hold what was billed at the time.
So a price edit alone made `usage.reconcile` report a gap between the two, and
that gap is surfaced on the admin overview as evidence of spend that bypassed
the meter. A false positive there is worse than no signal, because it teaches
whoever reads it to stop reading it.

Nullable, and deliberately **not** backfilled. A backfill would have to invent
a price for rows nobody recorded one for, which is the same retroactive
pricing this column exists to stop; NULL keeps the old live-pricing behaviour
for those rows and nothing else changes. New rows are priced once, at write.
"""

from alembic import op
import sqlalchemy as sa

revision = "023_llm_call_cost"
down_revision = "022_chat_model_choice"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("llm_calls") as batch:
        batch.add_column(sa.Column("cost_uusd", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("llm_calls") as batch:
        batch.drop_column("cost_uusd")
