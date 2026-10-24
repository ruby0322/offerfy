"""allow workday as a job source"""

from alembic import op

revision = "016_workday_source"
down_revision = "015_scope_catalog"
branch_labels = None
depends_on = None

_OLD = "source IN ('greenhouse', 'lever', 'ashby', 'smartrecruiters', 'taiwanjobs')"
_NEW = (
    "source IN ('greenhouse', 'lever', 'ashby', 'smartrecruiters', "
    "'workday', 'taiwanjobs')"
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
    # Fails while any workday row exists, which is correct: the narrower
    # constraint cannot describe that data. Delete those rows first if you
    # genuinely mean to go back.
    op.drop_constraint("job_source_values", "jobs", type_="check")
    op.create_check_constraint("job_source_values", "jobs", _OLD)
