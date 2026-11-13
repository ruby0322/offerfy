from datetime import datetime, timezone

import pytest

from sqlalchemy import event

from app.models import Job, JobFacets
from app.services.jobs import enrich as enrich_module
from app.services.jobs.boilerplate import recompute_boilerplate
from app.services.jobs.enrich import (
    LocationFacets,
    _ENRICH_COLUMNS,
    _enrich_job_rows,
    default_taxonomy,
    description_hash,
    enrich_jobs,
    function_of,
    load_taxonomy,
    location_facets,
    seniority_of,
    valid_functions,
)


def _add_job(db_session, **kwargs) -> Job:
    now = kwargs.pop("now", datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc))
    job = Job(
        source=kwargs.get("source", "greenhouse"),
        source_id=kwargs["source_id"],
        company=kwargs.get("company", "Stripe"),
        title=kwargs.get("title", "Engineer"),
        location=kwargs.get("location", "Remote"),
        remote=kwargs.get("remote", True),
        apply_url=kwargs.get("apply_url", "https://example.com/apply"),
        source_url=kwargs.get("source_url", "https://example.com/job"),
        description_text=kwargs.get("description_text", "Build things"),
        description_html=kwargs.get("description_html", "<p>Build things</p>"),
        posted_at=kwargs.get("posted_at", now),
        first_seen_at=kwargs.get("first_seen_at", now),
        last_seen_at=kwargs.get("last_seen_at", now),
        is_active=kwargs.get("is_active", True),
    )
    if "id" in kwargs:
        job.id = kwargs["id"]
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


@pytest.mark.parametrize("title,expected", [
    ("Staff Software Engineer, Infrastructure", "staff_plus"),
    ("Principal Engineer", "staff_plus"),
    ("Member of Technical Staff", "senior"),
    ("Senior Backend Engineer", "senior"),
    ("SDE II", "mid"),
    ("Software Engineer II", "mid"),
    ("Software Engineer", "mid"),
    ("New Grad Software Engineer", "new_grad"),
    ("Software Engineer, University Graduate", "new_grad"),
    ("Software Engineering Intern", "intern"),
    ("Junior Developer", "junior"),
    ("Engineering Manager, Billing", "manager"),
    ("資深後端工程師", "senior"),
    ("Wizard of Light Bulb Moments", "unknown"),
])
def test_seniority_of(title, expected):
    assert seniority_of(title) == expected


@pytest.mark.parametrize("title,description,expected", [
    ("Backend Engineer", "", "backend"),
    ("Frontend Engineer", "", "frontend"),
    ("Full Stack Engineer", "", "fullstack"),
    ("Machine Learning Engineer", "", "ml"),
    ("Research Scientist", "We train large language models.", "ml"),
    ("Data Engineer", "", "data"),
    ("Site Reliability Engineer", "", "infra"),
    ("Security Engineer", "", "security"),
    ("Silicon Design Engineer", "", "hardware"),
    ("Product Manager", "", "product"),
    ("Product Designer", "", "design"),
    ("後端工程師", "", "backend"),
    # Was pinned as "other" until the function_fallback_rules tier landed:
    # the most common title in the catalogue had no function, so it was
    # unreachable as a target and invisible to every specialist.
    ("Software Engineer", "", "software"),
])
def test_function_of(title, description, expected):
    assert function_of(title, description) == expected


def test_function_falls_back_to_description_keywords():
    assert function_of("Engineer, Platform", "You will own our Kubernetes fleet.") == "infra"


@pytest.mark.parametrize("title,expected", [
    # " intern" (leading pad only) used to be a prefix match inside "internal".
    ("Internal Tools Engineer", "mid"),
    ("Internal Platform Engineer", "mid"),
    # "head of" (no padding at all) used to match inside "ahead of".
    ("Ahead of Curve Engineer", "mid"),
    # " fellow" (leading pad only) used to be a prefix match inside "fellowship".
    ("Research Fellowship Coordinator", "unknown"),
])
def test_seniority_of_does_not_match_inside_a_longer_word(title, expected):
    assert seniority_of(title) == expected


@pytest.mark.parametrize("title,description", [
    # "aws " (trailing pad only) used to match the suffix of "flaws ".
    ("Engineer, Platform", "This team debates design flaws in legacy systems all day."),
    # "ml engineer" (no padding) used to match inside "html engineer".
    ("HTML Engineer", ""),
    # "ai engineer" (no padding) used to match inside a city name ending in "ai".
    ("Shanghai Engineer", ""),
    # "ic design" (no padding) used to match inside "graphic design".
    ("Graphic Design Engineer", ""),
    # "asic" (no padding) used to match inside "basic".
    ("Basic Systems Administrator", ""),
    # "etl"/"elt" (no padding) used to match inside "settle"/"melt".
    ("Materials Engineer", "We monitor the silicon melt process closely."),
])
def test_function_of_does_not_match_inside_a_longer_word(title, description):
    assert function_of(title, description) == "other"


@pytest.mark.parametrize("title,description,expected", [
    # " etl " (both-sides padded, no punctuation variant) stopped matching
    # the moment the word sat at the end of a sentence or before a comma --
    # exactly how these acronyms show up in prose. Verbatim repro case.
    ("Engineer, Platform", "We maintain batch ETL.", "data"),
    # Same acronym, comma-adjacent instead of period-adjacent (the other
    # literal repro case; written as a full sentence rather than with a
    # leading ellipsis, since the ellipsis in the bug report stood for
    # elided context, not three literal periods glued to "ETL").
    ("Engineer, Platform", "We also handle ETL, plus reporting.", "data"),
    ("Engineer, Platform", "We built the ELT, then the dashboards.", "data"),
    ("Engineer, Platform", "Skills needed: SQL.", "data"),
    ("Engineer, Platform", "Must be comfortable with AWS.", "infra"),
    ("Engineer, Platform", "We use AWS, GCP, and Azure.", "infra"),
])
def test_function_of_matches_keyword_adjacent_to_punctuation(title, description, expected):
    assert function_of(title, description) == expected


