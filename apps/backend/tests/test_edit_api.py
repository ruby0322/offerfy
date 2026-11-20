"""POST /v1/resumes/{id}/edit: guards, compile gate, revision, response."""

import pytest
from sqlalchemy import event

from app import models
from app.db import get_engine, get_session_factory
from app.services import resume_write
from app.services.resume_content import ResumeContent, store_content, stored_content
from app.services.revisions import set_draft_source, source_hash
from app.services.typst_compile import typst_available
from tests.test_projection_api import expected, read, resume_with

pytestmark = pytest.mark.skipif(not typst_available(), reason="Typst CLI is required")


def title_intent(client, rid: str, value: str, **over) -> dict:
    """A set_arg intent for the `title` argument, built from the live projection."""
    proj = client.get(f"/v1/resumes/{rid}/projection").json()
    unit = next(u for u in proj["units"].values() if u["key"] == "title")
    body = {"hash": proj["hash"], "kind": "set_arg", "unit": unit["id"],
            "expect": unit["value"], "value": value}
    body.update(over)
    return body


def stored(db, rid: str) -> str:
    db.expire_all()
    return db.get(models.Resume, rid).typst_source


def revisions(db, rid: str) -> int:
    db.expire_all()
    return db.query(models.ResumeRevision).filter_by(resume_id=rid).count()


def test_edit_writes_source_and_returns_the_new_projection(client, db_session):
    rid = resume_with(client, read("synthetic-en"))
    r = client.post(
        f"/v1/resumes/{rid}/edit", json=title_intent(client, rid, "Staff Engineer"))
    assert r.status_code == 200
    out = r.json()
    assert "Staff Engineer" in out["typst_source"]
    assert out["typst_source"] == stored(db_session, rid)
    assert out["hash"] == source_hash(out["typst_source"])
    assert out["projection"] == expected(out["typst_source"])
    assert out["projection"]["hash"] == out["hash"]
    assert out["preview"]["pages"]


def test_stale_hash_is_refused_and_writes_nothing(client, db_session):
    rid = resume_with(client, read("synthetic-en"))
    body = title_intent(client, rid, "A")
    body["hash"] = "0" * 64
    before, n = stored(db_session, rid), revisions(db_session, rid)
    r = client.post(f"/v1/resumes/{rid}/edit", json=body)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "stale_hash"
    assert stored(db_session, rid) == before and revisions(db_session, rid) == n


def test_an_intent_is_stale_once_its_own_edit_has_landed(client, db_session):
    rid = resume_with(client, read("synthetic-en"))
    body = title_intent(client, rid, "First")
    assert client.post(f"/v1/resumes/{rid}/edit", json=body).status_code == 200
    again = client.post(f"/v1/resumes/{rid}/edit", json=body)
    assert again.status_code == 409 and again.json()["detail"]["code"] == "stale_hash"
    assert "First" in stored(db_session, rid)


def test_expect_mismatch_is_a_400_and_writes_nothing(client, db_session):
    rid = resume_with(client, read("synthetic-en"))
    before = stored(db_session, rid)
    r = client.post(f"/v1/resumes/{rid}/edit",
                    json=title_intent(client, rid, "X", expect="not the value"))
    assert r.status_code == 400 and r.json()["detail"]["code"] == "expect_mismatch"
    assert stored(db_session, rid) == before


def test_an_unknown_kind_is_unsupported(client, db_session):
    rid = resume_with(client, read("synthetic-en"))
    before = stored(db_session, rid)
    r = client.post(f"/v1/resumes/{rid}/edit",
                    json=title_intent(client, rid, "X", kind="rotate"))
    assert r.status_code == 400 and r.json()["detail"]["code"] == "unsupported"
    assert stored(db_session, rid) == before


def test_a_structural_intent_without_its_target_is_unsupported(client, db_session):
    rid = resume_with(client, read("synthetic-en"))
    before, n = stored(db_session, rid), revisions(db_session, rid)
    r = client.post(f"/v1/resumes/{rid}/edit",
                    json=title_intent(client, rid, "X", kind="insert_bullet"))
    assert r.status_code == 400 and r.json()["detail"]["code"] == "unsupported"
    assert stored(db_session, rid) == before and revisions(db_session, rid) == n


def test_edit_on_a_missing_resume_is_404(client):
    r = client.post("/v1/resumes/no-such-id/edit",
                    json={"hash": "0" * 64, "kind": "set_arg"})
    assert r.status_code == 404 and r.json()["detail"] == "Resume not found"


