"""One-off remediation: recompute per-company boilerplate and re-enrich every job.

Usage:
  .venv/bin/python -m app.jobs.recompute_boilerplate

Run once after migration 029_company_boilerplate. force=True is deliberate and
belongs only here: the first recompute must rewrite rows whose stored hash
predates the change of the hash input from raw to stripped text. Anywhere
else it is wrong, because it bypasses the description_hash skip and so
re-derives every posting in the catalogue on every ingest pass -- the gate
that keeps a six-hourly pass from re-deriving 21,773 unchanged rows.
"""

from __future__ import annotations

import argparse
import json
import sys

from sqlalchemy import select

from app.db import get_engine, get_session_factory
from app.models import CompanyBoilerplate
from app.services.jobs.boilerplate import recompute_boilerplate
from app.services.jobs.enrich import enrich_jobs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.jobs.recompute_boilerplate")
    parser.parse_args(argv)

    get_engine()
    session = get_session_factory()()
    try:
        changed = recompute_boilerplate(session)
        reenriched = enrich_jobs(session, force=True)
        # Counted before the commit, deliberately: a failure in this read after
        # a successful commit would report rc 1 on a remediation that in fact
        # landed. recompute_boilerplate has flushed, so the numbers are the
        # ones about to be committed.
        #
        # Total stored sentence hashes across all companies, NOT the number of
        # rows -- it is the number that says whether detection found anything
        # (1,806 across 87 companies in the measurement this work argues from),
        # and companies_changed already stands next to it. Do not "fix" it to
        # a row count.
        sentences = sum(
            len(json.loads(blob))
            for blob in session.scalars(select(CompanyBoilerplate.sentence_hashes))
        )
        session.commit()
    except Exception as exc:
        session.rollback()
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        session.close()

    print(
        f"companies_changed={len(changed)} boilerplate_sentences={sentences} "
        f"reenriched={reenriched}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
