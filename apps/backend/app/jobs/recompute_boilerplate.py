"""One-off remediation: recompute per-company boilerplate and re-enrich every job.

Usage:
  .venv/bin/python -m app.jobs.recompute_boilerplate

Run once after migration 029_company_boilerplate. force=True is deliberate and
belongs only here: the first recompute must rewrite rows whose stored hash
predates the change of the hash input from raw to stripped text.
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
        session.commit()
        sentences = sum(
            len(json.loads(blob))
            for blob in session.scalars(select(CompanyBoilerplate.sentence_hashes))
        )
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