def test_compile_failure_writes_nothing(client, db_session):
    # A real end-to-end failure: `accent-color` is a style argument whose value typst
    # parses as a colour. Quote/backslash escaping cannot break a compile (searched
    # for; see the task report), a value of the wrong *kind* can.
    rid = resume_with(client, read("synthetic-en"))
    proj = client.get(f"/v1/resumes/{rid}/projection").json()
    unit = next(u for u in proj["units"].values() if u["key"] == "accent-color")
    body = {"hash": proj["hash"], "kind": "set_arg", "unit": unit["id"],
            "expect": unit["value"], "value": "not a colour"}
    before, n = stored(db_session, rid), revisions(db_session, rid)
    r = client.post(f"/v1/resumes/{rid}/edit", json=body)
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert detail["code"] == "compile_failed"
    assert "color string" in detail["detail"] and "\n" not in detail["detail"]
    assert stored(db_session, rid) == before
    assert revisions(db_session, rid) == n == 1


def test_edit_never_rewrites_another_authors_revision(client, db_session):
    src = read("synthetic-en")
    rid = resume_with(client, src)
    resume = db_session.get(models.Resume, rid)
    chat_source = src + "\n// chat wrote this\n"
    chat = set_draft_source(db_session, resume, chat_source, coalesce=False)
    db_session.commit()
    chat_id, chat_hash = chat.id, chat.content_hash
    assert chat_hash == source_hash(chat_source)
    body = title_intent(client, rid, "B")  # built from the chat-written source
    out = client.post(f"/v1/resumes/{rid}/edit", json=body)
    assert out.status_code == 200
    db_session.expire_all()
    row = db_session.get(models.ResumeRevision, chat_id)
    assert row is not None
    assert row.typst_source == chat_source and row.content_hash == chat_hash
    newest = (
        db_session.query(models.ResumeRevision)
        .filter_by(resume_id=rid, content_hash=out.json()["hash"])
        .one()
    )
    assert newest.id != chat_id and newest.typst_source == out.json()["typst_source"]


def test_a_write_that_lands_during_the_compile_wins_and_the_edit_is_refused(
    client, db_session, monkeypatch
):
    src = read("synthetic-en")
    rid = resume_with(client, src)
    body = title_intent(client, rid, "Mine")
    interloper = src + "\n// landed while compiling\n"
    real = resume_write.compile_preview_pages

    def compile_then_someone_writes(source):
        result = real(source)
        other = get_session_factory()()
        try:
            set_draft_source(other, other.get(models.Resume, rid), interloper,
                             coalesce=False)
            other.commit()
        finally:
            other.close()
        return result

    monkeypatch.setattr(
        resume_write, "compile_preview_pages", compile_then_someone_writes)
    r = client.post(f"/v1/resumes/{rid}/edit", json=body)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "stale_hash"
    assert stored(db_session, rid) == interloper
    assert "Mine" not in stored(db_session, rid)


def test_an_edit_leaves_the_template_snapshot_stale(client, db_session):
    # Re-keying the snapshot to the edited source would make it claim to contain
    # the edit; the next template switch would then emit the pre-edit text.
    src = read("synthetic-en")
    rid = resume_with(client, src)
    resume = db_session.get(models.Resume, rid)
    store_content(db_session, resume, ResumeContent(name="Ruby"), model="m")
    db_session.commit()
    assert stored_content(db_session, resume) is not None
    out = client.post(f"/v1/resumes/{rid}/edit", json=title_intent(client, rid, "Z"))
    assert out.status_code == 200
    db_session.expire_all()
    resume = db_session.get(models.Resume, rid)
    assert resume.typst_source == out.json()["typst_source"] != src
    assert stored_content(db_session, resume) is None
    row = db_session.query(models.ResumeContent).filter_by(resume_id=rid).one()
    assert row.derived_from_hash == source_hash(src)


def test_no_pool_connection_is_held_while_typst_compiles(client, monkeypatch):
    # The test engine's StaticPool has no `checkedout()`, so count the pool's own
    # checkout/checkin events instead; they fire for every pool class.
    engine = get_engine()
    out = [0]

    def took(*_):
        out[0] += 1

    def gave(*_):
        out[0] -= 1

    rid = resume_with(client, read("synthetic-en"))
    body = title_intent(client, rid, "Pool")
    real = resume_write.compile_preview_pages
    seen: list[int] = []

    def compile_and_look(source):
        seen.append(out[0])
        return real(source)

    monkeypatch.setattr(resume_write, "compile_preview_pages", compile_and_look)
    event.listen(engine, "checkout", took)
    event.listen(engine, "checkin", gave)
    try:
        assert client.post(f"/v1/resumes/{rid}/edit", json=body).status_code == 200
    finally:
        event.remove(engine, "checkout", took)
        event.remove(engine, "checkin", gave)
    assert seen == [0]