@pytest.mark.parametrize("title,expected", [
    # This catalogue's own "Role, Qualifier" title idiom (see
    # "Engineering Manager, Billing" and "Staff Software Engineer,
    # Infrastructure" in the brief's table) puts a comma immediately after
    # a short acronym needle that was tightened to require a trailing
    # space -- that space is never there when a comma follows instead.
    ("CTO, Platform Engineering", "manager"),
    ("Lead, Platform Engineering", "senior"),
    ("SDE, Platform Team", "mid"),
    # "coop" shares the intern rule with " intern ", which already got its
    # comma variant; "coop" had none, an asymmetry within the same rule.
    ("Coop, Platform Team", "intern"),
])
def test_seniority_of_matches_acronym_before_a_title_comma(title, expected):
    assert seniority_of(title) == expected


@pytest.mark.parametrize("title,expected", [
    ("LLM, Applied Research Engineer", "ml"),
    ("ASIC, Physical Design Engineer", "hardware"),
    ("TPM, Platform Reliability", "product"),
])
def test_function_of_matches_acronym_before_a_title_comma(title, expected):
    assert function_of(title, "") == expected


@pytest.mark.parametrize("title,expected", [
    # "Role, Qualifier" where the qualifier is a bare word naming the
    # function, not a compound like "infrastructure engineer" -- the same
    # idiom gap seniority_of already closed for "intern"/"coop" etc. (see
    # test_seniority_of_matches_acronym_before_a_title_comma). Verbatim
    # repro: Staff Software Engineer, Infrastructure used to resolve to
    # function "other" despite being unambiguously an infra role.
    ("Staff Software Engineer, Infrastructure", "infra"),
    ("Engineer, Infrastructure", "infra"),
    ("Engineer, Security", "security"),
    ("Engineer, Data", "data"),
])
def test_function_of_matches_bare_qualifier_after_a_title_comma(title, expected):
    assert function_of(title, "") == expected


@pytest.mark.parametrize("title,expected", [
    # The bare " data " qualifier needle (added above, to close the
    # "Role, Qualifier" gap) sits earlier in job_taxonomy.json than infra's
    # "infrastructure engineer" compound. Under first-match-wins that made
    # function_of("Data Infrastructure Engineer", "") regress to "data" --
    # the longer, more specific compound must win regardless of which
    # rule's needle happens to be checked first.
    ("Data Infrastructure Engineer", "infra"),
    # Same shape with a different pair: the bare " security " qualifier
    # (also added above) sits earlier than hardware's compound needles, so
    # a title naming both a bare qualifier and a longer hardware compound
    # must resolve to the longer match, not whichever rule is listed first.
    ("Security Hardware Engineer", "hardware"),
])
def test_function_of_longest_needle_wins_over_rule_order(title, expected):
    assert function_of(title, "") == expected


def test_taxonomy_file_covers_both_vocabularies_exactly():
    taxonomy = load_taxonomy()
    assert {rule.seniority for rule in taxonomy.seniority_rules} <= {
        "intern", "new_grad", "junior", "mid", "senior", "staff_plus", "manager",
    }
    assert {rule.function for rule in taxonomy.function_rules} <= {
        "backend", "frontend", "fullstack", "ml", "data", "infra",
        "security", "hardware", "product", "design",
    }


def test_country_name_needles_are_all_real_location_needles():
    # location_country_name_needles is data (job_taxonomy.json), not a
    # Python frozenset -- see the enrich.py module docstring. If a needle
    # is renamed or removed from location_countries, this is the test
    # that should catch the drift instead of location_facets silently
    # stopping recognizing a country name.
    taxonomy = load_taxonomy()
    assert taxonomy.location_country_name_needles
    assert taxonomy.location_country_name_needles <= set(taxonomy.location_countries)


def test_taxonomy_file_covers_location_vocabularies():
    # location_countries and location_regions are coupled 1:1 by needle --
    # location_facets relies on this to look region up by the same needle
    # that already resolved the country, instead of re-scanning the
    # string. If a future edit to job_taxonomy.json adds a needle to one
    # dict but not the other, this is the test that should catch it.
    taxonomy = load_taxonomy()
    assert set(taxonomy.location_countries) == set(taxonomy.location_regions)
    assert set(taxonomy.location_regions.values()) <= {
        "north_america", "emea", "apac", "latam",
    }
    # "latam" arrived with the Brazil/Colombia needles. Mexico deliberately
    # stayed "north_america": moving it would reclassify rows the Brazil fix
    # was never measured against, so it is a separate judgement.
    assert taxonomy.location_regions["mexico"] == "north_america"
    assert taxonomy.location_regions["brazil"] == "latam"
    # And the two region-derivation support lists loaded from the data
    # file (location_country_name_needles, location_region_name_segments)
    # must actually be present and non-empty in the shipped data file --
    # an empty list here would make every segment a city candidate again,
    # silently reopening the bug the controller's ruling fixed.
    assert taxonomy.location_country_name_needles
    assert taxonomy.location_region_name_segments
    assert taxonomy.location_region_name_segments == {"emea", "apac", "north america"}


@pytest.mark.parametrize("raw,source,expected", [
    ("San Francisco, CA", "greenhouse", LocationFacets("US", "north_america", "San Francisco")),
    ("Taipei, Taiwan", "greenhouse", LocationFacets("TW", "apac", "Taipei")),
    ("London, UK", "lever", LocationFacets("GB", "emea", "London")),
    ("Remote - United States", "lever", LocationFacets("US", "north_america", None)),
    ("Hsinchu, Hsinchu City, Taiwan", "workday", LocationFacets("TW", "apac", "Hsinchu")),
    ("新竹市", "workday", LocationFacets("TW", "apac", "新竹市")),
    ("Multiple Locations", "smartrecruiters", LocationFacets(None, None, None)),
])
def test_location_facets(raw, source, expected):
    assert location_facets(raw, source=source, remote=None) == expected


# A needle padded on both sides (" x ") cannot match a token that sits next
# to punctuation, so every one of them needs its " x," sibling in the same
# rule. That gap is invisible in review -- it is how " coop " shipped without
# " coop," and only surfaced in round 3 of review -- so it is a test.
# Exempt: " head of " is always followed by a noun ("Head of Engineering"), so
# a comma can never immediately follow it.
_PADDING_SIBLING_EXEMPT = {" head of "}


