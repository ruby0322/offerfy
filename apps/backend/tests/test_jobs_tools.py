"""The editor agent's catalogue and personal-context tools, unit level.

No HTTP: the handlers are called the way the agent loop calls them, with a
dict of arguments, and asserted on the JSON string they return. The loop's own
wiring is covered by tests/test_chat_jobs.py.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.models import CompanyBoilerplate, GuestSession, JobFacets, Resume, User
from app.services.jobs.boilerplate import (
    normalize_company,
    sentence_hash,
    split_sentences,
)
from app.services.jobs.digest import UNRANKED_TRACK
from app.services.jobs.store import freshness_cutoff
from app.services.jobs_tools import (
    READ_JOB_PROFILE_TOOL,
    READ_JOB_TOOL,
    SEARCH_JOBS_TOOL,
    SEARCH_LIMIT_MAX,
    jobs_tool_handlers,
    jobs_tool_specs,
)

from tests.test_dashboard_digest import _digest, _item
from tests.test_jobs_api import _add_job

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)


def _user(db_session, sub="sub-tools") -> User:
    user = User(google_sub=sub, email=f"{sub}@example.com", locale="en")
    db_session.add(user)
    db_session.commit()
    return user


_guests = iter(range(1, 10_000))


def _resume(db_session, user=None) -> Resume:
    """A master resume owned by `user`, or by a guest session when None.

    resume_owner_xor means the guest case needs a real GuestSession row --
    `user_id IS NULL` alone will not insert.
    """
    guest_id = None
    if user is None:
        guest = GuestSession(key_hash=f"hash-{next(_guests):064d}", locale="en")
        db_session.add(guest)
        db_session.commit()
        guest_id = guest.id
    resume = Resume(
        user_id=user.id if user else None,
        guest_session_id=guest_id,
        kind="master",
        title="M",
        source="create",
        locale="en",
        typst_source="= CV",
    )
    db_session.add(resume)
    db_session.commit()
    return resume


def _handlers(db_session, *, user=None):
    resume = _resume(db_session, user)
    return resume, jobs_tool_handlers(db_session, resume, user)


def _call(handlers, name, args=None) -> dict:
    return json.loads(handlers[name](args or {}))


def _fresh(db_session, **kwargs):
    """A posting inside the freshness window, with facets unless told otherwise."""
    facets = kwargs.pop("facets", True)
    kwargs.setdefault("last_seen_at", freshness_cutoff() + timedelta(days=1))
    job = _add_job(db_session, **kwargs)
    if facets:
        db_session.add(
            JobFacets(
                job_id=job.id,
                country=facets if isinstance(facets, str) else "TW",
                region=None,
                city=None,
                seniority="mid",
                function="backend",
                description_hash="h",
            )
        )
        db_session.commit()
    return job


# --- the spec surface -------------------------------------------------------


def test_a_guest_is_offered_the_catalogue_but_not_the_personal_tool(client, db_session):
    resume = _resume(db_session)
    names = {spec["name"] for spec in jobs_tool_specs(resume)}
    assert names == {SEARCH_JOBS_TOOL, READ_JOB_TOOL}


def test_a_signed_in_person_is_offered_all_three(client, db_session):
    resume = _resume(db_session, _user(db_session))
    names = {spec["name"] for spec in jobs_tool_specs(resume)}
    assert names == {SEARCH_JOBS_TOOL, READ_JOB_TOOL, READ_JOB_PROFILE_TOOL}


def test_the_enums_come_from_the_taxonomy_not_a_second_copy(client, db_session):
    """A hardcoded list here would drift from the validator and 422 forever."""
    from app.services.jobs.enrich import (
        default_taxonomy,
        valid_countries,
        valid_functions,
        valid_seniorities,
    )

    spec = next(s for s in jobs_tool_specs(_resume(db_session)) if s["name"] == SEARCH_JOBS_TOOL)
    props = spec["parameters"]["properties"]
    taxonomy = default_taxonomy()
    assert set(props["function"]["enum"]) == valid_functions(taxonomy)
    assert set(props["seniority"]["enum"]) == valid_seniorities(taxonomy)
    assert set(props["country"]["enum"]) == valid_countries(taxonomy)


def test_the_search_description_warns_that_q_misses_descriptions(client, db_session):
    """The one thing a model cannot discover by trying: q is title/company/
    location only, so q='kubernetes' returns nothing from a catalogue full of
    Kubernetes jobs, and a silent zero reads as 'no such job exists'."""
    spec = next(s for s in jobs_tool_specs(_resume(db_session)) if s["name"] == SEARCH_JOBS_TOOL)
    description = spec["description"]
    assert "does NOT search the description" in description
    assert "no such job" in description


# --- search_jobs ------------------------------------------------------------


def test_search_returns_rows_with_a_job_id_and_an_apply_url(client, db_session):
    _fresh(db_session, source_id="s:1", title="Payments Engineer", company="Stripe")
    _, handlers = _handlers(db_session)
    body = _call(handlers, SEARCH_JOBS_TOOL, {"q": "Payments"})
    assert body["total"] == 1
    assert body["returned"] == 1
    row = body["items"][0]
    assert row["title"] == "Payments Engineer"
    assert row["company"] == "Stripe"
    assert row["apply_url"] == "https://example.com/apply"
    assert row["job_id"]
    assert row["function"] == "backend"


def test_search_reports_the_total_beyond_the_page(client, db_session):
    for n in range(4):
        _fresh(db_session, source_id=f"s:{n}", title=f"Engineer {n}")
    _, handlers = _handlers(db_session)
    body = _call(handlers, SEARCH_JOBS_TOOL, {"limit": 2})
    assert body["returned"] == 2
    assert body["total"] == 4


def test_search_caps_the_limit_rather_than_refusing_it(client, db_session):
    for n in range(SEARCH_LIMIT_MAX + 3):
        _fresh(db_session, source_id=f"s:{n}", title=f"Engineer {n}")
    _, handlers = _handlers(db_session)
    body = _call(handlers, SEARCH_JOBS_TOOL, {"limit": 500})
    assert body["returned"] == SEARCH_LIMIT_MAX
    assert body["total"] == SEARCH_LIMIT_MAX + 3


def test_a_bad_enum_is_an_error_the_model_can_read_not_an_exception(client, db_session):
    _, handlers = _handlers(db_session)
    assert _call(handlers, SEARCH_JOBS_TOOL, {"function": "vibes"}) == {
        "error": "Invalid function"
    }
    assert _call(handlers, SEARCH_JOBS_TOOL, {"seniority": "wizard"}) == {
        "error": "Invalid seniority"
    }
    assert _call(handlers, SEARCH_JOBS_TOOL, {"country": "NOTACOUNTRY"}) == {
        "error": "Invalid country"
    }


def test_search_hides_a_posting_that_is_active_but_past_the_freshness_window(
    client, db_session
):
    """`is_active` alone is not a claim that a role is open -- it only flips
    when an ingest run sees the posting gone, so a stalled ingest leaves every
    row active forever (AGENTS.md section 8). The agent tells people a role is
    open, so it reads the stricter window the sitemap and the per-user
    prefilter already read."""
    _fresh(db_session, source_id="s:fresh", title="Open Role")
    _add_job(
        db_session,
        source_id="s:stale",
        title="Stalled Role",
        is_active=True,
        last_seen_at=freshness_cutoff() - timedelta(days=1),
    )
    _, handlers = _handlers(db_session)
    body = _call(handlers, SEARCH_JOBS_TOOL)
    assert [row["title"] for row in body["items"]] == ["Open Role"]


def test_search_carries_when_the_catalogue_was_last_confirmed(client, db_session):
    job = _fresh(db_session, source_id="s:1")
    _, handlers = _handlers(db_session)
    body = _call(handlers, SEARCH_JOBS_TOOL)
    assert body["catalog_refreshed_at"].startswith(job.last_seen_at.date().isoformat())


def test_search_works_for_a_guest(client, db_session):
    _fresh(db_session, source_id="s:1", title="Open Role")
    _, handlers = _handlers(db_session, user=None)
    assert _call(handlers, SEARCH_JOBS_TOOL)["total"] == 1


# --- read_job ---------------------------------------------------------------


def test_read_job_returns_the_description_with_boilerplate_removed(client, db_session):
    blurb = "We are a mission driven company."
    job = _fresh(
        db_session,
        source_id="s:1",
        company="Acme",
        description_text=f"{blurb} You will build payment rails.",
    )
    db_session.add(
        CompanyBoilerplate(
            # Stored under the normalized key, as recompute_boilerplate does.
            company=normalize_company("Acme"),
            # sentence_hash expects an already-split, casefolded sentence;
            # hashing the raw string hashes something that never matches.
            sentence_hashes=json.dumps(
                [sentence_hash(s) for s in split_sentences(blurb)]
            ),
            posting_count=40,
            computed_at=NOW,
        )
    )
    db_session.commit()
    _, handlers = _handlers(db_session)
    body = _call(handlers, READ_JOB_TOOL, {"job_id": job.id})
    assert "payment rails" in body["description"]
    assert "mission driven" not in body["description"]
    assert body["description_truncated"] is False


def test_read_job_reports_a_closed_posting_instead_of_hiding_it(client, db_session):
    """A digest item or a saved application can point at a posting that has
    since closed. Filtering it out would leave the agent unable to answer
    about it at all, so it comes back flagged."""
    job = _add_job(
        db_session,
        source_id="s:gone",
        title="Closed Role",
        is_active=False,
        last_seen_at=freshness_cutoff() - timedelta(days=2),
    )
    _, handlers = _handlers(db_session)
    body = _call(handlers, READ_JOB_TOOL, {"job_id": job.id})
    assert body["title"] == "Closed Role"
    assert body["is_active"] is False
    assert body["stale"] is True


def test_read_job_marks_a_live_posting_as_neither_closed_nor_stale(client, db_session):
    job = _fresh(db_session, source_id="s:1")
    _, handlers = _handlers(db_session)
    body = _call(handlers, READ_JOB_TOOL, {"job_id": job.id})
    assert body["is_active"] is True
    assert body["stale"] is False


def test_read_job_truncates_a_very_long_description_and_says_so(client, db_session):
    job = _fresh(db_session, source_id="s:1", description_text="x" * 20000)
    _, handlers = _handlers(db_session)
    body = _call(handlers, READ_JOB_TOOL, {"job_id": job.id})
    assert body["description_truncated"] is True
    assert len(body["description"]) < 20000


def test_read_job_on_an_unknown_id_says_so_and_points_at_search(client, db_session):
    _, handlers = _handlers(db_session)
    body = _call(handlers, READ_JOB_TOOL, {"job_id": "no-such-job"})
    assert "search_jobs" in body["error"]


def test_read_job_without_an_id_is_an_error_not_a_crash(client, db_session):
    _, handlers = _handlers(db_session)
    assert "error" in _call(handlers, READ_JOB_TOOL, {})


# --- read_my_job_profile ----------------------------------------------------


def test_a_guest_asking_for_their_profile_is_told_to_sign_in(client, db_session):
    _, handlers = _handlers(db_session, user=None)
    body = _call(handlers, READ_JOB_PROFILE_TOOL)
    assert body["requires_login"] is True
    assert "search_jobs" in body["note"]


def test_a_brand_new_person_gets_every_section_empty_but_present(client, db_session):
    user = _user(db_session)
    _, handlers = _handlers(db_session, user=user)
    body = _call(handlers, READ_JOB_PROFILE_TOOL)
    assert set(body) == {
        "preference",
        "match_count",
        "matches",
        "applications",
        "resume_health",
        "digest",
    }
    assert body["preference"]["target_functions"] == []
    assert body["preference"]["saved_by_user"] is False
    assert body["preference"]["facts_state"] == "missing"
    assert body["match_count"] == 0
    assert body["matches"] == []
    assert body["applications"] == []
    # has_master is True because the editor chat always has a resume open --
    # the one this tool is being called from. "Brand new" here means no
    # preferences, no applications and no digest, not no resume.
    assert body["resume_health"]["has_master"] is True
    assert body["resume_health"]["published"] is False


def test_no_digest_is_reported_as_none_with_a_note_not_as_an_empty_digest(
    client, db_session
):
    """The digest timer is installed but deliberately not enabled, so this is
    the common answer. A null here would read to a model as 'their digest is
    empty', which is a different and false statement."""
    user = _user(db_session)
    _, handlers = _handlers(db_session, user=user)
    digest = _call(handlers, READ_JOB_PROFILE_TOOL)["digest"]
    assert digest["status"] == "none"
    assert "do not say their digest is empty" in digest["note"]


