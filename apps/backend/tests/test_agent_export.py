"""The signed PDF export: link minted by the `export_resume` tool, served by a
public route. Not a ResumeShare token: no table, no revoke, ten minutes."""

import json
import time
from urllib.parse import quote

import pytest

from app.config import get_settings
from app.deps import _sign
from app.models import ResumeShare
from tests.conftest import _resume_for
from tests.test_mcp_tools import _call, _caller, _is_tool_error, _text


def _token(resume_id: str, expiry: int, secret: str | None = None) -> str:
    return _sign(f"{resume_id}|{expiry}", secret or get_settings().auth_token_secret)


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
    rid, _exp = value.split("|")
    assert _fetch(client, f"{rid}|9999999999.{sig}").status_code in (403, 404)


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