def test_padded_needles_have_their_punctuation_siblings():
    taxonomy = load_taxonomy()
    rules = [(f"seniority:{r.seniority}", r.needles) for r in taxonomy.seniority_rules]
    rules += [(f"title:{r.function}", r.title_needles) for r in taxonomy.function_rules]
    rules += [
        (f"description:{r.function}", r.description_needles)
        for r in taxonomy.function_rules
    ]
    missing = [
        (name, needle)
        for name, needles in rules
        for needle in needles
        if needle.startswith(" ")
        and needle.endswith(" ")
        and len(needle) > 2
        and needle not in _PADDING_SIBLING_EXEMPT
        and f" {needle.strip()}," not in set(needles)
    ]
    assert missing == []


# --- the location parser's three real-catalogue gaps -----------------------
# Measured 2026-11-13 against production: 7,098 ACTIVE postings had a parseable
# location string and no country facet -- 33% of the catalogue, invisible to
# every location-constrained preference. Three causes, each its own test below.


@pytest.mark.parametrize("raw,expected_country,expected_city", [
    # 1,772 postings. Workday writes the country as a bare token; there was no
    # needle for it, because `needle in f" {segment} "` makes a 2-char needle
    # unsafe ("us" is a substring of "houston"), so it was simply omitted.
    # The city is asserted too: a segment that IS the country's own name must
    # never become the city, and asserting only the country is how "US, CA,
    # Santa Clara" briefly shipped with city="US".
    ("US, CA, Santa Clara", "US", "Santa Clara"),
    ("US, Oregon, Hillsboro", "US", "Hillsboro"),
    ("US, Arizona, Phoenix", "US", "Phoenix"),
    ("USA", "US", None),
    # 303 postings. Country names that were never in the list at all, unlike
    # Israel / India / Singapore / Japan / Germany, which are.
    ("Malaysia, Penang", "MY", "Penang"),
    ("Kuala Lumpur, Malaysia", "MY", "Kuala Lumpur"),
    ("Mexico - Mexico City", "MX", "Mexico City"),
    ("Manila, Philippines", "PH", "Manila"),
])
def test_a_bare_country_token_or_missing_country_name_resolves(
    raw, expected_country, expected_city
):
    got = location_facets(raw, source="workday", remote=None)
    assert (got.country, got.city) == (expected_country, expected_city)


def test_a_short_country_token_does_not_match_a_word_containing_it():
    """The reason the token was missing in the first place.

    An exact-segment match is what makes "us" safe; a substring needle would
    file every Houston and Austin posting in the wrong country.
    """
    for raw in ("Houston, Texas", "Austin, Texas", "Pusan"):
        got = location_facets(raw, source="greenhouse", remote=None)
        assert got.country in (None, "US"), raw
    # and specifically: Houston resolves via Texas or not at all, never via "us"
    assert location_facets("Pusan", source="greenhouse", remote=None).country is None


@pytest.mark.parametrize("raw", [
    "Santa Clara,CA",       # 386 -- Applied Materials
    "Santa Clara, CA",      # 123 -- Marvell
    "Boise, ID - Main Site",  # 386 -- Micron
    "Gloucester,MA",
    "Kalispell,MT",
    "Folsom, CA",
])
def test_a_us_state_code_resolves_the_country(raw):
    """2,001 postings. "City, ST" with no country token anywhere."""
    assert location_facets(raw, source="workday", remote=None).country == "US"


def test_the_state_pass_requires_an_uppercase_code_in_the_original_string():
    """`In-Office` is 43 live Cloudflare postings, and `-` is a separator, so
    the segments are ["In", "Office"] and "IN" is Indiana. Case is the only
    signal that separates a state code from an English word, and the country
    needles throw it away by casefolding -- so the state pass must not.
    """
    assert location_facets("In-Office", source="greenhouse", remote=None).country is None
    assert location_facets("Office-In", source="greenhouse", remote=None).country is None
    # The same letters as a real code still work when actually capitalised.
    assert location_facets("Indianapolis, IN", source="workday", remote=None).country == "US"


@pytest.mark.parametrize("raw,expected_city", [
    # Workday puts the state in the middle; the city is what comes after.
    ("US, CA, Santa Clara", "Santa Clara"),
    ("US, Oregon, Hillsboro", "Hillsboro"),
    ("Boise, ID - Main Site", "Boise"),
    # But a state name is often also a city, so the preference must not be an
    # exclusion. These were the regression the two-pass scan exists to avoid.
    ("New York, NY", "New York"),
    ("New York, New York", "New York"),
    ("Washington, WA", "Washington"),
])
def test_a_state_designation_is_a_last_resort_city_not_a_forbidden_one(raw, expected_city):
    assert location_facets(raw, source="workday", remote=None).city == expected_city


@pytest.mark.parametrize("raw", [
    "Indiana - Indianapolis",   # 22 postings, previously filed as India
    "Indiana - Remote",
    "Virginia - Mclean",
    "Georgia - Atlanta",
    "Atlanta, Georgia",
    "Denver, Colorado",
])
def test_a_spelled_out_state_name_also_resolves_the_country(raw):
    assert location_facets(raw, source="workday", remote=None).country == "US"


def test_the_state_pass_never_overrides_a_country_the_string_names():
    """CA is California and Canada; DE is Delaware and Germany. The state pass
    runs only after every country needle has missed, so a named country wins.
    """
    assert location_facets("Toronto, Canada", source="lever", remote=None).country == "CA"
    assert location_facets("Berlin, Germany", source="lever", remote=None).country == "DE"
    assert location_facets("Taipei, Taiwan", source="lever", remote=None).country == "TW"


def test_a_named_region_survives_an_unresolvable_country():
    """147 postings: "Home based - EMEA" named a region and got all-None.

    The module's unknown-is-kept bias forbids *inventing* a country from the
    remote flag; it does not require discarding a region the string states.
    prefilter_query matches on region, so these become reachable.
    """
    assert location_facets("Home based - EMEA", source="lever", remote=None) == LocationFacets(
        None, "emea", None
    )
    assert location_facets("Home based - APAC", source="lever", remote=None).region == "apac"
    # Still no invention: a remote flag with no place named stays empty.
    assert location_facets("Remote", source="ashby", remote=True) == LocationFacets(None, None, None)


def test_iso2_country_codes_are_deliberately_not_needles():
    """Declined, with a reason: CA/DE/IN/MO are unresolvable against US state
    codes, and the live data behind them is 127 postings (1.8% of the gap) --
    "Petaling Jaya, my" and "Rehovot,ISR". Ambiguity is not worth 1.8%.
    """
    taxonomy = load_taxonomy()
    for code in ("ca", "de", "in", "mo", "la", "ma", "il", "ga"):
        assert code not in taxonomy.location_countries, code


