"""allow line as a job source

LINE publishes on none of the four ATS providers -- it serves its own careers
site's Gatsby page-data as JSON -- so carrying it needs a seventh value in the
jobs.job_source_values check constraint. See AGENTS.md section 8b and
services/jobs/line.py.

This revises 038_synology_source rather than 037_tailor_runs: both sources were
added the same day and alembic would otherwise have two heads.

Revision ID: 039_line_source
Revises: 038_synology_source
"""

from alembic import op

revision = "039_line_source"
down_revision = "038_synology_source"
branch_labels = None
depends_on = None

_OLD = (
    "source IN ('greenhouse', 'lever', 'ashby', 'smartrecruiters', "
    "'workday', 'synology', 'taiwanjobs')"
)
_NEW = (
    "source IN ('greenhouse', 'lever', 'ashby', 'smartrecruiters', "
    "'workday', 'synology', 'line', 'taiwanjobs')"
)


def upgrade() -> None:
    # sqlite cannot ALTER a constraint, and the test suite builds its schema from
    # the models rather than from alembic, so skipping there costs nothing.
    if op.get_bind().dialect.name == "sqlite":
        return
    op.drop_constraint("job_source_values", "jobs", type_="check")
    op.create_check_constraint("job_source_values", "jobs", _NEW)


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        return
    # Fails while any line row exists, which is correct: the narrower constraint
    # cannot describe that data. Delete those rows first if you genuinely mean
    # to go back.
    op.drop_constraint("job_source_values", "jobs", type_="check")
    op.create_check_constraint("job_source_values", "jobs", _OLD)
