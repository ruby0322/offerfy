"""allow synology as a job source

Synology is not on an ATS we read -- it publishes its own keyless JSON API --
so carrying it needs a sixth value in the jobs.job_source_values check
constraint. See AGENTS.md section 8b and services/jobs/synology.py.

Revision ID: 038_synology_source
Revises: 037_tailor_runs
"""

from alembic import op

revision = "038_synology_source"
down_revision = "037_tailor_runs"
branch_labels = None
depends_on = None

_OLD = (
    "source IN ('greenhouse', 'lever', 'ashby', 'smartrecruiters', "
    "'workday', 'taiwanjobs')"
)
_NEW = (
    "source IN ('greenhouse', 'lever', 'ashby', 'smartrecruiters', "
    "'workday', 'synology', 'taiwanjobs')"
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
    # Fails while any synology row exists, which is correct: the narrower
    # constraint cannot describe that data. Delete those rows first if you
    # genuinely mean to go back.
    op.drop_constraint("job_source_values", "jobs", type_="check")
    op.create_check_constraint("job_source_values", "jobs", _OLD)