def test_a_missing_typst_binary_is_not_reported_as_a_broken_edit(client, monkeypatch):
    from fastapi import HTTPException

    rid = resume_with(client, read("synthetic-en"))
    body = title_intent(client, rid, "X")

    def unavailable(source):
        raise HTTPException(status_code=503, detail="Typst is not available")

    monkeypatch.setattr(resume_write, "compile_preview_pages", unavailable)
    r = client.post(f"/v1/resumes/{rid}/edit", json=body)
    assert r.status_code == 503 and r.json()["detail"] == "Typst is not available"


def first_comp_id(proj: dict) -> str:
    return next(b["id"] for sec in proj["sections"] for b in sec["blocks"]
                if b["kind"] == "comp" and b["bullets"])


def test_a_structural_edit_leaves_the_earlier_revision_as_it_was(client, db_session):
    # Undo must reach the document as it was before the block changed. A structural edit
    # that rewrote the previous revision in place would take that state with it.
    rid = resume_with(client, read("synthetic-en"))
    assert client.post(f"/v1/resumes/{rid}/edit",
                       json=title_intent(client, rid, "A")).status_code == 200
    db_session.expire_all()
    earlier = (db_session.query(models.ResumeRevision).filter_by(resume_id=rid)
               .order_by(models.ResumeRevision.created_at.desc()).first())
    earlier_id, earlier_source = earlier.id, earlier.typst_source
    assert earlier_source == stored(db_session, rid) and "（待填）" not in earlier_source
    n = revisions(db_session, rid)
    proj = client.get(f"/v1/resumes/{rid}/projection").json()
    r = client.post(f"/v1/resumes/{rid}/edit", json={
        "hash": proj["hash"], "kind": "insert_bullet", "target": first_comp_id(proj)})
    assert r.status_code == 200 and "（待填）" in r.json()["typst_source"]
    db_session.expire_all()
    row = db_session.get(models.ResumeRevision, earlier_id)
    assert row.typst_source == earlier_source and "（待填）" not in row.typst_source
    assert revisions(db_session, rid) == n + 1
    assert stored(db_session, rid) == r.json()["typst_source"]


def test_a_structural_edit_returns_a_projection_of_the_new_structure(client, db_session):
    rid = resume_with(client, read("synthetic-en"))
    proj = client.get(f"/v1/resumes/{rid}/projection").json()
    sec = next(s for s in proj["sections"] if s["blocks"] and len(s["blocks"]) == 1
               and s["title_unit"])
    r = client.post(f"/v1/resumes/{rid}/edit", json={
        "hash": proj["hash"], "kind": "delete", "target": sec["blocks"][0]["id"]})
    assert r.status_code == 200
    out = r.json()
    assert len(out["projection"]["sections"]) == len(proj["sections"]) - 1
    assert out["projection"] == expected(out["typst_source"])
    assert out["preview"]["pages"]


def test_every_structural_kind_compiles_through_the_endpoint(client, db_session):
    # The accepted half of the feature: an inserted placeholder in a `dates-helper(...)`
    # or `url:` argument must still compile, or "add" is a button that does nothing.
    src = read("synthetic-en")
    probe = resume_with(client, src)
    proj = client.get(f"/v1/resumes/{probe}/projection").json()
    secs = {proj["units"][s["title_unit"]]["value"]: s
            for s in proj["sections"] if s["title_unit"]}
    cases = [
        ("insert_bullet", {"target": first_comp_id(proj)}),
        ("insert_item", {"target": secs["Projects"]["id"]}),    # url + dates-helper args
        ("insert_item", {"target": secs["Experience"]["id"]}),  # a `#work` with bullets
        ("insert_item", {"target": secs["Skills & Awards"]["id"]}),
        ("insert_section", {}),
        ("delete", {"target": first_comp_id(proj)}),
        ("delete", {"target": secs["Education"]["id"]}),        # a whole section
        ("move_section", {"target": secs["Education"]["id"], "dir": "up"}),
    ]
    for kind, fields in cases:
        rid = resume_with(client, src)
        r = client.post(f"/v1/resumes/{rid}/edit",
                        json={"hash": proj["hash"], "kind": kind, **fields})
        assert r.status_code == 200, (kind, fields, r.status_code, r.text)
        out = r.json()
        assert out["typst_source"] != src, (kind, fields)
        assert out["preview"]["pages"], (kind, fields)
        assert stored(db_session, rid) == out["typst_source"]


