"""Scored arrivals on GET /v1/dashboard, and "since you last looked" measured
by the server (users.matches_seen_at) rather than by the caller's browser.

The GET reads that marker and never moves it: three of its four callers render
no arrivals, so a stamp here would close the window for someone who only
looked at their applications. POST /v1/me/matches/seen is the one advance, and
every test below that wants a second window says so by calling it.
"""

from datetime import datetime, timedelta, timezone

from app.models import Digest, JobPreference, JobScore, User

from tests.test_build_digests import _posting
from tests.test_dashboard_api import _signed_in


def _prefer(db_session, user):
    db_session.add(
        JobPreference(
            user_id=user.id,
            target_functions="|backend|",
            target_locations="|tw|",
            target_seniority=None,
            remote_required=None,
        )
    )
    db_session.commit()


def _score(db_session, user, job, scored_at, **over):
    """A cached judgement. created_at defaults to scored_at: a pair's first
    judgement is its creation, which is what write_back writes."""
    fields = dict(
        user_id=user.id,
        job_id=job.id,
        facts_input_hash="a" * 64,
        description_hash="b" * 64,
        model="test-model",
        outcome="scored",
        scored_at=scored_at,
        created_at=scored_at,
        tier="Strong fit",
        fit_score=0.8,
        reason="Because reasons.",
        gap="A gap.",
    )
    fields.update(over)
    row = JobScore(**fields)
    db_session.add(row)
    db_session.commit()
    return row


def _seen_at(db_session, user):
    db_session.expire_all()
    return db_session.get(User, user.id).matches_seen_at


def _aware(dt):
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _get(client):
    res = client.get("/v1/dashboard")
    assert res.status_code == 200
    return res.json()


def _mark_seen(client):
    """What the overview does once it has rendered the arrivals."""
    res = client.post("/v1/me/matches/seen")
    assert res.status_code == 200
    return res.json()


def _ago(**kw):
    return datetime.now(timezone.utc) - timedelta(**kw)


def _ready(client, db_session, sub):
    user = _signed_in(client, db_session, sub)
    _prefer(db_session, user)
    return user


def test_the_dashboard_get_does_not_move_the_marker(client, db_session):
    # The regression this guards: Applications, Matches and Profile all fetch
    # this endpoint and none of them shows an arrival. A GET that stamped let
    # a reader check a status and lose fifteen unseen roles.
    user = _ready(client, db_session, "sub-arr-nostamp")
    assert _seen_at(db_session, user) is None
    _get(client)
    assert _seen_at(db_session, user) is None, "the GET must not stamp"


def test_the_page_that_showed_them_says_so(client, db_session):
    user = _ready(client, db_session, "sub-arr-stamp")
    assert _seen_at(db_session, user) is None
    before = datetime.now(timezone.utc)
    body = _mark_seen(client)
    stamped = _aware(_seen_at(db_session, user))
    assert before <= stamped <= datetime.now(timezone.utc)
    # Echoed back, and it is the server's clock, not the caller's.
    assert body["seen_at"].startswith(stamped.isoformat()[:19])


def test_arrivals_are_what_was_scored_since_that_stamp(client, db_session):
    # Read-before-stamp: the previous visit's marker is what the window uses.
    # Were the stamp written first, the window would be empty on every visit.
    user = _ready(client, db_session, "sub-arr-window")
    old, new = _posting(db_session), _posting(db_session)
    user.matches_seen_at = _ago(hours=10)
    db_session.commit()
    _score(db_session, user, old, _ago(hours=20))
    _score(db_session, user, new, _ago(hours=2))

    first = _get(client)
    assert first["first_visit"] is False
    assert [a["job_id"] for a in first["arrivals"]] == [new.id]
    assert first["arrivals"][0]["tier"] == "Strong fit"

    # Still new until something says it was shown: a second fetch on its own
    # changes nothing.
    assert [a["job_id"] for a in _get(client)["arrivals"]] == [new.id]
    # Shown, and so not new twice.
    _mark_seen(client)
    assert _get(client)["arrivals"] == []


def test_a_first_visit_is_distinguishable_from_an_empty_window(client, db_session):
    user = _ready(client, db_session, "sub-arr-first")
    job = _posting(db_session)
    _score(db_session, user, job, _ago(days=3))

    first = _get(client)
    # A brand new account has fresh matches and must not be shown nothing.
    assert first["first_visit"] is True
    assert [a["job_id"] for a in first["arrivals"]] == [job.id]

    _mark_seen(client)
    again = _get(client)
    assert again["first_visit"] is False
    assert again["arrivals"] == []


