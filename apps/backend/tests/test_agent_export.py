"""The signed PDF export: link minted by the `export_resume` tool, served by a
public route. Not a ResumeShare token: no table, no revoke, ten minutes."""

import json
import time
from urllib.parse import quote

import pytest

from app.config import get_settings
from app.deps import _sign
from app.models import ResumeShare
from app.services import rate_limit
from tests.conftest import _resume_for
from tests.test_mcp_tools import _call, _caller, _is_tool_error, _text


def _token(resume_id: str, expiry: int, secret: str | None = None) -> str:
    return _sign(
        f"export|{resume_id}|{expiry}", secret or get_settings().auth_token_secret
    )


def _fetch(client, token: str):
    return client.get(f"/v1/resumes/export/{quote(token, safe='')}")


def _bare(url: str) -> str:
    """The tool's url is the public one, behind Next's /api rewrite; the test
    client talks to the backend directly, where routes are bare."""
    # `/api` is the frontend's rewrite onto the backend (see Next's config); the
    # test client talks to the bare backend, where the route has no prefix. The
    # tool is right to return the public path.
    assert url.startswith("/api/v1/resumes/export/"), url
    return url[len("/api"):]


def test_export_returns_a_link_that_serves_a_pdf(client, db_session, a_user, my_resume):
    out = json.loads(_call(client, db_session, a_user, "export_resume",
                           {"resume_id": my_resume.id}))
    served = client.get(_bare(out["url"]))
    assert served.status_code == 200
    assert served.headers["content-type"] == "application/pdf"
    assert served.content.startswith(b"%PDF")
    assert "attachment" in served.headers["content-disposition"]
    assert out["filename"].endswith(".pdf")
    assert out["expires_at"]


def test_the_link_lives_ten_minutes(client, db_session, a_user, my_resume):
    from datetime import datetime

    out = json.loads(_call(client, db_session, a_user, "export_resume",
                           {"resume_id": my_resume.id}))
    left = datetime.fromisoformat(out["expires_at"]).timestamp() - time.time()
    assert 590 < left <= 600


def test_a_fresh_hand_made_token_is_served(client, my_resume):
    # Makes the refusal tests below non-vacuous: the same recipe, unexpired, works.
    assert _fetch(client, _token(my_resume.id, int(time.time()) + 300)).status_code == 200


def test_an_expired_export_token_is_refused(client, my_resume):
    r = _fetch(client, _token(my_resume.id, int(time.time()) - 1))
    assert r.status_code in (403, 404)


def test_a_token_signed_with_another_secret_is_refused(client, my_resume):
    r = _fetch(client, _token(my_resume.id, int(time.time()) + 300, "not-the-secret"))
    assert r.status_code in (403, 404)


def test_a_tampered_export_token_is_refused(client, my_resume):
    assert client.get(f"/v1/resumes/export/{my_resume.id}|9999999999.bogus").status_code \
        in (403, 404)


def test_extending_the_expiry_of_a_real_token_is_refused(client, my_resume):
    good = _token(my_resume.id, int(time.time()) + 300)
    value, sig = good.rsplit(".", 1)
    purpose, rid, _exp = value.split("|")
    assert _fetch(client, f"{purpose}|{rid}|9999999999.{sig}").status_code in (403, 404)


def test_a_signed_value_without_the_export_purpose_is_not_a_token(client, my_resume):
    """`_sign` and `auth_token_secret` are shared with the session cookie. No
    confusion is possible today, which is exactly when to make it impossible:
    an export token says what it is for, and nothing else is one."""
    secret = get_settings().auth_token_secret
    expiry = int(time.time()) + 300
    for value in (
        f"{my_resume.id}|{expiry}",  # what this token used to look like
        f"session|{my_resume.id}|{expiry}",
        my_resume.id,  # the shape of the session cookie's own value
    ):
        assert _fetch(client, _sign(value, secret)).status_code in (403, 404), value


def test_the_export_route_releases_its_connection_before_the_compile(
    client, db_session, a_user, my_resume, monkeypatch
):
    """Public, unauthenticated, and every hit is up to 30s of Typst: holding a
    pool slot across that is how one worker stops answering everything,
    /healthz included. The budget caps how many such hits one link buys; it does
    not make holding a connection across one of them any cheaper."""
    from sqlalchemy import event

    from app.db import get_engine

    engine = get_engine()
    state = {"open": 0}
    seen = {}

    def out(*_a):
        state["open"] += 1

    def back(*_a):
        state["open"] -= 1

    def fake_compile(source, fmt):
        seen["open_during_compile"] = state["open"]
        return b"%PDF-1.7\n%%EOF\n"

    out_url = json.loads(
        _call(client, db_session, a_user, "export_resume", {"resume_id": my_resume.id})
    )["url"]
    db_session.commit()  # release what the fixtures left open on this session
    monkeypatch.setattr("app.routers.resumes.compile_typst", fake_compile)
    event.listen(engine, "checkout", out)
    event.listen(engine, "checkin", back)
    try:
        assert client.get(_bare(out_url)).status_code == 200
    finally:
        event.remove(engine, "checkout", out)
        event.remove(engine, "checkin", back)
    assert seen["open_during_compile"] == 0


def test_a_token_for_another_resume_id_is_refused(client, db_session, a_user, other_user,
                                                  my_resume):
    theirs = _resume_for(db_session, other_user)
    good = _token(my_resume.id, int(time.time()) + 300)
    _value, sig = good.rsplit(".", 1)
    forged = f"{theirs.id}|{int(time.time()) + 300}.{sig}"
    assert _fetch(client, forged).status_code in (403, 404)


