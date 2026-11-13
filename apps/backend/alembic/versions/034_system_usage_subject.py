"""allow 'system' as a usage_counters subject kind

Work no HTTP caller is waiting on -- the nightly profile refresh, the weekly
digest build -- is recorded by metering.system_guard under subject kind
'system' with the person's id, instead of under 'user'. `balance()` reads
kind='user' for used_uusd, so billing it to 'user' spent the person's weekly
credits on work they never asked for; `window_totals` sums every kind, so the
global ceiling sees it either way. See app.models.UsageCounter.

Constraint only. No data is moved: counters already written under 'user' stay
there. Backfilling would mean deciding retrospectively which of someone's rows
were the refresh's, and usage_counters is a sum per (subject, window) with no
purpose column to decide it with -- the rows are indistinguishable by
construction. The effect is that the window in which this ships may show a
person's allowance as more used than the new rule would have made it, which
self-corrects at the next Monday reset.

sqlite is skipped the same way 015/016 skip it: it cannot ALTER a constraint,
and the test suite builds its schema from the models with create_all rather
than from alembic. No test runs alembic; this is checked by eye against the
models and against a throwaway Postgres.
"""

from alembic import op

revision = "034_system_usage_subject"
down_revision = "033_digests"
branch_labels = None
depends_on = None

_OLD = "subject_kind IN ('user', 'guest', 'network')"
_NEW = "subject_kind IN ('user', 'guest', 'network', 'system')"


def upgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        return
    op.drop_constraint(
        "usage_counter_subject_kinds", "usage_counters", type_="check"
    )
    op.create_check_constraint(
        "usage_counter_subject_kinds", "usage_counters", _NEW
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        return
    # Fails while any 'system' row exists, which is correct: the narrower
    # constraint cannot describe that data. Those rows are one window old at
    # most; delete them, or wait for the pruner, if you genuinely mean to go
    # back.
    op.drop_constraint(
        "usage_counter_subject_kinds", "usage_counters", type_="check"
    )
    op.create_check_constraint(
        "usage_counter_subject_kinds", "usage_counters", _OLD
    )
