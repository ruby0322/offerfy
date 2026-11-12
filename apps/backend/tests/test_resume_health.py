"""Resume health: one read-only answer about whether matching runs on the
current master resume."""

from app.models import Resume, ResumeFacts, User
from app.services.resume_facts import facts_are_stale, facts_for_user, facts_state
from app.services.resume_health import health
from app.services.revisions import pin_current_draft, set_draft_source


def _user(db, sub="sub-health"):
    user = User(google_sub=sub, email=f"{sub}@example.com", locale="en", name="Chen Wei")
    db.add(user)
    db.commit()
    return user


def _master(db, user, *, publish=True, source="= CV\nChen Wei"):
    resume = Resume(
        title="Master", typst_source=source, source="create", locale="en",
        user_id=user.id, kind="master",
    )
    db.add(resume)
    db.flush()
    set_draft_source(db, resume, source)
    if publish:
        pin_current_draft(db, resume)
    db.commit()
    return resume


def _facts(db, user, resume, revision_id):
    from app.services.resume_facts import bundle, input_hash

    found = bundle(db, user, fall_back=True)
    h = input_hash(found[1], found[2], found[3]) if found else "h" * 64
    row = ResumeFacts(
        user_id=user.id,
        source_resume_id=resume.id,
        source_revision_id=revision_id,
        input_hash=h,
        facts_json="{}",
    )
    db.add(row)
    db.commit()
    return row


def test_no_master_resume_is_not_an_error(db_session):
    h = health(db_session, user=_user(db_session))
    assert h.has_master is False
    assert h.published is False
    assert h.needs_rebuild is False


def test_master_without_a_published_revision(db_session):
    user = _user(db_session)
    _master(db_session, user, publish=False)
    h = health(db_session, user=user)
    assert h.has_master is True
    assert h.published is False
    assert h.matching_revision_current is False


def test_missing_facts_need_a_rebuild(db_session):
    user = _user(db_session)
    _master(db_session, user)
    h = health(db_session, user=user)
    assert (h.has_master, h.published, h.facts_state) == (True, True, "missing")
    assert h.matching_revision_current is False
    assert h.needs_rebuild is True


def test_facts_from_an_older_revision_are_not_current(db_session):
    user = _user(db_session)
    resume = _master(db_session, user)
    _facts(db_session, user, resume, resume.published_revision_id)
    set_draft_source(db_session, resume, resume.typst_source + "\n= More\n")
    pin_current_draft(db_session, resume)
    db_session.commit()
    h = health(db_session, user=user)
    assert h.facts_state == "stale"
    assert h.matching_revision_current is False and h.needs_rebuild is True


def test_fresh_facts_on_the_published_revision_are_healthy(db_session):
    user = _user(db_session)
    resume = _master(db_session, user)
    _facts(db_session, user, resume, resume.published_revision_id)
    h = health(db_session, user=user)
    assert h.facts_state == "fresh"
    assert h.matching_revision_current is True
    assert h.needs_rebuild is False


def test_a_fallback_master_counts_when_no_pointer_is_set(db_session):
    user = _user(db_session)
    resume = _master(db_session, user)
    assert user.profile_resume_id is None
    _facts(db_session, user, resume, resume.published_revision_id)
    h = health(db_session, user=user)
    assert h.has_master is True and h.needs_rebuild is False


def test_facts_state_matches_facts_are_stale(db_session):
    user = _user(db_session)
    resume = _master(db_session, user)
    assert health(db_session, user=user).facts_state == facts_state(db_session, user) == "missing"
    _facts(db_session, user, resume, resume.published_revision_id)
    facts = facts_for_user(db_session, user)
    assert facts_are_stale(db_session, user, facts) is False
    assert health(db_session, user=user).facts_state == "fresh"
    set_draft_source(db_session, resume, resume.typst_source + "\n= More\n")
    pin_current_draft(db_session, resume)
    db_session.commit()
    assert facts_are_stale(db_session, user, facts) is True
    assert health(db_session, user=user).facts_state == "stale"
