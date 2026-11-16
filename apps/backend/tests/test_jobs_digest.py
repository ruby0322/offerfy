from datetime import datetime, timedelta, timezone

from app.models import Digest, DigestItem, Job, User
from app.services.jobs import digest as dg
from app.services.jobs.score import Scored

S, G, W, N = "Strong fit", "Good fit", "Worth a look", "Not a fit"


def sc(job_id, tier, score):
    return Scored(job_id=job_id, tier=tier, fit_score=score, components={})


def ids(items):
    return [s.job_id for s in items]


def test_strong_fit_admits_only_the_top_two_levels():
    out = dg.classify(
        [sc("s", S, 0.9), sc("g", G, 0.7), sc("w", W, 0.4), sc("n", N, 0.0)],
        new_job_ids=set(),
    )
    assert ids(out["strong_fit"]) == ["s", "g"]


def test_new_this_week_admits_worth_a_look_but_not_not_a_fit():
    out = dg.classify(
        [sc("s", S, 0.9), sc("w", W, 0.4), sc("n", N, 0.0)],
        new_job_ids={"s", "w", "n"},
    )
    # "s" is new too, but strong_fit is already showing it; "n" is in neither.
    assert ids(out["new_this_week"]) == ["w"]


def test_new_this_week_is_bounded_by_the_new_job_ids():
    out = dg.classify(
        [sc("old", S, 0.9), sc("fresh", W, 0.4)], new_job_ids={"fresh", "unscored"}
    )
    assert ids(out["new_this_week"]) == ["fresh"]


def test_a_posting_qualifying_for_both_tracks_is_shown_once():
    """The tracks are disjoint and strong_fit wins.

    Listing one posting under both headings read as two different roles, and
    made the per-track counts sum to more than the digest held.
    """
    out = dg.classify([sc("x", S, 0.9)], new_job_ids={"x"})
    assert ids(out["strong_fit"]) == ["x"]
    assert ids(out["new_this_week"]) == []


def test_the_tracks_never_share_a_job_id():
    scored = [sc(f"j{i}", (S, G, W)[i % 3], i / 20) for i in range(12)]
    out = dg.classify(scored, new_job_ids={s.job_id for s in scored})
    strong, fresh = set(ids(out["strong_fit"])), set(ids(out["new_this_week"]))
    assert strong and fresh and not (strong & fresh)
    # Counting the headings and counting the postings give the same answer.
    assert len(strong) + len(fresh) == len(strong | fresh)


def test_a_strong_posting_the_cap_dropped_can_still_be_new():
    """Disjointness is applied AFTER strong_fit's cap, so the digest keeps it.

    Were it applied before, every strong-tier posting beyond the fifth would
    fall out of both tracks and vanish from the digest.
    """
    scored = [sc(f"j{i}", S, i / 10) for i in range(7)]
    out = dg.classify(scored, new_job_ids={"j0", "j1"}, items_per_track=5)
    assert ids(out["strong_fit"]) == ["j6", "j5", "j4", "j3", "j2"]
    assert ids(out["new_this_week"]) == ["j1", "j0"]


def test_each_track_is_ordered_by_score_and_capped_at_five():
    scored = [sc(f"j{i}", S, i / 10) for i in range(8)]
    out = dg.classify(scored, new_job_ids={s.job_id for s in scored})
    assert ids(out["strong_fit"]) == ["j7", "j6", "j5", "j4", "j3"]
    # Every one of these is new AND strong-tier, so the second track holds
    # exactly what the first one's cap pushed out, still in score order.
    assert ids(out["new_this_week"]) == ["j2", "j1", "j0"]


def test_neither_track_is_padded_when_there_are_fewer_than_five():
    out = dg.classify(
        [sc("a", S, 0.9), sc("b", G, 0.8), sc("c", W, 0.3)], new_job_ids={"c"}
    )
    assert set(out) == {"strong_fit", "new_this_week"}
    assert len(out["strong_fit"]) == 2
    assert len(out["new_this_week"]) == 1
    assert all(s is not None for track in out.values() for s in track)
    assert dg.classify([], new_job_ids=set()) == {"strong_fit": [], "new_this_week": []}


def test_a_smaller_override_truncates_both_tracks():
    scored = [sc(f"j{i}", S, i / 10) for i in range(6)]
    out = dg.classify(
        scored, new_job_ids={s.job_id for s in scored}, items_per_track=2
    )
    assert ids(out["strong_fit"]) == ["j5", "j4"]
    assert ids(out["new_this_week"]) == ["j3", "j2"]
    assert dg.ITEMS_PER_TRACK == 5


def test_a_negative_override_yields_empty_tracks_not_a_tail_slice():
    scored = [sc(f"j{i}", S, i / 10) for i in range(4)]
    out = dg.classify(scored, new_job_ids={"j0"}, items_per_track=-2)
    assert out == {"strong_fit": [], "new_this_week": []}


