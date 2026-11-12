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

`strip_boilerplate` rejoins survivors with a newline, never a space.
Descriptions hold newline-separated bullets with no terminator; joining
with a space would splice the tail of one bullet onto the head of the next
and manufacture a needle ("computer" + "vision") that neither contained.
Needles are space-padded, and a newline is not a space.

Everything here is pure: no database, no I/O. Text is casefolded once up
front, so hashes are over casefolded sentences and stripped output is
casefolded too, which is what needle matching wants anyway.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Iterable

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
    if len(docs) < BOILERPLATE_MIN_POSTINGS:
        return frozenset()
    counts: Counter[str] = Counter()
    for doc in docs:
        counts.update({sentence_hash(s) for s in split_sentences(doc)})
    threshold = BOILERPLATE_DOC_FREQUENCY * len(docs)
    return frozenset(h for h, n in counts.items() if n >= threshold)


def strip_boilerplate(text: str, hashes: frozenset[str]) -> str:
    """Casefolded text without the sentences whose hash is in `hashes`.

    Short segments are always kept (they are never boilerplate). Survivors
    are joined with "\\n", never " ", so no needle forms across a junction.
    """
    kept = [
        s
        for s in _segments(text)
        if len(s) <= MIN_SENTENCE_CHARS or sentence_hash(s) not in hashes
    ]
    return "\n".join(kept)
