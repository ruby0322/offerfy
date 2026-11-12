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

`strip_boilerplate` EXCISES the matching sentences in place: it replaces
each one's span in the original string with a newline and leaves every other
byte -- case, punctuation, the separators between survivors -- exactly as
the board published it. It deliberately does not re-segment the text and
rejoin the survivors, which two earlier attempts did and which cannot be
made correct: whatever separator the rejoin picks is applied to every
boundary of every posting, including the postings of companies that have no
boilerplate at all. A bare "\\n" join dropped the space that space-padded
needles (' react ', ' sql,') need at a segment edge, so real matches were
lost; a " \\n " join instead ADDED a space at every edge, so bullet lists of
bare technology names ("Our stack:\\nReact\\nTypeScript") began matching
needles their raw text never matched. In-place excision has neither
failure: a posting with no boilerplate comes back byte-identical, so the
measured projection this mechanism was accepted on stays reproducible and
the first run's hash churn is confined to companies that really do have
boilerplate.

The anti-splice invariant survives, and is the reason the excised span is
replaced by a newline rather than by nothing: descriptions hold
newline-separated bullets with no terminator, so closing the gap would
splice the tail of one bullet onto the head of the next and manufacture a
needle ("computer" + "vision") that neither contained.

Hashing casefolds; excision does not. Segments are hashed in casefolded
form, so a case-only edit cannot change whether a sentence is recognised as
boilerplate, but the text handed back carries the original case --
`function_of` casefolds anyway. Identification and excision therefore work
on two different strings, which is why segments are located as (start, end)
offsets into the original: casefold is not length-preserving ("ß" folds to
"ss"), so a casefolded copy's offsets cannot be indexed back into the text
they came from.

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


def _segment_spans(text: str) -> list[tuple[int, int]]:
    """(start, end) of each non-empty segment, as offsets into `text` itself.

    Offsets rather than substrings because `strip_boilerplate` identifies a
    segment by the hash of its casefolded form but has to cut it out of the
    original string. Shared with `_segments`, so the text that gets hashed
    and the span that gets excised cannot drift apart.
    """
    bounds: list[tuple[int, int]] = []
    pos = 0
    for separator in _SPLIT_RE.finditer(text):
        bounds.append((pos, separator.start()))
        pos = separator.end()
    bounds.append((pos, len(text)))

    spans: list[tuple[int, int]] = []
    for start, end in bounds:
        raw = text[start:end]
        inner_start = start + (len(raw) - len(raw.lstrip()))
        inner_end = end - (len(raw) - len(raw.rstrip()))
        if inner_end > inner_start:
            spans.append((inner_start, inner_end))
    return spans


def _segments(text: str) -> list[str]:
    """Casefolded, stripped, non-empty segments of any length."""
    return [text[start:end].casefold() for start, end in _segment_spans(text)]


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
    """`text` with the sentences whose hash is in `hashes` excised in place.

    Each matching segment's span is replaced by a newline -- the anti-splice
    newline, see the module docstring -- and nothing else is rewritten, so a
    posting that contains none of its company's boilerplate comes back
    byte-identical. That covers every posting of a company with an empty hash
    set or no row at all, which is the case the two earlier rejoin-based
    implementations got wrong. Case is preserved; only the hashing casefolds.

    Short segments are never boilerplate (`MIN_SENTENCE_CHARS`), so they are
    never excised even when their hash is in `hashes`.

    Leading and trailing whitespace is stripped only when something was
    actually excised; that is what makes a posting whose every sentence is
    boilerplate come back as "" rather than as the newlines that replaced it,
    so `function_of` falls back to the title.
    """
    if not hashes:
        return text

    pieces: list[str] = []
    cut = 0
    for start, end in _segment_spans(text):
        segment = text[start:end].casefold()
        if len(segment) <= MIN_SENTENCE_CHARS:
            continue
        if sentence_hash(segment) not in hashes:
            continue
        pieces.append(text[cut:start])
        pieces.append("\n")
        cut = end

    if not pieces:
        return text
    pieces.append(text[cut:])
    return "".join(pieces).strip()


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
