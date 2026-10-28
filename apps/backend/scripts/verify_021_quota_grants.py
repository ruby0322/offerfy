#!/usr/bin/env python3
"""Check migration 021 against a throwaway Postgres before it meets prod.

Nothing in the test suite runs alembic -- the tests build the schema from the
models via ensure_schema() on SQLite -- so 021 has never actually executed
anywhere when you read this. Two things only Postgres can tell us:

  * the CHECK constraints and the two ON DELETE rules behave as written, and
  * the migration's table matches what ensure_schema() builds from the models,
    which is the drift the 012/015/017 comments exist to prevent.

Usage -- against a database you are happy to drop, never prod:

    createdb offerfy_mig_check
    DATABASE_URL=postgresql+psycopg://localhost/offerfy_mig_check \
        scripts/verify_021_quota_grants.py
    dropdb offerfy_mig_check

Exits non-zero on the first problem and says what it was.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, inspect, text  # noqa: E402

TABLE = "quota_grants"
EXPECTED_CHECKS = {
    "quota_grant_credits_positive",
    "quota_grant_interval",
    "quota_grant_reasons",
}
EXPECTED_FKS = {"fk_quota_grants_user_id", "fk_quota_grants_issued_by"}


def fail(message: str) -> None:
    print(f"FAIL  {message}")
    sys.exit(1)


def ok(message: str) -> None:
    print(f"ok    {message}")


def main() -> None:
    url = os.environ.get("DATABASE_URL")
    if not url:
        fail("set DATABASE_URL to a throwaway Postgres")
    if "postgres" not in url:
        fail("this check is about Postgres behaviour; point it at Postgres")
    for marker in ("prod", "offerfy_prod", "amazonaws", "supabase"):
        if marker in url:
            fail(f"refusing to run against something that looks like prod ({marker})")

    engine = create_engine(url, future=True)

    # 1. The migration runs, from zero.
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "021_quota_grants")
    ok("alembic upgrade head reaches 021")

    insp = inspect(engine)
    if TABLE not in insp.get_table_names():
        fail(f"{TABLE} was not created")
    ok(f"{TABLE} exists")

    names = {c["name"] for c in insp.get_columns(TABLE)}
    expected_cols = {
        "id",
        "user_id",
        "credits",
        "starts_at",
        "expires_at",
        "revoked_at",
        "reason",
        "detail",
        "issued_by_user_id",
        "created_at",
    }
    if names != expected_cols:
        fail(f"columns differ: missing {expected_cols - names}, extra {names - expected_cols}")
    ok("columns match the model")

    checks = {c["name"] for c in insp.get_check_constraints(TABLE)}
    if not EXPECTED_CHECKS <= checks:
        fail(f"missing CHECK constraints: {EXPECTED_CHECKS - checks}")
    ok("named CHECK constraints present")

    fks = {fk["name"] for fk in insp.get_foreign_keys(TABLE)}
    if not EXPECTED_FKS <= fks:
        fail(f"missing foreign keys: {EXPECTED_FKS - fks}")
    ok("named foreign keys present")

    indexes = {ix["name"] for ix in insp.get_indexes(TABLE)}
    if "ix_quota_grants_user" not in indexes:
        fail("ix_quota_grants_user is missing")
    ok("index present")

    # 2. The constraints actually bite, and the two ON DELETE rules differ.
    with engine.begin() as conn:
        recipient = str(uuid.uuid4())
        issuer = str(uuid.uuid4())
        for uid, email in ((recipient, "r@test"), (issuer, "i@test")):
            conn.execute(
                text(
                    "INSERT INTO users (id, google_sub, email, locale, created_at) "
                    "VALUES (:id, :id, :email, 'en', now())"
                ),
                {"id": uid, "email": email},
            )

        def insert(**kw) -> None:
            kw.setdefault("id", str(uuid.uuid4()))
            kw.setdefault("user_id", recipient)
            kw.setdefault("credits", 10)
            kw.setdefault("reason", "manual")
            kw.setdefault("starts_at", "now()")
            conn.execute(
                text(
                    "INSERT INTO quota_grants "
                    "(id, user_id, credits, reason, starts_at, expires_at, "
                    " issued_by_user_id, created_at) "
                    "VALUES (:id, :user_id, :credits, :reason, now(), "
                    "        :expires_at, :issued_by, now())"
                ),
                {
                    "id": kw["id"],
                    "user_id": kw["user_id"],
                    "credits": kw["credits"],
                    "reason": kw["reason"],
                    "expires_at": kw.get("expires_at"),
                    "issued_by": kw.get("issued_by"),
                },
            )

        for label, kw in (
            ("credits = 0 is rejected", {"credits": 0}),
            ("credits < 0 is rejected", {"credits": -5}),
            ("an unknown reason is rejected", {"reason": "because"}),
            (
                "expires_at before starts_at is rejected",
                {"expires_at": "2000-01-01T00:00:00+00:00"},
            ),
        ):
            sp = conn.begin_nested()
            try:
                insert(**kw)
            except Exception:
                sp.rollback()
                ok(label)
            else:
                sp.rollback()
                fail(label.replace("is rejected", "was ACCEPTED"))

        grant_id = str(uuid.uuid4())
        insert(id=grant_id, issued_by=issuer)
        ok("a valid grant inserts")

        # SET NULL on the issuer, so deleting an admin cannot delete someone
        # else's allowance.
        conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": issuer})
        left = conn.execute(
            text("SELECT issued_by_user_id FROM quota_grants WHERE id = :id"),
            {"id": grant_id},
        ).scalar_one_or_none()
        if left is not None:
            fail("deleting the issuer should SET NULL, not cascade")
        ok("deleting the issuer leaves the grant, with issued_by_user_id NULL")

        # CASCADE on the recipient, so a grant does not outlive its user.
        conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": recipient})
        remaining = conn.execute(
            text("SELECT count(*) FROM quota_grants WHERE id = :id"), {"id": grant_id}
        ).scalar_one()
        if remaining:
            fail("deleting the recipient should CASCADE the grant away")
        ok("deleting the recipient cascades the grant away")

    # 3. The migration and the models agree.
    from app.db import Base  # noqa: F401
    from app import models  # noqa: F401

    model_table = Base.metadata.tables[TABLE]
    live = {c["name"]: c for c in insp.get_columns(TABLE)}
    for col in model_table.columns:
        if col.name not in live:
            fail(f"models has {col.name}, migration does not")
        if col.nullable != live[col.name]["nullable"]:
            fail(
                f"{col.name}: nullable differs "
                f"(model {col.nullable}, database {live[col.name]['nullable']})"
            )
    ok("migration and models agree on columns and nullability")

    print("\nAll checks passed. Safe to run 021 on prod.")


if __name__ == "__main__":
    main()
