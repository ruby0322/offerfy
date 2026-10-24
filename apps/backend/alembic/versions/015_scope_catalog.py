"""allow smartrecruiters as a job source and retire the taiwanjobs rows"""

from alembic import op
import sqlalchemy as sa

revision = "015_scope_catalog"
down_revision = "014_job_ingest_run_id"
branch_labels = None
depends_on = None

_OLD = "source IN ('greenhouse', 'lever', 'ashby', 'taiwanjobs')"
_NEW = "source IN ('greenhouse', 'lever', 'ashby', 'smartrecruiters', 'taiwanjobs')"


def upgrade() -> None:
    bind = op.get_bind()
    # taiwanjobs stays permitted even though nothing ingests it: ~1,500
    # historical rows still carry it and dropping it from the constraint would
    # fail against them.
    if bind.dialect.name != "sqlite":
        op.drop_constraint("job_source_values", "jobs", type_="check")
        op.create_check_constraint("job_source_values", "jobs", _NEW)

    # One-time data step, not a schema change. expire_missing only deactivates
    # rows for a source that is still being ingested, so now that the taiwanjobs
    # fetcher is gone nothing else would ever retire these -- they would sit in
    # the sitemap until their 30-day freshness window lapsed, serving blue-collar
    # postings as Offerfy's job catalogue.
    op.execute(sa.text("UPDATE jobs SET is_active = false WHERE source = 'taiwanjobs'"))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        op.drop_constraint("job_source_values", "jobs", type_="check")
        op.create_check_constraint("job_source_values", "jobs", _OLD)
    # Deliberately NOT reactivating the taiwanjobs rows. They were retired
    # because they were the wrong catalogue, and a downgrade should not put 1,458
    # room-attendant and delivery-driver postings back on the public site.
    #
    # Note this downgrade fails while any smartrecruiters row exists, which is
    # correct: the narrower constraint genuinely cannot describe that data.
