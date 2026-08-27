"""add users.created_at and resumes.created_at"""

from alembic import op
import sqlalchemy as sa

revision = "004_created_at"
down_revision = "003_user_picture"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("CURRENT_TIMESTAMP"),
                nullable=False,
            )
        )
    with op.batch_alter_table("resumes") as batch:
        batch.add_column(
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("CURRENT_TIMESTAMP"),
                nullable=False,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("resumes") as batch:
        batch.drop_column("created_at")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("created_at")
