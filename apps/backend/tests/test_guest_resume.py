from fastapi.testclient import TestClient


def test_create_resume_sets_guest_cookie_and_starter(client: TestClient):
    response = client.post("/v1/resumes", json={"locale": "en", "title": "Master"})
    assert response.status_code in (200, 201)
    assert "offerfy_guest" in response.cookies
    data = response.json()
    assert data["title"] == "Master"
    assert data["source"] == "create"
    assert data["locale"] == "en"
    assert data["import_status"] == "idle"
    assert "@preview/basic-resume:0.2.9" in data["typst_source"]
    assert 'paper: "a4"' in data["typst_source"]


def test_list_and_get_resume_for_guest(client: TestClient):
    created = client.post("/v1/resumes", json={"title": "A", "locale": "en"}).json()
    listed = client.get("/v1/resumes")
    assert listed.status_code == 200
    ids = [row["id"] for row in listed.json()]
    assert created["id"] in ids
    got = client.get(f"/v1/resumes/{created['id']}")
    assert got.status_code == 200
    assert got.json()["id"] == created["id"]


def test_put_updates_typst_source_and_title(client: TestClient):
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    new_source = created["typst_source"].replace("Your Name", "Ada Lovelace", 1)
    response = client.put(
        f"/v1/resumes/{created['id']}",
        json={"typst_source": new_source, "title": "Ada"},
    )
    assert response.status_code == 200
    assert "Ada Lovelace" in response.json()["typst_source"]
    assert response.json()["title"] == "Ada"


def test_upload_txt_returns_resume_with_extracted_comments(client: TestClient):
    response = client.post(
        "/v1/resumes/upload",
        data={"title": "From file", "locale": "en"},
        files={"file": ("notes.txt", b"Hello from upload", "text/plain")},
    )
    assert response.status_code in (200, 201)
    data = response.json()
    assert data["source"] == "upload"
    assert "Hello from upload" in data["typst_source"]
    assert "@preview/basic-resume:0.2.9" in data["typst_source"]


def test_upload_rejects_oversize_or_bad_type(client: TestClient):
    too_big = b"x" * (10 * 1024 * 1024 + 1)
    response = client.post(
        "/v1/resumes/upload",
        files={"file": ("huge.txt", too_big, "text/plain")},
    )
    assert response.status_code == 413

    bad = client.post(
        "/v1/resumes/upload",
        files={"file": ("x.exe", b"MZ", "application/octet-stream")},
    )
    assert bad.status_code == 400


def test_ats_report_has_checks_not_score(client: TestClient):
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    response = client.get(f"/v1/resumes/{created['id']}/ats")
    # Compile may skip internally if typst is missing → 503; otherwise 200.
    if response.status_code == 503:
        return
    assert response.status_code == 200
    body = response.json()
    assert "checks" in body
    assert "score" not in body
    assert "grade" not in body
    names = {c["name"] for c in body["checks"]}
    assert "text_extractable" in names
    for check in body["checks"]:
        assert set(check.keys()) == {"name", "passed"}
