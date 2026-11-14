from sqlalchemy import event

from app.config import get_settings
from app.deps import SESSION_COOKIE, _sign
from app.models import Application, DisclosureEvent, JobPreference, Resume, User
from app.services.jobs import applications as service
from app.services.revisions import pin_current_draft, set_draft_source

from tests.test_applications_service import _job
from tests.test_match_sweep import _posting
from tests.test_talent_api import LIST, _bearer, _candidate, _key

TOP_LEVEL = {
    "preference", "match_count", "matches", "applications", "resume_health",
    "employer_views", "digest",
}
HEALTH_KEYS = {
    "has_master", "published", "facts_state", "matching_revision_current", "needs_rebuild",
}


def _signed_in(client, db_session, sub="sub-dash"):
    user = User(google_sub=sub, email=f"{sub}@example.com", locale="en")
    db_session.add(user)
    db_session.commit()
    client.cookies.set(SESSION_COOKIE, _sign(user.id, get_settings().auth_token_secret))
    return user


def test_requires_a_user(client):
    assert client.get("/v1/dashboard").status_code == 401


def test_the_brand_new_person_gets_every_section_empty(client, db_session):
    _signed_in(client, db_session)
    res = client.get("/v1/dashboard")
    assert res.status_code == 200
    body = res.json()
    assert body["preference"]["updated_at"] is None  # the transient seed
    assert body["preference"]["facts_state"] == "missing"
    assert body["preference"]["target_functions"] == []
    assert body["match_count"] == 0
    assert body["matches"] == []
    assert body["applications"] == []
    assert body["resume_health"] == {
        "has_master": False,
        "published": False,
        "facts_state": "missing",
        "matching_revision_current": False,
        # nothing to rebuild from: no card demanding a rebuild
        "needs_rebuild": False,
    }
    assert body["employer_views"] == {"disclosure_count": 0, "last_disclosed_at": None}


def test_it_returns_the_whole_view_in_one_request(client, db_session):
    _signed_in(client, db_session)
    body = client.get("/v1/dashboard").json()
    assert set(body) == TOP_LEVEL
    assert set(body["resume_health"]) == HEALTH_KEYS
    assert set(body["employer_views"]) == {"disclosure_count", "last_disclosed_at"}
    assert "preference_summary_parts" not in body


def test_applications_come_back_in_group_order(client, db_session):
    user = _signed_in(client, db_session)
    saved = _job(db_session, "j-saved")
    applied = _job(db_session, "j-applied")
    interviewing = _job(db_session, "j-int")
    service.save(db_session, user=user, job_id=saved.id)
    service.mark_applied(db_session, user=user, job_id=applied.id)
    service.save(db_session, user=user, job_id=interviewing.id)
    row = db_session.query(Application).filter_by(job_id=interviewing.id).one()
    service.set_status(db_session, application=row, status="interviewing")
    got = client.get("/v1/dashboard").json()["applications"]
    # GROUP_ORDER: interviewing, applied, tailored, saved. Written out, not
    # derived from the service, so a wrong ordering there cannot pass.
    assert [a["status"] for a in got] == ["interviewing", "applied", "saved"]
    assert [a["job_id"] for a in got] == [interviewing.id, applied.id, saved.id]
    assert got[0]["job_title"] == interviewing.title


def _writes(db_session, call):
    """Run call() and return every INSERT/UPDATE/DELETE statement it issued."""
    seen = []

    def watch(conn, cursor, statement, *a):
        seen.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", watch)
    try:
        response = call()
    finally:
        event.remove(engine, "before_cursor_execute", watch)
    writes = [
        st for st in seen if st.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
    ]
    return response, writes


def test_a_get_writes_nothing(client, db_session):
    # Pins "the transient seed stays transient" and "a visit spends no
    # credits" (a facts rebuild or a metered call would have to write).
    #
    # One exception, named: with a digest present, the GET stamps that digest's
    # opened_at (the build's eligibility gate reads it). Nothing else may be
    # written; test_marking_opened_is_the_only_write_a_dashboard_GET_makes in
    # test_dashboard_digest.py pins that exception's exact shape. This test
    # stays strict for everyone without a digest, and for everyone whose
    # digest is already opened.
    user = _signed_in(client, db_session)
    res, writes = _writes(db_session, lambda: client.get("/v1/dashboard"))
    assert res.status_code == 200
    assert writes == []
    db_session.expire_all()
    assert db_session.query(JobPreference).filter_by(user_id=user.id).count() == 0

    from datetime import datetime, timezone

    from app.models import Digest

    now = datetime.now(timezone.utc)
    db_session.add(
        Digest(
            user_id=user.id, period_start=now, built_at=now, model="m",
            input_hash="h" * 64, status="empty", candidate_count=0, opened_at=now,
        )
    )
    db_session.commit()
    res, writes = _writes(db_session, lambda: client.get("/v1/dashboard"))
    assert res.status_code == 200 and res.json()["digest"] is not None
    assert writes == []


