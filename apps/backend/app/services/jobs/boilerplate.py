"""Per-company boilerplate sentences, detected by document frequency.

`function_of` (see enrich.py) reads a posting's description for needles. A
description is mostly the employer talking about itself, and that part is
identical across a company's postings: one sentence shared by all 2,375
Anduril postings ("...committed to bringing cutting-edge autonomy, AI,
computer vision, sensor fusion...") filed 76% of them as machine learning,
"People Business Partner" and "Senior Buyer" included. The fix is not a
better needle list; it is to stop reading the sentences that say nothing
about the role. A sentence that appears in at least half of a company's
postings is the company's, not the role's, so it is removed before needles
are matched.

The 0.5 cutoff (`BOILERPLATE_DOC_FREQUENCY`) is not a tuning knob. Measured
against the live 21,773-posting catalogue, cutoffs of 0.30 and 0.50 produce
byte-identical output, because boilerplate is bimodal: a sentence sits in
~100% of a company's postings or well under 30%, and almost nothing lies
between. Do not spend time re-tuning it.

`MIN_SENTENCE_CHARS` is load-bearing. A short generic fragment ("Apply
now.", "Remote.") recurs in most postings of every company, so on document
frequency alone it would be classed as that company's boilerplate. It says
nothing about the company, and a short segment is just as likely to be a
whole bullet that names the role. Segments of that length or shorter are
neither counted nor stripped.

Document frequency is over DISTINCT sentences per document: a sentence
repeated inside one posting counts once toward its own frequency, otherwise
a single posting could single-handedly make its own sentence "common".

`strip_boilerplate` rejoins survivors with " \n " (space, newline, space).
Descriptions hold newline-separated bullets with no terminator; joining
with a bare space would splice the tail of one bullet onto the head of the
next and manufacture a needle ("computer" + "vision") that neither
contained -- the newline prevents that. But needles are space-padded
(' react ', ' sql,'), and a bare newline is not a space, so a needle at the
start or end of a segment would stop matching; the surrounding spaces
restore that boundary match. Both halves are load-bearing.

Text is casefolded once up front, so hashes are over casefolded sentences
and stripped output is casefolded too, which is what needle matching wants
anyway.

The detection and stripping functions are pure. The database seam at the
bottom (`recompute_boilerplate`, `load_boilerplate`) is the only part that
touches a Session; it flushes but never commits, the caller owns the
transaction, as with `enrich_jobs` and `upsert_jobs`.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Collection, Iterable
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CompanyBoilerplate, Job

BOILERPLATE_MIN_POSTINGS: int = 5
BOILERPLATE_DOC_FREQUENCY: float = 0.5
MIN_SENTENCE_CHARS: int = 25
# Declared here, not imported from enrich._BACKFILL_BATCH_SIZE: enrich imports
# this module, so the reverse import would be circular.
RECOMPUTE_BATCH_SIZE: int = 500

_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")


def _segments(text: str) -> list[str]:
    """Casefolded, stripped, non-empty segments of any length."""
    return [s for s in (p.strip() for p in _SPLIT_RE.split(text.casefold())) if s]


def split_sentences(text: str) -> list[str]:
    """Casefolded sentences longer than MIN_SENTENCE_CHARS."""
    return [s for s in _segments(text) if len(s) > MIN_SENTENCE_CHARS]


def sentence_hash(sentence: str) -> str:
    """sha256 hex of one already-split (casefolded, stripped) sentence."""
    return hashlib.sha256(sentence.encode("utf-8")).hexdigest()


def boilerplate_hashes(descriptions: Iterable[str]) -> frozenset[str]:
    """Hashes of sentences present in >= BOILERPLATE_DOC_FREQUENCY of the corpus.

    Empty when the corpus is smaller than BOILERPLATE_MIN_POSTINGS: too few
    postings to tell a company's voice from coincidence.
    """
    docs = list(descriptions)
    counts: Counter[str] = Counter()
    for doc in docs:
        counts.update({sentence_hash(s) for s in split_sentences(doc)})
    return _hashes_from_counts(counts, len(docs))


def _hashes_from_counts(counts: Counter[str], postings: int) -> frozenset[str]:
    """The cutoff itself, shared so the streaming path cannot drift from it."""
    if postings < BOILERPLATE_MIN_POSTINGS:
        return frozenset()
    threshold = BOILERPLATE_DOC_FREQUENCY * postings
    return frozenset(h for h, n in counts.items() if n >= threshold)


def strip_boilerplate(text: str, hashes: frozenset[str]) -> str:
    """Casefolded text without the sentences whose hash is in `hashes`.

    Short segments are always kept (they are never boilerplate). Survivors
    are joined with " \\n " (the newline stops needles splicing across a junction,
    the spaces keep space-padded needles matching at a segment edge).
    """
    kept = [
        s
        for s in _segments(text)
        if len(s) <= MIN_SENTENCE_CHARS or sentence_hash(s) not in hashes
    ]
    return " \n ".join(kept)


def normalize_company(name: str) -> str:
    """The one definition of a company's storage key and lookup key.

    Collapses whitespace runs and casefolds, so "Anduril" and "  anduril "
    are one company. `recompute_boilerplate` stores rows under this key and
    `load_boilerplate` normalizes its lookups with it; do not inline the
    expression elsewhere, import this.
    """
    return " ".join(name.split()).casefold()


def recompute_boilerplate(db: Session, *, now: datetime | None = None) -> set[str]:
    """Rebuild every company's boilerplate row from its ACTIVE postings.

    Returns the normalized names of companies whose hash set changed,
    including companies whose row was deleted because they no longer clear
    BOILERPLATE_MIN_POSTINGS (otherwise postings would keep being stripped
    against a corpus that no longer exists). Flushes, never commits.

    The catalogue's description_text is ~200MB, so only company and text are
    selected, streamed in RECOMPUTE_BATCH_SIZE chunks, and reduced at once
    to per-company sentence-hash counters; no text is retained.
    """
    now = now or datetime.now(timezone.utc)
    counters: dict[str, Counter[str]] = {}
    postings: Counter[str] = Counter()
    stmt = (
        select(Job.company, Job.description_text)
        .where(Job.is_active.is_(True))
        .execution_options(yield_per=RECOMPUTE_BATCH_SIZE)
    )
    for company, text in db.execute(stmt):
        key = normalize_company(company)
        postings[key] += 1
        counters.setdefault(key, Counter()).update(
            {sentence_hash(s) for s in split_sentences(text or "")}
        )

    existing = {row.company: row for row in db.scalars(select(CompanyBoilerplate))}
    changed: set[str] = set()

    for key, count in postings.items():
        if count < BOILERPLATE_MIN_POSTINGS:
            continue
        hashes = _hashes_from_counts(counters[key], count)
        row = existing.get(key)
        old = frozenset(json.loads(row.sentence_hashes)) if row else frozenset()
        if hashes != old:
            changed.add(key)
        payload = json.dumps(sorted(hashes))
        if row is None:
            db.add(CompanyBoilerplate(
                company=key, sentence_hashes=payload,
                posting_count=count, computed_at=now,
            ))
        else:
            row.sentence_hashes = payload
            row.posting_count = count
            row.computed_at = now

    for key, row in existing.items():
        if postings[key] < BOILERPLATE_MIN_POSTINGS:
            if json.loads(row.sentence_hashes):
                changed.add(key)
            db.delete(row)

    db.flush()
    return changed


def load_boilerplate(db: Session, companies: Collection[str]) -> dict[str, frozenset[str]]:
    """Hash sets for `companies` in one query, keyed by normalized name.

    Companies with no row are absent. Lookups are normalized with
    `normalize_company`, matching how `recompute_boilerplate` stores them.
    """
    keys = {normalize_company(c) for c in companies}
    if not keys:
        return {}
    rows = db.scalars(select(CompanyBoilerplate).where(CompanyBoilerplate.company.in_(keys)))
    return {row.company: frozenset(json.loads(row.sentence_hashes)) for row in rows}
