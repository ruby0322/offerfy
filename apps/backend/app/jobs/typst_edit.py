"""Apply a Typst source patch for a long chat job.

Phase 1 chat usually runs in-process. This CLI is the Argo WorkflowTemplate
entrypoint when a patch is offloaded. It does not compile or run ATS.
"""

from __future__ import annotations

import argparse
import json
import sys

from app.db import get_engine, get_session_factory
from app.models import Resume
from app.typst_edit import apply_typst_edit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.jobs.typst_edit")
    parser.add_argument("--resume-id", required=True)
    parser.add_argument("--patch", required=True, help="JSON object for apply_typst_edit")
    args = parser.parse_args(argv)

    try:
        patch = json.loads(args.patch)
    except json.JSONDecodeError as exc:
        print(f"invalid patch JSON: {exc}", file=sys.stderr)
        return 2
    if not isinstance(patch, dict):
        print("patch must be a JSON object", file=sys.stderr)
        return 2

    get_engine()
    session = get_session_factory()()
    try:
        resume = session.get(Resume, args.resume_id)
        if resume is None:
            print(f"resume not found: {args.resume_id}", file=sys.stderr)
            return 1
        resume.typst_source = apply_typst_edit(resume.typst_source, patch)
        session.commit()
    except ValueError as exc:
        session.rollback()
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
