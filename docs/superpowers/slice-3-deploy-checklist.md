# Slice 3 deploy — waiting on one copy commit

Written 2026-11-13. Slice 3 (applications and the dashboard) is merged to local
`main` at `979b4f0` and **not pushed**. Production is untouched.

## Why this is waiting

You chose "fix the copy first, then one deploy". Six strings say Offerfy does no
matching and no apply tracking, which slice 3 makes false. `/llms.txt` is
written for agents that take it literally, and two of the six are indexed page
descriptions.

| String | Owner | Problem |
|---|---|---|
| `lib/agent-markdown.ts:105` | the landing session | "Do not tell users Offerfy matches or ranks resumes against jobs… or applies on their behalf" |
| `lib/agent-markdown.ts:145` | the landing session | "Offerfy does not match or rank jobs for you" |
| `lib/agent-markdown.ts:155` | the landing session | "job matching and auto-apply are not [shipping]" |
| `about.metaDescription` ×3 locales | the landing session | "No matching, no auto-apply" |
| `blog.metaDescription` ×3 locales | pre-existing | "job matching and auto-apply are not [shipping]" |
| `jobs.metaDescription` ×3 locales | **slice 3's own** | "No matching, **no apply tracking**" — on the page that now starts the tracking |

The home description (`meta.description`) is clean. "No matching" has
contradicted a settings toggle literally named "Open to matching" since that
toggle shipped; what changes here is the blast radius.

The honest distinction, if it helps the rewrite: postings are filtered against
preferences you set, with a count and **no scoring or ranking**
(`match_sweep.py`'s own docstring: "hard filters only, no LLM, no scoring");
and separately, opt-in and off by default, "Open to matching" may show
employers a profile built from your published resume. One word was doing both
jobs.

**Correction, 2026-11-13: thirteen strings, not eleven.** `agent-markdown.ts`
has a fourth stale line at **:150** — "Matching, ranking, and apply tracking are
not included" — missed in the first two counts. So: 9 locale strings (three
keys × three locales) plus 4 lines in `agent-markdown.ts`. The landing session
has committed all thirteen as `52d9f17` on `claude/truthful-marketing-copy`,
based on `8973d2d`, **not pushed**, awaiting the user's own instruction. I
verified the commit exists, is a descendant of `8973d2d`, and has no remote ref.
It took `jobs.metaDescription` too, so nothing in that set is left to me.

## Open item for the user: the Terms of Service paragraph

**Not fixed, not assigned, and it does not block the deploy — but it should not
quietly survive it.** `legal.terms.sections[1].paragraphs[3]`, all three
locales. Flagged by the landing session; I verified every word of it.

It ends: *"We still do not rank or recommend postings for you, submit
applications, or track them."* The last clause is **now false**, for the same
reason `jobs.metaDescription` was — slice 3 tracks applications, deliberately
and as its whole point. zh-TW 「也不會追蹤你的投遞狀態」 and zh-CN
"也不会跟踪你的投递状态" say the same thing. The rest of the sentence stays
true: nothing ranks, recommends, or submits.

Second, separable question in the same paragraph: the **EN** version says the
editor can *"score how well it fits a job description you paste in"*, while
zh-TW says 評估契合度 and zh-CN 评估契合程度 — "assess fit". EN is the outlier,
and `editor.tailorKickstartPrompt` does literally instruct the model to "score
how well the current resume fits this role". That is a prose read: no number, no
storage, no ranking, nothing persisted. So either Terms should drop the word
"score" to match the Chinese and what the feature actually feels like, or the
prompt should stop using it. That is a call about what the product claims, not a
bug, and it is the user's.

Why neither session touched it: it is Terms of Service, so the risk class is
different from a meta description, and a clause about what Offerfy does with
your applications is exactly the kind of sentence that should not be edited by
whoever happens to notice it. The landing session left it in a commit message as
a flag; this is the same flag, recorded where the deploy will be read.

## The deploy, once that commit lands

Nothing below has been done yet.

1. **Say the downtime window out loud first.** The web restart is ~5-40s on the
   live site. The API reload is zero-downtime but takes ~20s to cycle workers,
   and a route probe inside that window returns the OLD code — a lone 404 is
   not evidence the reload failed.

2. **Migration.** `032_applications` has never been applied to any production
   database. It was verified up *and* down against a disposable container, with
   the FK delete rules, unique constraint and index read back out of
   `pg_constraint`. One new table; nothing existing is altered, so the rollback
   is `alembic downgrade -1` and no data is at risk.

3. **Build from the clean worktree, never from this checkout.**
   `/home/ruby0322/offerfy-verify-slice3` is a detached worktree with
   `node_modules` hardlinked (`cp -al`; a symlink fails — Turbopack rejects it
   with "points out of the filesystem root"). `npm run build:public` in
   `/home/ruby0322/offerfy` would replace the `.next` the live site serves, and
   it compiles the *working tree*, not HEAD — which is how another session's
   unreviewed landing rewrite nearly went live earlier today.

4. **Reload the API by the listener, not the worker count.** `kill -HUP` on
   `offerfy-api`'s MainPID (system scope, no `--user`, no sudo). Judge the
   reload by `ss -ltnpe | grep 8001` naming workers whose `lstart` is after the
   merge, then probe: a new route answering 401 is correct, 404 is not.

5. **Restart the web unit by PID from `ss -ltnp`.** `next start` re-execs into a
   child whose entire command line is `next-server (vX.Y.Z)` — no port, no
   flags — so a `/proc/*/cmdline` grep finds nothing and the kill silently
   no-ops. Never `pgrep -f`/`pkill -f`: the caller's own line self-matches.

6. **Smoke per AGENTS.md §5**, then report the new BUILD_ID and MainPID to the
   landing session, which has been doing the same in reverse.

## Disk, before you add an eleventh backup

Ten `.next.bak-*` directories under `apps/frontend`, ~4.5G, on a volume at 78%
(118G free). AGENTS.md §3 says to delete old ones once a deploy sticks. They are
rollback paths, so I have not touched any. The current live build is
`fxq-gtX2MspoHjFRT-qGm`; the two most recent backups are worth keeping and the
rest are from 2026-11-05 to 11-09.

## What shipped in slice 3

`applications` table and migration · the status machine that solely owns
`Application.status` · `match_sweep._exclusions` filled, so "N new matches"
means not-yet-acted-on · resume health · the application endpoints · `job_id` on
tailor creation · `GET /v1/dashboard` as one aggregate (measured at 10 SQL
statements, flat as applications grow) · the frontend client layer with Save and
*Tailor for this role* on the job page · the dashboard rebuilt in loop order.

Known limitations, deliberate: no notes editor though `PATCH` accepts notes; no
reopen for a `closed` row though the service permits it; a posting someone
deliberately skipped keeps counting, because skipping is not recorded (the
spec's amendment names and accepts this cost).
