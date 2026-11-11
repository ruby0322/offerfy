"""Deterministic facets derived from a posting's title (and, for function,
its description).

Offerfy's catalogue gives every posting only free-text title/company/location.
Two of the planned filters -- seniority and function -- can be read off the
title alone most of the time, the way a human skims a job board: the title
names a level ("Staff", "Senior", "Intern") and/or a role ("Backend Engineer",
"Product Designer"). Both derivations are ordered-list, first-match-wins
scans, the same shape as scope.py's title_in_scope, for the same reason: it
keeps the vocabulary in data (job_taxonomy.json) instead of in branching
code, so tuning a rule never touches this module.

Unknown is kept, not guessed into the nearest bucket -- same bias as
scope.py. A title that names no seniority level at all, including the
generic "engineer"/"developer"/... rule, is seniority "unknown" rather than
a silent "mid"; "mid" is just the last, most generic rule in the taxonomy
file, not special-cased here. A title (and description) that names no
function at all is function "other" rather than a silent guess, because
"other" is still a real, useful bucket on a filter UI whereas a wrong guess
erodes trust in all of them.

`function_of` checks title needles across every rule before it checks any
rule's description needles -- a title is the employer's deliberate choice of
what the role is; a description is boilerplate that happens to mention
adjacent technologies, so a title hit always outranks a description hit,
regardless of rule order.

`load_taxonomy` also loads the location vocabulary (`location_countries`,
`location_regions`, `remote_needles`); `location_facets` below is what
consumes it.

`location_countries` and `location_regions` are keyed by the exact same
needle set (verified by `test_taxonomy_file_covers_location_vocabularies`)
-- a needle such as "san francisco" maps to a country ("US") and, under
the identical key, straight to a region ("north_america"). So once a
needle resolves the country, the same needle hands back the region for
free; `location_facets` never scans the string a second time looking for
a region, it only ever looks one up by the needle that already won.

The needle set mixes country names ("united states", "taiwan") with city
and region names that happen to pin down a country ("san francisco",
"新竹", "hsinchu") -- a city needle resolving the country is normal and
expected, not a bug. Telling those two kinds of needle apart matters only
for the `city` facet: "Taipei, Taiwan" must still report city "Taipei"
even though the country came from the "taipei" needle, while "Remote -
United States" must report no city at all, because "United States" names
the country itself rather than a place within it. `job_taxonomy.json`'s
`location_country_name_needles` (loaded as `Taxonomy.location_country_name_needles`)
is the list of needles that name a country (or, for Singapore and Hong
Kong, a city-state/territory this vocabulary has no finer subdivision
for) rather than a place inside one, and `location_region_name_segments`
is the handful of literal region labels ("EMEA", "APAC") an ATS can emit
directly as a segment instead of a country. Both vocabularies, like
every other needle list in this module, live in the data file, never in
Python -- see `seniority_of`'s docstring for why.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


_BACKEND_ROOT = Path(__file__).resolve().parents[3]


def taxonomy_path() -> Path:
    return _BACKEND_ROOT / "data" / "job_taxonomy.json"


@dataclass(frozen=True)
class SeniorityRule:
    seniority: str
    needles: tuple[str, ...]


@dataclass(frozen=True)
class FunctionRule:
    function: str
    title_needles: tuple[str, ...]
    description_needles: tuple[str, ...]


@dataclass(frozen=True)
class Taxonomy:
    seniority_rules: tuple[SeniorityRule, ...]
    function_rules: tuple[FunctionRule, ...]
    location_countries: dict[str, str]
    location_regions: dict[str, str]
    remote_needles: tuple[str, ...]
    location_country_name_needles: frozenset[str]
    location_region_name_segments: frozenset[str]


def load_taxonomy(path: Path | None = None) -> Taxonomy:
    data = json.loads((path or taxonomy_path()).read_text(encoding="utf-8"))
    seniority_rules = tuple(
        SeniorityRule(
            seniority=rule["seniority"],
            needles=tuple(n.casefold() for n in rule.get("any", []) if n),
        )
        for rule in data.get("seniority_rules", [])
    )
    function_rules = tuple(
        FunctionRule(
            function=rule["function"],
            title_needles=tuple(
                n.casefold() for n in rule.get("title_any", []) if n
            ),
            description_needles=tuple(
                n.casefold() for n in rule.get("description_any", []) if n
            ),
        )
        for rule in data.get("function_rules", [])
    )
    location_countries = {
        needle.casefold(): value
        for needle, value in data.get("location_countries", {}).items()
        if needle
    }
    location_regions = {
        needle.casefold(): value
        for needle, value in data.get("location_regions", {}).items()
        if needle
    }
    remote_needles = tuple(
        n.casefold() for n in data.get("remote_needles", []) if n
    )
    location_country_name_needles = frozenset(
        n.casefold() for n in data.get("location_country_name_needles", []) if n
    )
    location_region_name_segments = frozenset(
        n.casefold() for n in data.get("location_region_name_segments", []) if n
    )
    return Taxonomy(
        seniority_rules=seniority_rules,
        function_rules=function_rules,
        location_countries=location_countries,
        location_regions=location_regions,
        remote_needles=remote_needles,
        location_country_name_needles=location_country_name_needles,
        location_region_name_segments=location_region_name_segments,
    )


@lru_cache(maxsize=1)
def default_taxonomy() -> Taxonomy:
    return load_taxonomy()


def seniority_of(title: str, taxonomy: Taxonomy | None = None) -> str:
    """First seniority rule whose needle appears in the padded title wins.

    No needle is hardcoded here -- see the module docstring on why "mid" is
    just data, the last and most generic rule in job_taxonomy.json. A title
    matching no rule at all, not even that one, is "unknown".
    """
    config = taxonomy or default_taxonomy()
    folded = f" {title.casefold()} "
    for rule in config.seniority_rules:
        if any(needle in folded for needle in rule.needles):
            return rule.seniority
    return "unknown"


@dataclass(frozen=True)
class LocationFacets:
    country: str | None
    region: str | None
    city: str | None


_MULTIPLE_LOCATIONS_PLACEHOLDER = "multiple locations"


def _best_country_match(
    folded_segment: str, taxonomy: Taxonomy
) -> tuple[str, str] | None:
    """Longest needle in `location_countries` that is a substring of the
    (already space-padded) segment, so "United States" wins over any
    shorter needle that happens to also be contained in it.
    """
    best: tuple[str, str] | None = None
    for needle, country in taxonomy.location_countries.items():
        if needle in folded_segment:
            if best is None or len(needle) > len(best[0]):
                best = (needle, country)
    return best


def _is_remote_placeholder(folded_segment: str, taxonomy: Taxonomy) -> bool:
    return any(needle in folded_segment for needle in taxonomy.remote_needles)


def _is_country_or_region_name(segment_casefolded: str, taxonomy: Taxonomy) -> bool:
    return (
        segment_casefolded in taxonomy.location_country_name_needles
        or segment_casefolded in taxonomy.location_region_name_segments
    )


def location_facets(
    location: str | None,
    *,
    source: str,
    remote: bool | None,
    taxonomy: Taxonomy | None = None,
) -> LocationFacets:
    """Parse a free-text ATS location string into country / region / city.

    `source` and `remote` are accepted (and not yet used) so the call site
    in the next task's `enrich_jobs` can pass a job's source and remote
    flag through uniformly for every derivation in this module, the same
    way `function_of` always takes a description even for rules that never
    look at it. `remote` in particular must never be used to invent a
    country when the string itself names none -- see
    `test_remote_flag_does_not_invent_a_country`.

    Segments come from splitting on "," and "-" (the two separators every
    ATS in this catalogue uses), stripped. The first segment that matches
    any country needle wins the country+region, longest needle first
    within that segment (`_best_country_match`); matching stops there, so
    a second, later-named country in the same string (an office paired
    with another office) is ignored rather than merged -- first-match-wins,
    the same bias `seniority_of`/`function_of` use. If no segment matches
    any country needle at all, the whole result is `LocationFacets(None,
    None, None)` -- no guessing, per the module's unknown-is-kept bias.

    `city` is a separate scan over the same segments, in order, for the
    first one that is not a remote placeholder, not the literal "multiple
    locations" placeholder, and not itself exactly a country or region
    name (`_is_country_or_region_name`). A segment that resolved the
    country via a city needle (e.g. "Taipei") still qualifies -- what's
    excluded is a segment that *is* the country's own name (e.g. "United
    States"), not a segment that merely matched a needle.
    """
    config = taxonomy or default_taxonomy()
    if not location or not location.strip():
        return LocationFacets(None, None, None)

    segments = [s.strip() for s in re.split(r"[,-]", location)]
    segments = [s for s in segments if s]
    if not segments:
        return LocationFacets(None, None, None)

    country: str | None = None
    region: str | None = None
    for segment in segments:
        folded = f" {segment.casefold()} "
        match = _best_country_match(folded, config)
        if match is not None:
            needle, country = match
            region = config.location_regions.get(needle)
            break

    if country is None:
        return LocationFacets(None, None, None)

    city: str | None = None
    for segment in segments:
        folded = f" {segment.casefold()} "
        segment_casefolded = segment.casefold()
        if _is_remote_placeholder(folded, config):
            continue
        if segment_casefolded == _MULTIPLE_LOCATIONS_PLACEHOLDER:
            continue
        if _is_country_or_region_name(segment_casefolded, config):
            continue
        city = segment
        break

    return LocationFacets(country, region, city)


def function_of(
    title: str, description_text: str, taxonomy: Taxonomy | None = None
) -> str:
    """Title needles, across every rule, win over any rule's description
    needles. See the module docstring for why.
    """
    config = taxonomy or default_taxonomy()
    folded_title = f" {title.casefold()} "
    for rule in config.function_rules:
        if any(needle in folded_title for needle in rule.title_needles):
            return rule.function
    folded_description = f" {description_text.casefold()} "
    for rule in config.function_rules:
        if any(needle in folded_description for needle in rule.description_needles):
            return rule.function
    return "other"
