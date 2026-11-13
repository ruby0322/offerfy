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
has committed all thirteen on `claude/truthful-marketing-copy`, **not pushed**,
awaiting the user's own instruction; it has rebased onto `main` twice as this
file moved, so take the branch tip rather than a sha from here. It took
`jobs.metaDescription` too, so nothing in that set is left to me.

Its first rewrite of `agent-markdown.ts:105` replaced a false denial with a
differently-worded false denial — "ranks or **scores** resumes against jobs",
which the editor's own prompt contradicts. Caught on a second read of the diff
rather than of the sha, and now removed entirely rather than reworded, so the
line is true under either ruling below. Every surviving negative in that file is
scoped to **postings**, never to resumes: that is the test that separates what
the catalog does from what the editor does.

## Resolved 2026-11-13: the fit-read wording, and the stale denials

The user ruled: **"consistent 就好"** — consistency is what matters. Standardised
on the accurate description rather than on "score", because the feature produces
a prose read of fit (what matches, what is missing, an overall impression) with
no number, nothing stored and no posting ranked against another. That is also
the term the Chinese strings already used in prose: 評估契合度 / 评估契合程度.

**Changed, 25 strings across 6 keys.** Every replacement was asserted to match
exactly once or the whole run aborted.

- the word "score": `editor.familyNewTailorHint`, `editor.tailorDialogHint`,
  `editor.tailorKickstartPrompt`, `editor.tailorKickstartContextUnknown` (×3
  locales), plus `legal.terms…paragraphs[3]` and `about.paragraphs[1]` (EN only)
- stale denials, all false since slice 2/3 shipped: `about.paragraphs[1]`'s
  "We do not match your resume to jobs", `contact.paragraphs[2]`'s "we do not
  apply or match on your behalf", `about.paragraphs[3]`'s 自動配對 / 自动匹配
  (zh only — EN has no fourth paragraph), and `legal.terms…paragraphs[3]`'s
  "...submit applications, **or track them**"
- what survived everywhere, because it is true: nothing ranks, recommends or
  submits. Each denial was rewritten to say exactly that and nothing wider.
- `editor.cmdJdLabel` / `cmdJdPrompt` keep 比對 in both Chinese locales: that is
  the *other* sense, comparing a resume against a pasted JD, and is correct.

**zh-TW terminology, same ruling.** It was split almost exactly in half — 18
媒合 against 17 配對 for one concept — while zh-CN was already consistent at 38
匹配. Unified on 媒合, the term `settings.matchingTitle` already used. This also
fixed a real defect rather than only a style one: the same toggle was called
「開放配對」 in settings, usage, pricing, admin and Terms but 「開放媒合」 in
`settings.matchingTitle`, so one UI control had two names.

**A cost the unification carried, and the follow-up.** 媒合 means job-placement
brokering, which is exactly right for the consent-gated employer introduction
and overclaims the preference prefilter — a filter over a public catalog. After
the unification the zh-TW dashboard said 符合的職缺 on the count card and 媒合
on the resume-health card: two words on one screen, with the health card on the
brokering one. A zh reader was told that publishing their resume enables the
thing the toggle gates, when `legal.privacy.sections[4].paragraphs[2]` states
the opposite — the facts extraction is explicitly independent of 開放媒合
consent and is kept "for your own job matching".

Diagnosis worth recording, because the obvious reading is wrong: **en and zh-CN
both use one word for both senses** — "matching" across the count card, the
health card and the toggle; 匹配 likewise — and the privacy policy disambiguates
in prose rather than by vocabulary. So splitting the word per concept would have
made zh-TW diverge from the other two. The defect was narrower: zh-TW alone had
two words inside one card. Fixed by moving the three `dashboard.health*` strings
onto 符合, that locale's own count-card word. The 32 other 媒合 are the
introduction sense and are correct; 開放媒合 keeps its single name.

Terms was edited in the narrowest possible way — the false clause removed and
"score" changed, nothing else rewritten. Worth a human read even so, since it is
the one file here where wording carries legal weight.

## Still open: English readers are missing three paragraphs

Found by sweeping rather than counting, and it outlives the item above.

`lib/i18n-parity.test.ts`'s `flatten()` treats a list as a single leaf, so array
**lengths are never compared**. Counting elements instead: **EN 1073 leaves,
zh-TW and zh-CN 1076**. The three the English pages do not have:

- `about.paragraphs[3]` — the "is Offerfy suitable for the people you'd
  recommend it to" paragraph, which is the one an evaluator most wants
- `contact.paragraphs[3]` and `[4]`

So "fixed in all three locales" has been a claim the test could not actually
check, and the English About and Contact pages are shorter than their Chinese
counterparts by content, not by phrasing. Two separable jobs, both the user's:
write the missing English paragraphs (new user-facing copy — not something an
agent session should invent unasked), and add a length comparison to that test,
which is about four lines. **Adding the test first would turn the suite red on a
real pre-existing gap**, so the copy should land first or the two together.

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
