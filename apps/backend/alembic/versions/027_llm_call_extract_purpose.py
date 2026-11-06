"""add 'extract' to the llm_calls purpose CHECK

A template-switch content extraction (app.services.resume_content.extract_content)
spends real provider tokens, same as a profile build or a resume import --
and `app.services.usage.reconcile` compares `usage_counters.uusd` against the
`llm_calls`-derived total per subject per window, so an extraction that moved
the counter without writing its row would leave a permanent false gap on the
admin overview and silence the one alarm that catches an unmetered call site.

It bills under its own purpose rather than as 'profile', following the same
precedent 'import' set in 025_llm_call_import_purpose: the by-purpose
breakdown is where this cost has to be visible, and it happens once per
drift rather than once per switch, so it is worth seeing on its own line.

Postgres only, like every other change to this constraint: SQLite cannot drop
a CHECK, and no test runs alembic -- conftest builds the schema from the
models with create_all. See 025_llm_call_import_purpose, which added
'import' the same way.
"""

from alembic import op

revision = "027_llm_call_extract_purpose"
down_revision = "026_resume_contents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint("llm_call_purpose_values", "llm_calls", type_="check")
        op.create_check_constraint(
            "llm_call_purpose_values",
            "llm_calls",
            "purpose IN ('chat', 'repair', 'vision', 'profile', 'import', 'extract')",
        )


def downgrade() -> None:
    # Rows already written as 'extract' would violate the narrower constraint,
    # so a downgrade on a database that has run an extraction has to have
    # those rows dealt with first. Deleting telemetry here would be the wrong
    # call to make silently, so this fails loudly instead -- the same
    # contract 025_llm_call_import_purpose's downgrade has for 'import'.
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint("llm_call_purpose_values", "llm_calls", type_="check")
        op.create_check_constraint(
            "llm_call_purpose_values",
            "llm_calls",
            "purpose IN ('chat', 'repair', 'vision', 'profile', 'import')",
        )
