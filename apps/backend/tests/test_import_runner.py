"""The import runner: one agent turn over an uploaded resume, translated into
progress rows.

Fixtures mirror tests/test_import_tools.py: there is no shared `resume` or
`run` fixture in conftest.py, and the partial unique index on
`resume_imports (resume_id) WHERE status = 'running'` means every test that
needs a run reuses one run per resume rather than starting a second.
"""

from datetime import timedelta

import pytest

from app.models import (
    ChatMessage,
    GuestSession,
    LlmCall,
    Resume,
    ResumeImportStep,
    _utcnow,
)
from app.config import get_settings
from app.services import import_run, usage
from app.services.import_run import RENDER_CHECK_KEY, STALE_AFTER_SECONDS
from app.services.import_runner import RENDER_CHECK_FAILED, run_import
from app.services.import_tools import DECLARE_PLAN_TOOL
from app.services.llm.prompts import EDIT_TOOL
from app.services.typst_compile import typst_available
from tests.openai_script import ScriptedOpenAI, llm_message, llm_usage, tool_call

# ScriptedOpenAI only speaks the Responses API, so every scripted test
# names an OpenAI catalogue row explicitly rather than relying on
# whichever model model_choice.selected() would default to -- see
# tests/test_chat.py's OPENAI_ROW for the same reasoning.
OPENAI_ROW = "gpt-6.1-terra"

pytestmark = [pytest.mark.filterwarnings("ignore::DeprecationWarning"), pytest.mark.anyio]


@pytest.fixture
def resume(db_session):
    guest = GuestSession(key_hash="deadbeef-import-runner", locale="en")
    db_session.add(guest)
    db_session.commit()
    resume = Resume(
        title="R",
        typst_source="= R",
        source="upload",
        guest_session_id=guest.id,
        upload_s3_key="uploads/guest-1/resume-1/My Resume.txt",
    )
    db_session.add(resume)
    db_session.commit()
    return resume


@pytest.fixture
def run(db_session, resume):
    # One `running` import per resume at a time (partial unique index), so
    # every test in this module that needs a run reuses this single one.
    return import_run.start_run(db_session, resume)


def _steps(db_session, run) -> list[ResumeImportStep]:
    return (
        db_session.query(ResumeImportStep)
        .filter(ResumeImportStep.import_id == run.id)
        .order_by(ResumeImportStep.seq)
        .all()
    )


def _messages(db_session, resume_id) -> list[ChatMessage]:
    return (
        db_session.query(ChatMessage)
        .filter(ChatMessage.resume_id == resume_id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )


def _stub_upload(monkeypatch, data: bytes = b"Contact: a@b.com\nExperience: things") -> None:
    monkeypatch.setattr("app.services.import_runner.get_object", lambda key: data)


async def test_missing_upload_fails_cleanly(db_session, resume, run, monkeypatch):
    monkeypatch.setattr("app.services.import_runner.get_object", lambda key: None)

    await run_import(db_session, resume, run)

    db_session.refresh(run)
    db_session.refresh(resume)
    assert run.status == "failed"
    assert run.error
    assert resume.import_status == "failed"


async def test_scripted_import_walks_its_plan(db_session, resume, run, monkeypatch, openai_enabled):
    _stub_upload(monkeypatch)
    scripted = ScriptedOpenAI(
        [
            llm_message(
                tool_calls=[
                    tool_call(
                        "declare_import_plan",
                        {
                            "steps": [
                                {"key": "basics", "label": "Basics"},
                                {"key": "experience", "label": "Experience"},
                                {"key": "education", "label": "Education"},
                            ]
                        },
                        "plan-1",
                    )
                ]
            ),
            llm_message(tool_calls=[tool_call("read_typst", {}, "read-1")]),
            llm_message(
                tool_calls=[
                    tool_call(
                        "apply_typst_edit",
                        {"search": "R", "replace": "R\nBasics", "step": "basics"},
                        "edit-1",
                    )
                ]
            ),
            llm_message(
                tool_calls=[
                    tool_call(
                        "apply_typst_edit",
                        {"search": "Basics", "replace": "Basics\nExperience", "step": "experience"},
                        "edit-2",
                    )
                ]
            ),
            llm_message(
                tool_calls=[
                    tool_call(
                        "apply_typst_edit",
                        {"search": "Experience", "replace": "Experience\nEducation", "step": "education"},
                        "edit-3",
                    )
                ]
            ),
            llm_message(content="Imported your resume."),
        ]
    )
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted)

    await run_import(db_session, resume, run, model=OPENAI_ROW)

    db_session.refresh(run)
    assert run.status == "done"
    assert run.applied == 3
    # Plus the server-owned render-check step the gate always appends
    # (see test_render_check_passes_without_repair_when_clean, below).
    assert [s.status for s in _steps(db_session, run)] == ["done", "done", "done", "done"]
    assert [s.key for s in _steps(db_session, run)] == [
        "basics",
        "experience",
        "education",
        RENDER_CHECK_KEY,
    ]


