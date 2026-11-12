"""The deterministic prefilter: SQL that reduces the catalogue to plausible matches."""

import itertools
import json
from datetime import timedelta

import pytest
from pydantic import ValidationError

from app.jobs.match_sweep import (
    _SOFTWARE_FAMILY,
    SENIORITY_LADDER,
    count_matches,
    expand_functions,
    prefilter_query,
)
from app.models import Job, JobFacets, JobPreference
from app.schemas_jobs import JobPreferenceIn
from app.services.jobs.enrich import default_taxonomy, valid_seniorities
from app.services.jobs.store import freshness_cutoff

_seq = itertools.count()


def _posting(db, *, seniority="mid", function="backend", country="TW", region=None,
             city=None, remote=None, is_active=True, stale=False, facets=True):
    n = next(_seq)
    seen = freshness_cutoff() - timedelta(days=1) if stale else freshness_cutoff() + timedelta(days=3)
    job = Job(source="greenhouse", source_id=f"ms-{n}", company="Acme", title="Engineer",
              remote=remote, apply_url="https://x/a", source_url="https://x/s",
              last_seen_at=seen, is_active=is_active)
    db.add(job)
    db.flush()
    if facets:
        db.add(JobFacets(job_id=job.id, country=country, region=region, city=city,
                         seniority=seniority, function=function, description_hash="h"))
    db.commit()
    return job


def _facts_row(roles):
    """An unsaved ResumeFacts, enough for seed_from_facts (which is pure)."""
    from app.models import ResumeFacts

    return ResumeFacts(
        user_id="u", source_resume_id="r", source_revision_id="rev",
        input_hash="h" * 64,
        facts_json=json.dumps({"roles": roles, "locations": []}),
    )


def _pref(**kw):
    kw.setdefault("target_functions", "")
    kw.setdefault("target_locations", "")
    kw.setdefault("target_seniority", None)
    kw.setdefault("remote_required", None)
    return JobPreference(user_id="transient", **kw)  # never added to a session


def test_ladder_excludes_off_ladder_members():
    assert SENIORITY_LADDER == ("intern", "new_grad", "junior", "mid", "senior", "staff_plus")


def test_hard_filters_exclude_inactive_stale_and_mismatched(db_session):
    _posting(db_session)
    _posting(db_session, is_active=False)
    _posting(db_session, stale=True)
    _posting(db_session, function="design")
    _posting(db_session, country="JP")
    _posting(db_session, facets=False)
    pref = _pref(target_functions="|backend|", target_locations="|tw|")
    assert count_matches(db_session, pref) == 1


@pytest.mark.parametrize(
    "target,seniority,admitted",
    [
        ("intern", "intern", True), ("intern", "new_grad", True),
        ("intern", "junior", False),
        ("staff_plus", "senior", True), ("staff_plus", "staff_plus", True),
        ("staff_plus", "mid", False),
        ("senior", "mid", True), ("senior", "staff_plus", True),
        ("senior", "junior", False),
    ],
)
def test_seniority_admits_only_within_one_band(db_session, target, seniority, admitted):
    _posting(db_session, seniority=seniority)
    pref = _pref(target_seniority=target, target_functions="|backend|", target_locations="|tw|")
    assert count_matches(db_session, pref) == (1 if admitted else 0)


@pytest.mark.parametrize("off_ladder", ["unknown", "manager"])
@pytest.mark.parametrize("target", ["intern", "staff_plus", "mid"])
def test_off_ladder_seniorities_are_never_admitted(db_session, off_ladder, target):
    _posting(db_session, seniority=off_ladder)
    pref = _pref(target_seniority=target, target_functions="|backend|", target_locations="|tw|")
    assert count_matches(db_session, pref) == 0