def test_a_digest_arrives_with_its_tracks_tiers_and_prose(client, db_session):
    user = _user(db_session)
    job = _fresh(db_session, source_id="s:1", title="Payments Engineer")
    row = _digest(db_session, user)
    _item(db_session, row, job, track="strong_fit", rank=1)
    _, handlers = _handlers(db_session, user=user)
    digest = _call(handlers, READ_JOB_PROFILE_TOOL)["digest"]
    assert digest["status"] == "full"
    assert digest["candidate_count"] == 12
    item = digest["items"][0]
    assert item["title"] == "Payments Engineer"
    assert item["track"] == "strong_fit"
    assert item["tier"] == "Strong fit"
    assert item["fit_score"] == pytest.approx(0.9)
    assert item["reason"] == "Because reasons."
    assert item["gap"] == "A gap."


def test_a_degraded_digests_items_keep_their_unranked_track(client, db_session):
    user = _user(db_session)
    job = _fresh(db_session, source_id="s:1")
    row = _digest(db_session, user, status="degraded")
    _item(db_session, row, job, track=UNRANKED_TRACK, tier=None, fit_score=None)
    _, handlers = _handlers(db_session, user=user)
    digest = _call(handlers, READ_JOB_PROFILE_TOOL)["digest"]
    assert digest["status"] == "degraded"
    assert digest["items"][0]["track"] == UNRANKED_TRACK
    assert digest["items"][0]["tier"] is None


