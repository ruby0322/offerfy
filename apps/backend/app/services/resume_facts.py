"""Resume facts: one extraction, two consumers, two purposes.

A language model reads a person's published resume and returns a structured
set of facts about them (headline, skills, roles, education, ...). That
extraction used to live inside the talent profile, because the talent profile
was its only consumer. It is lifted out so that job matching can read the same
facts for people who have never agreed to be shown to employers.

Two consumers, two purposes, and the purposes are not interchangeable:

* the talent profile (app.services.profile) stores the facts in a table
  employers can search, and only while the owner's newest answer for
  talent_introduction is 'granted';
* matching uses the same facts to rank jobs for their owner, and does not
  need that grant.

Keeping ONE prompt, ONE schema and ONE scrubber here is the point. Two copies
would drift, and the employer side and the match side would silently disagree
about what a resume says.

This module is pure extraction machinery: it holds no consent logic and
decides nothing about who may see the result. Anything that does lives in the
caller. profile.py imports from here; never the reverse.

disclosure_events.profile_content_hash cites the exact bytes of what an
employer was shown, so nothing here may change what a fixed input produces
without that being a deliberate, reviewed decision.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.models import TALENT_PROFILE_SCHEMA_VERSION, Resume, User
from app.schemas import (
    TALENT_EDUCATION_LIMIT,
    TALENT_HEADLINE_CHARS,
    TALENT_HIGHLIGHT_LIMIT,
    TALENT_LANGUAGE_LIMIT,
    TALENT_LOCATION_LIMIT,
    TALENT_NAME_CHARS,
    TALENT_ROLE_LIMIT,
    TALENT_SKILL_LIMIT,
    TALENT_SUMMARY_CHARS,
    TALENT_YEARS_MAX,
    TalentProfilePayload,
)
from app.services.family import family_master
from app.services.memory import accepted_memory_texts
from app.services.revisions import published_source


PROFILE_TIMEOUT = httpx.Timeout(90.0, connect=10.0)

# The model this call sends to OpenAI's Responses API when no pin names an
# OpenAI model. Deliberately not model_catalog.DEFAULT_MODEL: that constant is
# "claude-haiku-5-5", an Anthropic id, and sending an Anthropic id to this
# endpoint is not a fallback at all -- it is every unpinned build failing with
# a provider 400, because CHAT_MODEL is blank by default (so this is the
# *common* path, not an edge case). gpt-6-luna is the cheapest OpenAI model in
# the catalogue and is already the fixed model behind
# layout.DEFAULT_DECISIONS_MODEL, so this reuses rather than invents "the
# cheap OpenAI model we trust for a structured call nobody is choosing."
#
# Kept as its own constant rather than read from the catalogue's default so
# that DEFAULT_MODEL stays free to be a chat model -- other callers depend on
# that -- without this endpoint's requirement riding along with it.
PROFILE_FALLBACK_MODEL = "gpt-6-luna"

PROMPT = (
    "You read one resume and return a structured talent profile for it. "
    "Use only what the resume and the listed facts about the person say; "
    "never infer, guess or fill a gap. Leave a field null and a list empty "
    "when the resume does not answer it. "
    "Never include an email address, phone number, URL, social handle or "
    "street address in any field, including the summary and the role "
    "highlights -- employers are introduced through Offerfy, not directly. "
    "locations is city or region only. "
    "Use YYYY-MM for dates, or Present for an ongoing role. "
    "headline is the person's own current role in their words, not a pitch. "
    "summary is at most three sentences, in the resume's own language."
)


def _schema() -> dict[str, Any]:
    """The response schema, written out rather than derived from pydantic.

    Strict structured output requires every property in `required` and
    additionalProperties false throughout, which a pydantic schema with
    defaults does not produce. The payload is still validated against
    TalentProfilePayload afterwards, so this is the wire contract and that is
    the truth.
    """
    text = {"type": ["string", "null"]}
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "display_name",
            "headline",
            "summary",
            "years_experience",
            "locations",
            "languages",
            "skills",
            "roles",
            "education",
        ],
        "properties": {
            "display_name": text,
            "headline": text,
            "summary": text,
            "years_experience": {"type": ["integer", "null"]},
            "locations": {"type": "array", "items": {"type": "string"}},
            "languages": {"type": "array", "items": {"type": "string"}},
            "skills": {"type": "array", "items": {"type": "string"}},
            "roles": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "employer",
                        "title",
                        "start",
                        "end",
                        "highlights",
                    ],
                    "properties": {
                        "employer": text,
                        "title": text,
                        "start": text,
                        "end": text,
                        "highlights": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
            "education": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["institution", "credential", "field", "end"],
                    "properties": {
                        "institution": text,
                        "credential": text,
                        "field": text,
                        "end": text,
                    },
                },
            },
        },
    }


# --- contact scrubbing -------------------------------------------------------

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_URL = re.compile(
    r"(?:https?://|www\.)\S+|\b[\w-]+\.(?:com|net|org|io|dev|cc|tw|me|ai|co)\b",
    re.IGNORECASE,
)
_PHONE = re.compile(r"\+?\d[\d\s().-]{6,}\d")
_HANDLE = re.compile(r"(?<!\w)@[\w.]{2,}")
# Four or more digits in a row: postcodes and street numbers. Stripped from
# locations, where a city is the most precise thing we want.
_LONG_DIGITS = re.compile(r"\d{4,}")


def _scrub_text(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value
    for pattern in (_EMAIL, _URL, _PHONE, _HANDLE):
        cleaned = pattern.sub(" ", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ,;·|-")
    return cleaned or None


def scrub_contacts(payload: TalentProfilePayload) -> TalentProfilePayload:
    """Strip anything that could be used to reach the person directly.

    The prompt already says not to include it. This runs anyway: a model that
    puts an email in `summary` once in a hundred builds is a model that leaks
    a hundredth of the pool, and the whole promise of this surface is that the
    introduction goes through us.

    A field that is only a contact detail becomes None rather than an empty
    string, so the payload never carries a field that looks answered.
    """
    payload.display_name = _scrub_text(payload.display_name)
    payload.headline = _scrub_text(payload.headline)
    payload.summary = _scrub_text(payload.summary)
    payload.locations = [
        cleaned
        for raw in payload.locations
        if (cleaned := _scrub_text(_LONG_DIGITS.sub(" ", raw)))
    ]
    payload.languages = [c for raw in payload.languages if (c := _scrub_text(raw))]
    payload.skills = [c for raw in payload.skills if (c := _scrub_text(raw))]
    for role in payload.roles:
        role.employer = _scrub_text(role.employer)
        role.title = _scrub_text(role.title)
        role.start = _scrub_text(role.start)
        role.end = _scrub_text(role.end)
        role.highlights = [c for raw in role.highlights if (c := _scrub_text(raw))]
    for school in payload.education:
        school.institution = _scrub_text(school.institution)
        school.credential = _scrub_text(school.credential)
        school.field = _scrub_text(school.field)
        school.end = _scrub_text(school.end)
    return payload


def source_resume(db: Session, user: User) -> Resume | None:
    """The resume the person chose, if they chose one they still own."""
    if not user.profile_resume_id:
        return None
    resume = db.get(Resume, user.profile_resume_id)
    if resume is None or resume.user_id != user.id:
        return None
    return resume


def input_hash(revision_id: str, source: str, facts: list[str]) -> str:
    joined = "\x00".join([revision_id, source, "\n".join(facts)])
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def bundle(db: Session, user: User) -> tuple[Resume, str, str, list[str]] | None:
    """Everything a build reads: the resume, its published revision and facts."""
    resume = source_resume(db, user)
    if resume is None or resume.published_revision_id is None:
        return None
    source = published_source(db, resume)
    if not source:
        return None
    # Memory items hang off the master of the family, so a tailor chosen as
    # the source still reads its parent's facts.
    facts = accepted_memory_texts(db, family_master(db, resume).id)
    return resume, resume.published_revision_id, source, facts


def derived_columns(payload: TalentProfilePayload) -> dict[str, Any]:
    """The denormalised columns, rebuilt from the payload on every write.

    skills_text is pipe-delimited so LIKE '%|java|%' cannot match 'javascript'.
    """
    skills = [skill.lower() for skill in payload.skills]
    words = [
        payload.display_name or "",
        payload.headline or "",
        payload.summary or "",
        " ".join(payload.locations),
        " ".join(payload.languages),
        " ".join(payload.skills),
        " ".join(
            " ".join(filter(None, [role.title, role.employer]))
            for role in payload.roles
        ),
        " ".join(
            " ".join(filter(None, [school.institution, school.credential, school.field]))
            for school in payload.education
        ),
    ]
    return {
        "display_name": (payload.display_name or None),
        "headline": (payload.headline or None),
        "years_experience": payload.years_experience,
        "search_text": re.sub(r"\s+", " ", " ".join(words)).strip().lower(),
        "skills_text": ("|" + "|".join(skills) + "|") if skills else "",
    }


def is_empty(payload: TalentProfilePayload) -> bool:
    """No name, no headline and no roles is not a profile.

    Writing it would mean handing an employer an empty card and logging a
    disclosure for it.
    """
    return not (payload.display_name or payload.headline or payload.roles)


def request_body(model: str, source: str, facts: list[str]) -> dict[str, Any]:
    parts = [PROMPT, "", "RESUME (Typst source):", source]
    if facts:
        parts += ["", "FACTS THE PERSON CONFIRMED ABOUT THEMSELVES:"]
        parts += [f"- {fact}" for fact in facts]
    return {
        "model": model,
        "input": [{"role": "user", "content": "\n".join(parts)}],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "talent_profile",
                "strict": True,
                "schema": _schema(),
            }
        },
    }


def cut(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed[:limit] or None


def clamp_payload(payload: TalentProfilePayload) -> TalentProfilePayload:
    """Bring the payload inside every cap, by truncating.

    The caps bound what a single read hands over. They are deliberately not
    pydantic constraints: a length constraint rejects, and rejecting means
    throwing away an otherwise good profile because one role had a fifth
    bullet point -- which is precisely what happened to the first real resume
    this ran on. Truncating loses the fifth bullet. Rejecting loses the person.
    """
    payload.schema_version = TALENT_PROFILE_SCHEMA_VERSION
    payload.display_name = cut(payload.display_name, TALENT_NAME_CHARS)
    payload.headline = cut(payload.headline, TALENT_HEADLINE_CHARS)
    payload.summary = cut(payload.summary, TALENT_SUMMARY_CHARS)
    if payload.years_experience is not None:
        # The DB has a CHECK for this range, so a wild number must be clamped
        # here or the insert fails.
        payload.years_experience = max(0, min(TALENT_YEARS_MAX, payload.years_experience))
    payload.locations = payload.locations[:TALENT_LOCATION_LIMIT]
    payload.languages = payload.languages[:TALENT_LANGUAGE_LIMIT]
    payload.skills = payload.skills[:TALENT_SKILL_LIMIT]
    payload.education = payload.education[:TALENT_EDUCATION_LIMIT]
    payload.roles = payload.roles[:TALENT_ROLE_LIMIT]
    for role in payload.roles:
        role.highlights = role.highlights[:TALENT_HIGHLIGHT_LIMIT]
    return payload


def usage_of(data: dict[str, Any]) -> dict[str, Any]:
    usage = data.get("usage")
    if not isinstance(usage, dict):
        return {}
    cached = usage.get("input_tokens_details") or {}
    return {
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "cached_tokens": cached.get("cached_tokens")
        if isinstance(cached, dict)
        else None,
    }
