"""The tailor run store: lifecycle, staleness, supersession, snapshot.

The runner is covered in tests/test_tailor_runner.py and the HTTP surface in
tests/test_tailor_run_api.py; nothing here touches either.
"""

from datetime import timedelta

import pytest

from app.models import Application, GuestSession, Job, Resume, TailorRun, _utcnow
from app.services.chat_messages import persist_assistant_message
from app.services.tailor_run import (
    STALE_AFTER_SECONDS,
    advance_phase,
    TailorRunAlreadyRunning,
    TailorRunSuperseded,
    fail,
    finish,
    is_stale,
    latest_run,
    set_phase,
    snapshot,
    start_run,
    touch,
)

from tests.test_jobs_api import _add_job


def _user(db_session, sub="sub-tailor"):
    from app.models import User

    user = User(google_sub=sub, email=f"{sub}@example.com", locale="en")
    db_session.add(user)
    db_session.commit()
    return user


def _master(db_session, user=None, locale="zh-TW") -> Resume:
    guest_id = None
    if user is None:
        guest = GuestSession(key_hash=f"h-{_utcnow().timestamp()}", locale=locale)
        db_session.add(guest)
        db_session.commit()
        guest_id = guest.id
    resume = Resume(
        user_id=user.id if user else None,
        guest_session_id=guest_id,
        kind="master",
        title="M",
        source="create",
        locale=locale,
        typst_source="= CV",
    )
    db_session.add(resume)
    db_session.commit()
    return resume


def _tailor(db_session, master: Resume, **kw) -> Resume:
    resume = Resume(
        user_id=master.user_id,
        guest_session_id=master.guest_session_id,
        kind="tailor",
        parent_resume_id=master.id,
        title=kw.get("title", "T"),
        source="create",
        locale=master.locale,
        typst_source=kw.get("typst_source", "= CV"),
        target_label=kw.get("target_label"),
        target_context=kw.get("target_context"),
    )
    db_session.add(resume)
    db_session.commit()
    return resume


# --- lifecycle --------------------------------------------------------------


def test_a_new_run_starts_running_in_the_first_phase(client, db_session):
    tailor = _tailor(db_session, _master(db_session))
    run = start_run(db_session, tailor, job_id=None)
    assert run.status == "running"
    assert run.phase == "read_resume"
    assert run.version == 0
    assert latest_run(db_session, tailor.id).id == run.id


def test_a_second_run_while_one_is_live_is_refused(client, db_session):
    tailor = _tailor(db_session, _master(db_session))
    start_run(db_session, tailor, job_id=None)
    with pytest.raises(TailorRunAlreadyRunning):
        start_run(db_session, tailor, job_id=None)


def test_two_resumes_can_each_have_their_own_live_run(client, db_session):
    """The uniqueness is per resume, not global -- the index is partial on
    resume_id, and a global one would serialise the whole product."""
    master = _master(db_session)
    a = _tailor(db_session, master, title="A")
    b = _tailor(db_session, master, title="B")
    assert start_run(db_session, a, job_id=None).status == "running"
    assert start_run(db_session, b, job_id=None).status == "running"


def test_a_finished_run_lets_the_next_one_start(client, db_session):
    tailor = _tailor(db_session, _master(db_session))
    first = start_run(db_session, tailor, job_id=None)
    finish(db_session, first)
    assert first.status == "done"
    # finish KEEPS the phase: on a terminal row it is the furthest stage the
    # pass reached, which is what lets the card draw the steps past it as
    # skipped instead of claiming all four.
    assert first.phase == "read_resume"
    second = start_run(db_session, tailor, job_id=None)
    assert second.id != first.id


def test_finish_is_idempotent_and_does_not_revive_a_failed_run(client, db_session):
    tailor = _tailor(db_session, _master(db_session))
    run = start_run(db_session, tailor, job_id=None)
    fail(db_session, run, "boom")
    finish(db_session, run)
    assert run.status == "failed"
    assert run.error == "boom"