@pytest.mark.parametrize("target", sorted(valid_seniorities(default_taxonomy())))
def test_every_target_the_api_accepts_reaches_its_own_postings(db_session, target):
    """Parametrised over EVERY value the vocabulary holds, because the bug this
    replaces was a value the API accepted and the editor offered but _band
    could not place -- a seeded `manager` had a permanent count of 0.

    Each value must either be refused at the schema boundary, so no client can
    set it, or match a posting carrying that very seniority. The old test used
    "wizard", which the validator rejects, so it pinned a path no client has.
    """
    try:
        JobPreferenceIn(target_seniority=target)
    except ValidationError:
        return  # refused at the boundary; nothing reaches _band
    _posting(db_session, seniority=target)
    assert count_matches(db_session, _pref(target_seniority=target,
                                           target_functions="|backend|",
                                           target_locations="|tw|")) == 1


def test_manager_is_an_exact_axis_not_a_band(db_session):
    """`manager` is a different axis from the ladder, so it admits itself and
    only itself -- and is the only route any preference has to those postings."""
    _posting(db_session, seniority="manager")
    _posting(db_session, seniority="senior")
    _posting(db_session, seniority="staff_plus")
    assert count_matches(db_session, _pref(target_seniority="manager",
                                           target_functions="|backend|")) == 1


def test_unknown_is_refused_as_a_target_but_stays_a_browse_filter():
    with pytest.raises(ValidationError):
        JobPreferenceIn(target_seniority="unknown")
    # Still a member of the vocabulary /jobs filters on: "show me the
    # postings no rule could classify".
    assert "unknown" in valid_seniorities(default_taxonomy())


def test_an_unrecognised_target_seniority_is_refused_at_the_boundary(db_session):
    with pytest.raises(ValidationError):
        JobPreferenceIn(target_seniority="wizard")
    # Defence in depth: a hand-built row still matches nothing, not everything.
    _posting(db_session)
    assert count_matches(db_session, _pref(target_seniority="wizard",
                                           target_functions="|backend|")) == 0


def test_an_empty_axis_is_no_constraint_but_the_always_on_filters_still_apply(db_session):
    _posting(db_session, function="design", country="JP")
    _posting(db_session, function="design", country="JP", is_active=False)
    _posting(db_session, function="design", country="JP", stale=True)
    pref = _pref(target_functions="|design|", target_locations="", target_seniority=None)
    assert count_matches(db_session, pref) == 1


@pytest.mark.parametrize(
    "required,posting_remote,admitted",
    [(True, True, True), (True, False, False),
     (False, True, False), (False, False, True),
     (None, True, True), (None, False, True),
     (False, None, True), (True, None, False), (None, None, True)],
)
def test_remote_tri_state(db_session, required, posting_remote, admitted):
    _posting(db_session, remote=posting_remote)
    pref = _pref(target_functions="|backend|", target_locations="|tw|", remote_required=required)
    assert count_matches(db_session, pref) == (1 if admitted else 0)


def test_location_matches_the_whole_country_code_not_a_substring(db_session):
    _posting(db_session, country="TW")
    _posting(db_session, country="TWX")
    assert count_matches(db_session, _pref(target_functions="|backend|",
                                           target_locations="|tw|")) == 1


def test_function_matches_the_whole_token_not_a_substring(db_session):
    _posting(db_session, function="html")
    assert count_matches(db_session, _pref(target_functions="|ml|")) == 0


def test_region_and_city_also_satisfy_a_location(db_session):
    _posting(db_session, country="US", region="emea", city="Berlin")
    assert count_matches(db_session, _pref(target_locations="|berlin|")) == 1
    assert count_matches(db_session, _pref(target_locations="|emea|")) == 1


def test_locations_are_inert_when_remote_is_required(db_session):
    _posting(db_session, country="US", remote=True)
    _posting(db_session, country="TW", remote=True)
    assert count_matches(db_session, _pref(target_functions="|backend|",
                                           target_locations="|tw|",
                                           remote_required=True)) == 2


def test_locations_still_apply_for_on_site_only(db_session):
    _posting(db_session, country="US", remote=None)
    _posting(db_session, country="TW", remote=None)
    assert count_matches(db_session, _pref(target_locations="|tw|",
                                           remote_required=False)) == 1


def test_like_wildcards_in_a_token_are_literal(db_session):
    _posting(db_session, country="US", region="north-america")
    assert count_matches(db_session, _pref(target_locations="|north_america|")) == 0


