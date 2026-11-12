"""The deterministic prefilter: SQL that reduces the catalogue to plausible matches."""

import itertools
from datetime import timedelta

import pytest

from app.jobs.match_sweep import SENIORITY_LADDER, count_matches
from app.models import Job, JobFacets, JobPreference
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


def test_an_unrecognised_target_seniority_matches_nothing(db_session):
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
