"""Folding `JobFacets.city` into something a person would pick from.

`enrich.location_facets` stores the first usable segment of the ATS location
string **verbatim** -- that is the right call there, because the segment is
evidence and folding it would throw information away. It does mean the raw
column is not a vocabulary:

    Bengaluru 38 / Bangalore 35          one city, two spellings, split count
    Fab 10N/X, MSB, SGP, sg              Micron site codes and bare ISO codes
    "Bengaluru; Gurugram; Mumbai"        three cities in one row
    "Singapore (Hybrid)"                 a work arrangement glued on

So the picker folds at read time rather than at write time. Nothing here
touches the stored column: adding an alias changes what the next request
shows, with no migration and no re-enrichment of 21k rows. The cost is that
every read pays the fold -- which is why `top_cities` folds a GROUP BY of a
few hundred distinct values, never 21k rows.

A segment this module drops is hidden from the picker only. The posting
itself stays reachable by country, by search and by every other facet, which
is why dropping aggressively is safe and guessing would not be.
"""

from __future__ import annotations

import re
from collections import Counter

from app.services.jobs.enrich import Taxonomy, default_taxonomy

# A segment carrying either is a site code, not a place: "Fab 10N/X",
# "Boise, ID - Main Site", "RA3 Building". Cities do contain digits in a few
# places on earth, none of which any board in this catalogue posts from.
_HAS_DIGIT_OR_SLASH = re.compile(r"[0-9/\\]")

# "Singapore (Hybrid)" -> "Singapore". The parenthetical is always an
# arrangement or a site qualifier in this data, never part of the name.
_PARENTHETICAL = re.compile(r"\s*\([^)]*\)\s*$")

# Multi-city rows: Workday and SmartRecruiters both emit these.
_SPLIT = re.compile(r"\s*[;|]\s*|\s+/\s+")

# Shorter than this and it is an airport or ISO code ("SGP", "sg", "TPE"),
# not a city a person would recognise in a list.
_MIN_CHARS = 4


def _display(value: str) -> str:
    """Title-case only the spellings that are shouting or whispering.

    "TAIPEI" and "taipei" are the same city as "Taipei" and must not sit in
    the list three times. A string that already has mixed case is left alone,
    because that is the employer's own typography ("McLean", "s-Hertogenbosch")
    and title-casing it would be a downgrade.
    """
    if value.isupper() or value.islower():
        return value.title()
    return value


def canonical_cities(raw: str | None, taxonomy: Taxonomy | None = None) -> list[str]:
    """Every city a stored `JobFacets.city` value names, display-cased.

    Empty when the value names none -- a placeholder, a site code, or a bare
    country code. Callers treat that as "this posting has no city", never as
    an error: see the module docstring for why that is lossless in practice.
    """
    if not raw:
        return []
    config = taxonomy or default_taxonomy()
    out: list[str] = []
    for part in _SPLIT.split(raw):
        name = _PARENTHETICAL.sub("", part).strip().strip(",.-")
        if not name:
            continue
        folded = name.casefold()
        alias = config.city_aliases.get(folded)
        if alias is not None:
            if alias not in out:
                out.append(alias)
            continue
        if _HAS_DIGIT_OR_SLASH.search(name):
            continue
        if folded in config.city_placeholder_needles:
            continue
        # A segment that is the country's (or city-state's) own name belongs
        # to the country facet, not the city one. "Singapore" and "Hong Kong"
        # land here, correctly: this vocabulary has no finer subdivision for
        # them, so their pickers show no cities rather than a fake one.
        if folded in config.location_country_name_needles:
            continue
        if folded in config.location_region_name_segments:
            continue
        if len(name) < _MIN_CHARS:
            continue
        shown = _display(name)
        if shown not in out:
            out.append(shown)
    return out


def fold_city_counts(
    rows: list[tuple[str | None, int]],
    taxonomy: Taxonomy | None = None,
) -> list[tuple[str, int]]:
    """Fold a `(city, count)` GROUP BY into canonical names, biggest first.

    A multi-city row counts for each city it names: a posting open in both
    Bengaluru and Gurugram genuinely is a posting in each, and a reader of
    either list expects to find it. The folded counts therefore do not have
    to sum to the country's total, and the UI never presents them as a
    partition of it.
    """
    config = taxonomy or default_taxonomy()
    totals: Counter[str] = Counter()
    for raw, count in rows:
        for name in canonical_cities(raw, config):
            totals[name] += int(count or 0)
    # Count first, then name: ties would otherwise order by whatever the
    # database happened to hand back, which changes between calls.
    return sorted(totals.items(), key=lambda item: (-item[1], item[0]))


def canonical_name(city: str, taxonomy: Taxonomy | None = None) -> str | None:
    """What `?city=<city>` means once aliases are resolved, or None if blank."""
    config = taxonomy or default_taxonomy()
    wanted = city.strip()
    if not wanted:
        return None
    return config.city_aliases.get(wanted.casefold(), wanted)


def stored_values_for(
    city: str,
    stored: list[str | None],
    taxonomy: Taxonomy | None = None,
) -> list[str]:
    """Which stored `JobFacets.city` spellings mean this city.

    `?city=` resolves through the fold rather than through SQL, because no
    portable LIKE can express "names this city". A substring match reaches
    the wrong place -- `%york%` finds New York when the ask was York, which
    `test_city_filter_does_not_reach_a_different_city` pins -- and equality
    misses both the alias spelling (Bangalore) and the multi-city row
    ("Bengaluru; Gurugram"). Folding the handful of distinct values and
    matching exact names is the only form that gets all three right.
    """
    config = taxonomy or default_taxonomy()
    wanted = canonical_name(city, config)
    if wanted is None:
        return []
    target = wanted.casefold()
    out: list[str] = []
    for value in stored:
        if value is None:
            continue
        if any(name.casefold() == target for name in canonical_cities(value, config)):
            out.append(value)
    return out
