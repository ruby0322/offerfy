-- First-run funnel for new users.
--
-- Baseline for docs/superpowers/plans/2026-10-19-new-user-onboarding.md.
-- Read-only: every query here runs against tables that already exist. Nothing
-- in this file needs a migration, and nothing in it should ever write.
--
-- Run:  psql "$DATABASE_URL" -f apps/backend/scripts/funnel/first_run.sql
--
-- Three things this file CANNOT tell you. Do not read a missing number as a zero:
--
--   1. Landing abandonment. No row exists until a resume is created, so the
--      drop-off between `/` and `/create` or `/upload` is invisible here.
--      This is the biggest blind spot.
--   2. ATS pass rate. GET /v1/resumes/{id}/ats recompiles and returns on the
--      fly and persists nothing (app/routers/resumes.py), so "did the user
--      reach a passing resume" is not answerable from the database.
--   3. Exports by signed-in users. rate_events is written only for guests, and
--      only when app_env == 'production' (app/services/rate_limit.py). Export
--      counts are a floor, not a count.
--
-- Every rate is split by resumes.source, because `create` and `upload` are
-- different first-run experiences and are expected to move differently.
--
-- BEFORE TRUSTING ANY COHORT NUMBER, run query 0. Some resumes.created_at values
-- were stamped by a backfill rather than by the user, so they all share one
-- timestamp, land in one cohort week, and carry a time-to-first-chat that can be
-- negative. On the dev database as of 2026-10-19 that was 73 of 246 rows (30%),
-- all inside the 2026-08-24 week -- which is also the week with the healthiest
-- looking conversion. Exclude or discount those rows; do not read them as a
-- real cohort that later weeks are failing to live up to.


-- 0. Data-quality gate. Any created_at shared by more than a handful of resumes
--    is a backfill, not user behavior. Check this FIRST and carry the result
--    alongside every number below.
SELECT
    created_at                                              AS suspect_timestamp,
    count(*)                                                AS resumes,
    round(100.0 * count(*) / (SELECT count(*) FROM resumes), 1) AS pct_of_all_resumes
FROM resumes
GROUP BY created_at
HAVING count(*) > 5
ORDER BY resumes DESC;


-- 1. Weekly cohort: how many resumes started, by entry path.
--    The denominator for everything below.
SELECT
    date_trunc('week', created_at) AS cohort_week,
    source,
    count(*)                       AS resumes
FROM resumes
WHERE kind = 'master'
GROUP BY 1, 2
ORDER BY 1 DESC, 2;


-- 2. Upload imports: did the auto-import actually land?
--    'pending' rows that never moved are silent failures.
SELECT
    date_trunc('week', created_at) AS cohort_week,
    import_status,
    count(*)                       AS resumes,
    round(
        100.0 * count(*) / nullif(sum(count(*)) OVER (PARTITION BY date_trunc('week', created_at)), 0),
        1
    )                              AS pct_of_week
FROM resumes
WHERE source = 'upload'
GROUP BY 1, 2
ORDER BY 1 DESC, 2;


-- 3. The core funnel, per cohort week and entry path.
--    Reached chat / edited / tailored / published / claimed.
WITH master AS (
    SELECT
        r.id,
        r.source,
        date_trunc('week', r.created_at) AS cohort_week,
        r.claimed_at,
        r.user_id,
        r.published_revision_id,
        (
            SELECT count(*)
            FROM chat_messages m
            WHERE m.resume_id = r.id AND m.role = 'user'
        ) AS user_turns,
        (
            SELECT count(*)
            FROM resume_revisions v
            WHERE v.resume_id = r.id
        ) AS revisions,
        (
            SELECT count(*)
            FROM resumes t
            WHERE t.parent_resume_id = r.id AND t.kind = 'tailor'
        ) AS tailors
    FROM resumes r
    WHERE r.kind = 'master'
)
SELECT
    cohort_week,
    source,
    count(*)                                                   AS started,
    count(*) FILTER (WHERE user_turns > 0)                     AS talked_to_ai,
    count(*) FILTER (WHERE revisions > 1)                       AS edited_past_first_save,
    count(*) FILTER (WHERE tailors > 0)                         AS reached_tailoring,
    count(*) FILTER (WHERE published_revision_id IS NOT NULL)   AS published,
    count(*) FILTER (WHERE user_id IS NOT NULL)                 AS owned_by_account,
    count(*) FILTER (WHERE claimed_at IS NOT NULL)              AS claimed_from_guest
FROM master
GROUP BY 1, 2
ORDER BY 1 DESC, 2;


