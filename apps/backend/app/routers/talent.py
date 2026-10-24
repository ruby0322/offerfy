"""The gated read API: profiles, for credentialed readers only.

include_in_schema=False is load-bearing, not tidiness. create_app() passes no
openapi_url, and the Next rewrite publishes /api/openapi.json, /api/docs and
/api/redoc -- so a route that appears in that document is a route described on
the open web whatever it demands to call. Nothing new goes on the open web.

Three independent gates stand between a request and a person's profile, and
any one of them alone is enough:

  1. Existence  -- the row is only written under consent, deleted on
     withdrawal, cascaded when the source resume goes, and dropped when the
     owner points somewhere else.
  2. Live consent -- services.profile.readable_profiles filters the fetched
     page through services.consent on every request.
  3. Live source -- readable_profiles_query requires the owner's pointer to
     still name this resume and the resume to still have a published revision.

A consequence to live with: `total` is counted before gate 2, so it is
advisory and a page can come back shorter than `limit`. A short page is
cosmetic. A page with a withdrawn person in it is a breach.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_talent_caller
from app.models import DisclosureEvent, TalentApiKey, TalentProfile
from app.schemas import TalentProfileDetail, TalentProfileSummaryList
from app.services.disclosure import record_disclosure, record_disclosures
from app.services.profile import (
    detail_of,
    readable_profiles,
    readable_profiles_query,
    summary_of,
)

router = APIRouter(
    prefix="/v1/talent",
    include_in_schema=False,
    dependencies=[Depends(require_talent_caller)],
)

# Per credential, per hour, counted on the disclosure rows this request is
# about to write. No new table, no borrowed one: rate_limit.RateEvent requires
# a guest session and no-ops outside production, neither of which fits.
DISCLOSURE_LIMIT_PER_HOUR = 500


def _like_term(raw: str) -> str:
    escaped = raw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _not_found() -> HTTPException:
    """404, never 403.

    A 403 on a withdrawn person confirms that the person exists and once
    consented, which is exactly what we are not willing to say. The share
    token endpoints already settled this question for this codebase.
    """
    return HTTPException(status_code=404, detail="Not found")


def _check_budget(db: Session, key: TalentApiKey, about_to_write: int) -> None:
    """Refuse the page rather than truncate it.

    A silently shortened page hides people the reader asked for and gives them
    no way to know. Checked before serialising anything, so a refusal writes
    nothing at all.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    spent = (
        db.query(func.count(DisclosureEvent.id))
        .filter(
            DisclosureEvent.api_key_id == key.id,
            DisclosureEvent.created_at >= since,
        )
        .scalar()
        or 0
    )
    if spent + about_to_write > DISCLOSURE_LIMIT_PER_HOUR:
        raise HTTPException(status_code=429, detail="Too many requests")


@router.get("/profiles", response_model=TalentProfileSummaryList)
def list_profiles(
    q: str | None = None,
    skill: list[str] = Query(default_factory=list),
    min_years: int | None = None,
    location: str | None = None,
    limit: int | None = None,
    offset: int | None = None,
    db: Session = Depends(get_db),
    key: TalentApiKey = Depends(require_talent_caller),
):
    """Summaries only: enough to decide whom to open.

    The detail is a second deliberate act and a second ledger row, because
    handing over someone's whole history is a bigger disclosure than listing
    them.
    """
    lim = 20 if limit is None else limit
    off = 0 if offset is None else offset
    if lim < 1 or lim > 50 or off < 0:
        raise HTTPException(status_code=422, detail="Invalid pagination")

    query = readable_profiles_query(db)
    if q and q.strip():
        term = _like_term(q.strip().lower())
        if term != "%%":
            query = query.filter(TalentProfile.search_text.ilike(term, escape="\\"))
    for raw in skill:
        token = (raw or "").strip().lower()
        if not token:
            continue
        # Delimited on both sides, so 'java' cannot match 'javascript'.
        query = query.filter(
            TalentProfile.skills_text.ilike(_like_term(f"|{token}|"), escape="\\")
        )
    if min_years is not None:
        query = query.filter(TalentProfile.years_experience >= min_years)
    if location and location.strip():
        query = query.filter(
            TalentProfile.search_text.ilike(
                _like_term(location.strip().lower()), escape="\\"
            )
        )

    total = query.count()
    page = (
        query.order_by(TalentProfile.built_at.desc(), TalentProfile.id.desc())
        .limit(lim)
        .offset(off)
        .all()
    )
    visible = readable_profiles(db, page)
    _check_budget(db, key, len(visible))

    items = [summary_of(profile) for profile in visible]
    record_disclosures(
        db,
        profiles=visible,
        key=key,
        scope="summary",
        route="GET /v1/talent/profiles",
    )
    db.commit()
    return TalentProfileSummaryList(items=items, total=total, limit=lim, offset=off)


@router.get("/profiles/{profile_id}", response_model=TalentProfileDetail)
def get_profile(
    profile_id: str,
    db: Session = Depends(get_db),
    key: TalentApiKey = Depends(require_talent_caller),
):
    profile = (
        readable_profiles_query(db).filter(TalentProfile.id == profile_id).one_or_none()
    )
    if profile is None:
        raise _not_found()
    if not readable_profiles(db, [profile]):
        raise _not_found()
    _check_budget(db, key, 1)

    detail = detail_of(profile)
    record_disclosure(
        db,
        profile=profile,
        key=key,
        scope="full",
        route="GET /v1/talent/profiles/{id}",
    )
    db.commit()
    return detail


