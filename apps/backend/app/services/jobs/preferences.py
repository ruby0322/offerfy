"""Seed a person's job preferences from their resume facts.

One taxonomy, both sides. A posting's function, seniority and location come
from data/job_taxonomy.json via enrich.py; the person's role title and
locations go through the very same functions over the very same file. There
is no second vocabulary here: if the job side says "staff_plus", the resume
side says "staff_plus", so the two cannot drift.

Seeding is computed, not stored. current_or_seeded returns a saved row
untouched, because from the first save onward the preferences belong to the
user; before that it returns a transient seed that is never persisted.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.models import JobPreference, ResumeFacts, User
from app.schemas import TalentProfilePayload, TalentRole
from app.services.jobs.enrich import (
    default_taxonomy,
    function_of,
    location_facets,
    seniority_of,
)

# Fallback seniority bands, used only when the role title yields "unknown".
# Years of experience say nothing about staff_plus or manager, so the bands
# stop at senior; the values are members of the taxonomy's seniority list.
JUNIOR_MAX_YEARS = 2
MID_MAX_YEARS = 5

# Values the extractor may store for an ongoing role (TalentRole.start/end
# comment), compared casefolded.
_ONGOING = {"present", "至今", "现在", "現在", "current", "now"}
_YYYY_MM = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

ACTIVITY_DEFAULT = "actively_looking"


def _empty_seed() -> dict[str, Any]:
    return {
        "target_functions": "",
        "target_locations": "",
        "target_seniority": None,
        "remote_required": None,
        "activity": ACTIVITY_DEFAULT,
    }


def _pipe(values: list[str]) -> str:
    return f"|{'|'.join(values)}|" if values else ""


def _payload_of(facts: ResumeFacts) -> TalentProfilePayload | None:
    try:
        return TalentProfilePayload.model_validate(json.loads(facts.facts_json))
    except (ValueError, TypeError, ValidationError):
        return None


def _month(value: str | None) -> str | None:
    """A YYYY-MM value, or None. These sort correctly as plain strings."""
    v = (value or "").strip()
    return v if _YYYY_MM.match(v) else None


def _is_ongoing(role: TalentRole) -> bool:
    end = (role.end or "").strip()
    if end:
        return end.casefold() in _ONGOING
    # No end date at all: resumes often omit it for the current job.
    return bool((role.start or "").strip())


def _current_role(payload: TalentProfilePayload) -> TalentRole | None:
    """The role that best represents what the person does now.

    The extractor is asked to list roles newest first but nothing enforces
    it, so order is only the last tiebreak. Ranking: ongoing roles first,
    latest start among them; otherwise the latest parseable end; otherwise
    roles[0]. Only YYYY-MM values are compared; unparseable dates rank last.
    """
    roles = [r for r in payload.roles if r.title]
    if not roles:
        return None
    ongoing = [r for r in roles if _is_ongoing(r)]
    if ongoing:
        # max() keeps the first of equals, so list order breaks ties.
        return max(ongoing, key=lambda r: _month(r.start) or "")
    return max(roles, key=lambda r: _month(r.end) or "")


def _band_of(years: int | None) -> str | None:
    if years is None:
        return None
    if years <= JUNIOR_MAX_YEARS:
        return "junior"
    if years <= MID_MAX_YEARS:
        return "mid"
    return "senior"


def seed_from_facts(facts: ResumeFacts | None) -> dict[str, Any]:
    """Field values a new JobPreference would take. Pure: no DB access.

    Takes the row, not a payload, because facts_json is Text. A None row, or
    one with unparseable JSON or no roles, yields the empty seed.

    The role used is _current_role's pick, not simply roles[0]: the
    extractor is prompted for newest-first but that is not guaranteed.

    The function comes from function_of(title, "") with an empty description:
    a resume has no job description, and the description needles are tuned
    for employer boilerplate, the opposite of a role title. Seniority prefers
    seniority_of(title) because the title is the person's own words, the same
    bias enrich.py takes; years_experience bands are the fallback only.
    Locations seed the country alone: city is too narrow to filter on, region
    too broad.
    """
    seed = _empty_seed()
    if facts is None:
        return seed
    payload = _payload_of(facts)
    if payload is None:
        return seed

    taxonomy = default_taxonomy()

    countries: list[str] = []
    for location in payload.locations:
        country = location_facets(
            location, source="resume", remote=None, taxonomy=taxonomy
        ).country
        if country and country.lower() not in countries:
            countries.append(country.lower())
    seed["target_locations"] = _pipe(countries)

    current = _current_role(payload)
    title = current.title if current else None
    if title:
        function = function_of(title, "", taxonomy)
        if function and function != "other":
            seed["target_functions"] = _pipe([function])
        seniority = seniority_of(title, taxonomy)
        if seniority != "unknown":
            seed["target_seniority"] = seniority
    if seed["target_seniority"] is None and payload.roles:
        years = facts.years_experience
        if years is None:
            years = payload.years_experience
        seed["target_seniority"] = _band_of(years)
    return seed


def current_or_seeded(db: Session, user: User) -> JobPreference:
    """The user's saved preferences, or a transient seed computed from facts.

    A saved row is returned untouched. Otherwise the result is a JobPreference
    that is NOT added to the session and NOT flushed: it is recomputed from the
    current facts on every call until the user saves. The existence of a row is
    therefore the "the user has edited this" marker, which is why no extra
    column is needed, and why rebuilding the facts of a person who has never
    saved changes what they see. Callers that persist it must db.add it.
    """
    row = db.query(JobPreference).filter(JobPreference.user_id == user.id).one_or_none()
    if row is not None:
        return row
    facts = db.query(ResumeFacts).filter(ResumeFacts.user_id == user.id).one_or_none()
    return JobPreference(user_id=user.id, **seed_from_facts(facts))
