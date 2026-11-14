"""One-off remediation: re-derive every active posting's facets after a rule change.

Usage (production; the systemd unit's own working directory and env file,
per `docs/deploy.md`):

  cd /home/ruby0322/offerfy/apps/backend
  set -a && . /home/ruby0322/offerfy/.env.production && set +a
  .venv/bin/python -m app.jobs.survey_facets          # BEFORE
  .venv/bin/python -m app.jobs.rederive_facets
  .venv/bin/python -m app.jobs.survey_facets          # AFTER

Why this script has to exist at all. `enrich_jobs` skips a posting whose
stored `description_hash` still matches the newly computed one
(`enrich.py`'s gate), and that gate is keyed on the DESCRIPTION -- it knows
nothing about the taxonomy or the parser. So a change to
`data/job_taxonomy.json` or to `location_facets` changes what a *new* or
*re-described* posting derives and leaves every one of the ~21k rows
already in the catalogue exactly as it was. The fix never reaches a reader
until something forces the re-derive. That is this.

`force=True` is deliberate and belongs only in a script like this one.
Anywhere else it is wrong, because it bypasses the skip and so re-derives
every posting in the catalogue on every ingest pass -- the gate is what
keeps a six-hourly pass from rewriting 21,773 unchanged rows. Precedent and
shape: `app.jobs.recompute_boilerplate`, which does the same thing for a
change to the boilerplate hash input.

What it rewrites: `job_facets.seniority`, `function`, `country`, `region`,
`city`, `description_hash` and `enriched_at`, for every ACTIVE posting
(`enrich_jobs(job_ids=None)`'s scope). It does not touch `jobs`, does not
fetch anything over the network, and does not recompute boilerplate -- if a
boilerplate change is what moved, `recompute_boilerplate` is the script,
and it already calls `enrich_jobs(force=True)` itself.

Blast radius, stated because it is not small: every row of `job_facets` is
rewritten in one transaction, and `job_facets` is what every filter on
/jobs reads. A derivation that regressed shows up as a filter that lost
postings, catalogue-wide, immediately. Hence the survey either side -- the
BEFORE run is what makes a bad AFTER visible, and it cannot be taken
afterwards. `survey_facets` is read-only, so taking it costs nothing but
the walk.

Not idempotent in the gate's sense but idempotent in effect: running it
twice derives the same values twice. The second run's `reenriched` count is
the same as the first's, because `force=True` counts rows written, not rows
changed.
"""

from __future__ import annotations

import argparse
import sys

from app.db import get_engine, get_session_factory
from app.services.jobs.enrich import enrich_jobs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="app.jobs.rederive_facets",
        description="Force a re-derive of job_facets for every active posting.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Derive everything and roll back instead of committing. Reports the"
            " same count a real run would write, without changing a row."
        ),
    )
    args = parser.parse_args(argv)

    get_engine()
    session = get_session_factory()()
    try:
        reenriched = enrich_jobs(session, force=True)
        if args.dry_run:
            # enrich_jobs flushes per chunk but never commits -- the caller
            # owns the transaction, so a rollback here really does undo all
            # of it. See enrich_jobs's docstring on that contract.
            session.rollback()
        else:
            session.commit()
    except Exception as exc:
        session.rollback()
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        session.close()

    print(f"reenriched={reenriched} committed={not args.dry_run}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
