"""The HTTP surface for starting, reading and streaming a resume import.

The runner itself (``app.services.import_runner.run_import``) is covered end
to end with a scripted model in ``tests/test_import.py``'s sibling suite for
Task 6; nothing here re-tests that. These tests exercise the *route*
behaviour only -- status codes, the snapshot shape, ownership, and the
stream's termination contract -- so every POST test disables the real
scheduling seam (``app.routers.resumes._schedule_import``) to keep them
deterministic and independent of model-scripting or timing.
"""

import pytest
from fastapi.testclient import TestClient

from app.models import Resume, ResumeImport
from app.services.import_run import advance, declare_plan, finish


def _upload(client: TestClient, body: bytes = b"Resume body\ncontact@example.com"):
    return client.post(
        "/v1/resumes/upload",
        data={"title": "Uploaded", "locale": "en"},
        files={"file": ("resume.txt", body, "text/plain")},
    )


def _disable_scheduling(monkeypatch) -> None:
    """No real import may start under a route test -- see the module docstring."""
    monkeypatch.setattr("app.routers.resumes._schedule_import", lambda *a, **k: None)


def _latest_run(db_session, resume_id: str) -> ResumeImport:
    return (
        db_session.query(ResumeImport)
        .filter(ResumeImport.resume_id == resume_id)
        .order_by(ResumeImport.created_at.desc())
        .first()
    )


@pytest.fixture
def uploaded_resume(client, db_session):
    """A resume with an upload on record.

    The real upload route fails soft on S3 in tests (`APP_ENV=development`
    is in `_FAIL_SOFT_ENVS`, see `app.services.s3.put_object`) and leaves
    `upload_s3_key` unset, so the key is written directly here -- the same
    way `tests/test_import_run.py`, `tests/test_import_tools.py` and
    `tests/test_import_drift.py` build a resume with one.
    """
    resume_id = _upload(client).json()["id"]
    row = db_session.get(Resume, resume_id)
    row.upload_s3_key = f"uploads/test/{resume_id}/resume.txt"
    db_session.commit()
    return resume_id


@pytest.fixture
def created_resume(client):
    return client.post("/v1/resumes", json={"locale": "en"}).json()["id"]


@pytest.fixture
def other_guest_resume(client):
    """A resume owned by a guest distinct from whichever identity `client`
    settles on for the rest of the test. Clearing cookies before AND after
    the upload means the upload gets its own fresh guest, and the test's own
    later calls start from no identity at all rather than reusing it.
    """
    client.cookies.clear()
    resume_id = _upload(client).json()["id"]
    client.cookies.clear()
    return resume_id


def test_start_import_returns_snapshot(client, uploaded_resume, monkeypatch):
    _disable_scheduling(monkeypatch)
    r = client.post(f"/v1/resumes/{uploaded_resume}/import")
    assert r.status_code == 201
    assert r.json()["status"] == "running"


def test_start_import_conflicts_while_running(client, uploaded_resume, monkeypatch):
    _disable_scheduling(monkeypatch)
    client.post(f"/v1/resumes/{uploaded_resume}/import")
    assert client.post(f"/v1/resumes/{uploaded_resume}/import").status_code == 409


def test_start_import_requires_an_upload(client, created_resume, monkeypatch):
    _disable_scheduling(monkeypatch)
    assert client.post(f"/v1/resumes/{created_resume}/import").status_code == 400


def test_get_import_is_404_before_any_run(client, uploaded_resume):
    assert client.get(f"/v1/resumes/{uploaded_resume}/import").status_code == 404


def test_second_client_attaching_sees_current_state(
    client, uploaded_resume, monkeypatch, db_session
):
    _disable_scheduling(monkeypatch)
    client.post(f"/v1/resumes/{uploaded_resume}/import")

    run = _latest_run(db_session, uploaded_resume)
    declare_plan(
        db_session,
        run,
        [
            {"key": "summary", "label": "Summary"},
            {"key": "experience", "label": "Experience"},
        ],
    )
    advance(db_session, run, "summary")
    advance(db_session, run, "experience")

    body = client.get(f"/v1/resumes/{uploaded_resume}/import").json()
    assert body["applied"] == 2
    done_steps = [s for s in body["steps"] if s["status"] == "done"]
    assert len(done_steps) == 2


def test_stream_closes_once_the_run_is_done(client, uploaded_resume, monkeypatch, db_session):
    _disable_scheduling(monkeypatch)
    client.post(f"/v1/resumes/{uploaded_resume}/import")

    run = _latest_run(db_session, uploaded_resume)
    finish(db_session, run)

    with client.stream("GET", f"/v1/resumes/{uploaded_resume}/import/stream") as s:
        frames = [line for line in s.iter_lines() if line.startswith("data:")]
    assert len(frames) == 1


def test_import_endpoints_reject_another_owner(client, other_guest_resume):
    assert client.get(f"/v1/resumes/{other_guest_resume}/import").status_code in (403, 404)
