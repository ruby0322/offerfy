from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.models import Job
from app.services.jobs import workday as workday_mod
from app.services.jobs.ingest_workday import ingest_workday_board
from app.services.jobs.store import known_source_ids, touch_jobs, upsert_jobs
from app.services.jobs.types import NormalizedJob
from app.services.jobs.workday import (
    WorkdayBoard,
    WorkdayUnavailable,
    fetch_workday,
)


@pytest.fixture(autouse=True)
def _no_pacing(monkeypatch):
    """Real pacing would add ~0.4s per detail call to every test in this file.

    The pacing itself is covered by `test_detail_calls_are_paced`, which injects
    a recorder instead of waiting.
    """
    monkeypatch.setattr(workday_mod, "DETAIL_DELAY_SECONDS", 0.0)

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


# Intel's real shape: promoted postings carry the words "Spotlight Job" in
# bulletFields instead of a requisition id, and the same requisition can be
# listed twice under slightly different paths.
INTEL_LISTING = {
    "total": 4,
    "jobPostings": [
        # Intel's real shape: the label comes first, the requisition id second.
        {"title": "Senior Software Development Engineer", "externalPath": "/job/US-AZ-Phoenix/SWE_JR0287775",
         "locationsText": "US, Arizona, Phoenix", "bulletFields": ["Spotlight Job", "JR0287775"]},
        # And the degenerate case with no id at all, which must fall back to the path.
        {"title": "Yield Product Development Engineer", "externalPath": "/job/US-OR-Hillsboro/Yield_JR0288027",
         "locationsText": "US, Oregon, Hillsboro", "bulletFields": ["Spotlight Job"]},
        {"title": "Defect Metrology Engineer", "externalPath": "/job/Ireland-Leixlip/DefMet_JR0284843",
         "locationsText": "Ireland, Leixlip", "bulletFields": ["JR0284843"]},
        {"title": "Defect Metrology Engineer", "externalPath": "/job/Ireland-Leixlip/DefMet_JR0284843-1",
         "locationsText": "Ireland, Leixlip", "bulletFields": ["JR0284843"]},
    ],
}

INTEL = WorkdayBoard(
    host="intel.wd1.myworkdayjobs.com", tenant="intel", site="External", company="Intel"
)


def _intel_client() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/jobs") and request.method == "POST":
            return httpx.Response(200, json=INTEL_LISTING)
        return httpx.Response(200, json={
            "jobPostingInfo": {
                "title": "T", "jobDescription": "<p>d</p>",
                "externalUrl": f"https://intel.wd1.myworkdayjobs.com{request.url.path}",
                "location": "US", "startDate": "2026-10-21",
            }
        })

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_a_bulletfield_that_is_not_a_requisition_id_is_rejected():
    """The bug that failed the first production run.

    Intel labels promoted postings "Spotlight Job" in bulletFields -- 21 of its
    first 100 -- so trusting that field collapsed them all onto one source_id
    and violated the unique constraint mid-pass.
    """
    result = fetch_workday(_intel_client(), INTEL)
    assert result is not None
    ids = [j.source_id for j in result.new]
    assert len(ids) == len(set(ids)), f"duplicate source_id: {ids}"
    assert not any("Spotlight" in i for i in ids)
    # The first finds its real requisition id further down bulletFields; the
    # second has none at all and falls back to its (distinct) path.
    assert f"{INTEL.slug}:JR0287775" in ids
    assert any(i.startswith(f"{INTEL.slug}:/job/") and "JR0288027" in i for i in ids)


def test_the_same_requisition_listed_twice_is_only_taken_once():
    """upsert_jobs adds per row, so a repeat inside one batch would violate the
    unique constraint before a second pass ever ran."""
    result = fetch_workday(_intel_client(), INTEL)
    assert result is not None
    assert sum(1 for j in result.new if j.source_id.endswith("JR0284843")) == 1


def test_an_intel_style_board_survives_a_real_upsert(db_session):
    """End to end against the payload that actually broke: it must now persist."""
    first = ingest_workday_board(db_session, _intel_client(), INTEL)
    assert first.status == "ok", first.error
    rows = db_session.query(Job).filter(Job.source == "workday").all()
    assert len(rows) == 3  # 4 listed, two of which are one requisition
    assert len({r.source_id for r in rows}) == 3


# --- the edge refusing to serve a board -------------------------------------
#
# Every one of the 15 boards failed on 2026-11-19 with the error `board not
# found`, which reads as a board config problem. It was not: Workday fronts
# these hosts with Cloudflare, and it was answering this host's IP with a
# "Security Check" interstitial under a 429. The tests below pin the
# distinction, because the label is what someone debugging at 03:40 acts on.

CHALLENGE_BODY = (
    "<!DOCTYPE html><html><head><style>body{}</style></head><body>"
    "<div>Security Check</div><div>Enable JavaScript and cookies to continue</div>"
    "<div>Support ID: a5c9f94abdeba386</div></body></html>"
)


def _status_client(status: int, **kwargs) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, **kwargs)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_a_security_check_is_reported_as_one_not_as_a_missing_board():
    client = _status_client(429, html=CHALLENGE_BODY)
    with pytest.raises(WorkdayUnavailable) as caught:
        fetch_workday(client, BOARD, sleep=lambda _: None)
    message = str(caught.value)
    assert "429" in message
    assert "security check" in message
    # The old behaviour, and the whole point of the change.
    assert "not found" not in message


