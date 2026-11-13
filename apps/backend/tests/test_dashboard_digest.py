"""The latest digest on GET /v1/dashboard."""

from datetime import datetime, timezone

from sqlalchemy import event

from app.models import Digest, DigestItem, User
from app.services.jobs.digest import UNRANKED_TRACK

from tests.test_applications_service import _job
from tests.test_dashboard_api import _signed_in, _writes
from tests.test_talent_api import LIST, _bearer, _candidate, _key

WHEN = datetime(2026, 11, 9, tzinfo=timezone.utc)


def _digest(db_session, user, *, period=WHEN, status="full", opened=False):
    row = Digest(
        user_id=user.id,
        period_start=period,
        built_at=period,
        model="test-model",
        input_hash="h" * 64,
        status=status,
        candidate_count=12,
        opened_at=period if opened else None,
    )
    db_session.add(row)
    db_session.commit()
    return row


def _item(db_session, digest, job, *, track="strong_fit", rank=1, **over):
    fields = dict(
        tier="Strong fit",
        fit_score=0.9,
        reason="Because reasons.",
        gap="A gap.",
        requirements_met=0.9,
        seniority_fit=0.9,
        location_fit=0.9,
        direction_fit=0.9,
    )
    fields.update(over)
    row = DigestItem(
        digest_id=digest.id, job_id=job.id, track=track, rank=rank, **fields
    )
    db_session.add(row)
    db_session.commit()
    return row


def _get(client):
    res = client.get("/v1/dashboard")
    assert res.status_code == 200
    return res.json()


def _stored_opened_at(db_session, digest):
    db_session.expire_all()
    return db_session.get(Digest, digest.id).opened_at


def test_a_person_with_no_digest_gets_null_not_an_empty_object(client, db_session):
    _signed_in(client, db_session)
    body = _get(client)
    assert "digest" in body and body["digest"] is None


def test_the_digest_comes_back_with_both_tracks(client, db_session):
    user = _signed_in(client, db_session)
    digest = _digest(db_session, user)
    strong, fresh = _job(db_session, "s"), _job(db_session, "n")
    _item(db_session, digest, strong, track="strong_fit", rank=1)
    _item(
        db_session, digest, fresh, track="new_this_week", rank=1,
        tier="Worth a look", fit_score=0.4, reason="Fresh.", gap=None,
    )
    out = _get(client)["digest"]
    assert out["status"] == "full" and out["candidate_count"] == 12
    assert isinstance(out["built_at"], str) and out["built_at"]
    by_track = {i["track"]: i for i in out["items"]}
    assert set(by_track) == {"strong_fit", "new_this_week"}
    item = by_track["strong_fit"]
    assert item["job_id"] == strong.id
    assert item["job_title"] == strong.title and item["company"] == "Acme"
    assert item["apply_url"] == strong.apply_url and item["job_is_active"] is True
    assert (item["tier"], item["fit_score"]) == ("Strong fit", 0.9)
    assert (item["reason"], item["gap"]) == ("Because reasons.", "A gap.")
    assert by_track["new_this_week"]["gap"] is None


def test_a_degraded_digest_reports_its_status_and_null_tiers(client, db_session):
    user = _signed_in(client, db_session)
    digest = _digest(db_session, user, status="degraded")
    _item(
        db_session, digest, _job(db_session, "d"), track=UNRANKED_TRACK,
        tier=None, fit_score=None, reason=None, gap=None,
        requirements_met=None, seniority_fit=None, location_fit=None,
        direction_fit=None,
    )
    out = _get(client)["digest"]
    assert out["status"] == "degraded"
    (item,) = out["items"]
    # Passed through honestly, never coerced to one of the two real tracks.
    assert item["track"] == "unranked"
    assert item["tier"] is None and item["fit_score"] is None
    assert item["reason"] is None and item["gap"] is None


def test_items_are_ordered_by_track_then_rank(client, db_session):
    user = _signed_in(client, db_session)
    digest = _digest(db_session, user)
    plan = [("strong_fit", 2), ("new_this_week", 2), ("strong_fit", 1), ("new_this_week", 1)]
    for i, (track, rank) in enumerate(plan):
        _item(db_session, digest, _job(db_session, f"o-{i}"), track=track, rank=rank)
    got = [(i["track"], i["rank"]) for i in _get(client)["digest"]["items"]]
    # Written out rather than derived: (track, rank), alphabetical on track.
    assert got == [
        ("new_this_week", 1), ("new_this_week", 2), ("strong_fit", 1), ("strong_fit", 2),
    ]


def test_it_serves_the_latest_digest_by_period(client, db_session):
    user = _signed_in(client, db_session)
    old = _digest(db_session, user, period=datetime(2026, 11, 2, tzinfo=timezone.utc))
    new = _digest(db_session, user)
    _item(db_session, old, _job(db_session, "old"))
    _item(db_session, new, _job(db_session, "new"))
    items = _get(client)["digest"]["items"]
    assert [i["job_title"] for i in items] == ["Backend Engineer new"]


