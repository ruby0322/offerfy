"""Offerfy's own data, as tools for the editor agent.

Three read-only tools, shaped exactly like `family_tools.py`: flat
provider-neutral specs plus a handler factory closing over the request's
`db` / `resume` / `user`. They are passed to `aiter_chat_edit` as
`extra_tools` / `tool_handlers`, so they go through the one translation in
`providers.anthropic.tool_specs` with everything else.

  search_jobs         -- the catalogue, filtered. Public; guests included.
  read_job            -- one posting in full, for tailoring against it.
  read_my_job_profile -- the signed-in person's own job context.

Why tools and not a prompt block: the role card is rebuilt and re-sent on
every turn, so putting postings or a digest in it would charge every
conversation about margins for job data it never looks at.

Why its own projections rather than the dashboard's Pydantic schemas: those
are a browser's wire format and they live in routers, which a service must not
import. These payloads are for a model -- compact, and carrying the honesty
flags (`stale`, `requires_login`, `status: "none"`) that the UI expresses by
rendering a different component instead.

Nothing here writes. In particular `read_my_job_profile` must never call
`digest.mark_opened`, which `GET /v1/dashboard` does: that stamp feeds the
digest build's own eligibility gate, so an agent setting it would forge the
engagement signal the next build reads.
"""

from __future__ import annotations

import json
from datetime import timezone
from typing import Any, Callable

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.jobs.match_sweep import count_matches, match_preview
from app.models import Job, JobFacets, Resume, User
from app.services.jobs import applications as applications_service
from app.services.jobs import digest as digest_service
from app.services.jobs.boilerplate import (
    load_boilerplate,
    normalize_company,
    strip_boilerplate,
)
from app.services.jobs.enrich import (
    default_taxonomy,
    valid_countries,
    valid_functions,
    valid_seniorities,
)
from app.services.jobs.preferences import current_or_seeded
from app.services.jobs.search import (
    filtered_query,
    iso,
    posted_at_order,
    valid_regions,
)
from app.services.jobs.store import freshness_cutoff
from app.services.resume_facts import facts_state
from app.services.resume_health import health

SEARCH_JOBS_TOOL = "search_jobs"
READ_JOB_TOOL = "read_job"
READ_JOB_PROFILE_TOOL = "read_my_job_profile"

# A page the model reads, not a page a person scrolls. Ten postings is already
# more than a turn can say anything useful about, and every row costs context
# the resume itself needs.
SEARCH_LIMIT_DEFAULT = 5
SEARCH_LIMIT_MAX = 10

# Enough of a posting to tailor against. Descriptions run to 200k characters;
# the boilerplate strip takes out the company blurb, and this takes the tail.
DESCRIPTION_CHARS = 6000

# The dashboard's own preview cap is 12; eight is the same idea one notch
# tighter, for the same reason SEARCH_LIMIT_MAX is 10.
PROFILE_MATCHES = 8
PROFILE_APPLICATIONS = 20
NOTES_CHARS = 300


def _sorted(values: set[str]) -> list[str]:
    return sorted(values)


def _is_stale(job: Job) -> bool:
    """Whether the catalogue has stopped confirming this posting.

    The window itself is compared in Python here rather than in SQL, so the
    stamp has to be made aware first: a DateTime(timezone=True) column reads
    back naive on SQLite, and comparing that against `freshness_cutoff()`
    raises. `iso` normalises the same way for the same reason.
    """
    seen = job.last_seen_at
    if seen is None:
        return True
    if seen.tzinfo is None or seen.utcoffset() is None:
        seen = seen.replace(tzinfo=timezone.utc)
    return seen < freshness_cutoff()


