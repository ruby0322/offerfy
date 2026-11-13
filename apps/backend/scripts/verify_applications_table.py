#!/usr/bin/env python3
"""Read-only check that migration 032_applications landed correctly on production.

Two agent sessions deployed slice 3 together on 2026-11-13. Only one of them
could read the live database, so the schema check below rests on a single
party's word — and "both agents agree" is not verification when only one of
them could look. This script exists so a human can confirm it independently.

It reads and never writes: no INSERT, UPDATE, DELETE or DDL, and it opens the
connection in a read-only transaction so the database itself would refuse one.

    cd /home/ruby0322/offerfy/apps/backend
    .venv/bin/python scripts/verify_applications_table.py

Exits 0 when every expectation holds, 1 otherwise, and prints what it found
either way. It resolves DATABASE_URL through the app's own settings, so it
cannot diverge from what the app uses, and it never prints the credential.
"""

from __future__ import annotations

import sys

from sqlalchemy import create_engine, inspect, text

EXPECTED_COLUMNS = {
    "id",
    "user_id",
    "job_id",
    "tailor_resume_id",
    "status",
    "source",
    "applied_at",
    "applied_confirmed_at",
    "last_status_at",
    "notes",
    "created_at",
}

EXPECTED_CONSTRAINTS = {
    "applications_user_id_fkey": "FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE",
    "applications_job_id_fkey": "FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE",
    # SET NULL, not CASCADE: deleting a tailored resume must not erase the
    # record that you applied for that posting.
    "applications_tailor_resume_id_fkey": "FOREIGN KEY (tailor_resume_id) REFERENCES resumes(id) ON DELETE SET NULL",
    "applications_user_id_job_id_key": "UNIQUE (user_id, job_id)",
}

EXPECTED_INDEX = "ix_applications_user_id"
EXPECTED_REVISION = "032_applications"


def main() -> int:
    sys.path.insert(0, ".")
    from app.config import get_settings  # noqa: PLC0415 — needs sys.path first

    url = get_settings().database_url
    engine = create_engine(url)
    ok = True

    def check(label: str, passed: bool, detail: str = "") -> None:
        nonlocal ok
        ok = ok and passed
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}{(' — ' + detail) if detail else ''}")

    with engine.connect().execution_options(postgresql_readonly=True) as conn:
        dialect = conn.dialect.name
        print(f"database dialect: {dialect}")
        if dialect != "postgresql":
            print("NOT production — a non-postgresql dialect means the env did not load. Stopping.")
            return 1

        print("\nalembic revision")
        revision = conn.execute(text("select version_num from alembic_version")).scalar()
        check(f"alembic_version = {EXPECTED_REVISION}", revision == EXPECTED_REVISION, f"found {revision!r}")

        print("\ntable and columns")
        names = inspect(engine).get_table_names()
        check("table 'applications' exists", "applications" in names)
        if "applications" not in names:
            return 1
        found_columns = {c["name"] for c in inspect(engine).get_columns("applications")}
        check("all 11 expected columns present", EXPECTED_COLUMNS <= found_columns,
              f"missing {sorted(EXPECTED_COLUMNS - found_columns)}" if EXPECTED_COLUMNS - found_columns else "")
        extra = found_columns - EXPECTED_COLUMNS
        check("no unexpected columns", not extra, f"extra {sorted(extra)}" if extra else "")

        print("\nconstraints (the delete rules are the point)")
        rows = conn.execute(text(
            "select conname, pg_get_constraintdef(oid) from pg_constraint "
            "where conrelid = 'applications'::regclass"
        )).all()
        found = {name: definition for name, definition in rows}
        for name, expected in EXPECTED_CONSTRAINTS.items():
            check(name, found.get(name) == expected, f"found {found.get(name)!r}")

        print("\nindex")
        indexes = {r[0] for r in conn.execute(text(
            "select indexname from pg_indexes where tablename = 'applications'"
        ))}
        check(EXPECTED_INDEX, EXPECTED_INDEX in indexes, f"found {sorted(indexes)}")

        print("\nrow count (0 means nobody has saved a posting yet; any number is fine)")
        count = conn.execute(text("select count(*) from applications")).scalar()
        print(f"  applications rows: {count}")

    print("\n" + ("all checks passed" if ok else "SOMETHING IS WRONG — see the FAIL lines above"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