# --- two gaps measured over 3,000 live postings, 2026-11-13 ---------------
# Both were found while building the country/city pickers and deliberately
# left out of that change: neither reaches an existing `job_facets` row until
# someone runs `enrich_jobs(force=True)` over the catalogue, which is its own
# job with its own before/after measurement (app.jobs.rederive_facets).


@pytest.mark.parametrize("raw,expected", [
    # Workday's `CC-City` form. 26 of 3,000 sampled postings use it; 12 lost a
    # city the string plainly names, because the bare country code won the
    # city slot. Roughly 90 live postings, all EMEA/APAC.
    ("GB-London", LocationFacets("GB", "emea", "London")),
    ("NL-Amsterdam", LocationFacets("NL", "emea", "Amsterdam")),
    ("PL-Warsaw-Lixa C", LocationFacets("PL", "emea", "Warsaw")),
    # Same form, lowercase code, from the Brazil sample below.
    ("São Paulo, br", LocationFacets("BR", "latam", "São Paulo")),
])
def test_a_bare_two_letter_code_never_takes_the_city_slot(raw, expected):
    assert location_facets(raw, source="workday", remote=None) == expected


def test_the_two_letter_rule_does_not_cost_a_us_state_its_fallback_city():
    """The skip sits after the state branch, deliberately.

    A US state code is also two letters, and `location_facets` keeps one as a
    last-resort city when the string offers nothing better -- which is the
    only reason "New York, NY" still yields a city at all. Skipping every
    two-letter segment before that branch would have taken it away.
    """
    assert location_facets("New York, NY", source="greenhouse", remote=None) == LocationFacets(
        "US", "north_america", "New York"
    )
    # Nothing but a state code in the string: the fallback is all there is.
    assert location_facets("NY", source="greenhouse", remote=None).city == "NY"
    assert location_facets("Santa Clara, CA", source="greenhouse", remote=None).city == "Santa Clara"


def test_the_two_letter_rule_is_ascii_only():
    """"台北" is two characters and is a city; "GB" is two characters and is not.

    Counting characters without checking the script would have deleted every
    CJK-spelled city from the facet.
    """
    assert location_facets("台北", source="workday", remote=None).city == "台北"
    assert location_facets("新竹市", source="workday", remote=None).city == "新竹市"


def test_the_two_letter_rule_did_not_become_iso2_country_resolution():
    """The declined ruling still holds, and this is where the line falls.

    `test_iso2_country_codes_are_deliberately_not_needles` refuses to make
    ISO-2 codes *country* needles. Dropping them from the *city* scan is a
    different question and does not reopen it: a code the country scan cannot
    resolve still yields no country, it just no longer poses as a city.

    "AU-Brisbane-MSO" is the case this leaves on the floor -- the country is
    lost too, because "brisbane" is not a city needle. Resolving it needs
    either an ISO-2 country reading (declined) or "brisbane" in
    `location_countries` (a vocabulary question, not this one).
    """
    assert location_facets("Petaling Jaya, my", source="workday", remote=None).country is None
    assert location_facets("AU-Brisbane-MSO", source="workday", remote=None) == LocationFacets(
        None, None, None
    )


@pytest.mark.parametrize("raw,expected", [
    # 19 of 3,000 sampled postings, roughly 138 live, every one of them with
    # no country, no city and no region -- invisible to every filter. The
    # plain-ASCII country name resolved to nothing too, so this was a
    # vocabulary gap and not an encoding one.
    ("São Paulo", LocationFacets("BR", "latam", "São Paulo")),
    ("São Paulo, Brazil", LocationFacets("BR", "latam", "São Paulo")),
    ("São Paulo, São Paulo, Brazil", LocationFacets("BR", "latam", "São Paulo")),
    ("Sao Paulo, Brazil", LocationFacets("BR", "latam", "Sao Paulo")),
    ("Bogotá, Colombia", LocationFacets("CO", "latam", "Bogotá")),
    # Named but unsampled: the needle list covers the region's other
    # economies so the next posting from one is not a second backfill.
    ("Buenos Aires, Argentina", LocationFacets("AR", "latam", "Buenos Aires")),
    ("Santiago, Chile", LocationFacets("CL", "latam", "Santiago")),
    ("Lima, Peru", LocationFacets("PE", "latam", "Lima")),
])
def test_latin_america_resolves_instead_of_yielding_nothing(raw, expected):
    assert location_facets(raw, source="workday", remote=None) == expected


def test_the_sao_paulo_city_alias_can_finally_fire():
    """`city_aliases` already held "sao paulo" and could never reach it.

    The alias folds the ASCII spelling onto the accented one for the picker,
    but `canonical_cities` only ever sees a stored city, and the city was
    None until the country resolved. This is the test that the two halves
    now meet.
    """
    from app.services.jobs.cities import canonical_cities

    stored = location_facets("Sao Paulo, Brazil", source="workday", remote=None).city
    assert canonical_cities(stored) == ["São Paulo"]
    accented = location_facets("São Paulo, Brazil", source="workday", remote=None).city
    assert canonical_cities(accented) == ["São Paulo"]


def test_a_new_country_needle_does_not_outrank_a_us_state():
    """"Brazil, Indiana" is a real US town, and the country scan runs first.

    This is the exact shape of the bug 76446f4 fixed for Indiana/India, so
    the new needles get the same check. Word boundaries keep "brasil" out of
    "Brasilia" and "chile" out of "Chilecito"; they cannot help when the
    whole segment IS the country name, which is why this case is asserted
    rather than claimed. The live catalogue has no such posting -- if one
    appears, it needs a city needle, not a weaker country scan.
    """
    taxonomy = load_taxonomy()
    for needle in ("brazil", "brasil", "colombia", "chile", "peru", "argentina"):
        assert needle in taxonomy.location_countries, needle
    # Inside a longer word, the needles stay silent.
    assert location_facets("Chilecito", source="workday", remote=None).country is None
    assert location_facets("Brasilia", source="workday", remote=None).country is None
    # And the known collision, documented rather than silently wrong.
    assert location_facets("Brazil, Indiana", source="workday", remote=None).country == "BR"


