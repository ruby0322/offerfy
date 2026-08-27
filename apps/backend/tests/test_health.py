def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body.get("status") == "ok" or body == {"ok": True}


def test_healthz_sse_probe(client, monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    with client.stream("GET", "/healthz/sse") as response:
        assert response.status_code == 200
        assert "event-stream" in (response.headers.get("content-type") or "")
        assert response.headers.get("x-accel-buffering") == "no"
        body = "".join(response.iter_text())
    assert '"n":1' in body and '"n":2' in body


def test_readyz(client):
    response = client.get("/readyz")
    assert response.status_code == 200


def test_client_fixture_uses_sqlite(client):
    from app.db import get_engine

    assert get_engine().dialect.name == "sqlite"
