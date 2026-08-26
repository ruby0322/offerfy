from __future__ import annotations

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.config import get_settings


def _client():
    settings = get_settings()
    if not settings.s3_configured():
        return None
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
        config=Config(s3={"addressing_style": "path"}),
    )


def put_object(key: str, data: bytes, content_type: str) -> str | None:
    settings = get_settings()
    client = _client()
    if client is None:
        return None
    try:
        client.put_object(
            Bucket=settings.s3_bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )
    except (BotoCoreError, ClientError):
        # Storage is optional for local; do not block resume creation.
        return None
    return key