async def test_import_writes_no_user_message(db_session, resume, run, monkeypatch, openai_enabled):
    _stub_upload(monkeypatch)
    scripted = ScriptedOpenAI(
        [
            llm_message(tool_calls=[tool_call("read_typst", {}, "read-1")]),
            llm_message(content="Imported."),
        ]
    )
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted)

    await run_import(db_session, resume, run, model=OPENAI_ROW)

    assert not [m for m in _messages(db_session, resume.id) if m.role == "user"]
    # The turn did run, though -- an assistant row and a tool row both landed.
    roles = [m.role for m in _messages(db_session, resume.id)]
    assert "assistant" in roles
    assert "tool" in roles


async def test_import_applies_sections_incrementally(db_session, resume, run, monkeypatch, openai_enabled):
    _stub_upload(monkeypatch)
    scripted = ScriptedOpenAI(
        [
            llm_message(
                tool_calls=[
                    tool_call(
                        "apply_typst_edit",
                        {"search": "R", "replace": "R\nBasics", "step": "basics"},
                        "edit-1",
                    )
                ]
            ),
            llm_message(
                tool_calls=[
                    tool_call(
                        "apply_typst_edit",
                        {"search": "Basics", "replace": "Basics\nExperience", "step": "experience"},
                        "edit-2",
                    )
                ]
            ),
            llm_message(content="Imported."),
        ]
    )
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted)

    await run_import(db_session, resume, run, model=OPENAI_ROW)

    db_session.refresh(run)
    # Two changed edits in one turn -> applied is 2, not 1. A runner that
    # counted once per *turn* rather than once per *changed edit* would be
    # stuck at 1 here regardless of how many sections were actually edited.
    assert run.applied == 2
    # Plus the server-owned render-check step the gate always appends.
    assert [s.status for s in _steps(db_session, run)] == ["done", "done", "done"]


async def test_model_error_marks_failed_not_running(db_session, resume, run, monkeypatch, openai_enabled):
    _stub_upload(monkeypatch)
    # truncate=True drops the response.completed event, so the adapter never
    # gets a usable round and raises -- the same path a dropped connection or
    # a provider 5xx takes mid-turn.
    scripted = ScriptedOpenAI([llm_message(content="never seen")], truncate=True)
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted)

    await run_import(db_session, resume, run, model=OPENAI_ROW)

    db_session.refresh(run)
    db_session.refresh(resume)
    assert run.status == "failed"
    assert run.status != "running"
    assert run.error
    assert resume.import_status == "failed"


