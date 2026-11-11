"""add job_facets

A derived-state table for the deterministic facets app.services.jobs.enrich
computes from a job's own title/description/location -- see
app.models.JobFacets for why it is a separate table (same reasoning as
resume_contents/talent_profiles: the whole row can be dropped and rebuilt
from jobs at any time) rather than six more columns on jobs.

job_id is the primary key, not a separate id column: a job has at most one
facets row, and the FK is ondelete="CASCADE" so deleting a job never leaves
an orphan facets row behind -- test_deleting_a_job_cascades_to_its_facets
in tests/test_jobs_enrich.py exercises exactly this.

seniority and function have no CHECK constraint, unlike jobs.source and
llm_calls.purpose elsewhere in this file: both vocabularies may grow, and
enrich.py is their only writer, so a constraint here would just be a
migration tax on every future taxonomy addition for no safety this module
doesn't already give by construction ("unknown"/"other" are real members,
not a fallback that needs guarding against).

country, seniority and function are indexed because the jobs list endpoint
(a later task) filters on all three; region and city are not, because
nothing filters on them yet.

Plain create_table with no dialect guard, matching 026_resume_contents.py
-- it runs the same on SQLite and Postgres. No test runs alembic either
way; conftest builds the schema from the models with create_all, so this
migration is checked by eye against app.models.JobFacets, not by the
suite.
"""

from alembic import op
import sqlalchemy as sa

revision = "028_job_facets"
down_revision = "027_llm_call_extract_purpose"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_facets",
        sa.Column("job_id", sa.String(length=36), primary_key=True),
        sa.Column("country", sa.String(length=2), nullable=True),
        sa.Column("region", sa.String(length=32), nullable=True),
        sa.Column("city", sa.String(length=255), nullable=True),
        sa.Column("seniority", sa.String(length=16), nullable=False),
        sa.Column("function", sa.String(length=16), nullable=False),
        sa.Column("description_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "enriched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        # CASCADE: a facets row with no source job is unsourced, same as
        # resume_contents.resume_id in 026_resume_contents.py.
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_job_facets_country", "job_facets", ["country"])
    op.create_index("ix_job_facets_seniority", "job_facets", ["seniority"])
    op.create_index("ix_job_facets_function", "job_facets", ["function"])


def downgrade() -> None:
    op.drop_index("ix_job_facets_function", table_name="job_facets")
    op.drop_index("ix_job_facets_seniority", table_name="job_facets")
    op.drop_index("ix_job_facets_country", table_name="job_facets")
    op.drop_table("job_facets")