def test_reading_the_profile_does_not_mark_the_digest_opened(client, db_session):
    """GET /v1/dashboard stamps opened_at, and the digest build's eligibility
    gate reads that stamp. An agent setting it would forge the signal: the
    person never saw anything."""
    user = _user(db_session)
    job = _fresh(db_session, source_id="s:1")
    row = _digest(db_session, user)
    _item(db_session, row, job)
    _, handlers = _handlers(db_session, user=user)
    _call(handlers, READ_JOB_PROFILE_TOOL)
    db_session.expire_all()
    assert db_session.get(type(row), row.id).opened_at is None


def test_the_profile_carries_the_matches_the_preference_selects(client, db_session):
    from app.models import JobPreference

    user = _user(db_session)
    _fresh(db_session, source_id="s:1", title="Backend Engineer")
    db_session.add(
        JobPreference(
            user_id=user.id,
            target_functions="|backend|",
            target_locations="|TW|",
            target_seniority="mid",
            remote_required=None,
            activity="actively_looking",
        )
    )
    db_session.commit()
    _, handlers = _handlers(db_session, user=user)
    body = _call(handlers, READ_JOB_PROFILE_TOOL)
    assert body["preference"]["target_functions"] == ["backend"]
    assert body["preference"]["saved_by_user"] is True
    assert body["match_count"] == 1
    assert [row["title"] for row in body["matches"]] == ["Backend Engineer"]


def test_the_profile_carries_what_the_person_saved_or_applied_to(client, db_session):
    from app.services.jobs import applications as service

    user = _user(db_session)
    job = _fresh(db_session, source_id="s:1", title="Saved Role")
    service.save(db_session, user=user, job_id=job.id)
    _, handlers = _handlers(db_session, user=user)
    body = _call(handlers, READ_JOB_PROFILE_TOOL)
    assert len(body["applications"]) == 1
    row = body["applications"][0]
    assert row["title"] == "Saved Role"
    assert row["status"] == "saved"
    assert row["job_is_active"] is True


def test_another_persons_data_is_never_returned(client, db_session):
    from app.services.jobs import applications as service

    mine = _user(db_session, sub="sub-mine")
    theirs = _user(db_session, sub="sub-theirs")
    job = _fresh(db_session, source_id="s:1", title="Theirs")
    service.save(db_session, user=theirs, job_id=job.id)
    _digest(db_session, theirs)
    _, handlers = _handlers(db_session, user=mine)
    body = _call(handlers, READ_JOB_PROFILE_TOOL)
    assert body["applications"] == []
    assert body["digest"]["status"] == "none"
