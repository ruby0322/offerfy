"""add resume_revisions and published_revision_id"""

import hashlib
import uuid
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

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

    _backfill_revisions(op.get_bind())


def _backfill_revisions(bind) -> None:
    # Pin the columns that exist at this revision; the live ORM models gain
    # columns in later migrations and would break a fresh upgrade.
    resumes = sa.table(
        "resumes",
        sa.column("id", sa.String),
        sa.column("typst_source", sa.Text),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("published_revision_id", sa.String),
    )
    revisions = sa.table(
        "resume_revisions",
        sa.column("id", sa.String),
        sa.column("resume_id", sa.String),
        sa.column("typst_source", sa.Text),
        sa.column("content_hash", sa.String),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    shares = sa.table("resume_shares", sa.column("resume_id", sa.String))

    now = datetime.now(timezone.utc)
    shared = {row.resume_id for row in bind.execute(sa.select(shares.c.resume_id))}
    rows = bind.execute(
        sa.select(resumes.c.id, resumes.c.typst_source, resumes.c.created_at)
    ).all()
    for row in rows:
        source = row.typst_source or ""
        revision_id = str(uuid.uuid4())
        bind.execute(
            revisions.insert().values(
                id=revision_id,
                resume_id=row.id,
                typst_source=source,
                content_hash=hashlib.sha256(source.encode("utf-8")).hexdigest(),
                created_at=row.created_at or now,
            )
        )
        if row.id in shared:
            bind.execute(
                resumes.update()
                .where(resumes.c.id == row.id)
                .values(published_revision_id=revision_id)
            )


def downgrade() -> None:
    with op.batch_alter_table("resumes") as batch:
        batch.drop_constraint("fk_resumes_published_revision_id", type_="foreignkey")
        batch.drop_column("published_revision_id")
        batch.drop_column("updated_at")
    op.drop_index("ix_resume_revisions_resume_id", table_name="resume_revisions")
    op.drop_table("resume_revisions")
