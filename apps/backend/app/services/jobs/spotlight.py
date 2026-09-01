from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import Job

_BACKEND_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class RoleRule:
    weight: float
    needles: tuple[str, ...]


@dataclass(frozen=True)
class SpotlightConfig:
    recency_half_life_days: float
    remote_boost: float
    default_role_weight: float
    source_weights: dict[str, float]
    role_rules: tuple[RoleRule, ...]
    featured_limit: int
    candidate_pool: int
    max_per_company: int
    min_sources: int
    max_consecutive_source: int


def spotlight_path() -> Path:
    return _BACKEND_ROOT / "data" / "spotlight.json"


def load_spotlight_config(path: Path | None = None) -> SpotlightConfig:
    data = json.loads((path or spotlight_path()).read_text(encoding="utf-8"))
    rules = tuple(
        RoleRule(
            weight=float(row["weight"]),
            needles=tuple(str(item).casefold() for item in row.get("any") or ()),
        )
        for row in data.get("role_rules") or ()
    )
    weights = {str(key): float(value) for key, value in (data.get("source_weights") or {}).items()}
    return SpotlightConfig(
        recency_half_life_days=max(float(data.get("recency_half_life_days") or 21), 0.001),
        remote_boost=float(data.get("remote_boost") or 1.0),
        default_role_weight=float(data.get("default_role_weight") or 0.12),
        source_weights=weights,
        role_rules=rules,
        featured_limit=max(int(data.get("featured_limit") or 24), 1),
        candidate_pool=max(int(data.get("candidate_pool") or 400), 1),
        max_per_company=max(int(data.get("max_per_company") or 2), 1),
        min_sources=max(int(data.get("min_sources") or 1), 1),
        max_consecutive_source=max(int(data.get("max_consecutive_source") or 2), 1),
    )


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None or dt.utcoffset() is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _role_weight(title: str, config: SpotlightConfig) -> float:
    folded = title.casefold()
    for rule in config.role_rules:
        if any(needle and needle in folded for needle in rule.needles):
            return rule.weight
    return config.default_role_weight


def score_job(
    *,
    title: str,
    source: str,
    remote: bool | None,
    posted_at: datetime | None,
    last_seen_at: datetime,
    now: datetime,
    config: SpotlightConfig,
) -> float:
    stamp = posted_at or last_seen_at
    age_days = max((_aware(now) - _aware(stamp)).total_seconds() / 86400.0, 0.0)
    recency = 2 ** (-age_days / config.recency_half_life_days)
    source_weight = config.source_weights.get(source, 1.0)
    remote_factor = config.remote_boost if remote else 1.0
    return recency * _role_weight(title, config) * source_weight * remote_factor


def rescore_all(
    db: Session,
    *,
    config: SpotlightConfig | None = None,
    now: datetime | None = None,
) -> int:
    policy = config or load_spotlight_config()
    stamp = now or datetime.now(timezone.utc)
    rows = db.query(Job).all()
    for job in rows:
        job.spotlight_score = score_job(
            title=job.title,
            source=job.source,
            remote=job.remote,
            posted_at=job.posted_at,
            last_seen_at=job.last_seen_at,
            now=stamp,
            config=policy,
        )
    db.commit()
    return len(rows)


def select_featured(db: Session, config: SpotlightConfig | None = None) -> list[Job]:
    policy = config or load_spotlight_config()
    candidates = (
        db.query(Job)
        .filter(Job.is_active.is_(True))
        .order_by(Job.spotlight_score.desc(), Job.id.desc())
        .limit(policy.candidate_pool)
        .all()
    )
    picked: list[Job] = []
    remaining = list(candidates)
    while len(picked) < policy.featured_limit and remaining:
        choice = None
        need_source = len({job.source for job in picked}) < policy.min_sources
        for job in remaining:
            if not _allowed(job, picked, policy):
                continue
            if need_source and job.source in {row.source for row in picked}:
                continue
            choice = job
            break
        if choice is None:
            for job in remaining:
                if _allowed(job, picked, policy):
                    choice = job
                    break
        if choice is None:
            break
        remaining.remove(choice)
        picked.append(choice)
    return picked


def _allowed(job: Job, picked: list[Job], config: SpotlightConfig) -> bool:
    company_count = sum(1 for row in picked if row.company == job.company)
    if company_count >= config.max_per_company:
        return False
    tail = picked[-config.max_consecutive_source :]
    if len(tail) == config.max_consecutive_source and all(row.source == job.source for row in tail):
        return False
    return True