def test_a_marker_in_the_future_clamps_instead_of_going_negative(client, db_session):
    user = _ready(client, db_session, "sub-arr-future")
    job = _posting(db_session)
    user.matches_seen_at = _ago(days=-30)
    db_session.commit()
    _score(db_session, user, job, _ago(hours=1))

    body = _get(client)
    # Clamped to now: "I looked just now", so nothing older than now is new,
    # and the posting is not mistaken for a first visit's catalogue-wide dump.
    assert body["first_visit"] is False
    assert body["arrivals"] == []
    # And the stamp repairs the marker rather than preserving the skew.
    _mark_seen(client)
    assert _aware(_seen_at(db_session, user)) <= datetime.now(timezone.utc)


def test_an_inactive_posting_is_marked_not_dropped(client, db_session):
    user = _ready(client, db_session, "sub-arr-inactive")
    job = _posting(db_session)
    _score(db_session, user, job, _ago(hours=1))
    job.is_active = False
    user.matches_seen_at = _ago(hours=5)
    db_session.commit()

    arrivals = _get(client)["arrivals"]
    assert [a["job_id"] for a in arrivals] == [job.id]
    assert arrivals[0]["job_is_active"] is False


def test_a_matching_posting_with_no_score_yet_is_present_without_one(
    client, db_session
):
    user = _ready(client, db_session, "sub-arr-unscored")
    scored, waiting = _posting(db_session), _posting(db_session)
    _score(db_session, user, scored, _ago(hours=1))

    body = _get(client)
    by_id = {a["job_id"]: a for a in body["arrivals"]}
    assert set(by_id) == {scored.id, waiting.id}
    assert by_id[waiting.id]["tier"] is None
    assert by_id[waiting.id]["fit_score"] is None
    assert by_id[waiting.id]["scored_at"] is None
    assert body["match_count"] == 2, "the list does not disagree with the count"


def test_a_rescore_does_not_make_an_old_match_new_again(client, db_session):
    # The anti-goal, pinned. A resume edit makes the next sweep pass rebuild
    # the facts and rescore up to 30 pairs, each getting a fresh scored_at.
    # Windowed on that, the dashboard said "new roles since you last looked"
    # about roles the reader had already been shown.
    user = _ready(client, db_session, "sub-arr-rescore")
    job = _posting(db_session)
    row = _score(db_session, user, job, _ago(days=3))
    user.matches_seen_at = _ago(days=2)
    db_session.commit()
    assert _get(client)["arrivals"] == [], "already seen before the marker"

    # The rescore: a new judgement of the same pair.
    row.scored_at = _ago(minutes=5)
    db_session.commit()
    assert _get(client)["arrivals"] == [], "a rescore is not an arrival"
    # The page still shows WHEN it was judged; that is scored_at's own job.
    user.matches_seen_at = _ago(days=4)
    db_session.commit()
    (arrival,) = _get(client)["arrivals"]
    assert arrival["scored_at"].startswith(_aware(row.scored_at).isoformat()[:16])


def test_a_pending_posting_already_shown_is_not_new_again(client, db_session):
    # The unscored branch's window, which nothing pinned. It decides whether
    # a matching posting with no score row yet reappears on a second visit,
    # and it is the clause the first-visit NULL marker bypasses entirely.
    user = _ready(client, db_session, "sub-arr-pending-window")
    old = _posting(db_session, first_seen=_ago(days=3))
    fresh = _posting(db_session, first_seen=_ago(hours=1))
    user.matches_seen_at = _ago(days=1)
    db_session.commit()

    body = _get(client)
    assert [a["job_id"] for a in body["arrivals"]] == [fresh.id], (
        "a posting first seen before the marker was already on the page unscored"
    )
    assert body["arrivals"][0]["scored_at"] is None
    # Both still count as matches: the window is about novelty, not matching.
    assert body["match_count"] == 2

    # Shown, so the next window starts after it; nothing is new any more.
    _mark_seen(client)
    assert _get(client)["arrivals"] == []

    # And a score arriving AFTER that makes the older one new exactly once, as
    # kind 1 -- the handover the two windows have to agree on. The sweep's
    # next pass lands a moment after the page was shown, so the row is dated
    # there rather than before the marker it has to clear.
    _score(db_session, user, old, datetime.now(timezone.utc) + timedelta(seconds=2))
    assert [a["job_id"] for a in _get(client)["arrivals"]] == [old.id]


