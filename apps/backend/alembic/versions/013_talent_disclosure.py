"""add talent_api_keys, disclosure_events and stop_use_notices"""

from alembic import op
import sqlalchemy as sa

revision = "013_talent_disclosure"
down_revision = "012_talent_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Keys first: both ledgers reference them.
    op.create_table(
        "talent_api_keys",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("contact", sa.String(length=320), nullable=False),
        sa.Column("created_by", sa.String(length=320), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("key_hash", name="talent_api_keys_key_hash_key"),
    )

    op.create_table(
        "disclosure_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), nullable=True),
        sa.Column("api_key_id", sa.String(length=36), nullable=True),
        # Copied in at write time, so the row still names someone to contact
        # after the key is revoked or deleted.
        sa.Column("recipient_label", sa.String(length=255), nullable=False),
        sa.Column("recipient_contact", sa.String(length=320), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("profile_content_hash", sa.String(length=64), nullable=False),
        sa.Column("policy_version", sa.String(length=32), nullable=False),
        sa.Column("route", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        # SET NULL on both, following consent_events: erasing an account or a
        # key must not erase the record that a disclosure happened.
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["api_key_id"], ["talent_api_keys.id"], ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            "purpose IN ('talent_introduction')", name="disclosure_purpose_values"
        ),
        sa.CheckConstraint(
            "scope IN ('summary', 'full')", name="disclosure_scope_values"
        ),
    )
    op.create_index("ix_disclosure_events_user_id", "disclosure_events", ["user_id"])
    op.create_index(
        "ix_disclosure_events_api_key_id", "disclosure_events", ["api_key_id"]
    )
    op.create_index(
        "ix_disclosure_events_created_at", "disclosure_events", ["created_at"]
    )

    op.create_table(
        "stop_use_notices",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), nullable=True),
        sa.Column("api_key_id", sa.String(length=36), nullable=True),
        sa.Column("recipient_contact", sa.String(length=320), nullable=False),
        sa.Column("withdrawn_consent_event_id", sa.String(length=36), nullable=True),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("sent_by", sa.String(length=320), nullable=False),
        sa.Column("detail", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["api_key_id"], ["talent_api_keys.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["withdrawn_consent_event_id"], ["consent_events.id"], ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            "channel IN ('manual_email')", name="stop_use_channel_values"
        ),
    )
    op.create_index("ix_stop_use_notices_user_id", "stop_use_notices", ["user_id"])
    op.create_index(
        "ix_stop_use_notices_created_at", "stop_use_notices", ["created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_stop_use_notices_created_at", table_name="stop_use_notices")
    op.drop_index("ix_stop_use_notices_user_id", table_name="stop_use_notices")
    op.drop_table("stop_use_notices")
    op.drop_index("ix_disclosure_events_created_at", table_name="disclosure_events")
    op.drop_index("ix_disclosure_events_api_key_id", table_name="disclosure_events")
    op.drop_index("ix_disclosure_events_user_id", table_name="disclosure_events")
    op.drop_table("disclosure_events")
    op.drop_table("talent_api_keys")
