"""resume_contents: the snapshot is derived state, not the source of truth.

`stored_content` must say so itself the moment the source it was built from
changes -- these tests pin that behaviour down, plus the schema's round trip
and its "every section defaults to empty" shape (the thing that makes
`extras` safe to leave unset when a resume genuinely has no extra section).
"""

import pytest

from app.models import Resume
from app.services.resume_content import (
    EducationEntry,
    ProjectEntry,
    ResumeContent,
    Section,
    SkillGroup,
    WorkEntry,
    store_content,
    stored_content,
)


@pytest.fixture
def db(db_session):
    return db_session


@pytest.fixture
def resume(client, db_session) -> Resume:
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    row = db_session.get(Resume, created["id"])
    assert row is not None
    return row


def test_resume_content_round_trips_through_json():
    content = ResumeContent(
        name="Ruby",
        summary="Builds things.",
        education=[
            EducationEntry(
                institution="NTU",
                degree="BS",
                start="2018-09",
                end="2022-06",
                bullets=["Dean's list"],
            )
        ],
        work=[
            WorkEntry(
                title="Engineer",
                company="Offerfy",
                start="2022-07",
                end="Present",
                bullets=["Shipped things"],
            )
        ],
        projects=[ProjectEntry(name="Offerfy", role="Founder", url="https://offerfy.com")],
        skills=[SkillGroup(category="Languages", items=["Python", "TypeScript"])],
        extras=[Section(title="Publications", entries=["A paper"])],
    )
    content.contact.email = "ruby@example.com"

    assert ResumeContent.model_validate_json(content.model_dump_json()) == content


def test_resume_content_defaults_every_section_to_empty():
    content = ResumeContent()
    assert content.work == []
    assert content.extras == []
    assert content.education == []
    assert content.projects == []
    assert content.skills == []


def test_stored_content_returns_none_when_the_source_has_changed(db, resume):
    store_content(db, resume, ResumeContent(name="Ruby"), model="m")
    resume.typst_source = resume.typst_source + "\n// edited"
    assert stored_content(db, resume) is None


def test_stored_content_returns_the_snapshot_when_the_hash_still_matches(db, resume):
    content = ResumeContent(name="Ruby")
    store_content(db, resume, content, model="m")
    assert stored_content(db, resume) == content


def test_store_content_overwrites_the_existing_row_for_a_resume(db, resume):
    from app.models import ResumeContent as ResumeContentRow

    store_content(db, resume, ResumeContent(name="Ruby"), model="m")
    store_content(db, resume, ResumeContent(name="Ada"), model="m2")

    rows = (
        db.query(ResumeContentRow)
        .filter(ResumeContentRow.resume_id == resume.id)
        .all()
    )
    assert len(rows) == 1
    assert stored_content(db, resume) == ResumeContent(name="Ada")