def test_fail_records_the_reason_and_is_idempotent(client, db_session):
    tailor = _tailor(db_session, _master(db_session))
    run = start_run(db_session, tailor, job_id=None)
    fail(db_session, run, "first")
    fail(db_session, run, "second")
    assert run.status == "failed"
    assert run.error == "first"


# --- the reconnection contract ---------------------------------------------


def test_every_mutation_moves_the_version(client, db_session):
    """`version` is what a reconnecting client re-renders on. A mutation that
    forgot to bump it would freeze the card while the pass kept going."""
    tailor = _tailor(db_session, _master(db_session))
    run = start_run(db_session, tailor, job_id=None)
    seen = [run.version]
    touch(db_session, run)
    seen.append(run.version)
    set_phase(db_session, run, "mapping")
    seen.append(run.version)
    finish(db_session, run)
    seen.append(run.version)
    assert seen == sorted(set(seen)), seen


def test_set_phase_on_an_unchanged_phase_does_not_churn_the_version(client, db_session):
    tailor = _tailor(db_session, _master(db_session))
    run = start_run(db_session, tailor, job_id=None)
    set_phase(db_session, run, "mapping")
    before = run.version
    set_phase(db_session, run, "mapping")
    assert run.version == before


def test_advance_phase_climbs_through_the_four_stages(client, db_session):
    tailor = _tailor(db_session, _master(db_session))
    run = start_run(db_session, tailor, job_id=None)
    for phase in ("read_job", "mapping", "proposing"):
        advance_phase(db_session, run, phase)
        assert run.phase == phase


def test_advance_phase_refuses_to_go_backwards(client, db_session):
    """The model picks the order the stages are entered in, not this module.
    A second resume read after proposing, or proposing before mapping, would
    otherwise untick steps the person watched complete -- the card's claim is
    "it got at least this far"."""
    tailor = _tailor(db_session, _master(db_session))
    run = start_run(db_session, tailor, job_id=None)
    advance_phase(db_session, run, "proposing")
    before = run.version
    advance_phase(db_session, run, "read_resume")
    advance_phase(db_session, run, "mapping")
    assert run.phase == "proposing"
    # And it costs nothing: a refused move must not churn the version either,
    # or every repeated read wakes every client up for no change.
    assert run.version == before


def test_advance_phase_ranks_the_legacy_names_too(client, db_session):
    """A pass can outlive a deploy -- only finish/fail clear `phase`, and a
    stale run keeps its last one forever -- so the old vocabulary still has
    to sort rather than read as "unknown"."""
    tailor = _tailor(db_session, _master(db_session))
    run = start_run(db_session, tailor, job_id=None)
    # Written straight onto the row, because that is the only way it happens:
    # the runner in the *old* process writes it with the old code, and
    # `set_phase` here deliberately refuses to -- nothing on this side should
    # ever produce a legacy name, only rank one it finds.
    run.phase = "assessing"
    db_session.commit()
    advance_phase(db_session, run, "read_job")
    assert run.phase == "assessing"
    advance_phase(db_session, run, "proposing")
    assert run.phase == "proposing"


def test_advance_phase_ignores_a_name_it_does_not_know(client, db_session):
    tailor = _tailor(db_session, _master(db_session))
    run = start_run(db_session, tailor, job_id=None)
    advance_phase(db_session, run, "nonsense")
    assert run.phase == "read_resume"


def test_an_unknown_phase_is_ignored_rather_than_raised(client, db_session):
    """The phases are ours, not the model's. A name this module does not know
    means it and its caller drifted -- a reason to log, not to fail a pass the
    person is watching."""
    tailor = _tailor(db_session, _master(db_session))
    run = start_run(db_session, tailor, job_id=None)
    set_phase(db_session, run, "nonsense")
    assert run.phase == "read_resume"


