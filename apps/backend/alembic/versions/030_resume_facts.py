"""add resume_facts

The match-side twin of talent_profiles: the structured facts extracted from
one person's resume, kept so Offerfy can match jobs for that person. See
app.models.ResumeFacts for why this is a separate table rather than a flag on
talent_profiles -- the consent lifetime, the deletion rule, and the
disclosure ledger's citation of talent_profiles' bytes all differ.

user_id is unique, one facts row per person, and both foreign keys are
ondelete="CASCADE": erasing the account or the source resume erases the
facts, the same as it does for talent_profiles.

Column types and indexes mirror talent_profiles exactly, including
String(128) for model and the Text blobs for facts_json (canonical JSON, not
JSONB, so the bytes survive a round trip). The unique constraint is named to
match the model, so that create_all and this migration produce the same
objects.

Plain create_table with no dialect guard, matching 028_job_facets.py -- it
runs the same on SQLite and Postgres. No test runs alembic either way;
conftest builds the schema from the models with create_all, so this
migration is checked by eye against app.models.ResumeFacts, not by the suite.
"""

from alembic import op
import sqlalchemy as sa

revision = "030_resume_facts"
down_revision = "029_company_boilerplate"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "resume_facts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("source_resume_id", sa.String(length=36), nullable=False),
        sa.Column("source_revision_id", sa.String(length=36), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "schema_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column("facts_json", sa.Text(), nullable=False),
        sa.Column("skills_text", sa.Text(), nullable=False),
        sa.Column("search_text", sa.Text(), nullable=False),
        sa.Column("years_experience", sa.Integer(), nullable=True),
        sa.Column("built_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_resume_id"], ["resumes.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("user_id", name="resume_facts_user_id_key"),
    )
    op.create_index("ix_resume_facts_user_id", "resume_facts", ["user_id"])
    op.create_index("ix_resume_facts_built_at", "resume_facts", ["built_at"])


def downgrade() -> None:
    op.drop_index("ix_resume_facts_built_at", table_name="resume_facts")
    op.drop_index("ix_resume_facts_user_id", table_name="resume_facts")
    op.drop_table("resume_facts")