def test_city_is_truncated_to_the_column_width():
    """A long location segment must not overflow JobFacets.city.

    city is String(255), Job.location is String(512), and a single segment can
    fill the latter. SQLite ignores VARCHAR lengths so the suite would pass
    either way; Postgres raises DataError at enrich's flush, which (before the
    savepoint in ingest_board) took the whole ingest pass with it.
    """
    facets = location_facets("Taipei " + "\u9577" * 300, source="workday", remote=None)
    assert facets.country == "TW"
    assert facets.city is not None
    assert len(facets.city) == 255


def test_country_and_region_values_fit_their_columns():
    """The other two facet columns, for the same class of bug.

    Unlike city, neither is ever copied out of the posting: both are *values*
    from job_taxonomy.json's closed vocabularies, so the bound belongs on the
    vocabulary rather than on the assignment. country is String(2) and region
    is String(32) (app/models.py JobFacets).
    """
    taxonomy = load_taxonomy()
    assert all(len(v) <= 2 for v in taxonomy.location_countries.values())
    assert all(len(v) <= 32 for v in taxonomy.location_regions.values())


def test_location_none_yields_empty_facets_without_raising():
    assert location_facets(None, source="ashby", remote=True) == LocationFacets(None, None, None)


def test_remote_flag_does_not_invent_a_country():
    assert location_facets("Remote", source="ashby", remote=True).country is None


def test_location_empty_string_yields_empty_facets_without_raising():
    assert location_facets("", source="ashby", remote=None) == LocationFacets(None, None, None)


def test_location_whitespace_only_yields_empty_facets_without_raising():
    assert location_facets("   ", source="ashby", remote=None) == LocationFacets(None, None, None)


def test_location_punctuation_only_yields_empty_facets_without_raising():
    assert location_facets(" - , - ", source="ashby", remote=None) == LocationFacets(None, None, None)


def test_location_multi_country_string_keeps_the_first_country_mentioned():
    # Postings that paste together several office locations ("City, Country;
    # City, Country") name more than one nation in a single field. This
    # module stays first-match-wins, the same bias used for seniority and
    # function: the first segment (reading left to right, after the usual
    # comma/dash split) that resolves a country wins, and any country named
    # later in the string -- here "Germany" -- is ignored rather than
    # merged into a list. A future facet could surface every country if the
    # product needs it; this derivation only ever reports one.
    assert location_facets(
        "London, UK; Berlin, Germany", source="greenhouse", remote=None
    ) == LocationFacets("GB", "emea", "London")


def test_location_city_segment_after_country_segment_is_still_found():
    # "Taiwan, Taipei" puts the bare country name first and the city
    # second. The country-vs-city ruling says city is the first segment
    # that is NOT itself exactly a country (or region) name, not simply
    # "the first segment" -- so the scan must keep going past "Taiwan" to
    # find "Taipei".
    assert location_facets("Taiwan, Taipei", source="workday", remote=None) == (
        LocationFacets("TW", "apac", "Taipei")
    )


def test_location_remote_placeholder_between_country_segments_yields_no_city():
    # A remote_needles placeholder segment must never be picked as the
    # city, even when it sits between two other segments and a country
    # still resolves elsewhere in the string.
    assert location_facets(
        "Remote - United States - Anywhere", source="lever", remote=True
    ) == LocationFacets("US", "north_america", None)


def test_enrich_writes_facets_for_a_job(db_session):
    job = _add_job(db_session, source_id="anthropic:1",
                   title="Staff Software Engineer, Infrastructure",
                   location="San Francisco, CA", description_text="Own the fleet.")
    assert enrich_jobs(db_session, job_ids=[job.id]) == 1
    row = db_session.get(JobFacets, job.id)
    assert (row.seniority, row.function, row.country, row.region) == (
        "staff_plus", "infra", "US", "north_america")


def test_enrich_is_a_noop_when_the_description_has_not_changed(db_session):
    job = _add_job(db_session, source_id="anthropic:2", description_text="Same text.")
    enrich_jobs(db_session, job_ids=[job.id])
    assert enrich_jobs(db_session, job_ids=[job.id]) == 0


def test_enrich_rewrites_when_the_description_changes(db_session):
    job = _add_job(db_session, source_id="anthropic:3", title="Engineer, Platform",
                   description_text="You will write Django.")
    enrich_jobs(db_session, job_ids=[job.id])
    job.description_text = "You will own our Kubernetes fleet."
    db_session.commit()
    assert enrich_jobs(db_session, job_ids=[job.id]) == 1
    assert db_session.get(JobFacets, job.id).function == "infra"


def test_force_rewrites_an_unchanged_row(db_session):
    job = _add_job(db_session, source_id="anthropic:4")
    enrich_jobs(db_session, job_ids=[job.id])
    assert enrich_jobs(db_session, job_ids=[job.id], force=True) == 1


def test_enrich_backfills_every_active_job_when_no_ids_are_given(db_session):
    _add_job(db_session, source_id="a:1")
    _add_job(db_session, source_id="a:2")
    _add_job(db_session, source_id="a:3", is_active=False)
    assert enrich_jobs(db_session) == 2


def test_enrich_handles_a_maximum_length_description(db_session):
    from app.services.jobs.types import MAX_DESCRIPTION_CHARS
    job = _add_job(db_session, source_id="a:4", description_text="x" * MAX_DESCRIPTION_CHARS)
    assert enrich_jobs(db_session, job_ids=[job.id]) == 1
    assert len(db_session.get(JobFacets, job.id).description_hash) == 64


def test_deleting_a_job_cascades_to_its_facets(db_session):
    job = _add_job(db_session, source_id="a:5")
    enrich_jobs(db_session, job_ids=[job.id])
    db_session.delete(job)
    db_session.commit()
    assert db_session.get(JobFacets, job.id) is None


def test_enrich_backfill_walks_active_jobs_in_bounded_batches(db_session, monkeypatch):
    # Pins the batching behaviour the backfill path (job_ids=None) relies
    # on to bound peak memory, without fabricating an 11k-row fixture: a
    # tiny batch size against 5 real jobs is enough to show the scan is
    # chunked rather than loaded whole. Wrapping the real _enrich_job_rows
    # (rather than replacing it outright) keeps the assertion honest -- the
    # jobs still get enriched for real, we just also record how big each
    # chunk handed to it was.
    monkeypatch.setattr(enrich_module, "_BACKFILL_BATCH_SIZE", 2)
    for i in range(5):
        _add_job(db_session, source_id=f"batch:{i}")

    real_enrich_job_rows = enrich_module._enrich_job_rows
    batch_sizes = []

    def spy(db, rows, **kwargs):
        batch_sizes.append(len(rows))
        return real_enrich_job_rows(db, rows, **kwargs)

    monkeypatch.setattr(enrich_module, "_enrich_job_rows", spy)

    assert enrich_jobs(db_session) == 5
    assert batch_sizes == [2, 2, 1]


