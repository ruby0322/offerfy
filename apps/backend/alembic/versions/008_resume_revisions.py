"""add resume_revisions and published_revision_id"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.orm import Session

revision = "008_resume_revisions"
down_revision = "007_jobs_spotlight"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "resume_revisions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("resume_id", sa.String(length=36), nullable=False),
        sa.Column("typst_source", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["resume_id"], ["resumes.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_resume_revisions_resume_id",
        "resume_revisions",
        ["resume_id"],
    )
    with op.batch_alter_table("resumes") as batch:
        batch.add_column(
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("CURRENT_TIMESTAMP"),
                nullable=False,
            )
        )
        batch.add_column(
            sa.Column("published_revision_id", sa.String(length=36), nullable=True)
        )
        batch.create_foreign_key(
            "fk_resumes_published_revision_id",
            "resume_revisions",
            ["published_revision_id"],
            ["id"],
            ondelete="SET NULL",
        )

    from app.services.revisions import backfill_existing_resumes

    bind = op.get_bind()
    session = Session(bind=bind)
    backfill_existing_resumes(session)
    session.commit()


def downgrade() -> None:
    with op.batch_alter_table("resumes") as batch:
        batch.drop_constraint("fk_resumes_published_revision_id", type_="foreignkey")
        batch.drop_column("published_revision_id")
        batch.drop_column("updated_at")
    op.drop_index("ix_resume_revisions_resume_id", table_name="resume_revisions")
    op.drop_table("resume_revisions")
