"""The source list exists in three places, and they must agree.

`app.models.JOB_SOURCES` had sat at four values while two providers
(smartrecruiters and workday) were live, because nothing compared it to
anything. These tests are that comparison.
"""

import re
from pathlib import Path

from app.models import JOB_SOURCES as MODEL_SOURCES, Job
from app.services.jobs.boards import load_boards, load_workday_boards
from app.services.jobs.types import JOB_SOURCES as TYPE_SOURCES

BACKEND = Path(__file__).resolve().parent.parent


def _check_constraint_sql() -> str:
    for constraint in Job.__table__.constraints:
        if getattr(constraint, "name", None) == "job_source_values":
            return str(constraint.sqltext)
    raise AssertionError("job_source_values constraint is gone")


def test_the_two_source_tuples_agree():
    assert MODEL_SOURCES == TYPE_SOURCES


def test_the_check_constraint_lists_exactly_the_known_sources():
    sql = _check_constraint_sql()
    assert set(re.findall(r"'([a-z]+)'", sql)) == set(MODEL_SOURCES)


def test_taiwanjobs_stays_listed_even_though_nothing_ingests_it():
    """Historical rows still carry it; narrowing either the tuple or the
    constraint would fail against them."""
    assert "taiwanjobs" in MODEL_SOURCES
    assert "taiwanjobs" in _check_constraint_sql()


def test_every_configured_board_uses_a_known_source():
    """load_boards silently drops a row whose source is unknown, so a typo in
    ats_boards.json would otherwise just make an employer quietly vanish."""
    configured = {board.source for board in load_boards()}
    assert configured <= set(TYPE_SOURCES)
    # The retired source must not come back via the config file.
    assert "taiwanjobs" not in configured


def test_the_latest_migration_widens_the_constraint_to_match():
    """The models are the schema the tests build, but production is migrated.
    A source added to the tuple without a revision would pass every other test
    here and then violate the check constraint on the first live upsert.
    """
    revisions = sorted((BACKEND / "alembic" / "versions").glob("*.py"))
    widening = [
        path.read_text(encoding="utf-8")
        for path in revisions
        if "job_source_values" in path.read_text(encoding="utf-8")
    ]
    assert widening, "no revision touches job_source_values"
    newest = widening[-1]
    for source in MODEL_SOURCES:
        assert f"'{source}'" in newest, f"{source} is in the tuple but not in the migration"


def test_synology_is_configured_with_a_location_slug_not_a_company_slug():
    """The one row in the catalogue where `slug` is not an employer handle.
    If this is ever 'synology', the fetcher will ask the API for a location
    that does not exist and the board will read as missing.
    """
    rows = [board for board in load_boards() if board.source == "synology"]
    assert [board.slug for board in rows] == ["HQ"]
    assert rows[0].company == "Synology"


def test_workday_boards_are_not_in_the_slug_based_catalogue():
    """They need host/tenant/site, so they live in their own file; a workday
    row in ats_boards.json would be dropped on the floor by _fetch."""
    assert "workday" not in {board.source for board in load_boards()}
    assert load_workday_boards()