def test_the_last_section_can_go_and_a_section_can_be_added_back(client, db_session):
    # Deleting the last heading used to strand the document: `insert_section` refused
    # with nothing to add beside. Both halves must compile, or the way back is a lie.
    src = '#set page(margin: 1cm)\n\nIntro line\n\n== A\n\ntext\n'
    rid = resume_with(client, src)
    proj = client.get(f"/v1/resumes/{rid}/projection").json()
    sec = next(s for s in proj["sections"] if s["title_unit"])
    gone = client.post(f"/v1/resumes/{rid}/edit", json={
        "hash": proj["hash"], "kind": "delete", "target": sec["id"]})
    assert gone.status_code == 200, gone.text
    out = gone.json()
    assert "==" not in out["typst_source"] and out["preview"]["pages"]
    assert not any(s["title_unit"] for s in out["projection"]["sections"])
    back = client.post(f"/v1/resumes/{rid}/edit", json={
        "hash": out["projection"]["hash"], "kind": "insert_section"})
    assert back.status_code == 200, back.text
    assert "== 新段落" in back.json()["typst_source"] and back.json()["preview"]["pages"]
    assert stored(db_session, rid) == back.json()["typst_source"]


def test_preview_and_edit_carry_bands_for_the_lines_and_the_arguments(client):
    rid = resume_with(client, read("synthetic-en"))
    got = client.get(f"/v1/resumes/{rid}/preview?bands=1").json()
    proj = client.get(f"/v1/resumes/{rid}/projection").json()
    line_ids = {u["id"] for u in proj["units"].values() if u["role"]}
    # A string argument carries no label and falls back to the rows of its block, which
    # is most of the form: without it the highlight was missing from 24 of 37 units.
    arg_ids = {u["id"] for u in proj["units"].values()
               if u["kind"] == "arg" and not u["style"]}
    assert len(line_ids) == 13 and len(arg_ids) == 24
    assert set(got["bands"]) == line_ids | arg_ids
    assert all(len(got["bands"][uid]) == 1 for uid in line_ids)
    # An edit returns the bands of the document it just wrote, not of the old one: a
    # bullet rewritten to wrap over several lines has one band before and several after.
    bullet = next(u for u in proj["units"].values() if u["role"] == "bullet")
    assert len(got["bands"][bullet["id"]]) == 1
    out = client.post(f"/v1/resumes/{rid}/edit", json={
        "hash": proj["hash"], "kind": "set_line", "unit": bullet["id"],
        "expect": bullet["value"], "text": "Rebuilt the ledger " * 40, "marks": [],
    }).json()
    bands = out["preview"]["bands"]
    assert set(bands) == line_ids | arg_ids
    assert len(bands[bullet["id"]]) >= 4
    assert all(len(rows) >= 1 for rows in bands.values())


def test_a_preview_nobody_is_highlighting_pays_for_no_bands(client):
    """The design runs the band eval 「只在欄位分頁開著的時候跑」. As written, `/preview`
    computed them for everyone -- and the shell's autosave calls it 400ms after every
    keystroke burst in the *Typst* tab, where the band cache misses every time because
    the source just changed. So: off unless the caller says it is showing them."""
    rid = resume_with(client, read("synthetic-en"))
    plain = client.get(f"/v1/resumes/{rid}/preview")
    assert plain.status_code == 200
    assert plain.json()["pages"] and plain.json()["text_layers"]
    assert plain.json()["bands"] == {}
    # ...and asking still works, which is what makes the default a choice.
    assert client.get(f"/v1/resumes/{rid}/preview?bands=true").json()["bands"]


def test_a_failing_eval_costs_the_bands_not_the_preview(client, monkeypatch):
    rid = resume_with(client, read("synthetic-en"))

    def broken(*args, **kwargs):
        raise RuntimeError("eval is down")

    monkeypatch.setattr(resume_write, "anchor_bands", broken)
    r = client.get(f"/v1/resumes/{rid}/preview")
    assert r.status_code == 200 and r.json()["pages"] and r.json()["bands"] == {}


def test_a_failing_eval_costs_the_bands_not_the_edit(client, monkeypatch):
    rid = resume_with(client, read("synthetic-en"))

    def broken(*args, **kwargs):
        raise RuntimeError("eval is down")

    monkeypatch.setattr(resume_write, "anchor_bands", broken)
    r = client.post(
        f"/v1/resumes/{rid}/edit", json=title_intent(client, rid, "Staff Engineer"))
    assert r.status_code == 200
    out = r.json()
    assert out["preview"]["pages"] and out["preview"]["bands"] == {}
    assert "Staff Engineer" in out["typst_source"]