def test_a_refused_score_travels_without_a_tier(client, db_session):
    user = _ready(client, db_session, "sub-arr-refused")
    job = _posting(db_session)
    _score(
        db_session, user, job, _ago(hours=1),
        outcome="refused", tier=None, fit_score=None, reason=None, gap=None,
    )
    (arrival,) = _get(client)["arrivals"]
    assert arrival["tier"] is None and arrival["scored_at"] is not None


def test_scores_belong_to_the_person_they_were_made_for(client, db_session):
    _ready(client, db_session, "sub-arr-mine")
    other = User(google_sub="sub-arr-other", email="o@example.com", locale="en")
    db_session.add(other)
    db_session.commit()
    job = _posting(db_session)
    _score(db_session, other, job, _ago(hours=1), tier="Strong fit")

    (arrival,) = _get(client)["arrivals"]
    assert arrival["tier"] is None, "someone else's score is not mine"


def test_the_client_since_parameter_is_gone(client, db_session):
    # Two sources for one number is how they disagree.
    user = _ready(client, db_session, "sub-arr-nosince")
    job = _posting(db_session)
    _score(db_session, user, job, _ago(hours=1))
    user.matches_seen_at = _ago(hours=5)
    db_session.commit()

    res = client.get("/v1/dashboard", params={"since": _ago(minutes=1).isoformat()})
    body = res.json()
    assert "new_match_count" not in body
    assert [a["job_id"] for a in body["arrivals"]] == [job.id]


def test_the_clamp_itself(client):
    # Pinned directly: the stamp writes `now` regardless, so no response-level
    # assertion can tell a clamped marker from an unclamped one.
    from app.routers.dashboard import _window_start

    now = datetime.now(timezone.utc)
    assert _window_start(now + timedelta(days=30), now) == now
    assert _window_start(now - timedelta(days=1), now) == now - timedelta(days=1)
    assert _window_start(None, now) is None


def test_a_failed_stamp_cannot_take_down_the_page(client, db_session, monkeypatch):
    # It cannot by construction now: the stamp is its own request, made after
    # the page the person is reading has been served. Pinned anyway, because
    # the old arrangement needed a try/except to say the same thing and a
    # later reader must not fold the stamp back into the GET to "save a call".
    from sqlalchemy.orm import Session

    user = _ready(client, db_session, "sub-arr-stampfail")
    job = _posting(db_session)
    real_commit = Session.commit

    def failing_commit(self):
        if any(isinstance(o, User) for o in self.dirty):
            raise RuntimeError("db hiccup")
        return real_commit(self)

    res = client.get("/v1/dashboard")
    assert res.status_code == 200
    body = res.json()
    assert body["first_visit"] is True
    assert [a["job_id"] for a in body["arrivals"]] == [job.id]
    assert body["preference"] is not None and body["match_count"] == 1

    monkeypatch.setattr(Session, "commit", failing_commit)
    try:
        failed = client.post("/v1/me/matches/seen")
    except RuntimeError:
        failed = None  # TestClient re-raises; either way the page stood
    monkeypatch.undo()
    assert failed is None or failed.status_code >= 500
    # The marker did not move, so the arrivals come back rather than vanish.
    assert _seen_at(db_session, user) is None
    assert [a["job_id"] for a in _get(client)["arrivals"]] == [job.id]


def test_a_score_outlives_the_preference_that_produced_it(client, db_session):
    # An arrival is a score row after the marker, whatever the preference says
    # now: it was scored because it matched then.
    user = _ready(client, db_session, "sub-arr-pref-edit")
    job = _posting(db_session)
    _score(db_session, user, job, _ago(hours=1))
    user.matches_seen_at = _ago(hours=5)
    db_session.query(JobPreference).filter_by(user_id=user.id).update(
        {"target_functions": "|design|"}
    )
    db_session.commit()

    (arrival,) = _get(client)["arrivals"]
    assert arrival["job_id"] == job.id and arrival["tier"] == "Strong fit"


def test_the_digest_still_travels_and_is_still_stamped_opened(client, db_session):
    user = _ready(client, db_session, "sub-arr-digest")
    when = datetime(2026, 11, 9, tzinfo=timezone.utc)
    row = Digest(
        user_id=user.id, period_start=when, built_at=when, model="test-model",
        input_hash="h" * 64, status="full", candidate_count=0,
    )
    db_session.add(row)
    db_session.commit()

    body = _get(client)
    assert body["digest"]["status"] == "full"
    db_session.expire_all()
    assert db_session.get(Digest, row.id).opened_at is not None