async def test_failure_after_partial_progress_keeps_what_landed(
    db_session, resume, run, monkeypatch
):
    """The `failed` card's actual promise: "N sections were saved. Nothing
    you had was lost" -- not just "a failure is recorded as failed".

    `ScriptedOpenAI`'s `truncate` flag is constructor-level, applied to
    every round it serves, so it cannot express "two sections succeed, then
    the model fails" -- there is no per-round failure hook, and extending
    the shared scripted client for one test is a wider change than this
    needs (many other tests depend on its current shape). The behaviour
    under test lives entirely in `run_import`'s own event loop, not in the
    turn or the provider adapter, so the turn is faked directly at the name
    `import_runner.py` imports it under: a realistic prefix (a plan
    declaration, two labelled changed edits each followed by their `source`
    event) and then a raise, standing in for a dropped connection or a
    provider 5xx partway through a real turn.
    """
    _stub_upload(monkeypatch)

    async def fake_turn(*args, **kwargs):
        yield {
            "kind": "tool",
            "trace": {
                "name": DECLARE_PLAN_TOOL,
                "arguments": {
                    "steps": [
                        {"key": "basics", "label": "Basics"},
                        {"key": "experience", "label": "Experience"},
                    ]
                },
                "result": {"accepted": 2},
            },
        }
        yield {
            "kind": "tool",
            "trace": {
                "name": EDIT_TOOL,
                "arguments": {"search": "R", "replace": "R\nBasics", "step": "basics"},
                "result": {"ok": True, "changed": True, "source": "= R\nBasics"},
            },
        }
        yield {"kind": "source", "typst_source": "= R\nBasics"}
        yield {
            "kind": "tool",
            "trace": {
                "name": EDIT_TOOL,
                "arguments": {
                    "search": "Basics",
                    "replace": "Basics\nExperience",
                    "step": "experience",
                },
                "result": {"ok": True, "changed": True, "source": "= R\nBasics\nExperience"},
            },
        }
        yield {"kind": "source", "typst_source": "= R\nBasics\nExperience"}
        raise RuntimeError("provider connection dropped mid-turn")

    monkeypatch.setattr("app.services.import_runner.aiter_chat_edit", fake_turn)

    await run_import(db_session, resume, run)

    db_session.refresh(run)
    db_session.refresh(resume)
    assert run.status == "failed"
    assert run.error
    # The two sections that landed before the failure are still counted and
    # still done -- a crash mid-turn must not roll back work already applied.
    assert run.applied == 2
    assert [s.status for s in _steps(db_session, run)] == ["done", "done"]
    assert resume.import_status == "failed"


async def test_every_source_write_is_observable_through_the_snapshot(
    db_session, resume, run, monkeypatch
):
    """What a polling client can actually see, event by event.

    The editor repaints its preview when `snapshot()`'s `source_hash`
    changes, and it only ever reads a snapshot a `version` change asked it
    to read (`routers/resumes._stream_import_snapshots`). Two things have
    to hold for that to track the resume, and both are about ordering
    rather than about any single write:

    - `version` must move *after* the source is committed. The bumps that
      come with the edit's own tool event all happen while the resume still
      holds the previous source.
    - every changed edit must be observable, including one that reuses a
      step key the agent already marked done -- `advance` is a no-op on
      those, so `applied` cannot be the signal.

    Two probes, both sampling what a poll landing there would have found.
    One runs inside the fake turn, which works because a generator is only
    resumed once the consumer has finished with the previous event. The
    other wraps `set_draft_source` to sample the window *inside* the source
    event -- after that event's own heartbeat bump, before the new source
    is committed -- which is the window the post-write bump exists for: a
    client that read the snapshot there has already spent this event's
    version change on the old source.

    The turn is faked at the name `import_runner` imports it under for the
    same reason `test_failure_after_partial_progress_keeps_what_landed`
    does it: the behaviour under test is `run_import`'s event loop, not the
    provider.
    """
    import app.services.import_runner as import_runner_module

    _stub_upload(monkeypatch)
    seen: list[dict] = []
    windows: list[dict] = []

    def probe() -> None:
        seen.append(import_run.snapshot(db_session, resume.id))

    real_set_draft_source = import_runner_module.set_draft_source

    def probing_set_draft_source(db, res, src, **kwargs):
        windows.append(import_run.snapshot(db_session, resume.id))
        return real_set_draft_source(db, res, src, **kwargs)

    monkeypatch.setattr(
        import_runner_module, "set_draft_source", probing_set_draft_source
    )

    def edit(step: str, source: str) -> dict:
        return {
            "kind": "tool",
            "trace": {
                "name": EDIT_TOOL,
                "arguments": {"source": source, "step": step},
                "result": {"ok": True, "changed": True, "source": source},
            },
        }

    async def fake_turn(*args, **kwargs):
        yield {
            "kind": "tool",
            "trace": {
                "name": DECLARE_PLAN_TOOL,
                "arguments": {"steps": [{"key": "experience", "label": "Experience"}]},
                "result": {"accepted": 1},
            },
        }
        probe()  # 0: the plan, nothing applied
        yield edit("experience", "= R\nExperience")
        probe()  # 1: counted, not yet written
        yield {"kind": "source", "typst_source": "= R\nExperience"}
        probe()  # 2: written
        yield edit("experience", "= R\nExperience\n- Fixed a date")
        probe()  # 3: the same step again -- the counter cannot see this one
        yield {"kind": "source", "typst_source": "= R\nExperience\n- Fixed a date"}
        probe()  # 4: written anyway
        yield {"kind": "assistant", "content": "Imported."}

    monkeypatch.setattr("app.services.import_runner.aiter_chat_edit", fake_turn)

    await run_import(db_session, resume, run)

    plan, counted, written, again, written_again = seen[:5]

    # The counter leads the source by one commit. This is the window that
    # used to strand the preview one edit behind for the rest of the run:
    # `applied` never comes back to the same value, so nothing retried.
    assert counted["applied"] == plan["applied"] + 1
    assert counted["source_hash"] == plan["source_hash"]

    # ...and the bump that follows the write is what lets the client find
    # out. Sampled inside the event, the version has already moved while
    # the hash has not; the bump *after* the write is what a client reading
    # the snapshot there is waiting for, and without it the next one is a
    # whole LLM round away.
    first_window, second_window = windows[:2]
    assert first_window["source_hash"] == counted["source_hash"]
    assert written["source_hash"] != counted["source_hash"]
    assert written["version"] > first_window["version"]

    # An edit reusing a done step key: invisible to `applied`, visible in
    # the hash, and still announced by a version bump of its own.
    assert again["applied"] == counted["applied"]
    assert second_window["source_hash"] == written["source_hash"]
    assert written_again["source_hash"] != written["source_hash"]
    assert written_again["version"] > second_window["version"]

    db_session.refresh(resume)
    assert resume.typst_source == "= R\nExperience\n- Fixed a date"


