"""index chat_messages.resume_id

Every read of this table filters on `resume_id` and on nothing else --
`chat.list_messages` for a transcript, `chat._history` for the model's window,
and `tailor_run.snapshot`'s `message_count`, which a live tailor pass runs once
per 0.4s poll tick. Postgres does not index a foreign key on its own, so all of
those were sequential scans over every chat message on the platform. The table
stores whole tool traces in `content` (one production resume held 1.44M
characters of them), so what made this worth paying for is the width of the
rows being scanned rather than their number.

**Built CONCURRENTLY, which is new in this repo and deliberate.** A plain
`CREATE INDEX` holds ACCESS EXCLUSIVE on the table until it finishes, which
blocks every reader and writer -- and with 15 pool slots and sync handlers,
requests piling up behind it take the whole API down, `/healthz` included
(see docs/deploy.md, *Security posture* and the pool notes). CONCURRENTLY
takes only SHARE UPDATE EXCLUSIVE, so the site keeps serving while it builds.

The cost of that choice: it cannot run inside a transaction, hence the
`autocommit_block`, and a failed build leaves an INVALID index behind rather
than rolling back. If this migration errors, check with

    select indexrelid::regclass, indisvalid from pg_index
     where indexrelid = 'ix_chat_messages_resume_id'::regclass;

and `DROP INDEX ix_chat_messages_resume_id` before retrying -- an INVALID index
is not used by the planner but is still maintained on every write, so leaving
one is worse than having none.

The name matches what `index=True` on the model produces, so
`Base.metadata.create_all()` on an API start sees it already there and does
nothing. sqlite is skipped the way 015/016/034/035/040 skip their work: the
test suite builds its schema from the models, where `index=True` creates this
index anyway, so pytest covers the indexed shape without running alembic.
"""

from alembic import op

revision = "041_chat_messages_resume_index"
down_revision = "040_tailor_phase_steps"
branch_labels = None
depends_on = None

_NAME = "ix_chat_messages_resume_id"


def upgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        return
    with op.get_context().autocommit_block():
        op.create_index(
            _NAME, "chat_messages", ["resume_id"], postgresql_concurrently=True
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        return
    with op.get_context().autocommit_block():
        op.drop_index(_NAME, table_name="chat_messages", postgresql_concurrently=True)
