"""GET /v1/resumes/{id}/projection: the parser's output, served unchanged."""

import dataclasses
import json

from app import models
from app.services.revisions import source_hash
from app.services.typst_projection import parse_projection
from tests.test_typst_fixtures import FIXTURES


def read(name: str) -> str:
    return (FIXTURES / f"{name}.typ").read_text(encoding="utf-8")


def resume_with(client, source: str) -> str:
    rid = client.post("/v1/resumes", json={"locale": "en", "title": "T"}).json()["id"]
    r = client.put(f"/v1/resumes/{rid}", json={"typst_source": source})
    assert r.status_code == 200
    return rid


def expected(source: str) -> dict:
    """The dataclasses as JSON: tuples become lists, nothing is renamed or dropped."""
    return json.loads(json.dumps(dataclasses.asdict(parse_projection(source))))


def test_projection_returns_units_for_the_owner(client):
    src = read("synthetic-en")
    rid = resume_with(client, src)
    r = client.get(f"/v1/resumes/{rid}/projection")
    assert r.status_code == 200
    body = r.json()
    assert body["tier"] == "A" and body["hash"] == source_hash(src)
    assert body["units"] and {u["kind"] for u in body["units"].values()} == {"arg", "line"}


def test_projection_is_the_parsers_output_field_for_field(client):
    # Includes the preamble section (title_unit None), marks on a line unit and
    # two-item spans; any field the schema drops or renames shows up as a diff.
    src = read("synthetic-en")
    body = client.get(f"/v1/resumes/{resume_with(client, src)}/projection").json()
    assert body == expected(src)
    assert body["sections"][0]["title_unit"] is None
    assert any(u["marks"] for u in body["units"].values())
    assert all(len(u["span"]) == 2 for u in body["units"].values())


def test_projection_offsets_are_code_points_not_bytes(client):
    src = read("synthetic-zh")
    assert len(src.encode("utf-8")) > len(src)
    body = client.get(f"/v1/resumes/{resume_with(client, src)}/projection").json()
    assert body == expected(src)
    plain_cjk = [
        u
        for u in body["units"].values()
        if u["kind"] == "line"
        and not (u["marks"] or u["escapes"] or u["opaque"])
        and any(ord(c) > 0x2E80 for c in u["value"])
    ]
    assert plain_cjk
    for u in plain_cjk:
        s, e = u["span"]
        assert src[s:e] == u["value"]  # indexing the str by code point lands on the text


def test_projection_serves_tier_b_and_c_sources(client):
    for name, tier in (("synthetic-tier-b", "B"), ("synthetic-tier-c", "C")):
        src = read(name)
        body = client.get(f"/v1/resumes/{resume_with(client, src)}/projection").json()
        assert body["tier"] == tier
        assert body == expected(src)


def test_projection_is_not_persisted(client, db_session):
    rid = resume_with(client, read("synthetic-en"))
    assert client.get(f"/v1/resumes/{rid}/projection").status_code == 200
    assert db_session.query(models.ResumeContent).count() == 0


def test_projection_refuses_a_stranger(client):
    rid = resume_with(client, read("synthetic-en"))
    assert client.get(f"/v1/resumes/{rid}/projection").status_code == 200  # route exists
    client.cookies.clear()
    client.post("/v1/resumes", json={"locale": "en", "title": "mine"})  # a second guest
    assert client.get(f"/v1/resumes/{rid}/projection").status_code in (403, 404)


def test_projection_of_a_missing_resume_is_404(client):
    client.post("/v1/resumes", json={"locale": "en", "title": "x"})
    assert client.get("/v1/resumes/nope/projection").status_code == 404
