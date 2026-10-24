#!/usr/bin/env python3
"""Measure what `app.services.chat_window` would do to a real transcript.

The bound in `chat_window` is a cost change that also changes what the model
sees, so it needs two numbers, not one: how much it saves, and how much it
could cost a turn in lost context. This reports both for a transcript, so the
parameters can be re-chosen against evidence rather than re-argued.

    # On the host, against the live database (read-only):
    scripts/history_window_report.py --resume-id <id>
    scripts/history_window_report.py --busiest 10

    # Anywhere, from a dump of `[{"role": ..., "content": ...}, ...]`:
    scripts/history_window_report.py --file transcript.json \
        --typst-file resume.typ

**Cost.** `sent` against `total`, in characters and at the measured 3.99
chars/token, plus the fold count over the conversation's whole life. The fold
count is the one that decides whether this saves money at all: each fold is a
prompt-cache rewrite, and a scheme that folds every turn costs about five times
what it saves. Expect roughly one fold per `FOLD_STEP` messages. Anything near
one per turn means the grid has been broken.

**Context.** Of the user messages the window drops, how many carry something
that looks durable -- a quoted string, a proper noun, a number, an "always" or
a "never" -- and of those, how many have no trace left in what the model can
still reach: the retained window, the current Typst document, or the accepted
family memory that `role_card` replays every turn.

That last count is the quality signal, and it is a proxy, not a verdict. It
asks "could this turn still find out?", which is the failure the bound risks:
a turn that cannot see an earlier instruction, assumes, and undoes settled
work. A dropped "call me Ada" whose name is in the document is reachable; a
dropped "never mention the 2019 gap" that left no mark anywhere is not, and a
non-zero count there is the reason to widen the window or to push the fact into
`propose_memory` instead. It does not measure what the model actually does with
what it is given -- only a replay against the provider does that, and this
script deliberately makes no API calls.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.chat_window import (  # noqa: E402
    FOLD_STEP,
    HEAD_MESSAGES,
    TAIL_MIN_MESSAGES,
    message_chars,
    window_history,
)

CHARS_PER_TOKEN = 3.99

# What "looks durable" means. Deliberately broad: a false positive costs a line
# of output, a false negative hides the failure this is looking for.
STICKY_PATTERNS = (
    re.compile(r'"[^"]{3,}"'),
    re.compile(r"[‘’“”]([^“”‘’]{3,})"),
    re.compile(r"\b(?:always|never|don'?t|do not|must|stop|keep|prefer|instead of)\b", re.I),
    re.compile(r"\b(?:總是|永遠|不要|別|必須|保持|改成|一律)"),
    re.compile(r"\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})+\b"),
    re.compile(r"\b\d{4}\b"),
)

# The distinctive strings pulled out of a sticky message and looked for in what
# the model can still reach. Short and common words would match everywhere.
TERM = re.compile(r"[A-Za-z][A-Za-z'\-]{5,}|\d{4}|[一-鿿]{2,}")
STOPWORDS = {
    "always",
    "because",
    "before",
    "change",
    "instead",
    "please",
    "resume",
    "should",
    "something",
    "without",
}


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            part["text"]
            for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
    return ""


def _is_sticky(text: str) -> bool:
    return any(pattern.search(text) for pattern in STICKY_PATTERNS)


def _terms(text: str) -> set[str]:
    found = set()
    for match in TERM.findall(text):
        word = match.lower()
        if word not in STOPWORDS:
            found.add(word)
    return found


def _tokens(chars: int) -> int:
    return round(chars / CHARS_PER_TOKEN)


def folds_over_life(history: list[dict]) -> int:
    """How many prompt-cache rewrites this conversation would have paid for."""
    rewrites = 0
    previous = None
    for count in range(1, len(history) + 1):
        cut = window_history(history[:count]).cut
        if previous is not None and cut != previous:
            rewrites += 1
        previous = cut
    return rewrites


def report(
    label: str,
    history: list[dict],
    typst_source: str = "",
    memory: list[str] | None = None,
) -> dict:
    window = window_history(history)
    reachable = (
        typst_source
        + "\n"
        + "\n".join(memory or [])
        + "\n"
        + "\n".join(_text(item["content"]) for item in window.items)
    ).lower()

    dropped = history[HEAD_MESSAGES : window.cut] if window.folded else []
    sticky: list[tuple[int, str, set[str]]] = []
    for offset, message in enumerate(dropped):
        if message.get("role") != "user":
            continue
        text = _text(message.get("content"))
        if _is_sticky(text):
            sticky.append((HEAD_MESSAGES + offset, text, _terms(text)))

    unreachable = [
        (index, text)
        for index, text, terms in sticky
        if terms and not any(term in reachable for term in terms)
    ]

    result = {
        "label": label,
        "messages": len(history),
        "total_chars": window.total_chars,
        "sent_chars": window.sent_chars,
        "total_tokens": _tokens(window.total_chars),
        "sent_tokens": _tokens(window.sent_chars),
        "cut": window.cut,
        "dropped": window.dropped,
        "folds": folds_over_life(history),
        "sticky_dropped": len(sticky),
        "unreachable": len(unreachable),
        "unreachable_samples": [
            {"index": index, "head": text[:120]} for index, text in unreachable[:5]
        ],
    }
    return result


def print_report(result: dict) -> None:
    total, sent = result["total_tokens"], result["sent_tokens"]
    saved = total - sent
    share = f"{saved / total:.0%}" if total else "n/a"
    print(f"\n=== {result['label']} ===")
    print(f"  rows (user/assistant)   {result['messages']}")
    print(
        f"  history tokens per round {total} -> {sent}"
        f"  ({saved} saved, {share})"
    )
    print(f"  cut / dropped            {result['cut']} / {result['dropped']}")
    turns = max(1, result["messages"] // 2)
    print(
        f"  prompt-cache rewrites    {result['folds']} over ~{turns} turns"
        f"  (one per {result['messages'] / result['folds']:.0f} rows)"
        if result["folds"]
        else "  prompt-cache rewrites    0 (nothing dropped)"
    )
    print(
        f"  dropped user messages that look durable: {result['sticky_dropped']}"
        f", of which unreachable: {result['unreachable']}"
    )
    for sample in result["unreachable_samples"]:
        print(f"      #{sample['index']}: {sample['head']}")


def _from_db(resume_id: str | None, busiest: int) -> list[tuple[str, list[dict], str, list[str]]]:
    from app.db import get_session_factory
    from app.models import ChatMessage, Resume
    from app.services.memory import accepted_memory_texts

    session = get_session_factory()()
    try:
        ids = [resume_id] if resume_id else []
        if not ids:
            rows = (
                session.query(ChatMessage.resume_id)
                .filter(ChatMessage.role.in_(("user", "assistant")))
                .all()
            )
            counts: dict[str, int] = {}
            for (rid,) in rows:
                counts[rid] = counts.get(rid, 0) + 1
            ids = [rid for rid, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:busiest]]
        out = []
        for rid in ids:
            resume = session.get(Resume, rid)
            history = [
                {"role": row.role, "content": row.content}
                for row in session.query(ChatMessage)
                .filter(ChatMessage.resume_id == rid)
                .filter(ChatMessage.role.in_(("user", "assistant")))
                .order_by(ChatMessage.created_at.asc())
                .all()
            ]
            out.append(
                (
                    rid,
                    history,
                    resume.typst_source if resume else "",
                    accepted_memory_texts(session, rid),
                )
            )
        return out
    finally:
        session.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume-id")
    parser.add_argument("--busiest", type=int, default=0)
    parser.add_argument("--file", type=Path)
    parser.add_argument("--typst-file", type=Path)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    print(
        f"window: head={HEAD_MESSAGES} step={FOLD_STEP} floor={TAIL_MIN_MESSAGES}"
        f"  (nothing dropped below {HEAD_MESSAGES + FOLD_STEP + TAIL_MIN_MESSAGES} rows)",
        file=sys.stderr,
    )

    cases: list[tuple[str, list[dict], str, list[str]]] = []
    if args.file:
        history = json.loads(args.file.read_text())
        source = args.typst_file.read_text() if args.typst_file else ""
        cases.append((str(args.file), history, source, []))
    elif args.resume_id or args.busiest:
        cases = _from_db(args.resume_id, args.busiest or 10)
    else:
        parser.error("pass --file, --resume-id or --busiest")

    results = [report(label, history, source, memory) for label, history, source, memory in cases]
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for result in results:
            print_report(result)
        unreachable = sum(r["unreachable"] for r in results)
        print(
            f"\n{len(results)} transcript(s); "
            f"{unreachable} dropped instruction(s) with no trace anywhere."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
