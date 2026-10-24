"""Ingest official ATS boards and TaiwanJobs into the jobs table.

Usage (compose):
  docker compose run --rm backend python -m app.jobs.ingest_catalog

On abbey this runs every 6 hours from the offerfy-ingest systemd user timer;
see ops/systemd/offerfy-ingest.timer and docs/deploy.md. Exits non-zero when a
pass refreshed nothing, so a stalled catalog shows up as a failed unit instead
of quietly ageing every posting out of its validThrough window.
"""

from __future__ import annotations

import argparse
import sys

from app.db import get_engine, get_session_factory
from app.services.jobs.http import new_client
from app.services.jobs.ingest import ingest_catalog


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.jobs.ingest_catalog")
    parser.add_argument(
        "--skip-taiwanjobs",
        action="store_true",
        help="Only poll ATS boards listed in data/ats_boards.json",
    )
    args = parser.parse_args(argv)

    get_engine()
    session = get_session_factory()()
    try:
        with new_client() as client:
            result = ingest_catalog(
                session, client, include_taiwanjobs=not args.skip_taiwanjobs
            )
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        session.close()

    for item in result.failed:
        label = f"{item.source}:{item.board_slug}" if item.board_slug else item.source
        print(f"source failed: {label}: {item.error}", file=sys.stderr)
    print(
        f"sources_ok={len(result.ok)} sources_failed={len(result.failed)} "
        f"upserted={result.upserted}"
    )

    if result.refreshed_nothing:
        print(
            "ingest refreshed nothing — the catalog will go stale and every "
            "posting will age out of its validThrough window",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
