"""Ingest official ATS boards and TaiwanJobs into the jobs table.

Usage (compose):
  docker compose run --rm backend python -m app.jobs.ingest_catalog
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
            ingest_catalog(session, client, include_taiwanjobs=not args.skip_taiwanjobs)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
