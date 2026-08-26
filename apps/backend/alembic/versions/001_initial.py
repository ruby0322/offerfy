"""initial Offerfy schema"""

from alembic import op
import sqlalchemy as sa

revision = "001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("google_sub", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("locale", sa.String(length=16), nullable=False, server_default="zh-TW"),
        sa.UniqueConstraint("google_sub", name="users_google_sub_key"),
    )
    op.create_table(
        "guest_sessions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("locale", sa.String(length=16), nullable=False, server_default="zh-TW"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("key_hash", name="guest_sessions_key_hash_key"),
    )
    op.create_table(
        "resumes",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("typst_source", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("locale", sa.String(length=16), nullable=False, server_default="zh-TW"),
        sa.Column("guest_session_id", sa.String(length=36), sa.ForeignKey("guest_sessions.id"), nullable=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("upload_s3_key", sa.String(length=512), nullable=True),
        sa.CheckConstraint(
            "(guest_session_id IS NOT NULL AND user_id IS NULL) "
            "OR (guest_session_id IS NULL AND user_id IS NOT NULL)",
            name="resume_owner_xor",
        ),
        sa.CheckConstraint("source IN ('create', 'upload')", name="resume_source_values"),
    )
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("resume_id", sa.String(length=36), sa.ForeignKey("resumes.id"), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "rate_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "guest_session_id",
            sa.String(length=36),
            sa.ForeignKey("guest_sessions.id"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('chat', 'export')", name="rate_event_kind_values"),
    )


def downgrade() -> None:
    op.drop_table("rate_events")
    op.drop_table("chat_messages")
    op.drop_table("resumes")
    op.drop_table("guest_sessions")
    op.drop_table("users")
