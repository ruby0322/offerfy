"""resume_contents: the snapshot is derived state, not the source of truth.

`stored_content` must say so itself the moment the source it was built from
changes -- these tests pin that behaviour down, plus the schema's round trip
and its "every section defaults to empty" shape (the thing that makes
`extras` safe to leave unset when a resume genuinely has no extra section).

The second half covers `extract_content`, the one model call that fills a
snapshot (a direct httpx call, mirroring services.profile.build_profile), and
`record_extract_spend`, its telemetry.
"""

import json

import httpx
import pytest

from app.models import GuestSession, LLM_CALL_PURPOSES, LlmCall, Resume, User
from app.services.metering import Meter, system_guard
from app.services.resume_content import (
    EducationEntry,
    ExtractResult,
    ProjectEntry,
    ResumeContent,
    Section,
    SkillGroup,
    WorkEntry,
    extract_content,
    record_extract_spend,
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


def test_stored_content_returns_none_when_the_stored_blob_no_longer_validates(
    db, resume
):
    """A row that no longer validates is treated the same as a stale one.

    None already means "re-derive"; an unhandled ValidationError here would
    become a 500 on an ordinary click once a later task wires this up behind
    an endpoint.
    """
    from app.models import ResumeContent as ResumeContentRow
    from app.services.revisions import source_hash

    store_content(db, resume, ResumeContent(name="Ruby"), model="m")
    row = (
        db.query(ResumeContentRow)
        .filter(ResumeContentRow.resume_id == resume.id)
        .one()
    )
    row.content_json = "{not json"
    row.derived_from_hash = source_hash(resume.typst_source)
    db.flush()

    assert stored_content(db, resume) is None


# --- extract_content: the one model call that fills a snapshot --------------


SOURCE = "= Ruby Chen\n== Education\nNTU, BS\n== Work\nOfferfy, Engineer"


class _StubLlm:
    """Stands in for httpx.AsyncClient inside services.resume_content."""

    def __init__(self, payload: dict | None = None, status_code: int = 200):
        self.payload = payload
        self.status_code = status_code
        self.calls: list[dict] = []

    def __call__(self, *args, **kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, headers=None, json=None):
        self.calls.append({"url": url, "body": json or {}})
        return self

    def json(self):
        if self.payload is None:
            raise ValueError("no json")
        return self.payload


class _BoomLlm:
    """A transport that never reaches the provider at all."""

    def __call__(self, *args, **kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, *args, **kwargs):
        raise httpx.ConnectError("boom")


def _said(content: dict) -> dict:
    """A Responses-API envelope carrying one content payload. See the
    identical helper in tests/test_talent_profile.py for why this shape (a
    message item's output_text block, no top-level output_text key) is the
    one verified against a live call.
    """
    return {
        "status": "completed",
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": json.dumps(content)}],
            }
        ],
    }


@pytest.mark.anyio
async def test_extract_content_parses_a_strict_json_response(
    openai_enabled, monkeypatch
):
    stub = _StubLlm(
        _said(
            {
                "name": "Ruby Chen",
                "work": [{"title": "Engineer", "company": "Offerfy"}],
            }
        )
    )
    monkeypatch.setattr("app.services.resume_content.httpx.AsyncClient", stub)

    result = await extract_content(SOURCE)

    assert result.error is None
    assert result.content == ResumeContent(
        name="Ruby Chen",
        work=[WorkEntry(title="Engineer", company="Offerfy")],
    )
    assert result.model
    assert result.source_chars == len(SOURCE)


@pytest.mark.anyio
async def test_extract_content_reports_an_empty_payload_as_an_error(
    openai_enabled, monkeypatch
):
    stub = _StubLlm(_said({}))
    monkeypatch.setattr("app.services.resume_content.httpx.AsyncClient", stub)

    result = await extract_content(SOURCE)

    assert result.content is None
    assert result.error == "empty"


@pytest.mark.anyio
async def test_extract_content_reports_a_transport_failure_without_raising(
    openai_enabled, monkeypatch
):
    monkeypatch.setattr("app.services.resume_content.httpx.AsyncClient", _BoomLlm())

    result = await extract_content(SOURCE)

    assert result.content is None
    assert result.error == "provider_error"


# --- record_extract_spend: the guest/user billing split ---------------------


def _resume_for(db, *, user: User | None = None, guest: GuestSession | None = None) -> Resume:
    resume = Resume(
        title="R",
        typst_source="= R",
        source="create",
        locale="en",
        kind="master",
        user_id=user.id if user else None,
        guest_session_id=guest.id if guest else None,
    )
    db.add(resume)
    db.flush()
    return resume


def test_record_extract_spend_bills_a_guest_to_the_guest_column(db):
    guest = GuestSession(key_hash="h-extract-guest")
    db.add(guest)
    db.flush()
    r = _resume_for(db, guest=guest)
    result = ExtractResult(
        content=ResumeContent(name="Ruby"), model="m", usage={}, source_chars=3
    )
    meter = Meter(system_guard(guest=guest, purpose="extract", reason="test"))

    record_extract_spend(
        db, user=None, guest=guest, resume_id=r.id, result=result, meter=meter
    )

    row = db.query(LlmCall).one()
    assert row.purpose == "extract"
    assert row.user_id is None
    assert row.guest_session_id == guest.id


def test_record_extract_spend_bills_a_signed_in_user_to_the_user_column(db):
    user = User(google_sub="s-extract-user", email="ruby@example.com", locale="en")
    db.add(user)
    db.flush()
    r = _resume_for(db, user=user)
    result = ExtractResult(
        content=ResumeContent(name="Ruby"), model="m", usage={}, source_chars=3
    )
    meter = Meter(system_guard(user=user, purpose="extract", reason="test"))

    record_extract_spend(
        db, user=user, guest=None, resume_id=r.id, result=result, meter=meter
    )

    row = db.query(LlmCall).one()
    assert row.purpose == "extract"
    assert row.guest_session_id is None
    assert row.user_id == user.id


def test_extract_purpose_is_allowed():
    assert "extract" in LLM_CALL_PURPOSES
