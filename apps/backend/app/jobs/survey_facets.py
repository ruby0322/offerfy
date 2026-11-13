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
- `no_pickable_city`: the posting has a facets row whose `city` names no
  city a person could pick -- it is NULL, or it folds to nothing through
  `cities.canonical_cities` (a site code, a placeholder, or a bare country
  code). Measured through the fold rather than against NULL on purpose: the
  Workday `CC-City` bug stored city="GB" for "GB-London", which is not
  NULL and is not a city either, so a `city IS NULL` count reported it as
  covered. Any change to the city derivation has to be measured here or it
  cannot be seen at all.

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

Usage. Production runs the backend from systemd, not compose -- the unit's
`WorkingDirectory` is `/home/ruby0322/offerfy/apps/backend` and its
`EnvironmentFile` is `/home/ruby0322/offerfy/.env.production`, so a one-off
is the same interpreter with the same env loaded by hand
(`docs/deploy.md`, "Database migrations", documents this form):

  cd /home/ruby0322/offerfy/apps/backend
  set -a && . /home/ruby0322/offerfy/.env.production && set +a
  .venv/bin/python -m app.jobs.survey_facets
  .venv/bin/python -m app.jobs.survey_facets --limit 2000

The earlier `docker compose run --rm backend ...` line in this docstring was
wrong: nothing serves this catalogue from compose.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from sqlalchemy.orm import Session

from app.db import get_engine, get_session_factory
from app.models import CompanyBoilerplate, Job, JobFacets
from app.services.jobs.cities import canonical_cities
from app.services.jobs.enrich import default_taxonomy, function_of

# Three or four example titles per unclassified bucket, per the brief --
# "14% unknown seniority" is not actionable, "14% unknown seniority, e.g.
# 'Member of Technical Staff, Inference'" is. More than a handful would
# just be noise at a terminal.
_EXAMPLES_CAP = 4

_BUCKETS = (
    "no_facets",
    "unknown_seniority",
    "other_function",
    "null_country",
    "no_pickable_city",
)


def _empty_counts() -> dict[str, int]:
    return {"total": 0, **{bucket: 0 for bucket in _BUCKETS}}


def survey(db: Session, *, limit: int | None = None) -> dict[str, Any]:
    """Read-only tally of facet (non-)coverage across `jobs`.

    Scoped to active postings -- the same scope `enrich_jobs`'s backfill
    and the `/jobs` listing both use -- since a retired posting is not one
    the maintainer needs a filter to work for. `limit`, when given, caps
    how many active jobs are considered (ordered by `Job.id` for a stable,
    repeatable slice), for a quick look at a large catalogue without
    walking all of it.

    Returns a dict with at least `total` and every bucket in `_BUCKETS` at
    the top level (counts over every job considered, independent of each
    other -- a job can land in more than one bucket at once, e.g. unknown
    seniority *and* other function), plus `by_source` (the same counts, per
    `jobs.source`) and `examples` (up to `_EXAMPLES_CAP` per bucket, for
    exactly the jobs that landed in it).

    A posting with no `job_facets` row counts only in `no_facets`, never in
    the other buckets -- those describe a field the needles looked at and
    missed, which is not what happened to a row that was never enriched at
    all.

    `no_pickable_city` is the one bucket that is not a plain column read: it
    runs the stored city through `cities.canonical_cities`, the same fold
    the picker uses, because a stored city can be non-NULL and still name
    no city (see the module docstring).
    """
    query = (
        db.query(
            Job.id,
            Job.source,
            Job.title,
            JobFacets.seniority,
            JobFacets.function,
            JobFacets.country,
            JobFacets.city,
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

    taxonomy = default_taxonomy()
    for _job_id, source, title, seniority, function, country, city in query:
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
        if not canonical_cities(city, taxonomy):
            overall["no_pickable_city"] += 1
            source_counts["no_pickable_city"] += 1
            if len(examples["no_pickable_city"]) < _EXAMPLES_CAP:
                # The stored value, not the title: a city bucket is read to
                # find out WHAT was stored instead of a city ("GB", "Fab
                # 10N/X"), and a job title says nothing about that.
                examples["no_pickable_city"].append(f"{title} [{city!r}]")

    # recompute_boilerplate writes a row for every company clearing
    # BOILERPLATE_MIN_POSTINGS, including ones whose hash array came back
    # empty, so the row count would report "companies with >= 5 active
    # postings" under a name that promises something else. Count the rows
    # that actually strip something.
    bp_arrays = [json.loads(r[0]) for r in db.query(CompanyBoilerplate.sentence_hashes)]
    return {
        **overall,
        "description_driven_function": description_driven,
        "companies_with_boilerplate": sum(1 for hashes in bp_arrays if hashes),
        "boilerplate_sentences_total": sum(len(hashes) for hashes in bp_arrays),
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
    _print_bucket(
        "no pickable city", "no_pickable_city", report, report["examples"]["no_pickable_city"]
    )

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
                f" no_pickable_city={counts['no_pickable_city']} ({_pct(counts['no_pickable_city'], counts['total'])})"
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
