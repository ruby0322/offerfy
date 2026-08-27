"""add resume_shares for public unlisted links"""

from alembic import op
import sqlalchemy as sa

revision = "005_resume_shares"
down_revision = "004_created_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "resume_shares",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("resume_id", sa.String(length=36), nullable=False),
        sa.Column("token", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["resume_id"], ["resumes.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("resume_id"),
        sa.UniqueConstraint("token"),
    )


def downgrade() -> None:
    op.drop_table("resume_shares")