# --- staleness and supersession --------------------------------------------


def test_a_fresh_run_is_not_stale(client, db_session):
    tailor = _tailor(db_session, _master(db_session))
    run = start_run(db_session, tailor, job_id=None)
    assert is_stale(run) is False


def test_a_run_whose_heartbeat_stopped_is_stale(client, db_session):
    tailor = _tailor(db_session, _master(db_session))
    run = start_run(db_session, tailor, job_id=None)
    run.heartbeat_at = _utcnow() - timedelta(seconds=STALE_AFTER_SECONDS + 1)
    db_session.commit()
    assert is_stale(run) is True


def test_a_finished_run_is_never_stale_however_old(client, db_session):
    tailor = _tailor(db_session, _master(db_session))
    run = start_run(db_session, tailor, job_id=None)
    finish(db_session, run)
    run.heartbeat_at = _utcnow() - timedelta(days=30)
    db_session.commit()
    assert is_stale(run) is False


def test_a_stale_run_is_superseded_rather_than_blocking_forever(client, db_session):
    """Nothing reaps a run orphaned by an API restart, so without this a
    resume would be permanently unable to start another pass."""
    tailor = _tailor(db_session, _master(db_session))
    dead = start_run(db_session, tailor, job_id=None)
    dead.heartbeat_at = _utcnow() - timedelta(seconds=STALE_AFTER_SECONDS + 1)
    db_session.commit()
    fresh = start_run(db_session, tailor, job_id=None)
    db_session.refresh(dead)
    assert fresh.id != dead.id
    assert fresh.status == "running"
    assert dead.status == "failed"
    assert dead.error == "superseded"


def test_the_superseded_runner_is_stopped_by_its_next_touch(client, db_session):
    """The only mechanism that works across two uvicorn workers: the old
    runner's task is in the other process and cannot be cancelled, so it has
    to find out on its next event."""
    tailor = _tailor(db_session, _master(db_session))
    dead = start_run(db_session, tailor, job_id=None)
    dead.heartbeat_at = _utcnow() - timedelta(seconds=STALE_AFTER_SECONDS + 1)
    db_session.commit()
    start_run(db_session, tailor, job_id=None)
    with pytest.raises(TailorRunSuperseded):
        touch(db_session, dead)


def test_a_superseded_runner_cannot_change_the_phase_either(client, db_session):
    tailor = _tailor(db_session, _master(db_session))
    dead = start_run(db_session, tailor, job_id=None)
    dead.heartbeat_at = _utcnow() - timedelta(seconds=STALE_AFTER_SECONDS + 1)
    db_session.commit()
    start_run(db_session, tailor, job_id=None)
    with pytest.raises(TailorRunSuperseded):
        set_phase(db_session, dead, "mapping")


# --- snapshot ---------------------------------------------------------------


def test_no_run_snapshots_to_none(client, db_session):
    tailor = _tailor(db_session, _master(db_session))
    assert snapshot(db_session, tailor.id) is None


def test_the_snapshot_carries_the_posting_inline(client, db_session):
    """So the card can draw the employer mark and the job line from the first
    frame, with no second request."""
    user = _user(db_session)
    tailor = _tailor(db_session, _master(db_session, user))
    job = _add_job(db_session, source_id="s:1", company="Stripe", title="Payments Engineer")
    run = start_run(db_session, tailor, job_id=job.id)
    data = snapshot(db_session, tailor.id)
    assert data["run_id"] == run.id
    assert data["status"] == "running"
    assert data["phase"] == "read_resume"
    assert data["job_id"] == job.id
    assert data["job"]["company"] == "Stripe"
    assert data["job"]["title"] == "Payments Engineer"
    assert data["job"]["is_active"] is True
    assert data["stale"] is False
    assert data["source_hash"]