def test_enrich_explicit_job_ids_are_not_chunked(db_session, monkeypatch):
    # The per-board path (explicit job_ids) must stay the original
    # two-query shape -- never split across the backfill's batching loop,
    # even when the batch size is tiny enough that it otherwise would be.
    monkeypatch.setattr(enrich_module, "_BACKFILL_BATCH_SIZE", 2)
    jobs = [_add_job(db_session, source_id=f"explicit:{i}") for i in range(5)]

    real_enrich_job_rows = enrich_module._enrich_job_rows
    batch_sizes = []

    def spy(db, rows, **kwargs):
        batch_sizes.append(len(rows))
        return real_enrich_job_rows(db, rows, **kwargs)

    monkeypatch.setattr(enrich_module, "_enrich_job_rows", spy)

    assert enrich_jobs(db_session, job_ids=[job.id for job in jobs]) == 5
    assert batch_sizes == [5]


def test_survey_reports_unknown_shares(db_session, capsys):
    _add_job(db_session, source_id="a:1", title="Senior Backend Engineer", location="Taipei, Taiwan")
    _add_job(db_session, source_id="a:2", title="Wizard of Light Bulb Moments", location=None)
    enrich_jobs(db_session); db_session.commit()
    from app.jobs.survey_facets import survey
    report = survey(db_session)
    assert report["total"] == 2
    assert report["unknown_seniority"] == 1
    assert report["null_country"] == 1


def test_survey_counts_missing_facets_separately_from_unknown_seniority(db_session):
    # A posting with no job_facets row at all (enrich_jobs never ran on it)
    # is a different problem from one whose needle lists ran and came back
    # "unknown" -- conflating the two would send the maintainer tuning
    # needles when a backfill is what is actually missing.
    from app.jobs.survey_facets import survey

    _add_job(db_session, source_id="b:1", title="Totally Unenriched Posting")
    # No enrich_jobs call at all -- this job has no JobFacets row.
    report = survey(db_session)
    assert report["total"] == 1
    assert report["no_facets"] == 1
    assert report["unknown_seniority"] == 0


def test_survey_measures_city_through_the_fold_not_against_null(db_session):
    """The bucket that makes the city half of a backfill measurable.

    Before the `CC-City` fix, "GB-London" stored city="GB": not NULL, and
    not a city either. A `city IS NULL` count reported it as covered, so the
    fix would have moved no number the survey printed. This bucket counts a
    stored city that folds to nothing through `canonical_cities`, which is
    exactly what the picker shows.
    """
    from app.jobs.survey_facets import survey

    _add_job(db_session, source_id="c:1", title="Engineer", location="London, United Kingdom")
    _add_job(db_session, source_id="c:2", title="Engineer", location="Multiple Locations")
    enrich_jobs(db_session)
    # Put the pre-fix value back by hand: this is what the catalogue holds
    # today for every CC-City posting, and what the backfill will replace.
    facets = db_session.query(JobFacets).join(Job).filter(Job.source_id == "c:1").one()
    facets.city = "GB"
    db_session.flush()

    report = survey(db_session)
    assert report["total"] == 2
    # One stored-but-unpickable ("GB"), one genuinely NULL.
    assert report["no_pickable_city"] == 2
    assert any("'GB'" in example for example in report["examples"]["no_pickable_city"])

    # And with the real derivation in place, the London posting is covered.
    facets.city = "London"
    db_session.flush()
    assert survey(db_session)["no_pickable_city"] == 1


def test_survey_groups_counts_by_source(db_session):
    from app.jobs.survey_facets import survey

    _add_job(db_session, source_id="g:1", source="greenhouse",
              title="Senior Backend Engineer", location="Taipei, Taiwan")
    _add_job(db_session, source_id="g:2", source="greenhouse",
              title="Wizard of Light Bulb Moments", location=None)
    _add_job(db_session, source_id="l:1", source="lever",
              title="Mystery Role", location=None)
    enrich_jobs(db_session)
    db_session.commit()

    report = survey(db_session)
    by_source = report["by_source"]
    assert by_source["greenhouse"]["total"] == 2
    assert by_source["greenhouse"]["unknown_seniority"] == 1
    assert by_source["greenhouse"]["null_country"] == 1
    assert by_source["lever"]["total"] == 1
    assert by_source["lever"]["unknown_seniority"] == 1
    assert by_source["lever"]["null_country"] == 1


def test_survey_limit_caps_rows_considered(db_session):
    from app.jobs.survey_facets import survey

    for i in range(5):
        _add_job(db_session, source_id=f"lim:{i}")
    enrich_jobs(db_session)
    db_session.commit()

    report = survey(db_session, limit=2)
    assert report["total"] == 2


def test_survey_on_empty_catalogue_returns_zeroes(db_session):
    from app.jobs.survey_facets import survey

    report = survey(db_session)
    assert report["total"] == 0
    assert report["unknown_seniority"] == 0
    assert report["null_country"] == 0
    assert report["no_facets"] == 0
    assert report["by_source"] == {}


def test_survey_never_writes_facets(db_session):
    # Read-only: a maintainer runs this against production, so it must
    # never call enrich_jobs or otherwise create/modify a job_facets row.
    from app.jobs.survey_facets import survey

    job = _add_job(db_session, source_id="ro:1", title="Senior Backend Engineer")
    survey(db_session)
    assert db_session.get(JobFacets, job.id) is None


def test_survey_cli_main_prints_a_report(db_session, capsys, monkeypatch):
    from app import jobs as jobs_pkg  # noqa: F401
    import app.jobs.survey_facets as survey_facets
    from app.db import get_session_factory

    _add_job(db_session, source_id="cli:1", title="Senior Backend Engineer",
              location="Taipei, Taiwan")
    db_session.commit()

    # main() opens its own session via get_session_factory(); in this test
    # environment that factory is already bound to the same in-memory
    # sqlite engine the db_session fixture uses, via conftest's reset_engine.
    monkeypatch.setattr(survey_facets, "get_engine", lambda: None)
    monkeypatch.setattr(survey_facets, "get_session_factory", lambda: get_session_factory())

    exit_code = survey_facets.main([])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "total" in captured.out.lower()


