import pytest

from app.services.jobs.enrich import (
    LocationFacets,
    _COUNTRY_NAME_NEEDLES,
    function_of,
    load_taxonomy,
    location_facets,
    seniority_of,
)


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
    ("Software Engineer", "", "other"),
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
    # _COUNTRY_NAME_NEEDLES is hand-maintained code (see the enrich.py
    # module docstring for why it can't be derived from job_taxonomy.json).
    # If a needle is renamed or removed from location_countries, this is
    # the test that should catch the drift instead of location_facets
    # silently stopping recognizing a country name.
    taxonomy = load_taxonomy()
    assert _COUNTRY_NAME_NEEDLES <= set(taxonomy.location_countries)


def test_taxonomy_file_covers_location_vocabularies():
    # location_countries and location_regions are coupled 1:1 by needle --
    # location_facets relies on this to look region up by the same needle
    # that already resolved the country, instead of re-scanning the
    # string. If a future edit to job_taxonomy.json adds a needle to one
    # dict but not the other, this is the test that should catch it.
    taxonomy = load_taxonomy()
    assert set(taxonomy.location_countries) == set(taxonomy.location_regions)
    assert set(taxonomy.location_regions.values()) <= {
        "north_america", "emea", "apac",
    }


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