def test_whitespace_only_seniority_is_no_constraint(db_session):
    _posting(db_session)
    assert count_matches(db_session, _pref(target_seniority="  ",
                                           target_functions="|backend|")) == 1


def test_ladder_rungs_are_all_seniority_rules():
    from app.services.jobs.enrich import default_taxonomy, valid_seniorities
    assert set(SENIORITY_LADDER) <= valid_seniorities(default_taxonomy())


def test_a_preference_that_constrains_nothing_matches_nothing(db_session):
    for _ in range(3):
        _posting(db_session)
    empty = _pref()
    assert count_matches(db_session, empty) == 0
    assert count_matches(db_session, _pref(target_functions="|backend|")) == 3


def test_prefilter_query_itself_is_empty_for_a_preference_that_constrains_nothing(
    db_session,
):
    """The guard lives in the query, not only in count_matches: a later slice
    reads prefilter_query directly for digest candidates and must not be handed
    the whole catalogue for someone who has told us nothing."""
    for _ in range(3):
        _posting(db_session)
    assert prefilter_query(db_session, _pref()).all() == []
    assert len(prefilter_query(db_session, _pref(target_functions="|backend|")).all()) == 3


def test_a_seeded_manager_target_is_not_a_dead_end(db_session):
    """End to end over the four places that conspired in the original defect:
    seeding writes what seniority_of returns, the schema accepts it, and the
    prefilter must then reach the postings carrying it."""
    from app.services.jobs.preferences import seed_from_facts

    for title in ("Engineering Manager", "Director of Engineering", "產品經理"):
        seed = seed_from_facts(_facts_row(roles=[{"title": title}]))
        assert seed["target_seniority"] == "manager", title
        JobPreferenceIn(target_seniority=seed["target_seniority"])  # not a 422

    _posting(db_session, seniority="manager", function="backend")
    assert count_matches(db_session, _pref(target_seniority="manager",
                                           target_functions="|backend|")) == 1


def test_expand_functions_is_asymmetric_by_design():
    # A specialist gets their own field plus the unspecialised postings --
    # not a frontend job because they picked backend.
    assert expand_functions(["backend"]) == ["backend", "software"]
    assert expand_functions(["backend", "ml"]) == ["backend", "ml", "software"]
    # The generic target names no specialisation, so it means the family.
    assert expand_functions(["software"]) == sorted(_SOFTWARE_FAMILY)
    # Outside the family, exact: a generic software posting does not suit a
    # product manager.
    assert expand_functions(["product"]) == ["product"]
    assert expand_functions(["hardware", "design"]) == ["design", "hardware"]
    assert expand_functions(["other"]) == ["other"]
    assert expand_functions([]) == []


def test_a_specialist_target_reaches_generic_software_postings(db_session):
    # The whole point of the fallback tier: 899 live postings titled
    # "Software Engineer" with no specialisation, which a backend engineer
    # would otherwise never see.
    generic = _posting(db_session, function="software")
    backend = _posting(db_session, function="backend")
    _posting(db_session, function="frontend")
    _posting(db_session, function="product")
    pref = _pref(target_functions="|backend|")
    assert {j.id for j in prefilter_query(db_session, pref).all()} == {
        generic.id,
        backend.id,
    }
    assert count_matches(db_session, pref) == 2


def test_the_generic_target_reaches_the_whole_family_and_nothing_outside_it(db_session):
    family = {f: _posting(db_session, function=f) for f in sorted(_SOFTWARE_FAMILY)}
    for outside in ("product", "design", "hardware", "other"):
        _posting(db_session, function=outside)
    pref = _pref(target_functions="|software|")
    assert {j.id for j in prefilter_query(db_session, pref).all()} == {
        job.id for job in family.values()
    }


def test_a_non_family_target_stays_exact(db_session):
    product = _posting(db_session, function="product")
    _posting(db_session, function="software")
    _posting(db_session, function="backend")
    pref = _pref(target_functions="|product|")
    assert {j.id for j in prefilter_query(db_session, pref).all()} == {product.id}


def test_every_family_member_is_a_real_function():
    from app.services.jobs.enrich import valid_functions

    assert _SOFTWARE_FAMILY <= valid_functions(default_taxonomy())
