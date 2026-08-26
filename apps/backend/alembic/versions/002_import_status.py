"""add resumes.import_status"""

from alembic import op
import sqlalchemy as sa

revision = "002_import_status"
down_revision = "001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("resumes") as batch:
        batch.add_column(
            sa.Column(
                "import_status",
                sa.String(length=16),
                nullable=False,
                server_default="idle",
            )
        )
        batch.create_check_constraint(
            "resume_import_status_values",
            "import_status IN ('idle', 'pending', 'done', 'failed')",
        )


def downgrade() -> None:
    with op.batch_alter_table("resumes") as batch:
        batch.drop_constraint("resume_import_status_values", type_="check")
        batch.drop_column("import_status")
