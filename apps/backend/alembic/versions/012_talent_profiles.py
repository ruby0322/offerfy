"""add talent_profiles, users.name and users.profile_resume_id"""

from alembic import op
import sqlalchemy as sa

revision = "012_talent_profiles"
down_revision = "011_consent_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("name", sa.String(length=255), nullable=True))
    op.add_column(
        "users", sa.Column("profile_resume_id", sa.String(length=36), nullable=True)
    )
    # Named to match models.py character for character: ensure_schema() creates
    # these same objects from the models in any environment that skips alembic,
    # so a later drop_constraint has to find the same name either way.
    op.create_foreign_key(
        "fk_users_profile_resume_id",
        "users",
        "resumes",
        ["profile_resume_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_table(
        "talent_profiles",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("source_resume_id", sa.String(length=36), nullable=False),
        sa.Column("source_revision_id", sa.String(length=36), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("profile_json", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("headline", sa.String(length=255), nullable=True),
        sa.Column("years_experience", sa.Integer(), nullable=True),
        sa.Column("search_text", sa.Text(), server_default="", nullable=False),
        sa.Column("skills_text", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "built_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("model", sa.String(length=128), server_default="", nullable=False),
        # CASCADE, not the ledgers' SET NULL: this is a copy of personal data,
        # not evidence that it was collected. Erasing the account erases it,
        # and deleting the source resume leaves nothing unsourced behind.
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_resume_id"], ["resumes.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("user_id", name="talent_profiles_user_id_key"),
        sa.CheckConstraint(
            "years_experience IS NULL OR (years_experience >= 0 AND years_experience <= 60)",
            name="talent_profile_years_range",
        ),
    )
    op.create_index("ix_talent_profiles_user_id", "talent_profiles", ["user_id"])
    op.create_index("ix_talent_profiles_built_at", "talent_profiles", ["built_at"])

    # Profile builds are a fourth billable path and belong in LlmCall like the
    # other three. Postgres only: SQLite cannot drop a CHECK, and no test runs
    # alembic -- conftest builds the schema from the models with create_all.
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint("llm_call_purpose_values", "llm_calls", type_="check")
        op.create_check_constraint(
            "llm_call_purpose_values",
            "llm_calls",
            "purpose IN ('chat', 'repair', 'vision', 'profile')",
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint("llm_call_purpose_values", "llm_calls", type_="check")
        op.create_check_constraint(
            "llm_call_purpose_values",
            "llm_calls",
            "purpose IN ('chat', 'repair', 'vision')",
        )
    op.drop_index("ix_talent_profiles_built_at", table_name="talent_profiles")
    op.drop_index("ix_talent_profiles_user_id", table_name="talent_profiles")
    op.drop_table("talent_profiles")
    op.drop_constraint("fk_users_profile_resume_id", "users", type_="foreignkey")
    op.drop_column("users", "profile_resume_id")
    op.drop_column("users", "name")
