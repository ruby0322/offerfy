"""A structured snapshot of one resume's content, for deterministic template switches.

Switching a resume's template today means asking a model to rewrite the
whole Typst source in the new template's shape -- slow, and free to drop or
invent content on the way. Emitting the new document from a structured
snapshot instead needs that snapshot to exist, so this module defines its
shape (`ResumeContent`), the one model call that fills it (`extract_content`),
and the storage around it: `store_content`, called by that extraction, and
`stored_content`, called by whatever needs a fresh one -- emitting a document
from the snapshot is a later task.

The snapshot is derived state, never the source of truth: `Resume.typst_source`
stays authoritative. `stored_content` returns a payload only when
`models.ResumeContent.derived_from_hash` still equals
`revisions.source_hash(resume.typst_source)` -- the same hash function
`ResumeRevision.content_hash` uses, reused rather than duplicated so the two
hashes cannot drift apart in meaning. When the hashes disagree the snapshot
is simply stale, not wrong; the caller re-extracts.

`extras` exists because a resume's sections are not a fixed set --
`app.services.import_tools`'s tool description makes the same point about
publications, military service, or certifications that may or may not be
there. A closed schema would silently discard whatever it has no field for,
which is the worst place for that to happen since nothing would notice.

Dates stay strings in the `YYYY-MM` / `Present` convention `llm.SYSTEM_PROMPT`
already enforces inside the documents, for the reason `schemas.TalentRole`
gives: a date we could not parse is better kept verbatim than dropped.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from app import models
from app.config import get_settings
from app.models import GuestSession, LlmCall, Resume, User
from app.services.llm import _output_text, _responses_url
from app.services.llm.prompts import EXTRACT_PROMPT
from app.services.llm.providers import openai as openai_provider
from app.services.model_catalog import spec_or_passthrough
from app.services.revisions import source_hash
from app.services.usage import event_cost_uusd

logger = logging.getLogger(__name__)


class Contact(BaseModel):
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    github: str | None = None
    linkedin: str | None = None
    website: str | None = None


class EducationEntry(BaseModel):
    institution: str | None = None
    degree: str | None = None
    location: str | None = None
    # YYYY-MM, or Present -- see the module docstring.
    start: str | None = None
    end: str | None = None
    bullets: list[str] = Field(default_factory=list)


class WorkEntry(BaseModel):
    title: str | None = None
    company: str | None = None
    location: str | None = None
    start: str | None = None
    end: str | None = None
    bullets: list[str] = Field(default_factory=list)


class ProjectEntry(BaseModel):
    name: str | None = None
    role: str | None = None
    url: str | None = None
    start: str | None = None
    end: str | None = None
    bullets: list[str] = Field(default_factory=list)


class SkillGroup(BaseModel):
    category: str | None = None
    items: list[str] = Field(default_factory=list)


class Section(BaseModel):
    """One of the sections a fixed schema would have had no field for."""

    title: str
    entries: list[str] = Field(default_factory=list)


class ResumeContent(BaseModel):
    name: str | None = None
    contact: Contact = Field(default_factory=Contact)
    summary: str | None = None
    education: list[EducationEntry] = Field(default_factory=list)
    work: list[WorkEntry] = Field(default_factory=list)
    projects: list[ProjectEntry] = Field(default_factory=list)
    skills: list[SkillGroup] = Field(default_factory=list)
    extras: list[Section] = Field(default_factory=list)


def _row_for(db: Session, resume_id: str) -> models.ResumeContent | None:
    return (
        db.query(models.ResumeContent)
        .filter(models.ResumeContent.resume_id == resume_id)
        .one_or_none()
    )


def stored_content(db: Session, resume: Resume) -> ResumeContent | None:
    """The resume's snapshot, or None when it is missing or stale.

    Staleness is computed from the hash, not from a dirty flag: the resume's
    source can change out from under a snapshot in ways no code path ever
    marks (a direct `typst_source` assignment, a rolled-back edit that lands
    back on an old value), so comparing the live source's hash against the
    one the snapshot was built from is the only check that cannot be skipped.

    A row whose `content_json` no longer validates -- the schema moved on
    since it was written, or the blob was simply corrupted -- is treated the
    same as a stale one: None already means "re-derive", which is exactly the
    right answer here too, and the alternative is an unhandled ValidationError
    turning into a 500 on an ordinary click.
    """
    row = _row_for(db, resume.id)
    if row is None:
        return None
    if row.derived_from_hash != source_hash(resume.typst_source):
        return None
    try:
        return ResumeContent.model_validate_json(row.content_json)
    except ValidationError:
        logger.warning("resume_contents row failed to validate resume_id=%s", resume.id)
        return None


def store_content(
    db: Session,
    resume: Resume,
    content: ResumeContent,
    *,
    model: str,
    source: str | None = None,
) -> ResumeContent:
    """Upsert this resume's snapshot, stamped with the source it came from.

    `source` lets a caller pass the exact text `content` was actually
    derived from, rather than relying on `resume.typst_source` to still
    hold it by the time this runs. Defaults to `resume.typst_source` for
    every existing caller, but `app.routers.resumes.switch_template`
    passes it explicitly: it captures that text into a plain string before
    calling the model, and a commit in between (its own
    `record_extract_spend`) can otherwise silently refresh `resume` off an
    edit that landed while extraction was in flight (the same
    `expire_on_commit` hazard `stored_content`'s docstring warns about) --
    passing the string makes that invariant structural rather than
    ordering-dependent.
    """
    row = _row_for(db, resume.id)
    if row is None:
        row = models.ResumeContent(resume_id=resume.id)
        db.add(row)
    row.content_json = content.model_dump_json()
    row.derived_from_hash = source_hash(source if source is not None else resume.typst_source)
    row.model = model[:128]
    row.built_at = datetime.now(timezone.utc)
    db.flush()
    return content


def restamp_content(db: Session, resume: Resume, *, source: str) -> None:
    """Re-key an existing snapshot to a different source hash, leaving its
    content, model, and built_at untouched. A no-op if there is no row.

    Called after a deterministic template switch writes a new document:
    the snapshot's structured content did not change -- it is exactly what
    `emit()` was built from -- but `resume.typst_source` is no longer the
    text `derived_from_hash` names. It is the document *produced from*
    that content, which the snapshot equally well describes now. Leaving
    `derived_from_hash` pointed at the pre-switch text would make the very
    next switch pay for a needless re-extraction even though nothing about
    the person's underlying content changed; see `routers.resumes.
    switch_template`'s own call of this right after `set_draft_source`.
    """
    row = _row_for(db, resume.id)
    if row is None:
        return
    row.derived_from_hash = source_hash(source)
    db.flush()


# --- extraction: the one model call that fills a snapshot -------------------
#
# Mirrors app.services.resume_facts.extract_facts closely: a direct httpx call to
# the OpenAI Responses API (the only one of our two providers whose strict
# structured output can guarantee every property answered), a hand-written
# wire schema rather than one derived from pydantic, and a result dataclass
# where a failure never writes.


EXTRACT_TIMEOUT = httpx.Timeout(90.0, connect=10.0)


def _schema() -> dict[str, Any]:
    """The response schema, written out rather than derived from pydantic.

    Strict structured output requires every property in `required` and
    additionalProperties false throughout, which a pydantic schema with
    defaults does not produce -- see resume_facts._schema, which documents the
    same constraint for the same reason. The payload is still validated
    against ResumeContent afterwards, so this is the wire contract and that
    is the truth.
    """
    text = {"type": ["string", "null"]}
    bullets = {"type": "array", "items": {"type": "string"}}

    def entry(*date_fields: str) -> dict[str, Any]:
        properties = {name: text for name in date_fields}
        properties["bullets"] = bullets
        return {
            "type": "object",
            "additionalProperties": False,
            "required": list(properties),
            "properties": properties,
        }

    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "name",
            "contact",
            "summary",
            "education",
            "work",
            "projects",
            "skills",
            "extras",
        ],
        "properties": {
            "name": text,
            "contact": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "email",
                    "phone",
                    "location",
                    "github",
                    "linkedin",
                    "website",
                ],
                "properties": {
                    "email": text,
                    "phone": text,
                    "location": text,
                    "github": text,
                    "linkedin": text,
                    "website": text,
                },
            },
            "summary": text,
            "education": {
                "type": "array",
                "items": entry("institution", "degree", "location", "start", "end"),
            },
            "work": {
                "type": "array",
                "items": entry("title", "company", "location", "start", "end"),
            },
            "projects": {
                "type": "array",
                "items": entry("name", "role", "url", "start", "end"),
            },
            "skills": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["category", "items"],
                    "properties": {
                        "category": text,
                        "items": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
            "extras": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["title", "entries"],
                    "properties": {
                        "title": {"type": "string"},
                        "entries": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
        },
    }


def _request_body(model: str, source: str) -> dict[str, Any]:
    parts = [EXTRACT_PROMPT, "", "RESUME (Typst source):", source]
    return {
        "model": model,
        "input": [{"role": "user", "content": "\n".join(parts)}],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "resume_content",
                "strict": True,
                "schema": _schema(),
            }
        },
    }


def _usage_of(data: dict[str, Any]) -> dict[str, Any]:
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


def _is_empty(content: ResumeContent) -> bool:
    """No entries in any section is not a snapshot, name or no name.

    Deliberately independent of `name` (and of `contact`/`summary`): a
    payload carrying only a name emits a document that *compiles* cleanly
    through every emitter, so the switch endpoint's compile gate passes
    and `set_draft_source` writes a resume with a name and nothing else,
    silently replacing the person's real one. Treating that as a failure
    here is the only place that scenario is stopped -- an extraction that
    found no section at all did not read the document, whatever else it
    returned, so it is reported as a failure, the same call the caller's
    model path already makes when there is nothing worth keeping.
    """
    return not (
        content.education
        or content.work
        or content.projects
        or content.skills
        or content.extras
    )


# The model this call sends to OpenAI's Responses API when no pin names an
# OpenAI model. Deliberately not model_catalog.DEFAULT_MODEL: that constant is
# "claude-haiku-5-5", an Anthropic id, and sending an Anthropic id to this
# endpoint is not a fallback at all -- it is every unpinned call failing with
# a provider 400, silently, because CHAT_MODEL is blank by default (so this is
# the *common* path, not an edge case). gpt-6-luna is the cheapest OpenAI
# model in the catalogue and is already the fixed model behind
# layout.DEFAULT_DECISIONS_MODEL, so this reuses rather than invents "the
# cheap OpenAI model we trust for a structured call nobody is choosing."
EXTRACT_FALLBACK_MODEL = "gpt-6-luna"


@dataclass
class ExtractResult:
    """What one extraction did. A failure never writes."""

    content: ResumeContent | None = None
    error: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    model: str = ""
    source_chars: int = 0


async def extract_content(source: str) -> ExtractResult:
    """Read one resume's Typst source and return its structured content.

    Never raises -- every failure comes back as a populated `error` and a
    `None` content, exactly like build_profile. Runs on the OpenAI Responses
    API regardless of what anyone picked in the editor, and on a fixed
    default model rather than this person's choice, for the same two reasons
    build_profile has: the output has to validate against ResumeContent, and
    nobody is watching this call happen. Unlike build_profile, the fallback
    model is pinned to EXTRACT_FALLBACK_MODEL rather than to the catalogue
    default -- see that constant for why.
    """
    settings = get_settings()
    if not openai_provider.OpenAIProvider().configured():
        return ExtractResult(error="llm_not_configured", source_chars=len(source))
    pin = settings.pinned_chat_model()
    pinned = spec_or_passthrough(pin) if pin else None
    # An operator pin is honoured only when it names an OpenAI model, for the
    # same reason build_profile restricts it: pinning an Anthropic model must
    # not take this feature down with it. Anything else -- no pin, or a pin
    # naming a non-OpenAI model -- falls back to EXTRACT_FALLBACK_MODEL.
    spec = (
        pinned
        if pinned is not None and pinned.provider == openai_provider.NAME
        else spec_or_passthrough(EXTRACT_FALLBACK_MODEL)
    )
    if spec.provider != openai_provider.NAME:
        # The fallback itself did not resolve to an OpenAI model -- nothing
        # usable to call, so this makes no request at all rather than sending
        # the wrong provider's id to this endpoint.
        return ExtractResult(error="llm_not_configured", source_chars=len(source))
    model = spec.id
    url = _responses_url(settings.openai_base_url or "https://api.openai.com/v1")
    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }
    result = ExtractResult(model=model, source_chars=len(source))
    try:
        async with httpx.AsyncClient(timeout=EXTRACT_TIMEOUT) as client:
            response = await client.post(
                url, headers=headers, json=_request_body(model, source)
            )
            if response.status_code >= 400:
                logger.warning(
                    "content extraction failed status=%s", response.status_code
                )
                result.error = "provider_error"
                return result
            data = response.json()
    except httpx.HTTPError as exc:
        logger.warning(
            "content extraction transport failed: %s", exc.__class__.__name__
        )
        result.error = "provider_error"
        return result
    except ValueError:
        result.error = "unparseable"
        return result

    if not isinstance(data, dict):
        result.error = "unparseable"
        return result
    result.usage = _usage_of(data)
    output = data.get("output") if isinstance(data.get("output"), list) else []
    text = _output_text(data, output)
    if not text.strip():
        logger.warning(
            "content extraction returned no text status=%s", data.get("status")
        )
        result.error = "no_output"
        return result
    try:
        content = ResumeContent.model_validate_json(text)
    except ValidationError as exc:
        # Log where it failed, never what it said: the content is personal
        # data. Distinct from no_output so the two cannot be confused again.
        logger.warning(
            "extracted content rejected: %s",
            "; ".join(".".join(str(part) for part in err["loc"]) for err in exc.errors()),
        )
        result.error = "invalid_payload"
        return result
    except ValueError:
        logger.warning("content extraction output was not JSON")
        result.error = "unparseable"
        return result

    if _is_empty(content):
        logger.warning("content extraction produced an empty payload")
        result.error = "empty"
        return result

    result.content = content
    return result


def record_extract_spend(
    db: Session,
    *,
    user: User | None,
    guest: GuestSession | None,
    resume_id: str,
    result: ExtractResult,
    meter,
) -> None:
    """Record what one extraction billed.

    Unlike profile.record_build_spend, which takes only a User because a
    talent profile always belongs to a signed-in one, a résumé does not: a
    guest can switch templates, so exactly one of user/guest is set on the
    row -- never both, which llm_calls.llm_call_subject_not_both enforces.

    Commits on its own, same reasoning as record_build_spend: telemetry may
    be lost, the work it is describing may not.
    """
    if not result.model or resume_id is None:
        return
    cost, _ = event_cost_uusd({"model": result.model, **result.usage})
    try:
        db.add(
            LlmCall(
                resume_id=resume_id,
                user_id=user.id if user is not None else None,
                guest_session_id=guest.id if guest is not None else None,
                purpose="extract",
                model=result.model[:128],
                round_index=0,
                input_tokens=result.usage.get("input_tokens"),
                cached_tokens=result.usage.get("cached_tokens"),
                output_tokens=result.usage.get("output_tokens"),
                source_chars=result.source_chars,
                cost_uusd=cost,
            )
        )
        db.commit()
    except Exception:  # pragma: no cover - telemetry must never fail a caller
        db.rollback()
    meter.record(
        {
            "model": result.model,
            "purpose": "extract",
            "round_index": 0,
            **result.usage,
        }
    )
