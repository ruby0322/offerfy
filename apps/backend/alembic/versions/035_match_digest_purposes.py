"""allow 'match' and 'digest' as llm_calls purposes

The weekly digest's two provider calls: the /v1/decisions scoring pass
(`purpose="match"`) and the prose pass (`purpose="digest"`). Both have moved
usage_counters since they shipped, while llm_calls could not admit their
purpose -- so app.services.usage.reconcile saw counters with no telemetry
behind them and reported a gap on the admin overview for every window a digest
ran in. See app.models.LLM_CALL_PURPOSES.

Widening only: every value that was legal stays legal, so no row can violate
the new constraint and there is nothing to backfill. The rows themselves start
being written by app/services/jobs/score.py and reasons.py in the same change.

sqlite is skipped the same way 015/016/034 skip it: it cannot ALTER a
constraint, and the test suite builds its schema from the models with
create_all rather than from alembic. No test runs alembic; this is checked by
eye against the models and against a throwaway Postgres.
"""

from alembic import op

revision = "035_match_digest_purposes"
down_revision = "034_system_usage_subject"
branch_labels = None
depends_on = None

_OLD = "purpose IN ('chat', 'repair', 'vision', 'profile', 'import', 'extract')"
_NEW = (
    "purpose IN ('chat', 'repair', 'vision', 'profile', 'import', 'extract', "
    "'match', 'digest')"
)


def upgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        return
    op.drop_constraint("llm_call_purpose_values", "llm_calls", type_="check")
    op.create_check_constraint("llm_call_purpose_values", "llm_calls", _NEW)


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        return
    # Fails while any match/digest row exists, which is correct: the narrower
    # constraint cannot describe that data. Delete those rows first if you
    # genuinely mean to go back -- and expect reconcile to report the gap they
    # were written to close.
    op.drop_constraint("llm_call_purpose_values", "llm_calls", type_="check")
    op.create_check_constraint("llm_call_purpose_values", "llm_calls", _OLD)