def test_preview_holds_no_pool_connection_while_typst_runs(client, monkeypatch):
    engine = get_engine()
    out = [0]

    def took(*_):
        out[0] += 1

    def gave(*_):
        out[0] -= 1

    rid = resume_with(client, read("synthetic-en"))
    real_compile = resume_write.compile_preview_pages
    real_bands = resume_write.anchor_bands
    seen: dict[str, list[int]] = {"compile": [], "eval": []}

    def compile_and_look(source):
        seen["compile"].append(out[0])
        return real_compile(source)

    def bands_and_look(*args, **kwargs):
        seen["eval"].append(out[0])
        return real_bands(*args, **kwargs)

    monkeypatch.setattr(resume_write, "compile_preview_pages", compile_and_look)
    monkeypatch.setattr(resume_write, "anchor_bands", bands_and_look)
    event.listen(engine, "checkout", took)
    event.listen(engine, "checkin", gave)
    try:
        # With the bands, so both subprocesses are covered: the eval is the one added by
        # this branch and it must not pin a slot either.
        assert client.get(f"/v1/resumes/{rid}/preview?bands=1").status_code == 200
    finally:
        event.remove(engine, "checkout", took)
        event.remove(engine, "checkin", gave)
    assert seen == {"compile": [0], "eval": [0]}


def test_an_english_locale_gets_its_own_words_through_the_endpoint(client, db_session):
    """The wire half of the placeholder. `apply_intent` has no locale, so the caller
    brings the words; with the server's CJK defaults an English-locale résumé got
    「（待填）」 in its `url:` and date arguments, easy to miss before export."""
    src = read("synthetic-en")
    rid = resume_with(client, src)
    proj = client.get(f"/v1/resumes/{rid}/projection").json()
    r = client.post(f"/v1/resumes/{rid}/edit", json={
        "hash": proj["hash"], "kind": "insert_section",
        "placeholder": "(to fill in)", "title": "New section"})
    assert r.status_code == 200, r.text
    out = r.json()["typst_source"]
    assert out.endswith("\n== New section\n\n(to fill in)\n\n")
    assert "（待填）" not in out and "新段落" not in out
    assert stored(db_session, rid) == out
    # It still compiled -- that is what let it commit -- and the document is unchanged
    # apart from the appended section.
    assert out.startswith(src.rstrip("\n"))

    # A malformed word is refused with a reason, and nothing is written.
    bad = client.post(f"/v1/resumes/{rid}/edit", json={
        "hash": r.json()["projection"]["hash"], "kind": "insert_section",
        "placeholder": "two\nlines"})
    assert bad.status_code == 400, bad.text
    assert bad.json()["detail"]["code"] == "unsupported"
    assert "one line" in bad.json()["detail"]["detail"]
    assert stored(db_session, rid) == out


def test_a_refusal_carries_the_reason_the_panel_words_it_from(client):
    """`unsupported` covers four refusals for `insert_item`, and the panel has to say
    which in the person's own language -- it cannot do that off one English sentence, and
    was showing the positional case's wording for all four."""
    src = read("synthetic-en")
    # an empty Experience section whose template has no emitter: the common refusal now
    # that the button is offered on an empty section at all
    empty = src[:src.index("#work(")] + src[src.index("#pagebreak()"):]
    rid = resume_with(client, empty.replace("basic-resume:0.2.9", "not-a-template:1.0.0"))
    proj = client.get(f"/v1/resumes/{rid}/projection").json()
    sec = next(s for s in proj["sections"] if s["title_unit"]
               and proj["units"][s["title_unit"]]["value"] == "Experience")
    r = client.post(f"/v1/resumes/{rid}/edit", json={
        "hash": proj["hash"], "kind": "insert_item", "target": sec["id"]})
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "unsupported"
    assert r.json()["detail"]["reason"] == "no_emitter"

    # A refusal with nothing to distinguish sends no reason at all, and the panel then
    # shows the generic sentence with the detail.
    bad = client.post(f"/v1/resumes/{rid}/edit", json={
        "hash": proj["hash"], "kind": "insert_bullet", "target": "nope"})
    assert bad.status_code == 400
    assert bad.json()["detail"]["code"] == "unsupported"
    assert "reason" not in bad.json()["detail"]
