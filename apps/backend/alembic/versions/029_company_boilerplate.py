"""add company_boilerplate

A derived-state table holding, per company, the hashes of sentences that
recur across most of that company's job postings. Written by the
boilerplate pass that keeps per-company mission statements from deciding a
posting's function facet -- see app.models.CompanyBoilerplate for why it is
keyed on the free-text company string rather than a board or source, and
why sentence_hashes is Text rather than JSON.

company is the primary key, not a separate id column: a company has at most
one boilerplate row, and recomputing it replaces the row wholesale. There
is no foreign key -- company is a string taken from postings, not a
reference to a table, so there is nothing to cascade from.

posting_count is stored alongside the hashes so a later reader can tell how
large the corpus was that produced them without re-counting jobs.

No CHECK constraint on sentence_hashes: it is a JSON array of hex digests
written by one module (app.services.jobs.boilerplate), and a constraint on
its shape would be a migration tax on any future change to the hashing
without guarding against anything that module does not already rule out by
construction.

Plain create_table with no dialect guard, matching 028_job_facets.py -- it
runs the same on SQLite and Postgres. No test runs alembic either way;
conftest builds the schema from the models with create_all, so this
migration is checked by eye against app.models.CompanyBoilerplate, not by
the suite.
"""

from alembic import op
import sqlalchemy as sa

revision = "029_company_boilerplate"
down_revision = "028_job_facets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "company_boilerplate",
        sa.Column("company", sa.String(length=255), primary_key=True),
        sa.Column("sentence_hashes", sa.Text(), nullable=False),
        sa.Column("posting_count", sa.Integer(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("company_boilerplate")
