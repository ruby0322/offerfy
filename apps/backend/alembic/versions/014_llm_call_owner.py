"""attribute llm_calls to an owner, and record cache writes

Two separate reasons, one migration because both land on the same table.

The owner columns exist because the only route to an owner today is a join
through resume_id, and that join leaks in two directions, each of which clears
a cost quota: resume_id is ON DELETE SET NULL (deleting a resume is one click),
and /v1/auth/claim rewrites resumes.user_id (so a guest's spent rounds would
retroactively bill the account they just converted to). See the column comments
on models.LlmCall.

cache_write_tokens exists because the provider bills cache writes at 1.25x the
uncached input rate and we were not recording them -- 20-45% of the real bill
was invisible.
"""

from alembic import op
import sqlalchemy as sa

revision = "014_llm_call_owner"
down_revision = "013_talent_disclosure"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Named FKs and a named check: ensure_schema() builds this table from the
    # models in production, so the two shapes have to agree or a later
    # drop_constraint finds nothing. Same trap TalentProfile already documents.
    with op.batch_alter_table("llm_calls") as batch:
        batch.add_column(sa.Column("user_id", sa.String(length=36), nullable=True))
        batch.add_column(
            sa.Column("guest_session_id", sa.String(length=36), nullable=True)
        )
        batch.add_column(sa.Column("cache_write_tokens", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_llm_calls_user_id", "users", ["user_id"], ["id"], ondelete="SET NULL"
        )
        batch.create_foreign_key(
            "fk_llm_calls_guest_session_id",
            "guest_sessions",
            ["guest_session_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_check_constraint(
            "llm_call_subject_not_both",
            "NOT (user_id IS NOT NULL AND guest_session_id IS NOT NULL)",
        )

    # Backfill from the join while it is still available. Deliberately
    # imperfect and not worth reconstructing: anything already claimed now
    # points at the claiming user rather than the guest who spent it, and
    # anything whose resume was deleted has no owner to find. Rows written from
    # here on carry their own owner.
    #
    # Postgres-only UPDATE..FROM, with no SQLite branch, because this chain
    # cannot run on SQLite at all -- 012_talent_profiles calls drop_constraint
    # outside batch mode, which the SQLite dialect refuses. Production is
    # Postgres and the tests build from the models via ensure_schema(), so a
    # SQLite branch here would be untestable code implying a path that does
    # not exist.
    op.execute(
        """
        UPDATE llm_calls
           SET user_id = resumes.user_id,
               guest_session_id = resumes.guest_session_id
          FROM resumes
         WHERE resumes.id = llm_calls.resume_id
        """
    )

    # The balance query filters on (subject, created_at) together on every chat
    # turn. The composite covers the leading column too, so there is no
    # single-column index on either.
    op.create_index(
        "ix_llm_calls_user_window", "llm_calls", ["user_id", "created_at"]
    )
    op.create_index(
        "ix_llm_calls_guest_window", "llm_calls", ["guest_session_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_llm_calls_guest_window", table_name="llm_calls")
    op.drop_index("ix_llm_calls_user_window", table_name="llm_calls")
    with op.batch_alter_table("llm_calls") as batch:
        batch.drop_constraint("llm_call_subject_not_both", type_="check")
        batch.drop_constraint("fk_llm_calls_guest_session_id", type_="foreignkey")
        batch.drop_constraint("fk_llm_calls_user_id", type_="foreignkey")
        batch.drop_column("cache_write_tokens")
        batch.drop_column("guest_session_id")
        batch.drop_column("user_id")
