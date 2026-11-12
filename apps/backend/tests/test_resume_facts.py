"""resume_facts is a move, not a change: the talent profile must not shift.

disclosure_events.profile_content_hash cites the exact bytes of what an
employer was shown. If extraction output drifts for a fixed input, the ledger
stops describing what was disclosed -- so the derived columns are pinned here
against a frozen literal, not against a second live implementation.
"""

from app.schemas import TalentEducation, TalentProfilePayload, TalentRole
from app.services import resume_facts


def _fixture_payload() -> TalentProfilePayload:
    return TalentProfilePayload(
        display_name="Mei-Ling Chen",
        headline="Senior Backend Engineer",
        summary="Builds payment systems. Reach me at mei@example.com or +886 912 345 678.",
        years_experience=9,
        locations=["Taipei 11049", "Hsinchu"],
        languages=["Mandarin", "English"],
        skills=["Python", "Go", "PostgreSQL", "Kubernetes"],
        roles=[
            TalentRole(
                employer="Acme Pay",
                title="Staff Engineer",
                start="2021-03",
                end="Present",
                highlights=["Cut p99 latency by 40%", "Led a team of six"],
            ),
            TalentRole(
                employer="Widget Co",
                title="Engineer",
                start="2016-07",
                end="2021-02",
                highlights=["Built the billing service"],
            ),
        ],
        education=[
            TalentEducation(
                institution="National Taiwan University",
                credential="BSc",
                field="Computer Science",
                end="2016-06",
            )
        ],
    )


def _payload(**kw) -> TalentProfilePayload:
    return TalentProfilePayload(**kw)


EXPECTED_DERIVED = {'display_name': 'Mei-Ling Chen',
 'headline': 'Senior Backend Engineer',
 'years_experience': 9,
 'search_text': 'mei-ling chen senior backend engineer builds payment systems. reach '
                'me at mei@example.com or +886 912 345 678. taipei 11049 hsinchu '
                'mandarin english python go postgresql kubernetes staff engineer acme '
                'pay engineer widget co national taiwan university bsc computer '
                'science',
 'skills_text': '|python|go|postgresql|kubernetes|'}


def _over_cap_payload() -> TalentProfilePayload:
    """Every cap exceeded by one or more, so clamp_payload has to slice."""
    return TalentProfilePayload(
        schema_version=99,
        display_name="  " + "N" * 300 + "  ",
        headline="H" * 130,
        summary="S" * 650,
        years_experience=500,
        locations=[f"City{i}" for i in range(6)],
        languages=[f"Lang{i}" for i in range(8)],
        skills=[f"skill{i}" for i in range(45)],
        roles=[
            TalentRole(
                employer=f"Emp{i}",
                title=f"Title{i}",
                highlights=[f"h{i}-{j}" for j in range(6)],
            )
            for i in range(14)
        ],
        education=[TalentEducation(institution=f"School{i}") for i in range(8)],
    )


def _sparse_payload() -> TalentProfilePayload:
    """A role and a school each missing the parts derived_columns filters out."""
    return TalentProfilePayload(
        display_name="Sparse Person",
        roles=[
            TalentRole(employer="OnlyEmployer"),
            TalentRole(title="OnlyTitle"),
            TalentRole(),
        ],
        education=[
            TalentEducation(institution="OnlySchool"),
            TalentEducation(credential="OnlyCredential", field="OnlyField"),
            TalentEducation(),
        ],
    )


EXPECTED_SCRUBBED = {
    "schema_version": 1,
    "display_name": "Mei-Ling Chen",
    "headline": "Senior Backend Engineer",
    "summary": "Builds payment systems. Reach me at or .",
    "years_experience": 9,
    "locations": ["Taipei", "Hsinchu"],
    "languages": ["Mandarin", "English"],
    "skills": ["Python", "Go", "PostgreSQL", "Kubernetes"],
    "roles": [
        {
            "employer": "Acme Pay",
            "title": "Staff Engineer",
            "start": "2021-03",
            "end": "Present",
            "highlights": ["Cut p99 latency by 40%", "Led a team of six"],
        },
        {
            "employer": "Widget Co",
            "title": "Engineer",
            "start": "2016-07",
            "end": "2021-02",
            "highlights": ["Built the billing service"],
        },
    ],
    "education": [
        {
            "institution": "National Taiwan University",
            "credential": "BSc",
            "field": "Computer Science",
            "end": "2016-06",
        }
    ],
}

# Written from the pre-move clamp_payload run on _over_cap_payload(). Each cap is
# spelled out (255/120/600 chars, 60 years, 4/6/40/12/6 items, 4 highlights) so
# a loosened cap shows up as a diff in this literal.
EXPECTED_CLAMPED = {
    "schema_version": 1,
    "display_name": "N" * 255,
    "headline": "H" * 120,
    "summary": "S" * 600,
    "years_experience": 60,
    "locations": ["City0", "City1", "City2", "City3"],
    "languages": ["Lang0", "Lang1", "Lang2", "Lang3", "Lang4", "Lang5"],
    "skills": [f"skill{i}" for i in range(40)],
    "roles": [
        {
            "employer": f"Emp{i}",
            "title": f"Title{i}",
            "start": None,
            "end": None,
            "highlights": [f"h{i}-0", f"h{i}-1", f"h{i}-2", f"h{i}-3"],
        }
        for i in range(12)
    ],
    "education": [
        {"institution": f"School{i}", "credential": None, "field": None, "end": None}
        for i in range(6)
    ],
}

# derived_columns branches the full fixture does not reach: a role/school with
# a None part (the filter(None, ...) path), and no skills at all.
EXPECTED_DERIVED_SPARSE = {
    "display_name": "Sparse Person",
    "headline": None,
    "years_experience": None,
    "search_text": "sparse person onlyemployer onlytitle onlyschool onlycredential onlyfield",
    "skills_text": "",
}
EXPECTED_DERIVED_EMPTY = {
    "display_name": None,
    "headline": None,
    "years_experience": None,
    "search_text": "",
    "skills_text": "",
}

def test_talent_profile_bytes_are_unchanged_by_the_extraction_move():
    """The disclosure ledger cites profile_json's bytes; they must not move.

    Every assertion is against a frozen literal. profile re-exports these
    functions, so comparing resume_facts to profile would compare an object to
    itself and pass for any implementation.
    """
    assert resume_facts.scrub_contacts(_fixture_payload()).model_dump() == EXPECTED_SCRUBBED
    assert resume_facts.clamp_payload(_over_cap_payload()).model_dump() == EXPECTED_CLAMPED
    assert resume_facts.derived_columns(_fixture_payload()) == EXPECTED_DERIVED


def test_derived_columns_filters_missing_parts_and_empty_skills():
    assert resume_facts.derived_columns(_sparse_payload()) == EXPECTED_DERIVED_SPARSE
    assert resume_facts.derived_columns(_payload()) == EXPECTED_DERIVED_EMPTY


def test_derived_columns_pipe_delimits_skills():
    d = resume_facts.derived_columns(_payload(skills=["Python", "Go"]))
    assert d["skills_text"] == "|python|go|"
