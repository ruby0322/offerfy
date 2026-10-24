"""Ingest the Workday boards in data/workday_boards.json.

Runs from the offerfy-workday systemd user timer, daily rather than 6-hourly:
every posting costs its own request, so this source cannot ride the fast pass.
See AGENTS.md and docs/deploy.md.

Exits non-zero when no board answered, which is what makes a stalled pass show
up as a failed unit. Note that upserting zero is normal here and not a failure:
in steady state every posting is already held.
"""

from __future__ import annotations

import argparse
import sys

from app.db import get_engine, get_session_factory
from app.services.jobs.http import new_client
from app.services.jobs.ingest_workday import ingest_workday


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(prog="app.jobs.ingest_workday").parse_args(argv)

    get_engine()
    session = get_session_factory()()
    try:
        with new_client() as client:
            result = ingest_workday(session, client)
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        session.close()

    for item in result.failed:
        print(f"board failed: {item.board_slug}: {item.error}", file=sys.stderr)
    print(
        f"boards_ok={len(result.ok)} boards_failed={len(result.failed)} "
        f"listed={result.fetched} new={result.upserted}"
    )
    if result.refreshed_nothing:
        print("no workday board answered — the catalogue will go stale", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