# --- the forced re-derive --------------------------------------------------
# Neither taxonomy fix above reaches an existing row: enrich_jobs's gate is
# keyed on the description hash and knows nothing about the parser. These
# tests pin the script that forces it, because the thing it does wrong is
# silent -- a backfill that skips every row still exits 0.


def test_rederive_rewrites_a_row_the_hash_gate_would_have_skipped(db_session, monkeypatch):
    import app.jobs.rederive_facets as rederive
    from app.db import get_session_factory

    _add_job(db_session, source_id="rd:1", title="Engineer", location="GB-London")
    enrich_jobs(db_session)
    facets = db_session.query(JobFacets).one()
    # The pre-fix value, by hand: the catalogue holds this today and the
    # description has not changed, so a plain enrich_jobs leaves it alone.
    facets.city = "GB"
    db_session.commit()
    assert enrich_jobs(db_session) == 0

    monkeypatch.setattr(rederive, "get_engine", lambda: None)
    monkeypatch.setattr(rederive, "get_session_factory", lambda: get_session_factory())
    assert rederive.main([]) == 0

    db_session.expire_all()
    assert db_session.query(JobFacets).one().city == "London"


def test_rederive_dry_run_reports_without_writing(db_session, monkeypatch, capsys):
    """The flag that makes the script safe to point at production first.

    enrich_jobs flushes per chunk and never commits, so the rollback really
    does undo every chunk -- if that contract ever changes, this is the test
    that catches it before 21k live rows do.
    """
    import app.jobs.rederive_facets as rederive
    from app.db import get_session_factory

    _add_job(db_session, source_id="rd:2", title="Engineer", location="GB-London")
    enrich_jobs(db_session)
    db_session.query(JobFacets).one().city = "GB"
    db_session.commit()

    monkeypatch.setattr(rederive, "get_engine", lambda: None)
    monkeypatch.setattr(rederive, "get_session_factory", lambda: get_session_factory())
    assert rederive.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "reenriched=1" in out
    assert "committed=False" in out

    db_session.expire_all()
    assert db_session.query(JobFacets).one().city == "GB"


def test_rederive_reports_a_failure_instead_of_exiting_zero(db_session, monkeypatch, capsys):
    import app.jobs.rederive_facets as rederive
    from app.db import get_session_factory

    monkeypatch.setattr(rederive, "get_engine", lambda: None)
    monkeypatch.setattr(rederive, "get_session_factory", lambda: get_session_factory())

    def boom(*args, **kwargs):
        raise RuntimeError("derivation exploded")

    monkeypatch.setattr(rederive, "enrich_jobs", boom)
    assert rederive.main([]) == 1
    assert "derivation exploded" in capsys.readouterr().err


# A short needle left unpadded hides inside ordinary words, and the description
# side is where that bites: a bare "llm" matched the "llm" in "fulfillment", so
# every DoorDash fulfilment-centre posting -- "Shift Lead - Nashville" and its
# siblings -- was filed under `ml`. Found in production, against the live
# catalogue, after `ml` came back as 29% of every enriched posting. "react"
# ("reactive", "reactor") and "silicon" ("Silicon Valley") are the same class.
#
# These are pinned individually rather than by a blanket "every short needle is
# padded" rule, because padding is not free either: " backend " would stop
# matching "Frontend/Backend Engineer", which the bare needle catches. Only
# needles with a demonstrated real-world collision are padded.
@pytest.mark.parametrize(
    "title,description,expected",
    [
        ("Shift Lead - Nashville", "We run a local fulfillment center.", "other"),
        ("Warehouse Lead", "Our fulfillment center handles enrollment.", "other"),
        ("Operations Associate", "Nuclear reactor maintenance.", "other"),
        ("Backend Engineer", "We build reactive streaming systems.", "backend"),
        ("Program Manager, Silicon Valley Office", "Office ops.", "product"),
        # The true positives these needles exist for must still land.
        ("Research Scientist", "We train large language models with an LLM.", "ml"),
        ("Frontend Engineer", "We use React.", "frontend"),
        ("Silicon Design Engineer", "Chip work.", "hardware"),
    ],
)
def test_short_needles_do_not_hide_inside_longer_words(title, description, expected):
    assert function_of(title, description) == expected


def test_a_batch_costs_two_queries_however_many_rows_it_holds(db_session):
    # Three docstrings promise this shape -- one bulk JobFacets lookup and one
    # bulk boilerplate lookup per batch, never a query per row -- and until now
    # nothing held them to it. Moving load_boilerplate inside the row loop
    # leaves every other test in this file green while issuing one query per
    # posting: 21,773 of them on a backfill. Six rows over three companies, so
    # a per-row lookup counts as six and a per-company one as three.
    jobs = [
        _add_job(db_session, source_id=f"q{i}", company=f"Company {i % 3}",
                 title=f"Engineer {i}", description_text="We build things here.")
        for i in range(6)
    ]
    db_session.flush()
    rows = (
        db_session.query(*_ENRICH_COLUMNS).filter(Job.id.in_([j.id for j in jobs])).all()
    )

    selects: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            selects.append(statement)

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", record)
    try:
        written = _enrich_job_rows(
            db_session,
            rows,
            taxonomy=default_taxonomy(),
            force=False,
            stamp=datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
        )
    finally:
        event.remove(bind, "before_cursor_execute", record)

    assert written == 6
    assert len(selects) == 2, selects


MISSION = "We bring cutting-edge autonomy and computer vision to every mission."
SOURCING = "You will own supplier contracts and the quarterly sourcing review."


def _anduril_buyers(db_session, count):
    return [
        _add_job(db_session, source_id=f"a{i}", company="Anduril",
                 title=f"Senior Buyer {i}", description_text=f"{MISSION} {SOURCING}")
        for i in range(count)
    ]


def test_boilerplate_sentence_does_not_decide_the_function(db_session):
    _anduril_buyers(db_session, 6)
    db_session.flush()
    recompute_boilerplate(db_session)
    enrich_jobs(db_session)
    assert {f.function for f in db_session.query(JobFacets).all()} == {"other"}


