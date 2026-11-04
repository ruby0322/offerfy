"""add 'import' to the llm_calls purpose CHECK

A resume import spends real provider tokens, and `app.services.usage.reconcile`
compares `usage_counters.uusd` against the `llm_calls`-derived total per
subject per window -- so an import that moved the counter without writing its
row would leave a permanent false gap on the admin overview and silence the
one alarm that catches an unmetered call site.

It bills under its own purpose rather than as 'chat', following the
compile-repair loop's precedent ('repair' exists so repair rounds do not hide
inside the chat total): the by-purpose breakdown is where import cost has to
be visible.

Postgres only, like every other change to this constraint: SQLite cannot drop
a CHECK, and no test runs alembic -- conftest builds the schema from the
models with create_all. See 012_talent_profiles, which added 'profile' the
same way.
"""

from alembic import op

revision = "025_llm_call_import_purpose"
down_revision = "024_resume_import_progress"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint("llm_call_purpose_values", "llm_calls", type_="check")
        op.create_check_constraint(
            "llm_call_purpose_values",
            "llm_calls",
            "purpose IN ('chat', 'repair', 'vision', 'profile', 'import')",
        )


def downgrade() -> None:
    # Rows already written as 'import' would violate the narrower constraint,
    # so a downgrade on a database that has run an import has to have those
    # rows dealt with first. Deleting telemetry here would be the wrong call
    # to make silently, so this fails loudly instead -- the same contract
    # 012_talent_profiles' downgrade has for 'profile'.
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint("llm_call_purpose_values", "llm_calls", type_="check")
        op.create_check_constraint(
            "llm_call_purpose_values",
            "llm_calls",
            "purpose IN ('chat', 'repair', 'vision', 'profile')",
        )
