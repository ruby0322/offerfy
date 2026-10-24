from datetime import datetime, timedelta, timezone

import httpx

from app.models import Job
from app.services.jobs.ingest_workday import ingest_workday_board
from app.services.jobs.store import known_source_ids, touch_jobs, upsert_jobs
from app.services.jobs.types import NormalizedJob
from app.services.jobs.workday import WorkdayBoard, fetch_workday

BOARD = WorkdayBoard(
    host="nvidia.wd5.myworkdayjobs.com",
    tenant="nvidia",
    site="NVIDIAExternalCareerSite",
    company="NVIDIA",
)

LISTING = {
    "total": 3,
    "jobPostings": [
        {
            "title": "Senior Software Engineer, NVLink",
            "externalPath": "/job/US-CA-Santa-Clara/Senior-SWE_JR1",
            "locationsText": "US, CA, Santa Clara",
            "postedOn": "Posted Yesterday",
            "bulletFields": ["JR1"],
        },
        {
            "title": "Enterprise Account Executive",
            "externalPath": "/job/US-CA-Santa-Clara/AE_JR2",
            "locationsText": "US, CA, Santa Clara",
            "postedOn": "Posted Today",
            "bulletFields": ["JR2"],
        },
        {
            "title": "Deep Learning Research Scientist",
            "externalPath": "/job/Remote/DL_JR3",
            "locationsText": "US, Remote",
            "postedOn": "Posted 5 Days Ago",
            "bulletFields": ["JR3"],
        },
    ],
}


def _detail(req_id: str) -> dict:
    return {
        "jobPostingInfo": {
            "title": f"Title {req_id}",
            "jobDescription": f"<p>Work on {req_id}.</p>",
            "externalUrl": f"https://nvidia.wd5.myworkdayjobs.com/job/{req_id}",
            "location": "US, CA, Santa Clara",
            "startDate": "2026-10-23",
            "postedOn": "Posted Yesterday",
            "remoteType": "Remote" if req_id == "JR3" else None,
        }
    }


def _client() -> tuple[httpx.Client, list[str]]:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        if request.url.path.endswith("/jobs") and request.method == "POST":
            return httpx.Response(200, json=LISTING)
        for req in ("JR1", "JR2", "JR3"):
            if req in request.url.path:
                return httpx.Response(200, json=_detail(req))
        return httpx.Response(404, json={})

    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def test_scopes_from_list_metadata_before_paying_for_a_detail_call():
    """The whole economics of this source.

    A posting costs one request, so one we would not carry must not cost it.
    """
    client, seen = _client()
    result = fetch_workday(client, BOARD)
    assert result is not None
    assert {j.source_id for j in result.new} == {
        "nvidia-nvidiaexternalcareersite:JR1",
        "nvidia-nvidiaexternalcareersite:JR3",
    }
    # The account-executive posting was never fetched.
    assert not any("JR2" in path for path in seen)
    assert result.fetched == 3


def test_parses_the_detail_payload_and_ignores_postedOn():
    client, _ = _client()
    result = fetch_workday(client, BOARD)
    assert result is not None
    job = next(j for j in result.new if j.source_id.endswith("JR1"))
    assert job.source == "workday"
    assert job.company == "NVIDIA"
    assert "Work on JR1" in job.description_text
    assert job.apply_url.endswith("/job/JR1")
    # startDate is the real date; postedOn is the words "Posted Yesterday".
    assert job.posted_at is not None
    assert job.posted_at.date().isoformat() == "2026-10-23"
    remote = next(j for j in result.new if j.source_id.endswith("JR3"))
    assert remote.remote is True
    assert job.remote is None


def test_a_posting_already_held_is_reported_live_not_refetched():
    """Incremental fetching: the reason a daily pass is affordable at all."""
    client, seen = _client()
    known = {"nvidia-nvidiaexternalcareersite:JR1"}
    result = fetch_workday(client, BOARD, known_ids=known)
    assert result is not None
    assert [j.source_id for j in result.new] == ["nvidia-nvidiaexternalcareersite:JR3"]
    assert result.still_live == known
    assert not any("JR1" in path for path in seen)
    # Both the new and the still-live ids count as seen, or expire_missing would
    # deactivate the board.
    assert result.seen_ids == known | {"nvidia-nvidiaexternalcareersite:JR3"}


def test_missing_board_returns_none_not_empty():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert fetch_workday(client, BOARD) is None


def test_touch_jobs_keeps_a_skipped_posting_inside_its_freshness_window(db_session):
    """The trap this design creates, and the guard against it.

    A posting we skip the detail call for must still have last_seen_at bumped.
    Otherwise it ages out of validThrough and goes noindex while the job is open
    -- the exact failure that took the whole catalogue dark in October.
    """
    stale = datetime.now(timezone.utc) - timedelta(days=40)
    upsert_jobs(
        db_session,
        [
            NormalizedJob(
                source="workday", source_id="nvidia-x:JR1", company="NVIDIA",
                title="Senior Software Engineer", location="Santa Clara", remote=None,
                apply_url="https://example.com/a", source_url="https://example.com/a",
                description_html="<p>x</p>", description_text="x", posted_at=None,
            )
        ],
        now=stale,
    )
    db_session.commit()

    touched = touch_jobs(db_session, source="workday", source_ids={"nvidia-x:JR1"})
    db_session.commit()
    assert touched == 1

    row = db_session.query(Job).filter(Job.source_id == "nvidia-x:JR1").one()
    assert (datetime.now(timezone.utc) - row.last_seen_at.replace(tzinfo=timezone.utc)).days == 0
    # Content untouched: that is the point of skipping the detail call.
    assert row.description_text == "x"


def test_known_source_ids_is_scoped_to_one_board(db_session):
    for sid in ("nvidia-a:JR1", "micron-b:JR2"):
        upsert_jobs(
            db_session,
            [
                NormalizedJob(
                    source="workday", source_id=sid, company="C", title="Engineer",
                    location=None, remote=None, apply_url="u", source_url="u",
                    description_html="", description_text="d", posted_at=None,
                )
            ],
        )
    db_session.commit()
    assert known_source_ids(db_session, source="workday", prefix="nvidia-a:") == {"nvidia-a:JR1"}


def test_a_pass_does_not_deactivate_the_postings_it_skipped(db_session):
    """End to end: the incremental path must not look like a vanished board."""
    client, _ = _client()
    first = ingest_workday_board(db_session, client, BOARD)
    assert first.status == "ok"
    assert first.upserted == 2

    # Second pass: both are now known, so nothing new -- and nothing expired.
    client2, seen2 = _client()
    second = ingest_workday_board(db_session, client2, BOARD)
    assert second.status == "ok"
    assert second.upserted == 0
    assert not any("JR1" in p or "JR3" in p for p in seen2), "re-fetched a known posting"

    rows = db_session.query(Job).filter(Job.source == "workday").all()
    assert len(rows) == 2
    assert all(r.is_active for r in rows), "a skipped posting was deactivated"