def test_a_run_with_no_posting_snapshots_a_null_job(client, db_session):
    tailor = _tailor(db_session, _master(db_session), target_label="PM @ Beta")
    start_run(db_session, tailor, job_id=None)
    data = snapshot(db_session, tailor.id)
    assert data["job_id"] is None
    assert data["job"] is None


def test_the_message_count_moves_only_when_a_chat_row_is_written(client, db_session):
    """The "re-read the transcript" signal, and why it is not `version`.

    `touch()` is called on every event the turn yields, including one `delta`
    per streamed chunk, so `version` moves hundreds of times per pass while
    the transcript does not move at all. A client keyed on `version` re-reads
    the whole transcript on every 0.4s frame; keyed on this it re-reads only
    when the runner actually wrote a row.
    """
    tailor = _tailor(db_session, _master(db_session))
    run = start_run(db_session, tailor, job_id=None)
    assert snapshot(db_session, tailor.id)["message_count"] == 0

    touch(db_session, run)
    touch(db_session, run)
    assert run.version > 0
    assert snapshot(db_session, tailor.id)["message_count"] == 0

    persist_assistant_message(db_session, tailor.id, "Here is what I found.")
    assert snapshot(db_session, tailor.id)["message_count"] == 1


def test_the_message_count_is_this_resume_only(client, db_session):
    """A sibling tailor's conversation must not make this card re-read."""
    master = _master(db_session)
    tailor = _tailor(db_session, master)
    other = _tailor(db_session, master)
    start_run(db_session, tailor, job_id=None)
    persist_assistant_message(db_session, other.id, "Not this resume's turn.")
    assert snapshot(db_session, tailor.id)["message_count"] == 0


def test_the_source_hash_moves_when_the_resume_source_does(client, db_session):
    """The editor repaints its preview on this, not on `version` -- the two
    are different events and `version` moves for reasons the preview must
    ignore."""
    tailor = _tailor(db_session, _master(db_session))
    start_run(db_session, tailor, job_id=None)
    before = snapshot(db_session, tailor.id)["source_hash"]
    tailor.typst_source = "= CV\n\nNew section"
    db_session.commit()
    assert snapshot(db_session, tailor.id)["source_hash"] != before


def test_the_snapshot_reports_the_latest_run_not_the_first(client, db_session):
    tailor = _tailor(db_session, _master(db_session))
    first = start_run(db_session, tailor, job_id=None)
    finish(db_session, first)
    second = start_run(db_session, tailor, job_id=None)
    assert snapshot(db_session, tailor.id)["run_id"] == second.id


def test_deleting_the_posting_takes_its_runs_with_it(client, db_session):
    """job_id is ON DELETE CASCADE, matching applications.job_id. Foreign keys
    are enforced here, so a run pointing at a posting that does not exist is
    not a state the database can be put into -- which is why `snapshot`'s
    null-job branch is reached by `job_id IS NULL` (the editor-dialog tailor)
    and not by a dangling reference."""
    tailor = _tailor(db_session, _master(db_session))
    job = _add_job(db_session, source_id="s:1")
    run = start_run(db_session, tailor, job_id=job.id)
    run_id = run.id
    assert db_session.get(TailorRun, run_id) is not None
    db_session.delete(db_session.get(Job, job.id))
    db_session.commit()
    db_session.expire_all()
    assert db_session.get(TailorRun, run_id) is None
    assert snapshot(db_session, tailor.id) is None


def test_a_stale_run_says_so_in_the_snapshot(client, db_session):
    """The client's cue to offer the pass again."""
    tailor = _tailor(db_session, _master(db_session))
    run = start_run(db_session, tailor, job_id=None)
    run.heartbeat_at = _utcnow() - timedelta(seconds=STALE_AFTER_SECONDS + 1)
    db_session.commit()
    data = snapshot(db_session, tailor.id)
    assert data["status"] == "running"
    assert data["stale"] is True


