"""add the account profile columns to users

An account had an email, a Google avatar and a Google-asserted name, and no way
for the person to say anything about themselves. `name` cannot carry an edit:
routers/auth.google_callback overwrites it from the userinfo response on every
login, so a typed name would silently revert the next time they signed in.
`display_name` is therefore a separate column, and it is the one the UI reads.

`username` is unique and nullable: every existing account predates it and none
is being invented for them, because a handle someone did not choose is not a
handle. Uniqueness is enforced here rather than only in the router -- two
concurrent claims both pass a SELECT.

See models.User for why these live beside the Google fields instead of on
TalentProfile, which is derived state and is deleted on withdrawal.
"""

from alembic import op
import sqlalchemy as sa

revision = "020_user_profile"
down_revision = "019_usage_refusals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("username", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("display_name", sa.String(length=80), nullable=True))
        batch.add_column(sa.Column("headline", sa.String(length=120), nullable=True))
        batch.add_column(sa.Column("location", sa.String(length=80), nullable=True))
        batch.add_column(sa.Column("website", sa.String(length=255), nullable=True))
        batch.add_column(sa.Column("bio", sa.Text(), nullable=True))
    # A unique *index*, not a unique constraint. Both enforce the same thing on
    # Postgres, but SQLite cannot ALTER a constraint into an existing table --
    # batch mode would have to copy the whole table to add one, and `recreate`
    # on `users` means rebuilding a table that two foreign keys point at. An
    # index is plain DDL on both dialects. models.User declares the same index
    # by the same name so create_all and this migration agree.
    op.create_index("uq_users_username", "users", ["username"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_users_username", table_name="users")
    with op.batch_alter_table("users") as batch:
        for column in ("bio", "website", "location", "headline", "display_name", "username"):
            batch.drop_column(column)