async def test_import_without_a_declared_plan_still_completes(
    db_session, resume, run, monkeypatch, openai_enabled
):
    _stub_upload(monkeypatch)
    # No declare_import_plan call at all -- just two unlabelled edits and a
    # final message. The import must still reach "done".
    scripted = ScriptedOpenAI(
        [
            llm_message(tool_calls=[tool_call("read_typst", {}, "read-1")]),
            llm_message(
                tool_calls=[tool_call("apply_typst_edit", {"search": "R", "replace": "R\nA"}, "edit-1")]
            ),
            llm_message(
                tool_calls=[tool_call("apply_typst_edit", {"search": "A", "replace": "A\nB"}, "edit-2")]
            ),
            llm_message(content="Imported."),
        ]
    )
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted)

    await run_import(db_session, resume, run, model=OPENAI_ROW)

    db_session.refresh(run)
    assert run.status == "done"
    assert run.applied == 2
    steps = _steps(db_session, run)
    # Plus the server-owned render-check step the gate always appends.
    assert [s.status for s in steps] == ["done", "done", "done"]
    # Synthesised keys, never the literal "" -- see import_run.advance.
    assert all(s.key for s in steps)
    assert len({s.key for s in steps}) == 3


async def test_runner_uses_its_own_round_budget(db_session, resume, run, monkeypatch, openai_enabled):
    import app.services.import_runner as import_runner_module

    _stub_upload(monkeypatch)
    real_aiter_chat_edit = import_runner_module.aiter_chat_edit
    seen: dict = {}

    def wrapper(*args, **kwargs):
        seen.update(kwargs)
        return real_aiter_chat_edit(*args, **kwargs)

    monkeypatch.setattr(import_runner_module, "aiter_chat_edit", wrapper)

    scripted = ScriptedOpenAI([llm_message(content="Imported.")])
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted)

    await run_import(db_session, resume, run, model=OPENAI_ROW)

    db_session.refresh(run)
    assert run.status == "done"
    assert seen["max_rounds"] == 20
    assert seen["prefer_full_source"] is False
    assert seen["timeout"] == 180.0
    assert seen["verify_compile"] is True