def test_the_job_link_survives_a_tailor_created_from_an_application(client, db_session):
    """job_id_for_tailor reads applications, which is where link_tailor puts
    the linkage when someone tailors from a posting."""
    from app.services.tailor_runner import job_id_for_tailor

    user = _user(db_session)
    tailor = _tailor(db_session, _master(db_session, user))
    job = _add_job(db_session, source_id="s:1")
    db_session.add(
        Application(
            user_id=user.id,
            job_id=job.id,
            tailor_resume_id=tailor.id,
            status="tailored",
            source="search",
            last_status_at=_utcnow(),
        )
    )
    db_session.commit()
    assert job_id_for_tailor(db_session, tailor) == job.id


def test_a_guest_tailor_has_no_application_and_that_is_not_an_error(client, db_session):
    """applications.user_id is NOT NULL, so a guest has nowhere to hold the
    linkage -- None is the ordinary answer, not a failure."""
    from app.services.tailor_runner import job_id_for_tailor

    tailor = _tailor(db_session, _master(db_session))
    assert tailor.user_id is None
    assert job_id_for_tailor(db_session, tailor) is None


def test_the_stale_threshold_sits_above_the_runners_read_budget(client, db_session):
    """The regression that cost the import feature real data: at 60s a slow
    first token was read as a dead runner, the person retried, and two agents
    wrote the same resume from two sessions."""
    from app.services.tailor_runner import TIMEOUT_SECONDS

    assert STALE_AFTER_SECONDS > TIMEOUT_SECONDS


# --- the role card names the posting ---------------------------------------
#
# These pin the defect this feature fixes. The "Tailor for this role" button
# creates the tailor with {title, job_id} and nothing else, so target_label
# and target_context are both NULL -- and the old kickstart, which built its
# prompt from those two fields in the browser, told the model "Target role:
# Not specified. No job description was provided" for the one path that always
# has a posting. The linkage was in `applications` the whole time.


def test_the_role_card_names_the_posting_for_a_job_created_tailor(client, db_session):
    from app.services.role_card import build_role_card

    user = _user(db_session)
    tailor = _tailor(db_session, _master(db_session, user))
    job = _add_job(db_session, source_id="s:1")
    db_session.add(
        Application(
            user_id=user.id,
            job_id=job.id,
            tailor_resume_id=tailor.id,
            status="tailored",
            source="search",
            last_status_at=_utcnow(),
        )
    )
    db_session.commit()

    card = build_role_card(db_session, tailor)
    assert job.id in card
    assert "read_job" in card


def test_the_role_card_carries_the_id_not_a_copy_of_the_description(client, db_session):
    """By id, because read_job fetches it on demand -- a description pasted
    into the card is re-sent on every turn for as long as the conversation
    lasts."""
    from app.services.role_card import build_role_card

    user = _user(db_session)
    tailor = _tailor(db_session, _master(db_session, user))
    job = _add_job(
        db_session,
        source_id="s:1",
        description_text="SECRET-MARKER you will build payment rails",
    )
    db_session.add(
        Application(
            user_id=user.id,
            job_id=job.id,
            tailor_resume_id=tailor.id,
            status="tailored",
            source="search",
            last_status_at=_utcnow(),
        )
    )
    db_session.commit()

    assert "SECRET-MARKER" not in build_role_card(db_session, tailor)


def test_an_editor_dialog_tailor_still_gets_its_pasted_description(client, db_session):
    """"Read both, posting first": with no applications row the card falls back
    to the target_context the dialog collected."""
    from app.services.role_card import build_role_card

    tailor = _tailor(
        db_session,
        _master(db_session),
        target_label="PM @ Beta",
        target_context="Own the roadmap",
    )
    card = build_role_card(db_session, tailor)
    assert "Own the roadmap" in card
    # No posting line. ("read_job" itself appears in every card -- it is part
    # of the general catalogue-tool guidance -- so the claim is specifically
    # that no posting was attributed to this tailor.)
    assert "This tailor is for Offerfy posting" not in card