def _search_spec() -> dict[str, Any]:
    taxonomy = default_taxonomy()
    return {
        "type": "function",
        "name": SEARCH_JOBS_TOOL,
        "description": (
            "Search Offerfy's job catalogue: roughly 21,000 postings pulled "
            "straight from ~90 employers' own Greenhouse, Lever, Ashby, "
            "SmartRecruiters and Workday boards. Use this instead of "
            "web_search for roles -- only a catalogue posting has a job_id "
            "you can pass to read_job and an apply_url that works. "
            "Returns at most "
            f"{SEARCH_LIMIT_MAX} postings, newest first, plus the total "
            "number that matched.\n"
            "Four things to know, because they change what a result means:\n"
            "- `q` matches the title, the company and the location ONLY. It "
            "does NOT search the description, so q='kubernetes' finds almost "
            "nothing even though hundreds of postings require it. To reach a "
            "skill area use `function`.\n"
            "- Any of country/region/city/function/seniority narrows to "
            "postings we have classified, and a large minority of live "
            "postings are not classified yet. Zero results from a filtered "
            "search means 'none that we have classified', never 'no such job "
            "exists'. Say it that way, and offer a wider search.\n"
            "- `posted_within_days` relies on the employer publishing a date, "
            "which most Workday boards do not, so an age filter silently "
            "drops them.\n"
            "- The catalogue carries no salary, compensation, benefit or "
            "skill-tag data at all. Do not offer to filter or compare on pay."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "q": {
                    "type": "string",
                    "description": (
                        "Substring of the job title, company name or location "
                        "string. Not the description."
                    ),
                },
                "company": {
                    "type": "string",
                    "description": "Exact employer name, as the results spell it.",
                },
                "country": {
                    "type": "string",
                    "enum": _sorted(valid_countries(taxonomy)),
                    "description": "ISO-3166 alpha-2 country code.",
                },
                "region": {
                    "type": "string",
                    "enum": _sorted(valid_regions(taxonomy)),
                },
                "city": {
                    "type": "string",
                    "description": (
                        "City name; common aliases resolve (sf, bengaluru). "
                        "Best paired with country."
                    ),
                },
                "remote": {
                    "type": "boolean",
                    "description": (
                        "true for remote-friendly postings only. Most "
                        "postings say nothing either way, so false means "
                        "'known to be on-site' and is a narrow filter."
                    ),
                },
                "function": {
                    "type": "string",
                    "enum": _sorted(valid_functions(taxonomy)),
                    "description": (
                        "Role family. 'software' is the generic bucket for "
                        "software engineering titles that name no "
                        "specialisation."
                    ),
                },
                "seniority": {
                    "type": "string",
                    "enum": _sorted(valid_seniorities(taxonomy)),
                    "description": (
                        "One rung. 'manager' is off the ladder (people "
                        "management), 'unknown' means the title said nothing."
                    ),
                },
                "posted_within_days": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 90,
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": SEARCH_LIMIT_MAX,
                    "description": f"Default {SEARCH_LIMIT_DEFAULT}.",
                },
            },
            "additionalProperties": False,
        },
    }


_READ_JOB = {
    "type": "function",
    "name": READ_JOB_TOOL,
    "description": (
        "Read one catalogue posting in full, including its description with "
        "the company's repeated boilerplate removed. Call this before "
        "tailoring a resume for a role, and tailor from this text rather than "
        "from the job title. Takes a job_id from search_jobs or from "
        "read_my_job_profile. A posting that has closed is still returned, "
        "flagged with is_active=false or stale=true -- tell the person rather "
        "than presenting it as open."
    ),
    "parameters": {
        "type": "object",
        "properties": {"job_id": {"type": "string"}},
        "required": ["job_id"],
        "additionalProperties": False,
    },
}

_READ_JOB_PROFILE = {
    "type": "function",
    "name": READ_JOB_PROFILE_TOOL,
    "description": (
        "Read the signed-in person's own job context on Offerfy: the roles, "
        "locations and seniority they said they want, how many live postings "
        "match that, the first few of those postings, the jobs they have "
        "saved or applied to, whether their matching profile is up to date, "
        "and their latest weekly digest if one has been built. Call this "
        "before asking them what they are looking for. Everything it returns "
        "is their own data, already theirs to see on their dashboard."
    ),
    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
}


