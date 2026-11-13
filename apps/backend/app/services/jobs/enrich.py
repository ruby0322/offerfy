"""Deterministic facets derived from a posting's title (and, for function,
its description).

Offerfy's catalogue gives every posting only free-text title/company/location.
Two of the planned filters -- seniority and function -- can be read off the
title alone most of the time, the way a human skims a job board: the title
names a level ("Staff", "Senior", "Intern") and/or a role ("Backend Engineer",
"Product Designer"). Neither derivation hardcodes a needle in Python -- both
read their vocabulary from job_taxonomy.json, the same shape as scope.py's
title_in_scope, so tuning a rule never touches this module.

`seniority_of` and `function_of` resolve a multi-needle-match tie in
deliberately opposite ways -- do not "tidy" one to match the other.
`seniority_of` is an ordered-list, first-match-wins scan: job_taxonomy.json
lists `staff_plus` before `senior` on purpose, which is why "Member of
Technical Staff" resolves to `senior` and not `staff_plus` -- rule order
there is load-bearing. `function_of` is longest-needle-wins across every
rule: its categories (backend, data, infra, ...) have no natural precedence
over each other, so "first rule in file order whose needle matches" would
just be an accident of list position -- and was one: a short bare qualifier
(" data ") once shadowed a longer, more specific compound
("infrastructure engineer") purely because the data rule happened to sit
earlier in the file. Longest-match removes that whole class of accident
instead of requiring a correct re-reading of file order on every edit.

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

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import Job, JobFacets
from app.services.jobs.boilerplate import (
    load_boilerplate,
    normalize_company,
    strip_boilerplate,
)


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
    function_fallback_rules: tuple[FunctionRule, ...]
    location_countries: dict[str, str]
    location_regions: dict[str, str]
    remote_needles: tuple[str, ...]
    location_country_name_needles: frozenset[str]
    location_region_name_segments: frozenset[str]
    location_us_state_codes: frozenset[str]
    location_us_state_names: frozenset[str]


def load_taxonomy(path: Path | None = None) -> Taxonomy:
    data = json.loads((path or taxonomy_path()).read_text(encoding="utf-8"))
    seniority_rules = tuple(
        SeniorityRule(
            seniority=rule["seniority"],
            needles=tuple(n.casefold() for n in rule.get("any", []) if n),
        )
        for rule in data.get("seniority_rules", [])
    )
    def function_rules_from(key: str) -> tuple[FunctionRule, ...]:
        return tuple(
            FunctionRule(
                function=rule["function"],
                title_needles=tuple(
                    n.casefold() for n in rule.get("title_any", []) if n
                ),
                description_needles=tuple(
                    n.casefold() for n in rule.get("description_any", []) if n
                ),
            )
            for rule in data.get(key, [])
        )

    function_rules = function_rules_from("function_rules")
    function_fallback_rules = function_rules_from("function_fallback_rules")
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
    # NOT casefolded: the state pass matches the original segment, because
    # case is the only signal separating "IN" (Indiana) from "In" (English).
    location_us_state_codes = frozenset(
        c for c in data.get("location_us_state_codes", []) if c
    )
    location_us_state_names = frozenset(
        n.casefold() for n in data.get("location_us_state_names", []) if n
    )
    return Taxonomy(
        seniority_rules=seniority_rules,
        function_rules=function_rules,
        function_fallback_rules=function_fallback_rules,
        location_countries=location_countries,
        location_regions=location_regions,
        remote_needles=remote_needles,
        location_country_name_needles=location_country_name_needles,
        location_region_name_segments=location_region_name_segments,
        location_us_state_codes=location_us_state_codes,
        location_us_state_names=location_us_state_names,
    )


def valid_seniorities(taxonomy: Taxonomy) -> set[str]:
    # "unknown" is a real value enrich.py writes when no rule matches a
    # title at all -- it is not a sentinel to exclude from the vocabulary.
    return {rule.seniority for rule in taxonomy.seniority_rules} | {"unknown"}


def valid_functions(taxonomy: Taxonomy) -> set[str]:
    # "other" is a real bucket, same reasoning as "unknown" above. The
    # fallback tier's functions are ordinary facet values -- a caller
    # filtering /v1/jobs by them must not be rejected -- so they belong in
    # the vocabulary alongside the specialised ones.
    return (
        {rule.function for rule in taxonomy.function_rules}
        | {rule.function for rule in taxonomy.function_fallback_rules}
        | {"other"}
    )


def valid_countries(taxonomy: Taxonomy) -> set[str]:
    # JobFacets.country can only ever hold one of these: location_facets
    # sources every country it emits from this same needle table, so the
    # set is closed.
    return set(taxonomy.location_countries.values())


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
# JobFacets.city is String(255) while Job.location is String(512), so a
# single long segment can overflow the column. SQLite ignores VARCHAR
# lengths, which is why no test caught this; Postgres raises DataError at
# enrich's flush. Truncating follows profile.clamp_payload's precedent:
# truncating loses a clause of a city name, rejecting loses the facets row
# (and with it the posting's visibility to every filtered query).
_CITY_MAX_CHARS = 255


@lru_cache(maxsize=None)
def _word_bounded(needle: str) -> re.Pattern[str]:
    r"""The needle anchored at whichever of its edges is alphanumeric.

    `\b` asserts a word/non-word transition, so putting one after a needle
    that already ends in punctuation can never match: `\bu\.s\.\b` misses
    "Remote (U.S.)" because "." and ")" are both non-word. Anchoring only the
    alphanumeric edges keeps "u.s." matching while still refusing "pusan".

    Cached because the backfill runs this over every segment of every active
    posting against every needle.
    """
    left = r"\b" if needle[:1].isalnum() else ""
    right = r"\b" if needle[-1:].isalnum() else ""
    return re.compile(rf"{left}{re.escape(needle)}{right}")


def _best_country_match(
    folded_segment: str, taxonomy: Taxonomy
) -> tuple[str, str] | None:
    """Longest needle in `location_countries` found in the (already
    space-padded) segment, so "United States" wins over any shorter needle
    also contained in it.

    An ASCII needle must match on WORD BOUNDARIES. Plain substring matching
    filed real postings in the wrong country, because a country name is often
    inside an unrelated place name: "india" is inside "Indianapolis", "usa" is
    inside "Pusan" and "Jerusalem", "zug" is inside "Zugspitze". Non-ASCII
    needles stay plain substrings -- CJK has no word boundaries, and `\b`
    against "新竹" would never match "新竹市".
    """
    best: tuple[str, str] | None = None
    for needle, country in taxonomy.location_countries.items():
        found = (
            _word_bounded(needle).search(folded_segment) is not None
            if needle.isascii()
            else needle in folded_segment
        )
        if found and (best is None or len(needle) > len(best[0])):
            best = (needle, country)
    return best


def _is_remote_placeholder(folded_segment: str, taxonomy: Taxonomy) -> bool:
    return any(needle in folded_segment for needle in taxonomy.remote_needles)


def _is_country_or_region_name(segment_casefolded: str, taxonomy: Taxonomy) -> bool:
    return (
        segment_casefolded in taxonomy.location_country_name_needles
        or segment_casefolded in taxonomy.location_region_name_segments
    )


def _us_state_fallback(
    segments: list[str], taxonomy: Taxonomy
) -> tuple[str | None, str | None]:
    """`("US", "north_america")` when a segment is a bare US state code.

    Runs only after every country needle and token has missed, so a country
    the string names always wins -- "Toronto, Canada" stays Canada even though
    "CA" is also California. The match is on the ORIGINAL segment, not the
    casefolded one: "In-Office" splits on "-" into ["In", "Office"] and "IN"
    is Indiana, so case is the only thing that separates a state code from an
    English word. 2,001 live postings are "City, ST" with no country token
    anywhere in them.

    Spelled-out state names count too, case-insensitively -- "Indiana -
    Indianapolis" was previously filed as *India*, because "india" is inside
    "Indiana". The one real ambiguity is Georgia, a US state and a country;
    it is not a country needle and the live catalogue's Georgia postings are
    all Atlanta, so a US reading is right for this data and wrong for Tbilisi.
    """
    for segment in segments:
        if (
            segment in taxonomy.location_us_state_codes
            or segment.casefold() in taxonomy.location_us_state_names
        ):
            alias = "united states"
            return taxonomy.location_countries.get(alias), taxonomy.location_regions.get(alias)
    return None, None


def _named_region(segments: list[str], taxonomy: Taxonomy) -> str | None:
    """The region a segment names outright, for strings with no country.

    `location_region_name_segments` holds them spelled as they appear ("north
    america"); the stored facet value uses the underscored form.
    """
    for segment in segments:
        folded = segment.casefold()
        if folded in taxonomy.location_region_name_segments:
            return folded.replace(" ", "_")
    return None


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
        country, region = _us_state_fallback(segments, config)

    if country is None:
        # A region the string actually names survives a country we cannot
        # resolve: "Home based - EMEA". The module's unknown-is-kept bias
        # forbids *inventing* a country from the remote flag; it does not
        # require discarding a place the string states, and prefilter_query
        # matches on region, so these postings become reachable.
        return LocationFacets(None, _named_region(segments, config), None)

    # Two passes. A US state designation is a poor city -- Workday writes
    # "US, Oregon, Hillsboro" and the city is Hillsboro -- but it cannot be
    # excluded outright, because a state name is often also a city and
    # "New York, NY" must still give "New York". So: prefer a segment that is
    # not a state designation, and settle for one that is only if nothing
    # else in the string qualifies.
    city: str | None = None
    fallback: str | None = None
    for segment in segments:
        folded = f" {segment.casefold()} "
        segment_casefolded = segment.casefold()
        if _is_remote_placeholder(folded, config):
            continue
        if segment_casefolded == _MULTIPLE_LOCATIONS_PLACEHOLDER:
            continue
        if _is_country_or_region_name(segment_casefolded, config):
            continue
        if (
            segment in config.location_us_state_codes
            or segment_casefolded in config.location_us_state_names
        ):
            if fallback is None:
                fallback = segment[:_CITY_MAX_CHARS]
            continue
        city = segment[:_CITY_MAX_CHARS]
        break

    return LocationFacets(country, region, city if city is not None else fallback)


def _longest_matching_function(
    folded_text: str,
    rules: tuple[FunctionRule, ...],
    needles_of: Callable[[FunctionRule], tuple[str, ...]],
) -> str | None:
    """The function owned by the single longest needle that matches.

    Same precedent as `_best_country_match` above: job_taxonomy.json's
    function_rules are an unordered bag of categories (unlike
    seniority_rules -- see `seniority_of`'s docstring), so "first rule in
    file order whose needle matches" is an accident of list position, not
    a deliberate precedence. A short bare qualifier like " data " sitting
    earlier in the file must not shadow a longer, more specific compound
    like "infrastructure engineer" sitting later in it -- that exact
    shadowing was a real regression (`function_of("Data Infrastructure
    Engineer", "")` briefly came back "data" instead of "infra") caused by
    this module's own first-match-wins function_of before this fix.
    """
    best: tuple[int, str] | None = None
    for rule in rules:
        for needle in needles_of(rule):
            if needle in folded_text and (best is None or len(needle) > best[0]):
                best = (len(needle), rule.function)
    return best[1] if best is not None else None


def function_of(
    title: str, description_text: str, taxonomy: Taxonomy | None = None
) -> str:
    """Longest matching needle wins; title needles, as a whole phase, still
    win over any description needle regardless of length.

    `function_of` and `seniority_of` resolve ties in opposite ways, on
    purpose. `seniority_of` stays ordered-rules-first-match: its rule
    order is deliberate and load-bearing (`staff_plus` listed before
    `senior` is why "Member of Technical Staff" resolves to `senior`
    rather than `staff_plus` -- see its docstring), so reordering or
    "tidying" it into longest-match would silently change results.
    `function_of`'s rule order carries no such meaning -- the categories
    (backend, data, infra, ...) have no natural precedence over one
    another -- so longest-match is strictly safer here: it removes the
    whole class of a short bare qualifier accidentally shadowing a longer,
    more specific compound elsewhere in the file, instead of leaving that
    to be re-discovered one ordering accident at a time. Do not swap
    either function's strategy for the other's.

    `function_fallback_rules` is consulted only after every specialised
    rule has missed on both the title and the description, and that
    ordering is the whole point of it being a separate tier rather than
    another entry in `function_rules`. A generic category cannot be
    expressed as needles inside a longest-match contest: "software
    engineer" is 17 characters and "backend" is 7, so a generic rule
    competing on length would answer `software` for "Senior Software
    Engineer, Backend" and strip the specialisation off the posting.
    Measured against the live catalogue when this was built, running the
    generic needles as an ordinary rule moved 2,864 postings off a real
    function to gain 899 -- a net loss. As a tier it can only ever claim
    postings that would otherwise be `other`, which makes the change
    additive by construction; `test_fallback_tier_never_overrides_a_specialised_function`
    pins that.
    """
    config = taxonomy or default_taxonomy()
    folded_title = f" {title.casefold()} "
    folded_description = f" {description_text.casefold()} "
    for rules in (config.function_rules, config.function_fallback_rules):
        title_match = _longest_matching_function(
            folded_title, rules, lambda rule: rule.title_needles
        )
        if title_match is not None:
            return title_match
        description_match = _longest_matching_function(
            folded_description, rules, lambda rule: rule.description_needles
        )
        if description_match is not None:
            return description_match
    return "other"


def description_hash(text: str) -> str:
    """sha256 hex of a posting's description with its company's boilerplate
    stripped (`enrich_jobs` passes `strip_boilerplate`'s output, not the raw
    `description_text`).

    Hashing the stripped text is what makes the gate self-invalidating: when
    a company's boilerplate set changes, every one of its postings hashes
    differently and is re-derived with no `force=True`. Case survives
    stripping -- `strip_boilerplate` excises spans from the original text and
    casefolds only what it hashes -- so a description edited in letter case
    alone still moves this hash and costs one re-derivation, even though
    `function_of` casefolds and would return the same answer. That waste is
    rare and one row wide; the alternative, hashing a casefolded copy, would
    make the gate's input a string no derivation actually reads.

    Not of title/location/anything else: those feed the three derivations
    too, but a posting whose title or location is edited without touching
    its description is rare enough, and cheap enough to catch on the next
    description change, that widening the hash's input would just mean
    re-deriving on every minor title tweak. Hex, not raw bytes, because
    JobFacets.description_hash is a String(64) column -- the same
    trade-off ResumeContent.derived_from_hash already makes.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# Rows at a time for the job_ids=None backfill path. The live catalogue is
# ~11k active postings at up to MAX_DESCRIPTION_CHARS (200k) each; loading
# all of them as full Job ORM entities (description_html included, never
# read by any derivation) at once was the memory risk the brief warned
# about. Chosen column projection already cuts most of that weight -- this
# batches the rest so peak memory is bounded by one batch, not the table.
_BACKFILL_BATCH_SIZE = 500

# Everything _enrich_job_rows reads, and nothing more. Six of these feed the
# three derivations (seniority_of, function_of, location_facets); Job.company
# is here for the per-batch boilerplate lookup, which is not a derivation.
# description_html is deliberately excluded -- nothing in this module looks at
# it, so loading it would be pure waste on every row, backfill or not.
_ENRICH_COLUMNS = (
    Job.id,
    Job.source,
    Job.company,
    Job.title,
    Job.location,
    Job.remote,
    Job.description_text,
)


def _enrich_job_rows(
    db: Session,
    rows: list,
    *,
    taxonomy: Taxonomy,
    force: bool,
    stamp: datetime,
) -> int:
    """Derive and upsert JobFacets for one already-loaded batch of rows.

    `rows` are `_ENRICH_COLUMNS`-projected Row objects, not Job entities --
    callers never load more than that. The existing JobFacets rows for just
    this batch are fetched in one bulk query keyed by job_id, so the
    two-query-per-batch shape `enrich_jobs` promises holds whether the
    batch is a handful of explicit `job_ids` or one chunk of a backfill.
    """
    if not rows:
        return 0

    existing = {
        facet.job_id: facet
        for facet in db.query(JobFacets)
        .filter(JobFacets.job_id.in_([row.id for row in rows]))
        .all()
    }
    # Second (and last) query: boilerplate for the batch's distinct companies.
    # An absent row and an empty hash set must take the same path (.get with
    # an empty default below) -- recompute_boilerplate's change-reporting
    # relies on that.
    boilerplate = load_boilerplate(db, {row.company for row in rows})

    written = 0
    for row in rows:
        stripped = strip_boilerplate(
            row.description_text,
            boilerplate.get(normalize_company(row.company), frozenset()),
        )
        new_hash = description_hash(stripped)
        facet = existing.get(row.id)
        if facet is not None and not force and facet.description_hash == new_hash:
            continue

        loc = location_facets(
            row.location, source=row.source, remote=row.remote, taxonomy=taxonomy
        )
        if facet is None:
            facet = JobFacets(job_id=row.id)
            db.add(facet)
        facet.seniority = seniority_of(row.title, taxonomy)
        facet.function = function_of(row.title, stripped, taxonomy)
        facet.country = loc.country
        facet.region = loc.region
        facet.city = loc.city
        facet.description_hash = new_hash
        facet.enriched_at = stamp
        written += 1

    db.flush()
    return written


def enrich_jobs(
    db: Session,
    *,
    job_ids: list[str] | None = None,
    force: bool = False,
    now: datetime | None = None,
) -> int:
    """Derive and store facets for a set of jobs, skipping unchanged ones.

    `job_ids=None` means every active posting -- the backfill path. With
    ~11k rows in the live catalogue, this must not issue a SELECT per row,
    must not load whole Job entities (description_html is never read by
    any derivation but is the single biggest column on the table), and
    must not hold all ~11k rows' description_text in memory at once. So
    this selects only `_ENRICH_COLUMNS`, and for the backfill path walks
    them in `_BACKFILL_BATCH_SIZE`-row chunks via `yield_per`, handing each
    chunk to `_enrich_job_rows`, which does its own small bulk lookup of
    existing JobFacets for just that chunk.

    The explicit-`job_ids` path does NOT batch: it loads every id it is
    given in one query. The per-board call sites pass a single board's
    newly-upserted ids, which is small, but a caller that can hold a large
    id list must window it itself -- `refresh_boilerplate` in
    app.services.jobs.ingest does, because a company it re-enriches can be
    thousands of postings. Either way each call into `_enrich_job_rows`
    keeps the same two-query shape, the unbatched path just never splits
    across a `yield_per` loop.

    The taxonomy is resolved once per call, here, and threaded through to
    all three derivations -- `default_taxonomy()` is cheap once cached
    (`lru_cache(maxsize=1)`), but there is no reason to pay even that
    per-row when one lookup covers the whole call.

    A row is skipped when it already exists and its stored
    `description_hash` equals the newly computed one, unless `force`.
    Returns the number of rows written (inserted or updated) -- not the
    number of jobs considered, which is why the no-op and force tests
    check this count rather than row presence alone.

    Flushes but does not commit: the caller owns the transaction, the same
    contract `upsert_jobs` in app.services.jobs.store follows. Flushing
    once per chunk (rather than once at the very end) is still safe under
    that contract -- a flush is not a commit, and the caller's rollback on
    failure undoes every chunk's writes along with everything else in its
    transaction.
    """
    stamp = now or datetime.now(timezone.utc)
    taxonomy = default_taxonomy()

    if job_ids is not None:
        if not job_ids:
            return 0
        rows = (
            db.query(*_ENRICH_COLUMNS).filter(Job.id.in_(job_ids)).all()
        )
        return _enrich_job_rows(db, rows, taxonomy=taxonomy, force=force, stamp=stamp)

    written = 0
    batch: list = []
    query = (
        db.query(*_ENRICH_COLUMNS)
        .filter(Job.is_active.is_(True))
        .yield_per(_BACKFILL_BATCH_SIZE)
    )
    for row in query:
        batch.append(row)
        if len(batch) >= _BACKFILL_BATCH_SIZE:
            written += _enrich_job_rows(
                db, batch, taxonomy=taxonomy, force=force, stamp=stamp
            )
            batch = []
    if batch:
        written += _enrich_job_rows(db, batch, taxonomy=taxonomy, force=force, stamp=stamp)
    return written
