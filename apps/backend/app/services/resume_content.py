"""A structured snapshot of one resume's content, for deterministic template switches.

Switching a resume's template today means asking a model to rewrite the
whole Typst source in the new template's shape -- slow, and free to drop or
invent content on the way. Emitting the new document from a structured
snapshot instead needs that snapshot to exist, so this module defines its
shape (`ResumeContent`) and the two operations around it: `store_content`,
called by the extraction step (a later task), and `stored_content`, called by
whatever needs a fresh one.

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

from datetime import datetime, timezone

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import models
from app.models import Resume
from app.services.revisions import source_hash


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
    """
    row = _row_for(db, resume.id)
    if row is None:
        return None
    if row.derived_from_hash != source_hash(resume.typst_source):
        return None
    return ResumeContent.model_validate_json(row.content_json)


def store_content(
    db: Session, resume: Resume, content: ResumeContent, *, model: str
) -> ResumeContent:
    """Upsert this resume's snapshot, stamped with the source it came from."""
    row = _row_for(db, resume.id)
    if row is None:
        row = models.ResumeContent(resume_id=resume.id)
        db.add(row)
    row.content_json = content.model_dump_json()
    row.derived_from_hash = source_hash(resume.typst_source)
    row.model = model[:128]
    row.built_at = datetime.now(timezone.utc)
    db.flush()
    return content
