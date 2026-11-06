#!/usr/bin/env python3
"""Count the profile builds the Anthropic-id-to-OpenAI defect took down.

Read-only. Run this against prod to size the damage the fix in
`services.profile.build_profile` stops: before that fix, an unpinned build
sent `model_catalog.DEFAULT_MODEL` ("claude-haiku-5-5", an Anthropic id) to
the OpenAI Responses endpoint, which rejects it. `CHAT_MODEL` is blank in
`.env`, so that was every build, not an edge case.

What makes the failures findable after the fact: `record_build_spend` writes
an `llm_calls` row whenever `result.model` is set, and the model id is set on
the result *before* the request goes out. So a failed build still leaves a
row -- carrying the Anthropic id it tried to send, and with NULL token
counts, because a 400 returns no usage. That pair is the fingerprint:

    purpose = 'profile' AND model LIKE 'claude-%' AND input_tokens IS NULL

A row with a claude id and real token counts would mean the opposite --
something did serve that id (a proxy `OPENAI_BASE_URL`, say) -- so the two
are reported separately rather than lumped together.

Usage, from apps/backend:

    DATABASE_URL=<prod url> scripts/check_profile_build_failures.py

Prints counts and a date range, never profile content: `talent_profiles`
rows are personal data and this only ever counts them.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, text  # noqa: E402

FINGERPRINT = "purpose = 'profile' AND model LIKE 'claude-%' AND input_tokens IS NULL"


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("Set DATABASE_URL to the database to inspect.")
        return 2

    engine = create_engine(url)
    with engine.connect() as conn:

        def one(sql: str):
            return conn.execute(text(sql)).first()

        print("=== llm_calls, purpose='profile' ===")
        row = one(
            "SELECT count(*) AS n,"
            " count(DISTINCT user_id) AS users,"
            " min(created_at) AS first, max(created_at) AS last"
            " FROM llm_calls WHERE purpose = 'profile'"
        )
        print(f"  all profile calls      : {row.n} ({row.users} users)")
        if row.n:
            print(f"  first / last           : {row.first} / {row.last}")

        print()
        print("=== the defect's fingerprint (claude id, no usage) ===")
        row = one(
            "SELECT count(*) AS n, count(DISTINCT user_id) AS users,"
            " min(created_at) AS first, max(created_at) AS last"
            f" FROM llm_calls WHERE {FINGERPRINT}"
        )
        print(f"  failed builds          : {row.n} ({row.users} users)")
        if row.n:
            print(f"  first / last           : {row.first} / {row.last}")
            print("  -> these are builds that never reached the provider.")

        print()
        print("=== a claude id that DID serve (proxy?) ===")
        row = one(
            "SELECT count(*) AS n FROM llm_calls"
            " WHERE purpose = 'profile' AND model LIKE 'claude-%'"
            " AND input_tokens IS NOT NULL"
        )
        print(f"  claude id with usage   : {row.n}")
        if row.n:
            print("  -> something served an Anthropic id; check OPENAI_BASE_URL.")

        print()
        print("=== per-model breakdown ===")
        rows = conn.execute(
            text(
                "SELECT model, count(*) AS n,"
                " count(input_tokens) AS with_usage"
                " FROM llm_calls WHERE purpose = 'profile'"
                " GROUP BY model ORDER BY n DESC"
            )
        ).fetchall()
        for item in rows:
            print(
                f"  {item.model:<28} {item.n:>6} calls,"
                f" {item.with_usage:>6} with usage"
            )
        if not rows:
            print("  (no profile calls recorded at all)")

        print()
        print("=== talent_profiles (counts only) ===")
        row = one("SELECT count(*) AS n FROM talent_profiles")
        print(f"  profiles stored        : {row.n}")
        row = one(
            "SELECT count(*) AS n FROM users"
            " WHERE profile_resume_id IS NOT NULL"
        )
        print(f"  users pointing at one  : {row.n}")
        print(
            "  -> a pointer set with no stored profile is the user-visible"
            " shape of this bug."
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
