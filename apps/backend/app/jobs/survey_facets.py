"""Measure how often the facet taxonomy fails to classify a live posting.

`app.services.jobs.enrich` derives seniority/function/location facets from
needle lists in `data/job_taxonomy.json` -- the same shape as
`data/job_scope.json`, and tuned the same way: against real data, by
someone who can see the misses. No agent session can read the production
database (see the maintainer's own note on that), so this script is the
artifact a human runs against prod to get the numbers back. Precedent:
`app/jobs/survey_templates.py`, which plays the same role for the Typst
template picker.

**This measures non-classification, not mis-classification.** `unknown`
seniority and `other` function are real, deliberate members of their
vocabularies (see `enrich.py`'s module docstring) -- a posting lands there
when no needle matched at all, which this script can detect by just
reading the stored facet. It cannot detect a *confident but wrong* answer,
because that needs ground truth this script does not have. Two such misses
are already known and accepted: `"DevOps Engineer, Computer Vision Team"`
resolves to function `ml` rather than `infra` because `function_of` is
longest-needle-wins and "computer vision" outruns "devops"; `"Site
Reliability Engineer, Machine Learning Platform"` is a 16-character needle
tie broken by file order, also landing on `ml`. Neither posting shows up
in this report's `other_function` bucket -- both produced a confident
wrong answer, not a non-answer -- so a clean report here is not proof the
needles are right. It is only proof they did not give up.

Three gates, reported separately because they point at different fixes:

- `no_facets`: the posting has no `job_facets` row at all. Enrichment never
  ran on it. Expected before the first backfill, and permanent for any
  Workday posting whose description is never re-fetched after ingest (see
  `enrich_jobs`'s module docstring on why `job_ids=None` only reaches
  active rows at all, and ingest's own notes on Workday's fetch model).
  The fix is to run the backfill, not to touch the taxonomy.
- `unknown_seniority` / `other_function` / `null_country`: the posting has
  a facets row, but the needle lists found nothing for that one field. The
  fix here is tuning `data/job_taxonomy.json`.

Conflating the first with the other three would send the maintainer
tuning needles when what is actually missing is a backfill run -- see the
Deployment order notes in the task-7 brief for the order this script is
meant to be run in (backfill once, survey, tune, re-backfill with
`force=True`).

Grouped by `jobs.source` too, not just in total: a single board's naming
convention is usually what drives a bad rate (one ATS's title idiom, one
board's location format), and a totals-only number cannot point at which
board to go look at.

READ-ONLY. This script never calls `enrich_jobs`, never writes a
`job_facets` row, and never commits anything. It only ever reads what is
already there -- safe to run against production, which is the whole point.

Usage (compose):
  docker compose run --rm backend python -m app.jobs.survey_facets
  docker compose run --rm backend python -m app.jobs.survey_facets --limit 2000
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from sqlalchemy.orm import Session

from app.db import get_engine, get_session_factory
from app.models import CompanyBoilerplate, Job, JobFacets
from app.services.jobs.enrich import function_of

# Three or four example titles per unclassified bucket, per the brief --
# "14% unknown seniority" is not actionable, "14% unknown seniority, e.g.
# 'Member of Technical Staff, Inference'" is. More than a handful would
# just be noise at a terminal.
_EXAMPLES_CAP = 4

_BUCKETS = ("no_facets", "unknown_seniority", "other_function", "null_country")


def _empty_counts() -> dict[str, int]:
    return {"total": 0, "no_facets": 0, "unknown_seniority": 0, "other_function": 0, "null_country": 0}


def survey(db: Session, *, limit: int | None = None) -> dict[str, Any]:
    """Read-only tally of facet (non-)coverage across `jobs`.

    Scoped to active postings -- the same scope `enrich_jobs`'s backfill
    and the `/jobs` listing both use -- since a retired posting is not one
    the maintainer needs a filter to work for. `limit`, when given, caps
    how many active jobs are considered (ordered by `Job.id` for a stable,
    repeatable slice), for a quick look at a large catalogue without
    walking all of it.

    Returns a dict with at least `total`, `no_facets`, `unknown_seniority`,
    `other_function` and `null_country` at the top level (counts over every
    job considered, independent of each other -- a job can land in more
    than one bucket at once, e.g. unknown seniority *and* other function),
    plus `by_source` (the same five counts, per `jobs.source`) and
    `examples` (up to `_EXAMPLES_CAP` titles per bucket, for exactly the
    jobs that landed in it).

    A posting with no `job_facets` row counts only in `no_facets`, never in
    `unknown_seniority` / `other_function` / `null_country` -- those three
    describe a field the needles looked at and missed, which is not what
    happened to a row that was never enriched at all.
    """
    query = (
        db.query(
            Job.id,
            Job.source,
            Job.title,
            JobFacets.seniority,
            JobFacets.function,
            JobFacets.country,
        )
        .outerjoin(JobFacets, JobFacets.job_id == Job.id)
        .filter(Job.is_active.is_(True))
        .order_by(Job.id)
    )
    if limit is not None:
        query = query.limit(limit)

    overall = _empty_counts()
    # Postings whose function came from the description alone: a function was
    # found but the title by itself derives nothing. This is the number that
    # exposes company boilerplate deciding facets (4,792 of 5,328 for `ml`).
    description_driven = 0
    by_source: dict[str, dict[str, int]] = {}
    examples: dict[str, list[str]] = {bucket: [] for bucket in _BUCKETS}

    for _job_id, source, title, seniority, function, country in query:
        overall["total"] += 1
        source_counts = by_source.setdefault(source, _empty_counts())
        source_counts["total"] += 1

        # JobFacets.seniority is NOT NULL on a real row -- None here only
        # ever means the outer join found no row at all, i.e. enrichment
        # never ran on this posting.
        if seniority is None:
            overall["no_facets"] += 1
            source_counts["no_facets"] += 1
            if len(examples["no_facets"]) < _EXAMPLES_CAP:
                examples["no_facets"].append(title)
            continue

        if seniority == "unknown":
            overall["unknown_seniority"] += 1
            source_counts["unknown_seniority"] += 1
            if len(examples["unknown_seniority"]) < _EXAMPLES_CAP:
                examples["unknown_seniority"].append(title)
        if function != "other" and function_of(title, "") == "other":
            description_driven += 1
        if function == "other":
            overall["other_function"] += 1
            source_counts["other_function"] += 1
            if len(examples["other_function"]) < _EXAMPLES_CAP:
                examples["other_function"].append(title)
        if country is None:
            overall["null_country"] += 1
            source_counts["null_country"] += 1
            if len(examples["null_country"]) < _EXAMPLES_CAP:
                examples["null_country"].append(title)

    bp_rows = db.query(CompanyBoilerplate.sentence_hashes).all()
    return {
        **overall,
        "description_driven_function": description_driven,
        "companies_with_boilerplate": len(bp_rows),
        "boilerplate_sentences_total": sum(len(json.loads(r[0])) for r in bp_rows),
        "by_source": by_source,
        "examples": examples,
    }


def _pct(count: int, total: int) -> str:
    if total == 0:
        return "0.0%"
    return f"{count / total * 100:.1f}%"


def _print_bucket(label: str, key: str, counts: dict[str, int], examples: list[str]) -> None:
    total = counts["total"]
    count = counts[key]
    line = f"{label}: {count}/{total} ({_pct(count, total)})"
    if examples:
        shown = "; ".join(f'"{title}"' for title in examples)
        line += f" -- e.g. {shown}"
    print(line)


def _print_report(report: dict[str, Any]) -> None:
    total = report["total"]
    print("Facet coverage survey (non-classification, not mis-classification)")
    print(f"total active postings considered: {total}")
    print()
    _print_bucket("no job_facets row", "no_facets", report, report["examples"]["no_facets"])
    _print_bucket("unknown seniority", "unknown_seniority", report, report["examples"]["unknown_seniority"])
    _print_bucket("other function", "other_function", report, report["examples"]["other_function"])
    _print_bucket("null country", "null_country", report, report["examples"]["null_country"])

    print(f"description-driven function: {report['description_driven_function']}/{total}")
    print(
        f"companies with boilerplate: {report['companies_with_boilerplate']}"
        f" ({report['boilerplate_sentences_total']} sentences)"
    )

    by_source = report["by_source"]
    if by_source:
        print()
        print("By source:")
        for source in sorted(by_source):
            counts = by_source[source]
            print(
                f"  {source}: total={counts['total']}"
                f" no_facets={counts['no_facets']} ({_pct(counts['no_facets'], counts['total'])})"
                f" unknown_seniority={counts['unknown_seniority']} ({_pct(counts['unknown_seniority'], counts['total'])})"
                f" other_function={counts['other_function']} ({_pct(counts['other_function'], counts['total'])})"
                f" null_country={counts['null_country']} ({_pct(counts['null_country'], counts['total'])})"
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.jobs.survey_facets")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Consider only the first N active jobs (ordered by id), instead of the whole catalogue",
    )
    args = parser.parse_args(argv)

    get_engine()
    session = get_session_factory()()
    try:
        report = survey(session, limit=args.limit)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        session.close()

    _print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
