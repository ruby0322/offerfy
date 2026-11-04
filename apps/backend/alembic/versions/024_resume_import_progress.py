"""tables for resume import runs and their steps

An uploaded resume is imported by an LLM agent that declares its own list of
sections and edits the resume one section at a time. Progress has to live
here rather than in process memory: offerfy-api runs uvicorn with two
workers, and a client that reconnects mid-import can land on either one, so
only Postgres is shared between them.

`resume_imports` is one attempt at the import. `version` lets a reconnecting
client ask "what changed since I last looked" without replaying the whole
step list, and `heartbeat_at` lets a runner that died mid-import be told
apart from one still working -- both bumped by the runner, not read back
here. `applied` is the count of edits the agent has made so far, read by a
later snapshot payload.

`resume_import_steps` is the agent's own declared section list, in order.
`key` is the agent's stable name for a section (e.g. "education"), unique
per import so the runner can report progress on a key without the caller
having to guess which row that refers to.
"""

from alembic import op
import sqlalchemy as sa

revision = "024_resume_import_progress"
down_revision = "023_llm_call_cost"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Named FKs and named checks: ensure_schema() builds this table from the
    # models for SQLite in the tests, so the names have to match models.py
    # character for character or the two schemas diverge invisibly.
    op.create_table(
        "resume_imports",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("resume_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("file_key", sa.String(length=512), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("applied", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "heartbeat_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["resume_id"],
            ["resumes.id"],
            name="fk_resume_imports_resume_id",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "status IN ('running', 'done', 'failed')",
            name="resume_import_status_values",
        ),
    )
    op.create_index("ix_resume_imports_resume_id", "resume_imports", ["resume_id"])

    op.create_table(
        "resume_import_steps",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("import_id", sa.String(length=36), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("detail", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(
            ["import_id"],
            ["resume_imports.id"],
            name="fk_resume_import_steps_import_id",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'active', 'done', 'skipped')",
            name="resume_import_step_status_values",
        ),
        sa.UniqueConstraint(
            "import_id", "key", name="resume_import_steps_import_id_key_key"
        ),
    )


def downgrade() -> None:
    op.drop_table("resume_import_steps")
    op.drop_index("ix_resume_imports_resume_id", table_name="resume_imports")
    op.drop_table("resume_imports")