async def test_import_cost_lands_in_telemetry_and_in_the_counter(
    db_session, resume, run, monkeypatch, openai_enabled
):
    """The pair is the invariant, not either half.

    `app.services.usage.reconcile` compares `usage_counters.uusd` against the
    `LlmCall`-derived total per subject per window, so a path that moves the
    counter without writing the rows reports a gap on the admin overview for
    that window forever -- and a gap that is always there is an alarm nobody
    can read. The import bills under its own purpose rather than inside the
    chat total, following the compile-repair loop's 'repair'.
    """
    _stub_upload(monkeypatch)
    scripted = ScriptedOpenAI(
        [
            llm_message(
                tool_calls=[tool_call("read_typst", {}, "read-1")],
                usage=llm_usage(input_tokens=10_000, cached_tokens=0, output_tokens=40),
            ),
            llm_message(
                content="Imported.",
                usage=llm_usage(input_tokens=12_000, cached_tokens=4_000, output_tokens=120),
            ),
        ]
    )
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted)

    await run_import(db_session, resume, run, model=OPENAI_ROW)

    db_session.rollback()
    calls = db_session.query(LlmCall).all()
    assert len(calls) == 2
    assert {c.purpose for c in calls} == {"import"}
    assert {c.guest_session_id for c in calls} == {resume.guest_session_id}
    assert {c.resume_id for c in calls} == {resume.id}
    billed = sum(c.cost_uusd for c in calls)
    assert billed > 0

    window = usage.window_start()
    counted, _turns, unmeasured = usage.counter_totals(
        db_session, kind="guest", subject_id=resume.guest_session_id, window_start=window
    )
    assert counted == billed
    assert unmeasured == 0
    # The reconciliation itself, which is the thing that would have been
    # permanently noisy with the rows missing.
    assert usage.reconcile(db_session, window_start=window) == []


async def test_a_guests_import_still_counts_toward_the_guest_pool(
    db_session, resume, run, monkeypatch, openai_enabled
):
    """The anti-abuse property, pinned.

    An import is the person's OWN work -- they uploaded the file -- so it goes
    through `deferred_guard`, which bills their own subject kind, and NOT
    through `system_guard`, which bills kind 'system' for work the platform
    decided to do on its own. The difference is not cosmetic for a guest: the
    pool ceiling sums `("guest", "network")` only, and a cookie is free to
    clear, so that pool is the wall and the per-session allowance is a speed
    bump in front of it. Billing an import to 'system' takes the wall away and
    nothing else in the suite would have noticed.
    """
    monkeypatch.setenv("USAGE_LIMITS_ENABLED", "true")
    monkeypatch.setenv("GUEST_WEEKLY_CEILING_UUSD", "1000")
    get_settings.cache_clear()
    _stub_upload(monkeypatch)
    scripted = ScriptedOpenAI(
        [llm_message(content="Imported.", usage=llm_usage(input_tokens=500_000))]
    )
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted)

    await run_import(db_session, resume, run, model=OPENAI_ROW)
    db_session.rollback()

    window = usage.window_start()
    pooled = usage.window_totals(db_session, window_start=window, kinds=("guest", "network"))
    assert pooled > 0
    assert usage.window_totals(db_session, window_start=window) == pooled

    # The wall itself: a newcomer with a fresh cookie is converted, not served.
    newcomer = GuestSession(key_hash="n" * 60, locale="en")
    db_session.add(newcomer)
    db_session.commit()
    with pytest.raises(Exception) as caught:
        usage.enforce_usage(db_session, None, newcomer)
    assert caught.value.detail["limit"] == "guest_pool"