-- 4. The same funnel as conversion percentages, which is what actually gets
--    compared before and after a change.
WITH master AS (
    SELECT
        r.id,
        r.source,
        date_trunc('week', r.created_at) AS cohort_week,
        r.claimed_at,
        r.published_revision_id,
        (
            SELECT count(*)
            FROM chat_messages m
            WHERE m.resume_id = r.id AND m.role = 'user'
        ) AS user_turns,
        (
            SELECT count(*)
            FROM resume_revisions v
            WHERE v.resume_id = r.id
        ) AS revisions
    FROM resumes r
    WHERE r.kind = 'master'
)
SELECT
    cohort_week,
    source,
    count(*) AS started,
    round(100.0 * count(*) FILTER (WHERE user_turns > 0)   / nullif(count(*), 0), 1) AS pct_talked_to_ai,
    round(100.0 * count(*) FILTER (WHERE revisions > 1)     / nullif(count(*), 0), 1) AS pct_edited,
    round(100.0 * count(*) FILTER (WHERE claimed_at IS NOT NULL) / nullif(count(*), 0), 1) AS pct_claimed,
    round(100.0 * count(*) FILTER (WHERE published_revision_id IS NOT NULL) / nullif(count(*), 0), 1) AS pct_published
FROM master
GROUP BY 1, 2
ORDER BY 1 DESC, 2;


-- 5. Edit depth distribution. A pile of resumes stuck at 1 revision means
--    people arrive, look, and leave without changing anything.
WITH master AS (
    SELECT
        r.source,
        (SELECT count(*) FROM resume_revisions v WHERE v.resume_id = r.id) AS revisions
    FROM resumes r
    WHERE r.kind = 'master'
)
SELECT
    source,
    CASE
        WHEN revisions = 0 THEN '0'
        WHEN revisions = 1 THEN '1'
        WHEN revisions BETWEEN 2 AND 5 THEN '2-5'
        WHEN revisions BETWEEN 6 AND 20 THEN '6-20'
        ELSE '21+'
    END       AS revision_bucket,
    count(*)  AS resumes
FROM master
GROUP BY 1, 2
ORDER BY 1, 2;


-- 6. Time from resume created to its first user chat turn. A long tail here
--    means people are hunting for how to start.
SELECT
    r.source,
    count(*) AS resumes_that_chatted,
    round(percentile_cont(0.5) WITHIN GROUP (
        ORDER BY extract(epoch FROM first_turn.at - r.created_at)
    )::numeric, 1) AS median_seconds_to_first_turn,
    round(percentile_cont(0.9) WITHIN GROUP (
        ORDER BY extract(epoch FROM first_turn.at - r.created_at)
    )::numeric, 1) AS p90_seconds_to_first_turn
FROM resumes r
JOIN LATERAL (
    SELECT min(m.created_at) AS at
    FROM chat_messages m
    WHERE m.resume_id = r.id AND m.role = 'user'
) AS first_turn ON first_turn.at IS NOT NULL
WHERE r.kind = 'master'
  -- A first chat turn that predates its own resume means created_at was
  -- backfilled (see query 0). Those rows cannot answer this question.
  AND first_turn.at >= r.created_at
GROUP BY 1
ORDER BY 1;


-- 7. Guest vs account split overall, and how much guest work is sitting
--    on a cookie with no account behind it.
SELECT
    count(*) FILTER (WHERE user_id IS NOT NULL)          AS owned_by_account,
    count(*) FILTER (WHERE guest_session_id IS NOT NULL) AS owned_by_guest,
    count(*) FILTER (WHERE claimed_at IS NOT NULL)       AS ever_claimed
FROM resumes;


-- 8. Export events. Guest-only and production-only (see caveat 3 above),
--    so treat this as a floor.
SELECT
    date_trunc('week', created_at) AS week,
    kind,
    count(*)                       AS events
FROM rate_events
GROUP BY 1, 2
ORDER BY 1 DESC, 2;


-- Baseline captured 2026-10-19, DEV DATABASE, before any onboarding change
-- shipped. 246 resumes, 3 users. Treat the shape as indicative and the absolute
-- numbers as untrustworthy: this database mixes real use with test traffic (one
-- week alone holds 110 `create` resumes at a 0.9% chat rate, which is automation,
-- not people) and 30% of rows have a backfilled created_at (query 0). Re-capture
-- against production before quoting any of this as a product metric.
--
--   resumes by entry path        create: 205      upload: 39
--   pct ever used chat           create: 3.9%     upload: 51.3%
--   pct edited past first save   create: 0.5%     upload: 2.6%
--   median sec to first chat     create: 353.3    upload: 3.4
--   p90 sec to first chat        create: 872.2    upload: 9.8
--   revisions stuck at exactly 1 create: 203/205  upload: 38/39
--   owned by account 6 · owned by guest 240 · ever claimed 3
--
-- What this says, and it is the whole argument for the onboarding work: the two
-- entry paths are not close. An `upload` user is chatting 3 seconds in, because
-- the editor auto-imports their file. A `create` user takes just under SIX
-- MINUTES to say anything, and 99.5% of them never change a line -- they land on
-- the starter template's Typst markup and stop. The gap is the cold start, not
-- the chat.
--
-- Re-run after the onboarding changes have had a few days of real traffic and
-- compare against these numbers. Report the three blind spots alongside, and say
-- plainly that landing-page abandonment is not in here.