def test_an_unknown_or_empty_tier_is_in_neither_track():
    out = dg.classify(
        [sc("a", "", 0.9), sc("b", "Maybe", 0.8), sc("c", W, 0.1)],
        new_job_ids={"a", "b", "c"},
    )
    assert ids(out["strong_fit"]) == []
    assert ids(out["new_this_week"]) == ["c"]


def test_equal_scores_order_by_job_id_whatever_the_input_order():
    a, b = sc("a", S, 0.5), sc("b", S, 0.5)
    c, d = sc("c", W, 0.4), sc("d", W, 0.4)
    for scored in ([a, b, c, d], [d, c, b, a]):
        out = dg.classify(scored, new_job_ids={"a", "b", "c", "d"})
        assert ids(out["strong_fit"]) == ["a", "b"]
        assert ids(out["new_this_week"]) == ["c", "d"]


def test_classify_does_not_mutate_its_input():
    scored = [sc("a", S, 0.1), sc("b", S, 0.9)]
    dg.classify(scored, new_job_ids=set())
    assert ids(scored) == ["a", "b"]


# --- database reads ---------------------------------------------------------

T0 = datetime(2026, 11, 2, tzinfo=timezone.utc)


def make_user(db, sub):
    user = User(google_sub=sub, email=f"{sub}@example.com", locale="en")
    db.add(user)
    db.flush()
    return user


def make_digest(db, user, period_start, **kw):
    d = Digest(
        user_id=user.id,
        period_start=period_start,
        built_at=period_start,
        model="m",
        input_hash="h",
        status="full",
        candidate_count=1,
        **kw,
    )
    db.add(d)
    db.flush()
    return d


def test_latest_for_user_returns_the_newest_period(db_session):
    user = make_user(db_session, "u1")
    make_digest(db_session, user, T0)
    newest = make_digest(db_session, user, T0 + timedelta(days=14))
    make_digest(db_session, user, T0 + timedelta(days=7))
    db_session.commit()
    assert dg.latest_for_user(db_session, user=user).id == newest.id


def test_latest_for_user_ignores_another_users_digest(db_session):
    mine, theirs = make_user(db_session, "u1"), make_user(db_session, "u2")
    own = make_digest(db_session, mine, T0)
    make_digest(db_session, theirs, T0 + timedelta(days=7))
    db_session.commit()
    assert dg.latest_for_user(db_session, user=mine).id == own.id
    assert dg.latest_for_user(db_session, user=make_user(db_session, "u3")) is None


def test_latest_for_user_eager_loads_items_and_jobs_in_order(db_session):
    user = make_user(db_session, "u1")
    d = make_digest(db_session, user, T0)
    jobs = []
    for i in range(2):
        job = Job(
            source="greenhouse", source_id=str(i), company="Acme", title=f"T{i}",
            apply_url="https://x", source_url="https://x",
        )
        db_session.add(job)
        jobs.append(job)
    db_session.flush()
    for rank, job in enumerate(reversed(jobs)):
        db_session.add(DigestItem(digest_id=d.id, job_id=job.id, rank=rank, track="strong_fit"))
    db_session.commit()
    got = dg.latest_for_user(db_session, user=user)
    db_session.expunge_all()  # any lazy load from here would raise
    assert [(i.rank, i.job.title) for i in got.items] == [(0, "T1"), (1, "T0")]


def test_items_are_ordered_by_track_before_rank(db_session):
    user = make_user(db_session, "u1")
    d = make_digest(db_session, user, T0)
    jobs = []
    for i in range(2):
        job = Job(
            source="greenhouse", source_id=str(i), company="Acme", title=f"T{i}",
            apply_url="https://x", source_url="https://x",
        )
        db_session.add(job)
        jobs.append(job)
    db_session.flush()
    # By rank alone strong_fit (0) would lead; by (track, rank) new_this_week does.
    for job, track, rank in (
        (jobs[0], "strong_fit", 0), (jobs[0], "new_this_week", 1), (jobs[1], "strong_fit", 2),
    ):
        db_session.add(DigestItem(digest_id=d.id, job_id=job.id, rank=rank, track=track))
    db_session.commit()
    got = dg.latest_for_user(db_session, user=user)
    assert [(i.track, i.rank) for i in got.items] == [
        ("new_this_week", 1), ("strong_fit", 0), ("strong_fit", 2),
    ]


def test_mark_opened_stamps_once_and_is_idempotent(db_session):
    user = make_user(db_session, "u1")
    d = make_digest(db_session, user, T0)
    db_session.commit()
    assert d.opened_at is None
    first = dg.mark_opened(db_session, digest=d).opened_at
    assert first is not None
    second = dg.mark_opened(db_session, digest=d).opened_at
    assert second == first
