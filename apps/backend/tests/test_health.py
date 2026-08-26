def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body.get("status") == "ok" or body == {"ok": True}


def test_readyz(client):
    response = client.get("/readyz")
    assert response.status_code == 200


def test_client_fixture_uses_sqlite(client):
    from app.db import get_engine

    assert get_engine().dialect.name == "sqlite"
