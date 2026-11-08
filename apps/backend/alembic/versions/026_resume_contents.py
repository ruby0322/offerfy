"""add resume_contents

A structured snapshot of one resume's content, for the deterministic template
switch: see app.services.resume_content and app.models.ResumeContent for why
it is a separate table (derived state, same reasoning as talent_profiles)
and why content_json is Text rather than JSONB (nothing queries inside the
blob; see profile_json in 012_talent_profiles).

No CHECK constraint here, unlike the purpose-column migrations in this repo,
so this is a plain create_table with no dialect guard -- it runs the same on
SQLite and Postgres. No test runs alembic either way; conftest builds the
schema from the models with create_all, so this migration is checked by eye
against models.ResumeContent, not by the suite.
"""

from alembic import op
import sqlalchemy as sa

revision = "026_resume_contents"
down_revision = "025_llm_call_import_purpose"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "resume_contents",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("resume_id", sa.String(length=36), nullable=False),
        sa.Column("content_json", sa.Text(), nullable=False),
        sa.Column("derived_from_hash", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), server_default="", nullable=False),
        sa.Column(
            "built_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        # CASCADE: a snapshot with no source resume is unsourced, same as
        # talent_profiles.source_resume_id.
        sa.ForeignKeyConstraint(["resume_id"], ["resumes.id"], ondelete="CASCADE"),
        # Named to match models.py character for character -- ensure_schema()
        # creates these same objects from the models in any environment that
        # skips alembic, so a later drop_constraint has to find the same name
        # either way. Same reasoning as talent_profiles_user_id_key.
        sa.UniqueConstraint("resume_id", name="resume_contents_resume_id_key"),
    )
    op.create_index("ix_resume_contents_resume_id", "resume_contents", ["resume_id"])


def downgrade() -> None:
    op.drop_index("ix_resume_contents_resume_id", table_name="resume_contents")
    op.drop_table("resume_contents")
