from __future__ import annotations

import logging

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.config import get_settings

logger = logging.getLogger(__name__)

# Environments where a missing or broken object store must not block a request.
# Everywhere else a failed write is surfaced to the caller: silently dropping an
# upload loses the user's file and still reports success, which is worse than a
# visible error. Keep this list in sync with APP_ENV values used by deployments.
_FAIL_SOFT_ENVS = frozenset({"development", "dev", "local", "test"})


def _fail_soft(app_env: str) -> bool:
    return app_env.strip().lower() in _FAIL_SOFT_ENVS


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


def put_object(
    key: str, data: bytes, content_type: str, *, required: bool = True
) -> str | None:
    """Store an object.

    required=True (default): outside fail-soft envs a failed write raises, so
    the caller's transaction rolls back instead of recording a file that was
    never written. Pass required=False for derivative or archival copies the
    request does not depend on -- those only get logged.
    """
    settings = get_settings()
    client = _client()
    if client is None:
        if required and not _fail_soft(settings.app_env):
            raise RuntimeError(
                f"Object storage is not configured (app_env={settings.app_env}); "
                f"refusing to silently drop upload key={key}"
            )
        logger.warning("S3 not configured; dropping object key=%s", key)
        return None
    try:
        client.put_object(
            Bucket=settings.s3_bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )
    except (BotoCoreError, ClientError):
        logger.exception(
            "S3 put_object failed: bucket=%s key=%s", settings.s3_bucket, key
        )
        if required and not _fail_soft(settings.app_env):
            # Let the caller's transaction roll back rather than committing a
            # record that claims a file exists when the write never landed.
            raise
        return None
    return key


def delete_object(key: str) -> bool:
    settings = get_settings()
    client = _client()
    if client is None:
        logger.warning("S3 not configured; cannot delete object key=%s", key)
        return False
    try:
        client.delete_object(Bucket=settings.s3_bucket, Key=key)
    except (BotoCoreError, ClientError):
        # A stale object must not fail the caller's delete, but the orphan has to
        # be visible: the file still holds user data after the record is gone.
        logger.exception(
            "S3 delete_object failed, object may be orphaned: bucket=%s key=%s",
            settings.s3_bucket,
            key,
        )
        return False
    return True