def test_reading_the_dashboard_marks_the_digest_opened(client, db_session):
    # The eligibility gate in the digest build reads opened_at; without this
    # stamp nobody is ever "engaged" and every user is skipped from week three.
    user = _signed_in(client, db_session)
    digest = _digest(db_session, user)
    _item(db_session, digest, _job(db_session, "m"))
    assert _stored_opened_at(db_session, digest) is None
    first = _get(client)["digest"]
    stamped = _stored_opened_at(db_session, digest)
    assert stamped is not None
    assert first["opened_at"] is not None
    # Idempotent: a second visit keeps the first stamp.
    _get(client)
    assert _stored_opened_at(db_session, digest) == stamped


def test_marking_opened_is_the_only_write_a_dashboard_GET_makes(client, db_session):
    user = _signed_in(client, db_session)
    digest = _digest(db_session, user)
    _item(db_session, digest, _job(db_session, "w"))
    res, writes = _writes(db_session, lambda: client.get("/v1/dashboard"))
    assert res.status_code == 200
    assert len(writes) == 1, writes
    stmt = writes[0].lstrip().upper()
    assert stmt.startswith("UPDATE DIGESTS SET OPENED_AT"), writes
    # Once opened, a visit is back to writing nothing at all.
    res, writes = _writes(db_session, lambda: client.get("/v1/dashboard"))
    assert res.status_code == 200 and writes == []


def test_the_query_count_stays_flat_as_items_grow(client, db_session):
    user = _signed_in(client, db_session)
    digest = _digest(db_session, user, opened=True)
    counts = []
    n = 0
    for target in (1, 8):
        while n < target:
            track = "strong_fit" if n % 2 == 0 else "new_this_week"
            _item(db_session, digest, _job(db_session, f"f-{n}"), track=track, rank=n)
            n += 1
        statements = []

        def count(conn, cursor, statement, *a):
            statements.append(statement)

        engine = db_session.get_bind()
        event.listen(engine, "before_cursor_execute", count)
        try:
            body = _get(client)
        finally:
            event.remove(engine, "before_cursor_execute", count)
        assert len(body["digest"]["items"]) == target  # really projected
        counts.append(len(statements))
    assert counts[0] == counts[1], counts


def test_the_first_open_costs_no_more_queries_per_item(client, db_session):
    # mark_opened commits and refreshes; that must not expire the eager-loaded
    # items and send the projection back to the database one row at a time.
    counts = []
    for size in (1, 8):
        user = _signed_in(client, db_session, sub=f"sub-first-{size}")
        digest = _digest(db_session, user)  # unopened: this GET stamps it
        for i in range(size):
            _item(db_session, digest, _job(db_session, f"p-{size}-{i}"), rank=i)
        statements = []

        def count(conn, cursor, statement, *a):
            statements.append(statement)

        engine = db_session.get_bind()
        event.listen(engine, "before_cursor_execute", count)
        try:
            assert len(_get(client)["digest"]["items"]) == size
        finally:
            event.remove(engine, "before_cursor_execute", count)
        counts.append(len(statements))
    assert counts[0] == counts[1], counts


def test_another_users_digest_is_never_returned(client, db_session):
    other = User(google_sub="sub-other", email="other@example.com", locale="en")
    db_session.add(other)
    db_session.commit()
    theirs = _digest(db_session, other)
    _item(db_session, theirs, _job(db_session, "theirs"), reason="ZZTHEIRS")
    user = _signed_in(client, db_session)
    res = client.get("/v1/dashboard")
    assert res.json()["digest"] is None
    assert "ZZTHEIRS" not in res.text
    # And reading it did not stamp their digest as opened.
    assert _stored_opened_at(db_session, theirs) is None


def test_digests_are_unreachable_from_the_employer_path(client, db_session):
    candidate, _resume, profile = _candidate(db_session)
    digest = _digest(db_session, candidate)
    job = _job(db_session, "digest-job")
    _item(
        db_session, digest, job,
        reason="ZZSENTINEL-REASON", gap="ZZSENTINEL-GAP",
    )
    _key_row, raw = _key(db_session)

    listing = client.get(LIST, headers=_bearer(raw))
    detail = client.get(f"{LIST}/{profile.id}", headers=_bearer(raw))
    assert listing.status_code == 200 and detail.status_code == 200
    # The consenting candidate is really being served, so absence is meaningful.
    assert "Chen Wei" in listing.text and "Chen Wei" in detail.text
    for res in (listing, detail):
        assert "ZZSENTINEL" not in res.text
        assert "digest" not in res.text.lower()
        assert job.id not in res.text and job.apply_url not in res.text
