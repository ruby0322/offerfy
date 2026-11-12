"""resume_facts is a move, not a change: the talent profile must not shift.

disclosure_events.profile_content_hash cites the exact bytes of what an
employer was shown. If extraction output drifts for a fixed input, the ledger
stops describing what was disclosed -- so the derived columns are pinned here
against a frozen literal, not against a second live implementation.
"""

from app.schemas import TalentEducation, TalentProfilePayload, TalentRole
from app.services import profile, resume_facts


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


def test_talent_profile_bytes_are_unchanged_by_the_extraction_move():
    """The disclosure ledger cites profile_json's bytes; they must not move."""
    assert resume_facts.clamp_payload(_fixture_payload()) == profile.clamp_payload(
        _fixture_payload()
    )
    assert resume_facts.scrub_contacts(_fixture_payload()) == profile.scrub_contacts(
        _fixture_payload()
    )
    assert resume_facts.derived_columns(_fixture_payload()) == EXPECTED_DERIVED


def test_derived_columns_pipe_delimits_skills():
    d = resume_facts.derived_columns(_payload(skills=["Python", "Go"]))
    assert d["skills_text"] == "|python|go|"
