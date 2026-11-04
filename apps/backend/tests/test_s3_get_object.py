import io

from botocore.exceptions import ClientError

from app.services.s3 import get_object


def test_get_object_returns_none_when_unconfigured(monkeypatch):
    monkeypatch.setattr("app.services.s3._client", lambda: None)
    assert get_object("uploads/x/y/z.pdf") is None


def test_get_object_returns_none_for_missing_key(monkeypatch):
    class _StubClient:
        def get_object(self, Bucket, Key):
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")

    monkeypatch.setattr("app.services.s3._client", lambda: _StubClient())
    assert get_object("uploads/missing") is None


def test_get_object_returns_bytes(monkeypatch):
    class _StubClient:
        def get_object(self, Bucket, Key):
            return {"Body": io.BytesIO(b"hello")}

    monkeypatch.setattr("app.services.s3._client", lambda: _StubClient())
    assert get_object("uploads/x") == b"hello"