def test_a_populated_person_gets_their_own_non_default_numbers(client, db_session):
    user = _signed_in(client, db_session)
    other = User(google_sub="sub-other", email="other@example.com", locale="en")
    db_session.add(other)
    db_session.commit()

    # match_count: a saved preference matching one active, fresh, faceted job.
    posting = _posting(db_session, function="backend", country="TW")
    db_session.add(
        JobPreference(
            user_id=user.id,
            target_functions="|backend|",
            target_locations="|tw|",
            target_seniority=None,
            remote_required=None,
        )
    )

    # employer_views: one disclosure for this user, three for someone else.
    def disclose(owner, n):
        for _ in range(n):
            db_session.add(
                DisclosureEvent(
                    user_id=owner.id,
                    recipient_label="Acme Talent",
                    recipient_contact="talent@acme.example",
                    purpose="talent_introduction",
                    scope="full",
                    profile_content_hash="h" * 64,
                    policy_version="2026-10-24",
                    route="GET /x",
                )
            )

    disclose(user, 1)
    disclose(other, 3)

    # resume_health: a published master for this user only.
    master = Resume(
        title="Master", typst_source="= CV", source="create", locale="en",
        user_id=user.id, kind="master",
    )
    db_session.add(master)
    db_session.flush()
    set_draft_source(db_session, master, "= CV")
    pin_current_draft(db_session, master)
    user.profile_resume_id = master.id
    db_session.commit()

    body = client.get("/v1/dashboard").json()

    assert body["match_count"] == 1
    # The roles themselves ride along with the count, in the jobs list shape.
    assert [row["id"] for row in body["matches"]] == [posting.id]
    assert body["matches"][0]["company"] == "Acme"
    pref = body["preference"]
    assert pref["target_functions"] == ["backend"]
    assert isinstance(pref["updated_at"], str) and pref["updated_at"]

    views = body["employer_views"]
    assert views["disclosure_count"] == 1  # not the other person's 3
    assert isinstance(views["last_disclosed_at"], str) and views["last_disclosed_at"]

    # A published master with no facts built yet: present and published, the
    # facts are missing so matching cannot be current, and a rebuild is due.
    assert body["resume_health"] == {
        "has_master": True,
        "published": True,
        "facts_state": "missing",
        "matching_revision_current": False,
        "needs_rebuild": True,
    }


def test_one_request_costs_a_flat_number_of_queries(client, db_session):
    user = _signed_in(client, db_session)
    counts = []
    for extra in (1, 5):
        for i in range(extra):
            job = _job(db_session, f"q-{extra}-{i}")
            service.save(db_session, user=user, job_id=job.id)
        statements = []

        def count(conn, cursor, statement, *a):
            statements.append(statement)

        engine = db_session.get_bind()
        event.listen(engine, "before_cursor_execute", count)
        try:
            assert client.get("/v1/dashboard").status_code == 200
        finally:
            event.remove(engine, "before_cursor_execute", count)
        counts.append(len(statements))
    # 1 application vs 6: no per-row query (the N+1 application_out had).
    assert counts[0] == counts[1], counts


def test_applications_are_unreachable_from_the_employer_path(client, db_session):
    candidate, _resume, profile = _candidate(db_session)
    job = _job(db_session, "secret-job")
    job.title = "ZZSENTINEL-TITLE"
    job.company = "ZZSENTINEL-CO"
    db_session.commit()
    service.save(db_session, user=candidate, job_id=job.id)
    service.mark_applied(db_session, user=candidate, job_id=job.id)
    row = db_session.query(Application).filter_by(user_id=candidate.id).one()
    row.notes = "ZZSENTINEL-NOTES"
    db_session.commit()
    _key_row, raw = _key(db_session)

    listing = client.get(LIST, headers=_bearer(raw))
    detail = client.get(f"{LIST}/{profile.id}", headers=_bearer(raw))
    assert listing.status_code == 200 and detail.status_code == 200
    # The consenting candidate is really being served, so absence is meaningful.
    assert "Chen Wei" in listing.text and "Chen Wei" in detail.text

    def keys(node):
        if isinstance(node, dict):
            for k, v in node.items():
                yield k
                yield from keys(v)
        elif isinstance(node, list):
            for item in node:
                yield from keys(item)

    for res in (listing, detail):
        assert "ZZSENTINEL" not in res.text
        assert job.id not in res.text and job.apply_url not in res.text
        names = {k.lower() for k in keys(res.json())}
        assert not {n for n in names if "applic" in n or "apply" in n or n == "notes"}