async def test_a_superseded_runner_stops_at_its_next_event(
    db_session, resume, run, monkeypatch
):
    """The only way to stop a runner that no longer owns its resume.

    `start_run` supersedes a stale run and inserts a fresh one, but nothing
    cancels the `asyncio.Task` still driving the old one -- across two uvicorn
    workers nothing can, because that task lives in the other process. So the
    old runner has to notice for itself, and `touch` before every event is
    where it does. Without it, two agents write `resume.typst_source`
    concurrently from two sessions, each holding its own snapshot of it.

    The turn is faked at the name `import_runner` imports it under, the same
    way `test_failure_after_partial_progress_keeps_what_landed` does: there is
    no hook on the scripted client for "another worker intervenes here".
    """
    _stub_upload(monkeypatch)
    reached = []

    async def fake_turn(*args, **kwargs):
        yield {
            "kind": "tool",
            "trace": {
                "name": EDIT_TOOL,
                "arguments": {"search": "R", "replace": "R\nBasics", "step": "basics"},
                "result": {"ok": True, "changed": True, "source": "= R\nBasics"},
            },
        }
        yield {"kind": "source", "typst_source": "= R\nBasics"}
        # The other worker: this run's heartbeat went stale, so a retry from
        # the browser started a fresh one over it.
        run.heartbeat_at = _utcnow() - timedelta(seconds=STALE_AFTER_SECONDS + 1)
        db_session.flush()
        newer = import_run.start_run(db_session, resume)
        reached.append(newer.id)
        yield {
            "kind": "tool",
            "trace": {
                "name": EDIT_TOOL,
                "arguments": {
                    "search": "Basics",
                    "replace": "Basics\nExperience",
                    "step": "experience",
                },
                "result": {"ok": True, "changed": True, "source": "= R\nBasics\nExperience"},
            },
        }
        # Only reachable if the runner carried on past the supersession.
        reached.append("carried on")
        yield {"kind": "assistant", "content": "never written"}

    monkeypatch.setattr("app.services.import_runner.aiter_chat_edit", fake_turn)

    await run_import(db_session, resume, run)

    assert "carried on" not in reached
    newer_id = reached[0]

    db_session.rollback()
    db_session.refresh(run)
    db_session.refresh(resume)
    newer = db_session.get(import_run.ResumeImport, newer_id)
    # The old run keeps the supersession's own verdict: `fail()` from the old
    # runner's handler must not overwrite it, bump a version nobody reads, or
    # -- the damaging one -- mark the resume failed under the new run.
    assert run.status == "failed"
    assert "superseded" in (run.error or "")
    assert run.applied == 1
    assert newer.status == "running"
    assert resume.import_status == "pending"
    # And no closing message from the stopped runner.
    assert not [m for m in _messages(db_session, resume.id) if m.content == "never written"]


# ---------------------------------------------------------------------------
# The render-check gate: verify_render, run right before finish(), with up
# to three automatic repair rounds.
#
# These need the real typst binary -- verify_render compiles for real, no
# mocking -- same requirement as the fixtures in tests/test_import_qa.py.
# ---------------------------------------------------------------------------

render_check_pytestmark = pytest.mark.skipif(
    not typst_available(), reason="typst binary not available"
)

# A clean two-section resume: every bullet is plain prose, nothing escapes
# anything, and every candidate should make it onto the page untouched.
CLEAN_SECTIONS = (
    "= Experience\n\n"
    "- Shipped a resilient billing pipeline that reconciles payment "
    "provider webhooks against internal ledgers across three currencies\n\n"
    "= Education\n\n"
    "National Taiwan University, BSc Computer Science\n\n"
    "= Skills\n\n"
    "Python, TypeScript, Rust\n"
)

# The exact real bug (see .superpowers/sdd/2026-11-04-resume-import-progress
# /t.typ and import_qa.py's module docstring): `\\@x402/*` opens a Typst
# block comment that runs to end-of-file. Compiles clean; renders almost
# nothing.
BROKEN_SECTIONS = (
    "= Experience\n\n"
    "- Led a phased re-architecture from bespoke payment infra to Coinbase "
    "\\@x402/* standard libraries; extracted IDRX adapter logic into "
    "published npm packages: [@transx402/client] and [@transx402/server].\n\n"
    "= Education\n\n"
    "National Taiwan University, BSc Computer Science\n\n"
    "= Skills\n\n"
    "Python, TypeScript, Rust\n"
)

