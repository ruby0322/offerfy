"""The guarded writes behind `edit_resume` and `put_resume_source`.

`write_source` and `apply_edit` share one order: compare hash -> commit (release
the pool connection) -> compile off the loop -> refresh -> compare hash again ->
`set_draft_source(coalesce=False)`.
"""

import json

import pytest
from fastapi import HTTPException

from app.models import ResumeRevision
from app.schemas_editing import IntentIn
from app.services import resume_write
from app.services.resume_write import apply_edit, write_source
from app.services.revisions import set_draft_source, source_hash
from app.services.typst_compile import typst_available
from app.services.typst_projection import parse_projection
from tests.conftest import _resume_for
from tests.test_mcp_tools import _caller, _is_tool_error, _raw_call, _text

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(not typst_available(), reason="Typst CLI is required"),
]


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _revisions(db, resume) -> int:
    return db.query(ResumeRevision).filter(ResumeRevision.resume_id == resume.id).count()


async def test_a_good_source_lands_and_returns_the_new_hash(db_session, my_resume):
    out = await write_source(
        db_session, my_resume, source="= Hired",
        expected_hash=source_hash(my_resume.typst_source))
    db_session.refresh(my_resume)
    assert my_resume.typst_source == "= Hired"
    assert out["hash"] == source_hash("= Hired")


async def test_a_stale_hash_is_409_and_writes_nothing(db_session, my_resume):
    before = my_resume.typst_source
    n = _revisions(db_session, my_resume)
    with pytest.raises(HTTPException) as caught:
        await write_source(
            db_session, my_resume, source="= Nope", expected_hash="not-the-hash")
    assert caught.value.status_code == 409
    db_session.refresh(my_resume)
    assert my_resume.typst_source == before
    assert _revisions(db_session, my_resume) == n


async def test_a_stale_hash_is_refused_before_any_compile(
    db_session, my_resume, monkeypatch
):
    """The first check is the cheap one: a caller that is already stale must not
    cost up to 90s of Typst subprocesses before hearing so."""
    compiled = []
    monkeypatch.setattr(
        resume_write, "_preview_pages", lambda *a, **k: compiled.append(a) or {"pages": []})
    with pytest.raises(HTTPException):
        await write_source(
            db_session, my_resume, source="= Nope", expected_hash="not-the-hash")
    assert compiled == []


async def test_a_source_typst_rejects_writes_nothing(db_session, my_resume):
    before = my_resume.typst_source
    n = _revisions(db_session, my_resume)
    with pytest.raises(HTTPException) as caught:
        await write_source(
            db_session, my_resume, source="#this-is-not-typst(",
            expected_hash=source_hash(before))
    assert caught.value.status_code == 400
    db_session.refresh(my_resume)
    assert my_resume.typst_source == before
    assert _revisions(db_session, my_resume) == n


async def test_a_write_that_lands_during_the_compile_is_409(
    db_session, my_resume, monkeypatch
):
    """The second hash check. A chat reply or a second tab lands while the
    compile runs, which is why the connection was released."""
    expected = source_hash(my_resume.typst_source)

    def _competing_compile(source, with_bands=False):
        set_draft_source(db_session, my_resume, "= Someone else got here first")
        db_session.commit()
        return {"pages": []}

    monkeypatch.setattr(resume_write, "_preview_pages", _competing_compile)
    with pytest.raises(HTTPException) as caught:
        await write_source(
            db_session, my_resume, source="= Mine", expected_hash=expected)
    assert caught.value.status_code == 409
    db_session.refresh(my_resume)
    assert my_resume.typst_source == "= Someone else got here first"


@pytest.mark.parametrize("empty", ["", "   \n\t "])
async def test_an_empty_source_is_refused_not_stored(db_session, my_resume, empty):
    before = my_resume.typst_source
    with pytest.raises(HTTPException) as caught:
        await write_source(
            db_session, my_resume, source=empty, expected_hash=source_hash(before))
    assert caught.value.status_code == 400
    db_session.refresh(my_resume)
    assert my_resume.typst_source == before


async def test_every_write_makes_its_own_revision(db_session, my_resume):
    """coalesce=False: two writes inside the coalescing window must not merge,
    or an agent edit would rewrite a chat reply's revision."""
    before = _revisions(db_session, my_resume)
    for text in ("= One", "= Two"):
        await write_source(
            db_session, my_resume, source=text,
            expected_hash=source_hash(my_resume.typst_source))
        db_session.refresh(my_resume)
    assert _revisions(db_session, my_resume) == before + 2


