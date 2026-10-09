"""add resume family columns and memory_items"""

from alembic import op
import sqlalchemy as sa

revision = "009_resume_family_memory"
down_revision = "008_resume_revisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("resumes") as batch:
        batch.add_column(
            sa.Column(
                "kind",
                sa.String(length=16),
                server_default="master",
                nullable=False,
            )
        )
        batch.add_column(sa.Column("parent_resume_id", sa.String(length=36), nullable=True))
        batch.add_column(
            sa.Column("forked_from_revision_id", sa.String(length=36), nullable=True)
        )
        batch.add_column(sa.Column("target_label", sa.String(length=255), nullable=True))
        batch.add_column(sa.Column("target_context", sa.Text(), nullable=True))
        batch.create_foreign_key(
            "fk_resumes_parent_resume_id",
            "resumes",
            ["parent_resume_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.create_foreign_key(
            "fk_resumes_forked_from_revision_id",
            "resume_revisions",
            ["forked_from_revision_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_resumes_parent_resume_id", ["parent_resume_id"])
        batch.create_check_constraint(
            "resume_kind_values", "kind IN ('master', 'tailor')"
        )
        batch.create_check_constraint(
            "resume_kind_parent",
            "(kind = 'master' AND parent_resume_id IS NULL) "
            "OR (kind = 'tailor' AND parent_resume_id IS NOT NULL)",
        )

    op.create_table(
        "memory_items",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("master_resume_id", sa.String(length=36), nullable=False),
        sa.Column("source_resume_id", sa.String(length=36), nullable=True),
        sa.Column("text", sa.String(length=280), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["master_resume_id"], ["resumes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_resume_id"], ["resumes.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'dismissed')",
            name="memory_status_values",
        ),
    )
    op.create_index("ix_memory_items_master_resume_id", "memory_items", ["master_resume_id"])


def downgrade() -> None:
    op.drop_index("ix_memory_items_master_resume_id", table_name="memory_items")
    op.drop_table("memory_items")
    with op.batch_alter_table("resumes") as batch:
        batch.drop_constraint("resume_kind_parent", type_="check")
        batch.drop_constraint("resume_kind_values", type_="check")
        batch.drop_index("ix_resumes_parent_resume_id")
        batch.drop_constraint("fk_resumes_forked_from_revision_id", type_="foreignkey")
        batch.drop_constraint("fk_resumes_parent_resume_id", type_="foreignkey")
        batch.drop_column("target_context")
        batch.drop_column("target_label")
        batch.drop_column("forked_from_revision_id")
        batch.drop_column("parent_resume_id")
        batch.drop_column("kind")
