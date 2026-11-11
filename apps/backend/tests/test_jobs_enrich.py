from datetime import datetime, timezone

import pytest

from app.models import Job, JobFacets
from app.services.jobs import enrich as enrich_module
from app.services.jobs.enrich import (
    LocationFacets,
    description_hash,
    enrich_jobs,
    function_of,
    load_taxonomy,
    location_facets,
    seniority_of,
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
        "north_america", "emea", "apac",
    }
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