# The same content, with the hazards escaped/removed rather than left open.
FIXED_SECTIONS = (
    "= Experience\n\n"
    "- Led a phased re-architecture from bespoke payment infra to Coinbase "
    "x402 standard libraries; extracted IDRX adapter logic into published "
    "npm packages: transx402 client and transx402 server\n\n"
    "= Education\n\n"
    "National Taiwan University, BSc Computer Science\n\n"
    "= Skills\n\n"
    "Python, TypeScript, Rust\n"
)


def _render_check_step(db_session, run) -> ResumeImportStep | None:
    return (
        db_session.query(ResumeImportStep)
        .filter(ResumeImportStep.import_id == run.id, ResumeImportStep.key == RENDER_CHECK_KEY)
        .first()
    )


@render_check_pytestmark
async def test_render_check_passes_without_repair_when_clean(
    db_session, resume, run, monkeypatch, openai_enabled
):
    _stub_upload(monkeypatch)
    scripted = ScriptedOpenAI(
        [
            llm_message(
                tool_calls=[
                    tool_call(
                        "apply_typst_edit",
                        {"source": CLEAN_SECTIONS, "search": "", "replace": "", "step": "experience"},
                        "edit-1",
                    )
                ]
            ),
            llm_message(content="Imported your resume."),
        ]
    )
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted)

    await run_import(db_session, resume, run, model=OPENAI_ROW)

    db_session.refresh(run)
    assert run.status == "done"
    check = _render_check_step(db_session, run)
    assert check is not None
    assert check.status == "done"
    # No repair round ran -- the only calls billed are the main turn's own
    # two rounds (the edit, then the closing message).
    assert len(db_session.query(LlmCall).all()) == 2


@render_check_pytestmark
async def test_render_check_catches_the_real_bug_and_repairs_it(
    db_session, resume, run, monkeypatch, openai_enabled
):
    _stub_upload(monkeypatch)
    scripted = ScriptedOpenAI(
        [
            llm_message(
                tool_calls=[
                    tool_call(
                        "apply_typst_edit",
                        {"source": BROKEN_SECTIONS, "search": "", "replace": "", "step": "experience"},
                        "edit-1",
                    )
                ]
            ),
            llm_message(content="Imported your resume."),
            # The repair round: the agent reads the problem description and
            # rewrites the source without the unterminated comment.
            llm_message(
                tool_calls=[
                    tool_call(
                        "apply_typst_edit",
                        {"source": FIXED_SECTIONS, "search": "", "replace": ""},
                        "edit-2",
                    )
                ]
            ),
            llm_message(content="Fixed the rendering issue."),
        ]
    )
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted)

    await run_import(db_session, resume, run, model=OPENAI_ROW)

    db_session.refresh(run)
    db_session.refresh(resume)
    assert run.status == "done"
    assert "/*" not in resume.typst_source
    check = _render_check_step(db_session, run)
    assert check is not None
    assert check.status == "done"
    # Proof a repair round actually ran: both turns' calls were billed.
    assert len(db_session.query(LlmCall).all()) == 4


@render_check_pytestmark
async def test_render_check_exhausts_three_repairs_and_fails(
    db_session, resume, run, monkeypatch, openai_enabled
):
    """Three repair rounds, each one a no-op (the agent replies without
    fixing anything), must exhaust the budget and fail the run with the
    render-check error code -- never loop a fourth time.
    """
    _stub_upload(monkeypatch)
    no_op_repair = llm_message(content="I could not find the issue.")
    scripted = ScriptedOpenAI(
        [
            llm_message(
                tool_calls=[
                    tool_call(
                        "apply_typst_edit",
                        {"source": BROKEN_SECTIONS, "search": "", "replace": "", "step": "experience"},
                        "edit-1",
                    )
                ]
            ),
            llm_message(content="Imported your resume."),
            no_op_repair,
            no_op_repair,
            no_op_repair,
        ]
    )
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted)

    await run_import(db_session, resume, run, model=OPENAI_ROW)

    db_session.refresh(run)
    db_session.refresh(resume)
    assert run.status == "failed"
    assert run.error == RENDER_CHECK_FAILED
    assert resume.import_status == "failed"
    # Exactly the main turn plus three repair turns -- a fourth would mean
    # the bound was not enforced.
    assert len(db_session.query(LlmCall).all()) == 5