async def test_the_connection_is_released_before_the_compile(
    db_session, my_resume, monkeypatch
):
    """Nothing may be open on the session when the compile starts."""
    seen = {}

    def _look(source, with_bands=False):
        seen["in_transaction"] = db_session.in_transaction()
        return {"pages": []}

    monkeypatch.setattr(resume_write, "_preview_pages", _look)
    await write_source(
        db_session, my_resume, source="= Released",
        expected_hash=source_hash(my_resume.typst_source))
    assert seen == {"in_transaction": False}


async def test_a_write_leaves_nothing_uncommitted(db_session, my_resume):
    await write_source(
        db_session, my_resume, source="= Done",
        expected_hash=source_hash(my_resume.typst_source))
    assert not db_session.new and not db_session.dirty
    assert not db_session.in_transaction()


def _title_intent(resume, value):
    proj = parse_projection(resume.typst_source)
    unit = next(u for u in proj.units.values() if u.key == "title")
    return IntentIn(
        hash=proj.hash, kind="set_arg", unit=unit.id, expect=unit.value, value=value)


async def test_apply_edit_writes_a_revision_of_its_own(db_session, my_resume):
    n = _revisions(db_session, my_resume)
    out = await apply_edit(db_session, my_resume, _title_intent(my_resume, "Staff"))
    db_session.refresh(my_resume)
    assert "Staff" in my_resume.typst_source
    assert out["hash"] == source_hash(my_resume.typst_source)
    assert _revisions(db_session, my_resume) == n + 1


# --- the tools -------------------------------------------------------------
# Plain `def`: the sync TestClient drives these, the anyio mark only touches coroutines.


def _ok(body) -> dict:
    assert not _is_tool_error(body), body
    return body["result"]["structuredContent"]


def test_put_resume_source_replaces_the_document(
    client, db_session, a_user, my_resume
):
    body = _raw_call(client, db_session, a_user, "put_resume_source", {
        "resume_id": my_resume.id, "hash": source_hash(my_resume.typst_source),
        "typst_source": "= Via the tool"})
    assert _ok(body)["hash"] == source_hash("= Via the tool")
    db_session.expire_all()
    assert db_session.get(type(my_resume), my_resume.id).typst_source == "= Via the tool"


def test_a_get_resume_round_trip_leaves_the_document_byte_identical(
    client, db_session, a_user, my_resume
):
    """What `get_resume` returns must be what `put_resume_source` can send back.

    The tool description tells the agent to read first and then write the whole
    document, so anything the read omits -- the first line, the
    `#show: resume.with(...)` preamble -- is content the agent has to invent,
    and the hash guard and the compile gate both pass on the invention. The
    projection alone cannot do this: it carries unit values and byte spans, not
    the document.
    """
    before = my_resume.typst_source
    call = _caller(client, db_session, a_user)
    got = json.loads(_text(call("get_resume", {"resume_id": my_resume.id})))
    assert got["typst_source"] == before
    body = call("put_resume_source", {
        "resume_id": my_resume.id, "hash": got["hash"],
        "typst_source": got["typst_source"]})
    assert not _is_tool_error(body), body
    db_session.expire_all()
    assert db_session.get(type(my_resume), my_resume.id).typst_source == before


def test_put_resume_source_with_a_stale_hash_is_a_tool_error(
    client, db_session, a_user, my_resume
):
    before = my_resume.typst_source
    body = _raw_call(client, db_session, a_user, "put_resume_source", {
        "resume_id": my_resume.id, "hash": "nope", "typst_source": "= X"})
    assert _is_tool_error(body)
    db_session.expire_all()
    assert db_session.get(type(my_resume), my_resume.id).typst_source == before


def test_put_resume_source_on_someone_elses_resume_is_404_and_writes_nothing(
    client, db_session, a_user, other_user, my_resume
):
    theirs = _resume_for(db_session, other_user)
    before = theirs.typst_source
    body = _raw_call(client, db_session, a_user, "put_resume_source", {
        "resume_id": theirs.id, "hash": source_hash(before), "typst_source": "= Mine now"})
    assert _is_tool_error(body)
    assert "not found" in body["result"]["content"][0]["text"].lower()
    db_session.expire_all()
    assert db_session.get(type(theirs), theirs.id).typst_source == before


def test_put_resume_source_requires_all_three_arguments(
    client, db_session, a_user, my_resume
):
    before = my_resume.typst_source
    for missing in ("resume_id", "hash", "typst_source"):
        args = {"resume_id": my_resume.id, "hash": source_hash(before),
                "typst_source": "= X"}
        del args[missing]
        assert _is_tool_error(
            _raw_call(client, db_session, a_user, "put_resume_source", args)), missing
    db_session.expire_all()
    assert db_session.get(type(my_resume), my_resume.id).typst_source == before