def test_a_real_ml_posting_keeps_its_function(db_session):
    _anduril_buyers(db_session, 5)
    _add_job(db_session, source_id="perception", company="Anduril",
             title="Perception Engineer",
             description_text=f"{MISSION} You will train deep learning models for perception.")
    db_session.flush()
    recompute_boilerplate(db_session)
    enrich_jobs(db_session)
    fn = {j.title: f.function
          for j, f in db_session.query(Job, JobFacets).join(JobFacets, JobFacets.job_id == Job.id)}
    assert fn["Perception Engineer"] == "ml"
    assert fn["Senior Buyer 0"] == "other"


def test_the_hash_gate_reacts_to_a_boilerplate_change(db_session):
    jobs = _anduril_buyers(db_session, 6)
    db_session.flush()
    recompute_boilerplate(db_session)
    enrich_jobs(db_session)
    assert enrich_jobs(db_session) == 0          # nothing changed: gate holds
    for job in jobs[:2]:
        job.is_active = False
    db_session.flush()
    recompute_boilerplate(db_session)            # mission drops below the cutoff
    assert enrich_jobs(db_session) == 4          # rewritten unforced, by the hash alone


# The other direction of the same property, which nobody checked when
# stripping still re-segmented and rejoined the whole description: inserting a
# space at every segment boundary made space-padded needles match bullets of
# bare technology names that the raw text never matched. Bullet lists like
# these are ubiquitous, and widening description matching is the exact signal
# this mechanism exists to distrust, so a posting with no boilerplate must
# derive what its raw text derives -- "other" -- not something stripping
# manufactured.
@pytest.mark.parametrize(
    "description",
    [
        "Our stack:\nReact\nTypeScript\nNode",
        "Responsibilities:\n- Build services in Go\n- Own our SQL\n- Mentor peers",
        "Day to day:\nSQL\nSpreadsheets\nMeetings",
    ],
)
def test_stripping_does_not_invent_needles_without_boilerplate(db_session, description):
    job = _add_job(db_session, source_id="invent", company="NoBoilerplateCo",
                   title="Member of Staff", description_text=description)
    assert function_of(job.title, description) == "other"   # raw baseline
    enrich_jobs(db_session)
    assert db_session.query(JobFacets).one().function == "other"


@pytest.mark.parametrize(
    "description, expected",
    [
        ("Our stack is modern.\n  React and TypeScript on the front end.", "frontend"),
        ("Data team. SQL, dbt and Airflow daily.", "data"),
    ],
)
def test_stripping_does_not_break_padded_needles_without_boilerplate(
    db_session, description, expected
):
    # No boilerplate row exists for this company: stripping must not change
    # the derived function. A space-padded needle at a segment boundary
    # (e.g. ' react ', ' sql,') must still match after segments are rejoined.
    job = _add_job(db_session, source_id="pad", company="NoBoilerplateCo",
                   title="Member of Staff", description_text=description)
    assert function_of(job.title, description) == expected   # raw baseline
    enrich_jobs(db_session)
    assert db_session.query(JobFacets).one().function == expected


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Software Engineer", "software"),
        ("Senior Software Engineer", "software"),
        ("Software Development Engineer II", "software"),
        ("Software Developer", "software"),
        ("Software Architect", "software"),
        ("iOS Engineer", "software"),
        ("Android Engineer", "software"),
        ("Engineering Manager, Payments", "software"),
        ("Tech Lead", "software"),
        ("Technical Lead", "software"),
        ("軟體工程師", "software"),
        ("软件开发工程师", "software"),
        # The fallback tier is for generic SOFTWARE titles. These are the
        # engineers who correctly stay in `other` -- 403 process, 289
        # manufacturing, 203 test and 167 mechanical titles in the live
        # catalogue when this was written. A bare " engineer " needle in the
        # fallback tier would swallow every one of them.
        ("Process Engineer", "other"),
        ("Manufacturing Engineer", "other"),
        ("Mechanical Engineer", "other"),
        ("Test Engineer", "other"),
        ("Process Support Engineer III", "other"),
        ("Equipment Technician", "other"),
        ("Kitchen Operations Associate", "other"),
    ],
)
def test_generic_software_titles_get_the_fallback_function(title, expected):
    assert function_of(title, "") == expected


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Senior Software Engineer, Backend", "backend"),
        ("Sr Software Engineer Backend", "backend"),
        ("Full Stack Software Engineer, Growth", "fullstack"),
        ("Software Engineer, Data and AI Platform", "data"),
        ("Software Engineer - Trust & Platform Security", "security"),
        ("Systems Software Engineer - Infrastructure", "infra"),
        ("Machine Learning Software Engineer", "ml"),
        ("Engineering Manager, Red Team", "security"),
        ("Hardware Engineer", "hardware"),
        ("Product Manager, Developer Tools", "product"),
    ],
)
def test_fallback_tier_never_overrides_a_specialised_function(title, expected):
    # The generic needles are LONGER than most specialised ones
    # ("software engineer" is 17 characters, "backend" is 7), so as an
    # ordinary rule in the longest-match contest they would win and strip
    # the specialisation off the posting. Measured on the live catalogue,
    # that moved 2,864 postings off a real function to gain 899.
    assert function_of(title, "") == expected


def test_a_specialised_description_outranks_a_generic_title():
    # Both specialised passes (title, then description) run before the
    # fallback tier is consulted at all. That ordering is what makes the
    # tier additive: it can only ever claim a posting that would otherwise
    # be "other".
    assert function_of("Software Engineer", "you will train deep learning models") == "ml"
    assert function_of("Software Engineer", "mostly writing and reviewing code") == "software"


def test_the_fallback_function_is_part_of_the_vocabulary():
    # /v1/jobs must accept it as a filter, and the preference validator as a
    # target; both read valid_functions().
    assert "software" in valid_functions(default_taxonomy())


def test_a_taxonomy_with_no_fallback_tier_still_loads(tmp_path):
    # The key is optional: an older data file must not break the loader.
    import json

    data = json.loads(enrich_module.taxonomy_path().read_text(encoding="utf-8"))
    data.pop("function_fallback_rules")
    path = tmp_path / "taxonomy.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    taxonomy = load_taxonomy(path)
    assert taxonomy.function_fallback_rules == ()
    assert function_of("Software Engineer", "", taxonomy) == "other"
