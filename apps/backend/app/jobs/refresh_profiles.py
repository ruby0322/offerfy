"""Rebuild talent profiles that no longer match what they were built from.

How you recover after a schema_version bump or a prompt change, without
waiting for every person to press Rebuild themselves.

Usage (compose):
  docker compose run --rm backend python -m app.jobs.refresh_profiles --stale-only
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.db import get_engine, get_session_factory
from app.models import TalentProfile, User
from app.services.consent import has_consent_many
from app.services.metering import metered, system_guard
from app.services.profile import build_profile, is_stale, record_build_spend


async def _run(session, *, limit: int, stale_only: bool) -> int:
    profiles = (
        session.query(TalentProfile).order_by(TalentProfile.built_at.asc()).all()
    )
    # Never rebuild for someone whose newest answer is no: the build itself
    # sends resume content to the provider, which only the grant covers.
    allowed = has_consent_many(
        session,
        [profile.user_id for profile in profiles],
        purpose="talent_introduction",
    )
    built = 0
    for profile in profiles:
        if built >= limit:
            break
        if profile.user_id not in allowed:
            continue
        user = session.get(User, profile.user_id)
        if user is None:
            continue
        if stale_only and not is_stale(session, user, profile):
            continue
        # system_guard, not preflight: nobody is waiting on an HTTP response
        # here, so refusing would only mean this job 429s itself. It still
        # records -- before this wrap, every build in this loop spent real
        # money that was invisible to the telemetry, to the weekly budget and
        # to the global ceiling alike.
        guard = system_guard(
            user=user,
            purpose="profile",
            reason="nightly stale-profile refresh; no HTTP caller to refuse",
        )
        async with metered(guard) as meter:
            result = await build_profile(session, user)
            if result.profile is None:
                session.rollback()
                print(f"{user.id}: {result.error}", file=sys.stderr)
                continue
            session.commit()
            record_build_spend(session, user, result, meter)
            built += 1
    return built


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.jobs.refresh_profiles")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument(
        "--stale-only",
        action="store_true",
        help="Skip profiles that still match their source revision and facts",
    )
    args = parser.parse_args(argv)

    get_engine()
    session = get_session_factory()()
    try:
        built = asyncio.run(
            _run(session, limit=args.limit, stale_only=args.stale_only)
        )
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        session.close()
    print(f"rebuilt {built}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
