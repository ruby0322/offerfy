from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.models import JOB_SOURCES, Job
from app.services.jobs.spotlight import (
    load_spotlight_config,
    rescore_all,
    score_job,
    select_featured,
    spotlight_path,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "jobs" / "spotlight.json"
NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def _job(**kwargs) -> Job:
    posted = kwargs.get("posted_at", NOW)
    return Job(
        id=kwargs["id"],
        source=kwargs.get("source", "greenhouse"),
        source_id=kwargs["id"],
        company=kwargs.get("company", "Acme"),
        title=kwargs["title"],
        location="Remote",
        remote=kwargs.get("remote", True),
        apply_url="https://example.com/apply",
        source_url="https://example.com/job",
        posted_at=posted,
        first_seen_at=NOW,
        last_seen_at=kwargs.get("last_seen_at", NOW),
        is_active=True,
        spotlight_score=kwargs.get("spotlight_score", 0.0),
    )


def test_real_spotlight_json_covers_all_sources():
    config = load_spotlight_config(spotlight_path())
    for source in JOB_SOURCES:
        assert source in config.source_weights


def test_score_job_prefers_engineer_over_default_title():
    config = load_spotlight_config(FIXTURE)
    engineer = score_job(
        title="後端工程師",
        source="taiwanjobs",
        remote=False,
        posted_at=NOW,
        last_seen_at=NOW,
        now=NOW,
        config=config,
    )
    waiter = score_job(
        title="餐廳服務員",
        source="taiwanjobs",
        remote=False,
        posted_at=NOW,
        last_seen_at=NOW,
        now=NOW,
        config=config,
    )
    assert engineer > waiter
    assert engineer == 2.0 * 0.5  # role 2.0 * taiwanjobs 0.5 * recency 1 * no remote boost


def test_score_job_recency_half_life():
    config = load_spotlight_config(FIXTURE)
    fresh = score_job(
        title="Engineer",
        source="greenhouse",
        remote=False,
        posted_at=NOW,
        last_seen_at=NOW,
        now=NOW,
        config=config,
    )
    old = score_job(
        title="Engineer",
        source="greenhouse",
        remote=False,
        posted_at=NOW - timedelta(days=14),
        last_seen_at=NOW,
        now=NOW,
        config=config,
    )
    assert fresh == 2.0
    assert old == 1.0


def test_select_featured_caps_company_and_alternates_sources(db_session, client):
    config = load_spotlight_config(FIXTURE)
    rows = [
        _job(id="s1", company="Stripe", title="Engineer", source="greenhouse", spotlight_score=9),
        _job(id="s2", company="Stripe", title="Engineer II", source="greenhouse", spotlight_score=8),
        _job(id="o1", company="OpenAI", title="Engineer", source="ashby", spotlight_score=7),
        _job(id="t1", company="範例", title="服務員", source="taiwanjobs", spotlight_score=6),
        _job(id="l1", company="Spotify", title="Analyst", source="lever", spotlight_score=5),
    ]
    for row in rows:
        db_session.add(row)
    db_session.commit()
    picked = select_featured(db_session, config)
    companies = [job.company for job in picked]
    sources = [job.source for job in picked]
    assert companies.count("Stripe") == 1
    assert len(picked) == 4
    assert len(set(sources)) >= 2
    for left, right in zip(sources, sources[1:]):
        assert left != right


def test_rescore_all_writes_spotlight_score(db_session, client):
    config = load_spotlight_config(FIXTURE)
    job = _job(id="eng-1", title="Engineer", source="greenhouse", remote=False)
    db_session.add(job)
    db_session.commit()
    n = rescore_all(db_session, config=config, now=NOW)
    db_session.refresh(job)
    assert n == 1
    assert job.spotlight_score == 2.0