@pytest.mark.parametrize("value", ["no-separator", "a|b", "|", "x|1|2", f"r|{10**30}"])
def test_a_signed_but_malformed_value_is_refused_not_a_500(client, value):
    token = _sign(value, get_settings().auth_token_secret)
    assert _fetch(client, token).status_code in (403, 404)


def test_an_export_token_for_a_deleted_resume_is_404(client, db_session, a_user,
                                                     my_resume, monkeypatch):
    out = json.loads(_call(client, db_session, a_user, "export_resume",
                           {"resume_id": my_resume.id}))
    db_session.delete(my_resume)
    db_session.commit()

    def boom(*a, **k):
        raise AssertionError("compiled a resume that no longer exists")

    monkeypatch.setattr("app.routers.resumes.compile_typst", boom)
    assert client.get(_bare(out["url"])).status_code == 404


def test_the_route_is_not_in_the_published_schema(client):
    paths = client.app.openapi()["paths"]
    assert not any("export/{token}" in p for p in paths)
    assert any(p.endswith("/{resume_id}/export") for p in paths)  # the sibling is


def test_export_creates_no_share_and_pins_nothing(client, db_session, a_user, my_resume):
    _call(client, db_session, a_user, "export_resume", {"resume_id": my_resume.id})
    db_session.refresh(my_resume)
    assert my_resume.published_revision_id is None
    assert db_session.query(ResumeShare).count() == 0


def test_export_on_someone_elses_resume_is_not_found(client, db_session, a_user,
                                                     other_user):
    theirs = _resume_for(db_session, other_user)
    call = _caller(client, db_session, a_user)
    body = call("export_resume", {"resume_id": theirs.id})
    assert _is_tool_error(body)
    assert "not found" in _text(body).lower()
    assert "url" not in _text(body)


def test_export_resume_id_of_the_wrong_type_is_a_400(client, db_session, a_user):
    call = _caller(client, db_session, a_user)
    for bad in (3, [], {}, None, False, ""):
        body = call("export_resume", {"resume_id": bad})
        assert _is_tool_error(body), bad
        assert "resume_id is required" in _text(body), (bad, _text(body))


def _fake_compile(_source, _fmt):
    """The budget tests care about status codes, not PDF bytes, and ten real
    compiles is a second and a half of suite time."""
    return b"%PDF-1.7\n%%EOF\n"


def test_one_link_is_served_up_to_its_budget_and_refused_past_it(
    client, my_resume, monkeypatch
):
    """The link needs no credential, so a leaked one is an amplifier for its
    whole ten minutes and every hit is a Typst compile. The budget is the only
    lever: it bounds how many compiles one link can buy."""
    monkeypatch.setattr("app.routers.resumes.compile_typst", _fake_compile)
    token = _token(my_resume.id, int(time.time()) + 300)
    for hit in range(rate_limit.EXPORT_DOWNLOAD_LIMIT):
        assert _fetch(client, token).status_code == 200, hit
    refused = _fetch(client, token)
    assert refused.status_code == 429
    assert refused.headers["retry-after"] == str(int(rate_limit.EXPORT_DOWNLOAD_WINDOW))


def test_a_refused_download_compiles_nothing(client, my_resume, monkeypatch):
    """Where the guard sits is the whole point. A 429 after the compile would
    cost exactly as much CPU as a 200."""
    calls = {"n": 0}

    def counted(source, fmt):
        calls["n"] += 1
        return _fake_compile(source, fmt)

    monkeypatch.setattr("app.routers.resumes.compile_typst", counted)
    token = _token(my_resume.id, int(time.time()) + 300)
    for _ in range(rate_limit.EXPORT_DOWNLOAD_LIMIT + 5):
        _fetch(client, token)
    assert calls["n"] == rate_limit.EXPORT_DOWNLOAD_LIMIT


def test_re_minting_the_link_does_not_reset_the_budget(client, my_resume, monkeypatch):
    """The bucket is keyed on the resume, not the token string. `export_resume`
    mints a fresh token on every call -- a token-keyed bucket would reopen with
    each mint and bound nothing at all."""
    monkeypatch.setattr("app.routers.resumes.compile_typst", _fake_compile)
    base = int(time.time()) + 300
    for hit in range(rate_limit.EXPORT_DOWNLOAD_LIMIT):
        # A different expiry is a different signed token for the same resume.
        assert _fetch(client, _token(my_resume.id, base + hit)).status_code == 200, hit
    assert _fetch(client, _token(my_resume.id, base + 999)).status_code == 429


def test_another_resume_has_its_own_budget(client, db_session, a_user, my_resume,
                                           monkeypatch):
    """Spent on one link, not on the site: a flood through one leaked link must
    not take anyone else's export with it."""
    monkeypatch.setattr("app.routers.resumes.compile_typst", _fake_compile)
    second = _resume_for(db_session, a_user, title="Second")
    db_session.commit()
    token = _token(my_resume.id, int(time.time()) + 300)
    for _ in range(rate_limit.EXPORT_DOWNLOAD_LIMIT + 1):
        _fetch(client, token)
    assert _fetch(client, token).status_code == 429  # the first one is spent
    assert _fetch(client, _token(second.id, int(time.time()) + 300)).status_code == 200
