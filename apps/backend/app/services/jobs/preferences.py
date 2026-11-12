"""Seed a person's job preferences from their resume facts.

One taxonomy, both sides. A posting's function, seniority and location come
from data/job_taxonomy.json via enrich.py; the person's role title and
locations go through the very same functions over the very same file. There
is no second vocabulary here: if the job side says "staff_plus", the resume
side says "staff_plus", so the two cannot drift.

Seeding happens once. get_or_seed returns an existing row untouched, because
from the first save onward the preferences belong to the user.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.models import JobPreference, ResumeFacts, User
from app.schemas import TalentProfilePayload
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

    Roles are taken in the order the extractor wrote them, newest first, so
    roles[0] is the latest.

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

    title = next((r.title for r in payload.roles if r.title), None)
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


def get_or_seed(db: Session, user: User) -> JobPreference:
    """The user's preferences row, seeded from their facts if none exists.

    An existing row is returned untouched. Flushes, never commits.
    """
    row = db.query(JobPreference).filter(JobPreference.user_id == user.id).one_or_none()
    if row is not None:
        return row
    facts = db.query(ResumeFacts).filter(ResumeFacts.user_id == user.id).one_or_none()
    row = JobPreference(user_id=user.id, **seed_from_facts(facts))
    db.add(row)
    db.flush()
    return row