def jobs_tool_specs(resume: Resume) -> list[dict]:
    """The catalogue tools for everyone; the personal one only when signed in.

    A guest gets no read_my_job_profile at all rather than one that always
    refuses: an advertised tool that cannot work is a worse prompt than an
    absent one. `jobs_tool_handlers` still registers a refusing handler, so a
    model that calls it from a stale transcript gets an explanation instead of
    "unknown tool".
    """
    specs: list[dict] = [_search_spec(), _READ_JOB]
    if resume.user_id is not None:
        specs.append(_READ_JOB_PROFILE)
    return specs


def _row(job: Job, facets: JobFacets | None) -> dict[str, Any]:
    return {
        "job_id": job.id,
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "remote": job.remote,
        "country": facets.country if facets else None,
        "city": facets.city if facets else None,
        "function": facets.function if facets else None,
        "seniority": facets.seniority if facets else None,
        "posted_at": iso(job.posted_at),
        "apply_url": job.apply_url,
    }


def _facets_for(db: Session, job_ids: list[str]) -> dict[str, JobFacets]:
    if not job_ids:
        return {}
    rows = db.query(JobFacets).filter(JobFacets.job_id.in_(job_ids)).all()
    return {row.job_id: row for row in rows}


def jobs_tool_handlers(
    db: Session, resume: Resume, user: User | None
) -> dict[str, Callable[[dict], str]]:
    def search(args: dict) -> str:
        raw_limit = args.get("limit")
        try:
            limit = int(raw_limit) if raw_limit is not None else SEARCH_LIMIT_DEFAULT
        except (TypeError, ValueError):
            limit = SEARCH_LIMIT_DEFAULT
        limit = max(1, min(SEARCH_LIMIT_MAX, limit))
        days = args.get("posted_within_days")
        try:
            days = int(days) if days is not None else None
        except (TypeError, ValueError):
            days = None
        if days is not None and not 1 <= days <= 90:
            return json.dumps({"error": "posted_within_days must be between 1 and 90"})
        try:
            query = filtered_query(
                db,
                q=args.get("q"),
                source=None,
                remote=args.get("remote"),
                active=True,
                company=args.get("company"),
                country=args.get("country"),
                region=args.get("region"),
                city=args.get("city"),
                seniority=args.get("seniority"),
                function=args.get("function"),
                posted_within_days=days,
                # The agent tells people a role is open, so is_active alone is
                # not enough; see services/jobs/search.py.
                fresh_only=True,
            )
        except ValueError as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)
        total = query.with_entities(func.count(Job.id)).scalar() or 0
        rows = query.order_by(*posted_at_order()).limit(limit).all()
        facets = _facets_for(db, [row.id for row in rows])
        refreshed_at = (
            db.query(func.max(Job.last_seen_at)).filter(Job.is_active.is_(True)).scalar()
        )
        return json.dumps(
            {
                "total": int(total),
                "returned": len(rows),
                "catalog_refreshed_at": iso(refreshed_at),
                "items": [_row(row, facets.get(row.id)) for row in rows],
            },
            ensure_ascii=False,
        )

    def read_job(args: dict) -> str:
        job_id = str(args.get("job_id") or "").strip()
        if not job_id:
            return json.dumps({"error": "job_id is required"})
        job = db.get(Job, job_id)
        if job is None:
            return json.dumps(
                {"error": f"No posting with job_id {job_id}. Call search_jobs first."}
            )
        facets = db.get(JobFacets, job_id)
        # Keyed by the NORMALIZED name, the way recompute_boilerplate stores
        # it and enrich_jobs reads it. Looking it up by job.company silently
        # finds nothing for every employer whose name has a capital letter,
        # which is all of them.
        hashes = load_boilerplate(db, [job.company]).get(
            normalize_company(job.company), frozenset()
        )
        text = strip_boilerplate(job.description_text or "", hashes).strip()
        payload = _row(job, facets)
        payload.update(
            {
                "region": facets.region if facets else None,
                "source": job.source,
                "source_url": job.source_url,
                "first_seen_at": iso(job.first_seen_at),
                "last_seen_at": iso(job.last_seen_at),
                "is_active": job.is_active,
                # Reported, not filtered: a digest item or a saved application
                # can point at a posting that has since closed, and hiding it
                # would leave the agent unable to answer about it at all.
                "stale": _is_stale(job),
                "description": text[:DESCRIPTION_CHARS],
                "description_truncated": len(text) > DESCRIPTION_CHARS,
            }
        )
        return json.dumps(payload, ensure_ascii=False)

    def read_profile(_args: dict) -> str:
        if user is None:
            return json.dumps(
                {
                    "requires_login": True,
                    "note": (
                        "This is only available to a signed-in account. Ask the "
                        "user to sign in; search_jobs and read_job still work."
                    ),
                }
            )
        pref = current_or_seeded(db, user)
        matches = match_preview(db, pref, PROFILE_MATCHES)
        facets = _facets_for(db, [row.id for row in matches])
        pipeline = applications_service.pipeline(db, user=user)[:PROFILE_APPLICATIONS]
        card = health(db, user=user)
        digest = digest_service.latest_for_user(db, user=user)
        return json.dumps(
            {
                "preference": {
                    "target_functions": [p for p in pref.target_functions.split("|") if p],
                    "target_locations": [p for p in pref.target_locations.split("|") if p],
                    "target_seniority": pref.target_seniority,
                    "remote_required": pref.remote_required,
                    "activity": pref.activity,
                    "saved_by_user": pref.updated_at is not None,
                    "facts_state": facts_state(db, user),
                },
                "match_count": count_matches(db, pref),
                "matches": [_row(row, facets.get(row.id)) for row in matches],
                "applications": [
                    {
                        "job_id": row.job_id,
                        "title": row.job.title,
                        "company": row.job.company,
                        "status": row.status,
                        "source": row.source,
                        "applied_at": iso(row.applied_at),
                        "job_is_active": row.job.is_active,
                        "notes": (row.notes or "")[:NOTES_CHARS] or None,
                    }
                    for row in pipeline
                ],
                "resume_health": {
                    "has_master": card.has_master,
                    "published": card.published,
                    "facts_state": card.facts_state,
                    "matching_revision_current": card.matching_revision_current,
                    "needs_rebuild": card.needs_rebuild,
                },
                "digest": _digest_payload(digest),
            },
            ensure_ascii=False,
        )

    return {
        SEARCH_JOBS_TOOL: search,
        READ_JOB_TOOL: read_job,
        READ_JOB_PROFILE_TOOL: read_profile,
    }


def _digest_payload(digest) -> dict[str, Any]:
    """The weekly digest, or an honest absence.

    `status: "none"` is the common answer today and will be until the digest
    timer is enabled, so it has to be a statement the agent can relay rather
    than a null it might read as an empty digest.
    """
    if digest is None:
        return {
            "status": "none",
            "note": (
                "No weekly digest has been built for this person yet. Use "
                "match_count and matches above instead; do not say their "
                "digest is empty."
            ),
        }
    return {
        "status": digest.status,
        "built_at": iso(digest.built_at),
        "candidate_count": digest.candidate_count,
        "items": [
            {
                "job_id": item.job_id,
                "title": item.job.title,
                "company": item.job.company,
                "job_is_active": item.job.is_active,
                "track": item.track,
                "rank": item.rank,
                "tier": item.tier,
                "fit_score": item.fit_score,
                "reason": item.reason,
                "gap": item.gap,
            }
            for item in digest.items
        ],
    }
