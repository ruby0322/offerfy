"""add users.picture"""

from alembic import op
import sqlalchemy as sa

revision = "003_user_picture"
down_revision = "002_import_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("picture", sa.String(length=1024), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("picture")
