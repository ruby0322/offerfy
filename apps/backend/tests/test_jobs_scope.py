import json

from app.services.jobs.scope import (
    default_scope,
    function_in_scope,
    load_scope,
    scope_path,
    title_in_scope,
)
from app.services.jobs.types import JOB_SOURCES


def test_plainly_technical_titles_are_in_scope():
    for title in [
        "Senior Software Engineer",
        "Staff Backend Engineer, Payments",
        "Machine Learning Researcher",
        "Site Reliability Engineer",
        "Compiler Engineer",
        "資深後端工程師",
    ]:
        assert title_in_scope(title), title


def test_plainly_non_technical_titles_are_out_of_scope():
    for title in [
        "Enterprise Account Executive",
        "Senior Payroll Analyst",
        "Growth Marketing Manager",
        "Talent Acquisition Partner",
        "Assistant General Counsel, Canada",
        "Customer Success Manager",
        "Room Attendant",
        "送貨人員",
    ]:
        assert not title_in_scope(title), title


def test_a_keep_needle_outranks_a_deny_needle():
    """The whole reason the rule is two-sided.

    These titles all contain a deny needle and are all roles this audience
    actually applies to. A bare deny-list would quietly eat every one.
    """
    for title in [
        "Compliance Engineer",
        "Engineering Manager, Billing",
        "Software Engineer, Financial Business Insights",
        "Senior Solutions Architect (Enterprise Data Warehouse)",
        "Senior Salesforce Engineer",
        "Backend Engineer, Legal Tech",
    ]:
        assert title_in_scope(title), title


def test_ambiguous_keep_needles_stay_out_of_the_config():
    """Guards the three substring accidents found while tuning on real titles.

    "cto" is inside Dire*cto*r, "ux" is inside L*ux*e, and "development"
    rescues every Sales Development Representative. Re-adding any of them
    silently refills the catalogue with sales roles.
    """
    rules = default_scope()
    for needle in ("cto", "ux", "development", "technical", "technology", "data"):
        assert needle not in rules.keep_title, needle

    assert not title_in_scope("Director, Commercial Sales")
    assert not title_in_scope("Sales Development Representative, Luxe")
    assert not title_in_scope("Business Development Representative")


def test_unknown_titles_are_kept():
    """A false negative loses a good job silently; a false positive is visible."""
    assert title_in_scope("Quantum Widget Wrangler")
    assert title_in_scope("")


def test_function_scope_uses_the_structured_taxonomy():
    assert function_in_scope("engineering")
    assert function_in_scope("Engineering")
    assert not function_in_scope("sales")
    assert not function_in_scope("human_resources")
    # Missing taxonomy must not drop the posting -- the title rule still runs.
    assert function_in_scope(None)
    assert function_in_scope("")


def test_real_job_scope_json_is_well_formed():
    """Mirrors test_real_spotlight_json_covers_all_sources: the shipped policy
    file is the one that matters, so assert on it and not only on a fixture."""
    rules = load_scope(scope_path())
    assert rules.keep_title and rules.deny_always and rules.deny_context
    assert rules.keep_function_ids
    for needles in (rules.keep_title, rules.deny_always, rules.deny_context):
        assert all(n == n.casefold() for n in needles)
    # A needle in both tiers would make deny_context dead for that word.
    assert not set(rules.deny_always) & set(rules.deny_context)
    raw = json.loads(scope_path().read_text(encoding="utf-8"))
    assert "smartrecruiters" in JOB_SOURCES
    assert "engineering" in raw["keep_function_ids"]


def test_a_non_technical_role_is_not_rescued_by_its_domain():
    """deny_always exists because of these.

    Every one leaked through a single-tier list: "hardware" and "robotics"
    rescued recruiting jobs, and "engineer" rescued sales ones.
    """
    for title in [
        "Technical Recruiter, Hardware - Air Dominance and Strike",
        "Senior Technical Recruiter, Production Engineering",
        "Mid-Market Sales Engineer",
        "Presales Engineer",
        "Director, Enterprise Sales Engineering - US Central",
        "Sr. AI Systems & Solutions Architect, Customer Success",
    ]:
        assert not title_in_scope(title), title


def test_an_engineering_role_survives_a_non_technical_domain():
    """The counterpart: these are engineers and must not be collateral.

    Each one was a real false drop while the deny tiers were being tuned --
    "recruit" ate a data engineer, "facilities" ate an electrical engineer.
    """
    for title in [
        "Recruiting Analytics Data Engineer",
        "Lead Electrical Engineer, Facilities Engineering",
        "Marketing AI Engineer",
        "Senior Salesforce Engineer",
    ]:
        assert title_in_scope(title), title