def test_edit_resume_applies_a_field_edit(client, db_session, a_user, my_resume):
    intent = _title_intent(my_resume, "Principal")
    out = _ok(_raw_call(client, db_session, a_user, "edit_resume", {
        "resume_id": my_resume.id, **intent.model_dump(exclude_none=True)}))
    db_session.expire_all()
    stored = db_session.get(type(my_resume), my_resume.id).typst_source
    assert "Principal" in stored
    assert out["hash"] == source_hash(stored)


def test_edit_resume_result_does_not_carry_the_rendered_pages(
    client, db_session, a_user, my_resume
):
    intent = _title_intent(my_resume, "Lean")
    out = _ok(_raw_call(client, db_session, a_user, "edit_resume", {
        "resume_id": my_resume.id, **intent.model_dump(exclude_none=True)}))
    assert "preview" not in out
    assert "<svg" not in json.dumps(out)


def test_edit_resume_refuses_kinds_that_delete_or_reorder(
    client, db_session, a_user, my_resume
):
    before = my_resume.typst_source
    proj = parse_projection(before)
    block = next(b for sec in proj.sections for b in sec.blocks)
    for kind, extra in (("delete", {"target": block.id}),
                        ("move_section", {"target": block.id, "dir": "down"})):
        body = _raw_call(client, db_session, a_user, "edit_resume", {
            "resume_id": my_resume.id, "hash": proj.hash, "kind": kind, **extra})
        assert _is_tool_error(body), kind
        # The message is the point: isError alone also passes when the call
        # failed for some unrelated reason, a bad target id for instance.
        text = _text(body)
        assert "kind must be one of" in text, (kind, text)
        assert kind not in text, (kind, text)
        for allowed in ("set_arg", "insert_bullet", "insert_section"):
            assert allowed in text, (kind, text)
    db_session.expire_all()
    assert db_session.get(type(my_resume), my_resume.id).typst_source == before


def test_edit_resume_on_someone_elses_resume_is_404(
    client, db_session, a_user, other_user
):
    theirs = _resume_for(db_session, other_user)
    intent = _title_intent(theirs, "Hijack")
    body = _raw_call(client, db_session, a_user, "edit_resume", {
        "resume_id": theirs.id, **intent.model_dump(exclude_none=True)})
    assert _is_tool_error(body)
    db_session.expire_all()
    assert "Hijack" not in db_session.get(type(theirs), theirs.id).typst_source


def test_edit_resume_insert_section_is_accepted_and_changes_the_document(
    client, db_session, a_user, my_resume
):
    before = my_resume.typst_source
    proj = parse_projection(before)
    out = _ok(_raw_call(client, db_session, a_user, "edit_resume", {
        "resume_id": my_resume.id, "hash": proj.hash, "kind": "insert_section",
        "title": "Publications"}))
    db_session.expire_all()
    stored = db_session.get(type(my_resume), my_resume.id).typst_source
    assert stored != before
    assert "Publications" in stored
    assert out["hash"] == source_hash(stored)


@pytest.mark.parametrize("over", [1, 5, 4096])
async def test_an_oversized_source_is_refused_before_the_compile(
    db_session, my_resume, monkeypatch, over
):
    """`over=1` is the edge that matters: at five bytes over, a `> MAX + 4`
    mutant of the comparison survives, so only the far side of the cap was
    pinned."""
    compiled = []
    monkeypatch.setattr(
        resume_write, "_preview_pages", lambda *a, **k: compiled.append(a) or {"pages": []})
    before = my_resume.typst_source
    big = "x" * (resume_write.MAX_SOURCE_BYTES + over)
    with pytest.raises(HTTPException) as caught:
        await write_source(
            db_session, my_resume, source=big, expected_hash=source_hash(before))
    assert caught.value.status_code == 400
    assert compiled == []
    db_session.refresh(my_resume)
    assert my_resume.typst_source == before


async def test_a_source_exactly_at_the_cap_is_accepted(db_session, my_resume, monkeypatch):
    monkeypatch.setattr(resume_write, "_preview_pages", lambda *a, **k: {"pages": []})
    edge = "x" * resume_write.MAX_SOURCE_BYTES
    await write_source(
        db_session, my_resume, source=edge, expected_hash=source_hash(my_resume.typst_source))
    db_session.refresh(my_resume)
    assert my_resume.typst_source == edge


async def test_the_cap_counts_bytes_not_characters(db_session, my_resume, monkeypatch):
    monkeypatch.setattr(resume_write, "_preview_pages", lambda *a, **k: {"pages": []})
    cjk = "履" * (resume_write.MAX_SOURCE_BYTES // 3 + 1)  # fewer chars than the cap
    assert len(cjk) < resume_write.MAX_SOURCE_BYTES
    with pytest.raises(HTTPException) as caught:
        await write_source(
            db_session, my_resume, source=cjk,
            expected_hash=source_hash(my_resume.typst_source))
    assert caught.value.status_code == 400