def test_a_throttled_board_without_a_challenge_page_still_names_its_status():
    client = _status_client(503, json={})
    with pytest.raises(WorkdayUnavailable) as caught:
        fetch_workday(client, BOARD, sleep=lambda _: None)
    assert "HTTP 503" in str(caught.value)
    assert "security check" not in str(caught.value)


@pytest.mark.parametrize("status", [404, 406, 422])
def test_the_statuses_that_really_mean_the_board_is_not_there(status):
    """404 no such site, 422 wrong site slug, 406 nonsense tenant on the
    wildcard host -- see AGENTS.md 8c. These, and only these, stay None."""
    assert fetch_workday(_status_client(status, json={}), BOARD) is None


def test_the_list_post_retries_a_transient_429():
    """It had no retry at all, so one bad first page failed the whole board."""
    attempts = []
    slept: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/jobs") and request.method == "POST":
            attempts.append(1)
            if len(attempts) < 3:
                return httpx.Response(429, html=CHALLENGE_BODY)
            return httpx.Response(200, json=LISTING)
        for req in ("JR1", "JR2", "JR3"):
            if req in request.url.path:
                return httpx.Response(200, json=_detail(req))
        return httpx.Response(404, json={})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = fetch_workday(client, BOARD, sleep=slept.append)
    assert result is not None
    assert len(attempts) == 3
    assert result.fetched == 3
    # Backoff grows, and it is the list retry doing the sleeping.
    assert slept == [1.5, 3.0]


def test_a_board_gives_up_after_the_retries_rather_than_hanging_on():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(429, html=CHALLENGE_BODY)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(WorkdayUnavailable):
        fetch_workday(client, BOARD, sleep=lambda _: None)
    assert len(calls) == 3


def test_detail_calls_are_paced():
    """Unpaced, a cold board is thousands of sequential requests at full speed,
    which is what earns the challenge in the first place."""
    client, seen = _client()
    slept: list[float] = []
    result = fetch_workday(client, BOARD, detail_delay=0.25, sleep=slept.append)
    assert result is not None
    assert len(result.new) == 2
    # Between the two detail calls, not before the first: a steady-state pass
    # that finds one new posting must not pay a pause for it.
    assert slept == [0.25]


def test_a_posting_already_held_costs_no_pause():
    client, _ = _client()
    slept: list[float] = []
    known = {
        "nvidia-nvidiaexternalcareersite:JR1",
        "nvidia-nvidiaexternalcareersite:JR3",
    }
    result = fetch_workday(client, BOARD, known_ids=known, detail_delay=0.25, sleep=slept.append)
    assert result is not None
    assert result.new == []
    assert slept == []


# --- a short read must not look like a shrunken board -----------------------

PAGED_LISTING = {"total": 40, "jobPostings": LISTING["jobPostings"]}


def test_an_unreadable_later_page_fails_the_board_instead_of_truncating_it():
    """The data-loss case behind this change.

    `seen_ids` from a truncated listing goes to expire_missing, so one
    throttled page in the middle of a 145-page board would have deactivated
    every posting below it. Failing the board leaves the rows alone.
    """
    posts = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/jobs") and request.method == "POST":
            posts.append(request)
            if len(posts) == 1:
                return httpx.Response(200, json=PAGED_LISTING)
            return httpx.Response(404, json={})
        return httpx.Response(200, json=_detail("JR1"))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(WorkdayUnavailable) as caught:
        fetch_workday(client, BOARD, sleep=lambda _: None)
    assert "offset 20" in str(caught.value)


def test_a_total_that_runs_ahead_of_the_pages_is_a_clean_end():
    """The legitimate short page: `total` is not always honest, and an empty
    batch means the end, not a failure."""
    posts = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/jobs") and request.method == "POST":
            posts.append(request)
            if len(posts) == 1:
                return httpx.Response(200, json=PAGED_LISTING)
            return httpx.Response(200, json={"total": 40, "jobPostings": []})
        for req in ("JR1", "JR2", "JR3"):
            if req in request.url.path:
                return httpx.Response(200, json=_detail(req))
        return httpx.Response(404, json={})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = fetch_workday(client, BOARD, sleep=lambda _: None)
    assert result is not None
    assert result.fetched == 3


# --- what the admin table ends up showing ------------------------------------


def test_a_challenged_board_records_its_status_and_expires_nothing(db_session):
    """End to end: the row an operator reads, and the rows that must survive."""
    client, _ = _client()
    first = ingest_workday_board(db_session, client, BOARD)
    assert first.status == "ok"
    assert first.upserted == 2

    challenged = ingest_workday_board(
        db_session, _status_client(429, html=CHALLENGE_BODY), BOARD
    )
    assert challenged.status == "error"
    assert challenged.error is not None
    assert "429" in challenged.error
    assert "security check" in challenged.error
    assert "board not found" not in challenged.error

    rows = db_session.query(Job).filter(Job.source == "workday").all()
    assert len(rows) == 2
    assert all(r.is_active for r in rows), "a challenged board deactivated its postings"
