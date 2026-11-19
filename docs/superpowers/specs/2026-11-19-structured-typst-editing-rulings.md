# Structured Typst editing — every ruling I made on your behalf

I executed `docs/superpowers/plans/2026-11-19-structured-typst-editing.md` with a fresh
subagent per task and a fresh reviewer after each, and ruled on everything the plan and the
spec did not settle rather than stopping to ask. This is the exhaustive list, in the order I
made them, each with **what it costs if I got it wrong** — so you can rework the ones you
disagree with without re-reading the branch.

Three of these amended the approved design doc; those amendments are commits on this branch
(`5e66348`, `49a886c`, and the coalescing one), each explaining what was wrong.

---

## Setup and the plan's own defects

1. **A worktree-local `.env` with only `TYPST_PACKAGE_PATH` and `TYPST_FONT_PATHS`.** Without
   it the worktree baseline has 7 failures that look like regressions. *Cost: none to the
   product; the file is gitignored.*
2. **`EditOut.preview` is one model that Task 6 defines and Task 8 extends with `bands`.**
   *Cost: a task boundary crossing one schema — which is exactly the seam where the frontend
   later turned out to be dropping `bands` entirely (see ruling 44).*
3. **A fixture-coverage test may assert on fixture text rather than code.** It pins that a
   later edit cannot quietly drop a case the parser needs. *Cost: a test that fails when the
   fixture is legitimately rewritten.*
4. **Test helper plumbing is the implementer's; the plan supplies names and assertions.**
   *Cost: helpers that drift in style between task authors.*
5. **The golden unit dump moves from Task 1 to Task 3.** Its contents are byte-offset spans,
   so it can only be a snapshot the parser generates, and only Task 3's parser produces a
   complete one. *Cost: Task 1 ships without the acceptance artefact the spec names.*
6. **My own `test_marks_fixture_covers_every_inline_case` was wrong and was rewritten.** It
   asserted bare substrings (`"*" in src`), which almost any file satisfies. *Cost: none.*
7. **The Tier C fixture keeps its `resume.with(author:)` args and the spec's Tier C definition
   stands.** *Cost: the fixture tests a near-Tier-C document rather than a pure one.*
8. **A parser defect found while building a fixture became a Task 2 requirement**: the
   prototype read continuation lines of a multi-line `#let data = (…)` as prose. *Cost: none —
   not fixing it would have put Typst syntax in a form field.*
9. **The fixtures are the fact; my plan's test values were guesses.** I wrote Task 2's
   expected values before Task 1's fixtures existed. *Cost: none.*
10. **An empty string literal IS an editable unit.** Otherwise the form cannot fill a date the
    template already declares. *Cost: a zero-width span to write into, which `serialize_unit`
    handles.*
11. **Offsets are code points, not bytes**, and the frontend indexes the same way; the spec's
    "byte" was loose wording. *Cost: a JS client that uses `.slice()` instead of `Array.from`
    bolds the wrong words in any line containing an emoji — which is why Task 9 has a test for
    exactly that.*
12. **Delete my `test_a_multi_line_expression_is_not_prose`.** It looped over a fixture that
    yields zero units, so the body never ran. *Cost: none.*
13. **`#align(left)[…]` bodies must be projected — a missing field in my plan.** It is where
    `basic-resume` keeps the professional summary. *Cost: none; leaving it would have made the
    form unable to edit a first-class résumé field.*
14. **Fix my weak coverage test rather than add a counter to satisfy its arithmetic.** *Cost:
    none.*
15. **Review Focus #1 (a heading carrying markup) needs a fixture, not only an inline source.**
    *Cost: deferred to Tasks 8 and 13, which consume headings.*
16. **A grid cell holding résumé content is projected content, not structure** — the
    implementer stopped and asked rather than extending a golden, and the finding was bigger
    than the question. *Cost: more units in Tier B documents.*
17. **I pulled an out-of-scope observation into a fix loop**: `_body_lines` took a content-body
    line as a whole `para` unit with the list marker inside it. *Cost: one extra round; the
    alternative was shipping Typst syntax in a field.*

## The write path

18. **The staleness check `source[span] != serialize_unit(unit)` is an improvement, not a
    deviation.** It catches what `expect` alone cannot: a client holding the right value
    against the wrong span. *Cost: none.*
19. **The structure guard must track the parser, not be stricter than it.** `=5% YoY`,
    `-20% latency` and `+15 points` are things people write in résumés. *Cost: a bullet
    someone wanted to begin with a literal `/ ` or `= ` is refused — rare and recoverable.*
20. **No value intent may change the projection's unit set** — `set_line` refuses empty text
    for every role and names `delete`. This overrode my own plan's test. *Cost: the fields tab
    must route "cleared the field" to `delete`, one extra wire call.*
21. **`compile_preview_pages` IS the compile gate** — two compiles per edit rather than three.
    *Cost: the gate is the SVG leg, and that function swallows a PDF-only failure, so a source
    that lays out but fails PDF export would commit.*
22. **Look for a value intent that breaks the compile; if none exists, say so.** The negative
    result is evidence the escape policy holds. *Cost: none — and it turned up the
    `accent-color` family, which is why the gate is load-bearing.*
23. **The spec's decision 10 is wrong; every edit uses `coalesce=False`.** The `hash` can never
    identify whose revision the latest one is, because `set_draft_source` keeps them equal.
    **Spec amended.** *Cost: one revision row per committed field edit; restoring coalescing
    needs a provenance column.*
24. **Refuse a blank link URL in `serialize_unit`, although that is another task's code.**
    *Cost: a stored `#link("")` becomes read-only rather than editable — verified, not
    reasoned.*
25. **Delete `restamp_content` from the edit path.** Re-stamping a snapshot that does not
    contain the edit makes the next template switch emit the pre-edit text. *Cost: one paid
    extraction on the next switch. Quota is recoverable; typed text is not.*
26. **`is_structural` and its coalescing wiring are dead and the brief's test vacuous** — a
    consequence of ruling 23 that I did not foresee when I made it. *Cost: none.*
27. **Accept Invariant 4's whitespace limit and make the suite say so.** A seam already holding
    three or more newlines collapses. *Cost: a one-newline difference on documents nobody has
    normalised.*

## Cost and concurrency

28. **A field edit costs three typst subprocesses, and I accept it.** The highlight is what the
    feature is for. *Cost: latency and CPU on the per-field path. Partly reversed later by
    ruling 46.*
29. **Fix `/preview`'s pool-connection hold although the constraint names `/edit` only.**
    Applying a rule only where it happens to be written down protects the rarer path. *Cost:
    none.*
30. **`domToMarks` takes a structural node type, not `HTMLElement`** — there is no DOM in this
    harness and adding a dependency would write into the tree that builds the live site.
    *Cost: a type that a real `HTMLElement` must stay assignable to, proven by `tsc`.*
31. **Accept the in-order search for opaque runs.** A run whose text also appears earlier as
    plain text puts the chip on the wrong occurrence. *Cost: one wrongly non-editable chip on a
    line that needs the same raw Typst twice, the first escaped. The real fix is server-side
    offsets.*
32. **Do not restructure the chip-splitting logic; document the fallback.** *Cost: a mark
    boundary inside a chip demotes it to editable text — correct text, wrong affordance.*
33. **A failed projection fetch must not look like an empty résumé.** This repo has shipped
    that bug. *Cost: none.*
34. **The client calls `/api/v1/...`** — the plan's own constraint is true of FastAPI and
    misleading for a browser. *Cost: none.*
35. **A link URL must never reach anything navigable, and the scheme is allow-listed.** *Cost:
    a `mailto:`-adjacent scheme someone wanted is refused with a message.*
36. **A revision restore wins over a field edit but must not interleave.** *Cost: up to a few
    seconds of a pending Restore button.*
37. **Publish the answer's projection to a shell-owned ref synchronously**, rather than
    chaining the queue tail on the caller's promise. *Cost: none; it removed the coupling that
    caused two separate defects.*
38. **Extract the queue into a pure module and test the ordering directly.** *Cost: none — and
    it is the only reason the ordering is verified rather than read.*
39. **No coalescing of rapid field edits.** *Cost: four quick edits to four fields are four
    compiles.*

## The UI

40. **Show characters, not bytes, in the armed delete.** *Cost: none.*
41. **Hide a control that will certainly be refused; where the projection cannot tell, let the
    refusal explain itself.** *Cost: a button that fails with a message on mixed
    positional+keyed templates.*
42. **Add the section-delete intent: the spec grants it and Task 7 declined it as YAGNI.**
    *Cost: the most destructive control in the feature exists. It is armed, counted and
    reversible through history.*
43. **`expect` should be the value the surface last painted — and I was wrong; the implementer
    declined with a better reason.** Threading it would raise false mismatches on an ordinary
    type / pause / type-more sequence. *Cost: `expect` is structurally unable to fire from this
    client. The real remedy is stable (content-derived) unit ids.*
44. **Remove the `insert_section` dead end rather than guard it** — then narrowed on the
    implementer's flag, then **widened back** on the reviewer's. Final rule: no `== ` section
    means append. **Spec amended twice.** *Cost: a document organising sections by another
    convention gains one possibly mis-styled heading — visible in the preview and deletable.*
45. **An empty URL means remove the link.** *Cost: none.*
46. **When a browser behaviour cannot be observed, stop depending on which one it is** — B/I is
    disabled over an opaque chip rather than guessing what `execCommand` does. *Cost: a
    formatting action refused where it might have worked.*
47. **I took three minor findings back to an implementer, bending my own rule**, because two
    closed silent-failure classes the task's brief was written around. *Cost: a precedent for
    doing it again on something less cheap.*

## The end of the plan

48. **Fix the escape round-trip in the parser generally, not by adding `/` to a list.**
    Re-emitting exactly what was there is correct by construction. *Cost: the recorded-escape
    set is wider; verified by 208k fuzzed lines and an exhaustive parity check.*
49. **Prefer offering a field over avoiding a doubled placeholder.** A missing argument is a
    field the form never offers; a doubled placeholder is text to overwrite. *Cost: a cosmetic
    oddity on one template's first project entry.*
50. **Keep the `\\`-parity finding Critical rather than taking the offered downgrade.** *Cost:
    none; the fix was five lines.*
51. **Generalise reachability: write only placeholders the form can reach, and refuse
    otherwise.** *Cost: acorn-resume's project entry offers one field instead of four.*
52. **Implement the spec's band fallback for string arguments rather than amending the spec.**
    63 of 104 units on the spec's own example had no highlight. *Cost: new geometry at the end
    of the plan — and it over-claims rows in two shapes (see the residuals below).*
53. **Let the intent carry the placeholder, so each locale gets its own.** *Cost: one more
    optional wire field — and it is currently the only `IntentIn` string that reaches the
    source unescaped (first residual below).*
54. **No base hash on `PUT /v1/resumes/{id}` in this branch.** That endpoint is shared by five
    writers and predates this work. *Cost: the general last-writer-wins hole stays; the
    reachable case is closed by the resync.*
55. **No scoped re-review for a tests-only round**, because the condition that makes one
    valuable was absent. *Cost: a wrongly pinned count would go unnoticed until a real change
    trips it.*

---

## Residuals I parked rather than fix

The process allows one fix wave after the whole-branch review, and it ran. These are what it
left, each with the cheapest known fix:

1. **The placeholder reaches the source unescaped** (`typst_intents.py:78`) — the only
   `IntentIn` string that does. **This hole is mine**: ruling 53 said "validated like any other
   value" and should have said *escaped* like any other value. API-only (the shipped client
   sends three fixed strings) and it fails closed through the compile gate, but markup goes in
   raw. **One line: run it through the same escaping `serialize_unit` uses.**
2. **The band latch keys on emptiness, not provenance** (`EditorShell.tsx:1635`) — an `/edit`
   answer with empty `pages` leaves the previous document's bands on screen with nothing
   refetching them, and a structural edit renumbers ids. A wrong band, which the spec's
   anti-goal 3 rates worse than none. Fix: key the latch on the source the bands came from.
3. **`_rows_between`'s order guard catches only a strict inversion** (`typst_anchors.py:207`) —
   a one-sided bracket or interleaved interior emits a band across unrelated rows, worst for
   the armed-delete highlight. Correct on all four shipped fixtures. Fix: require each emitted
   row to contain one of the block's arg values, as `_assign` already does for line units.
4. **A duplicate `/preview?bands=1` on every dirty handover** — two compiles and an eval,
   part of what ruling 46's gating saved.
5. **`_unreachable` counts a caller-supplied needle**, so a short placeholder refuses wrongly.
   Conservative and API-only.
6. **One pre-existing test went vacuous** under the band gating
   (`tests/test_edit_api.py:370`); its `/edit` twin still pins the guard.
7. **Same-kind nesting through a group loses the inner mark on a real edit** (`*[a *b*]*`).
   Needs hand-written Typst; no template emits it. A pure predicate would catch it.
8. **A source U+00A0 is rewritten to a space on a real edit.** The no-typing case is fixed.

## What is not verified

- **`npm run build:public` has never run.** Turbopack cannot start against this worktree's
  symlinked `node_modules`. The real build is yours to run.
- **Nothing has been seen in a browser.** There is no component test runner in this repo, so
  the fields tab, the popover, the highlight and every DOM interaction are verified by reading
  and by pure-module tests. **The manual walkthrough, in zh-TW, is the acceptance step** — and
  the first things to look at are a string-argument highlight (ruling 52's new geometry), the
  armed delete's band, and whether the edit highlight clearing on blur feels right.
- Test counts I ran myself at `620ce0c`, on a clean tree: **backend 2342 passed / 4 skipped,
  2346 collected**; **frontend 483 passed / 0 failed across 46 files**; `tsc --noEmit` exit 0.
  The fix wave's own report claimed 2368, which exceeds the number of backend tests that
  exist — so if you compare notes with that file, mine are the measured ones.

## Post-merge rulings (2026-11-20, after PR #4 merged and deployed)

An automated reviewer raised 8 findings on the merged PR. Three were fixed in
phase 1 (`8e6bd25`, `cf5bc23`, `7404ce8`), three go in phase 2, and **three got
no fix**. The three that got none are recorded here, because a finding closed
without a code change leaves nothing behind to explain itself, and the next
person to read the review will otherwise assume it was missed.

### 56. Finding 7 (a late PUT overwriting a field edit) is not a defect — no fix

**The claim:** switching to the fields tab cancels only the *armed* autosave
debounce, so a PUT already in flight is not aborted, and a late PUT of the
pre-edit buffer can overwrite a field edit the server has already applied. The
suggested fix was an `AbortController` on the autosave PUT.

**Why it cannot happen.** `fieldsOpen` is
`activeTab === "fields" && savedSource === source`, and **every** assignment to
`savedSource` happens after an `await putResumeSource(...)` resolves, or adopts
bytes the server already holds (`EditorShell.tsx` lines 266, 1205, 1345, 1585,
2019 — all five checked). The declaration's own comment states the invariant:
"Only ever set from such an answer."

So `savedSource === source` cannot become true while a PUT carrying `source` is
outstanding, because the only thing that makes it true is that PUT's completion.
The panel therefore cannot mount — and cannot send an `/edit` — until the server
has already processed the outstanding PUT. Server-side ordering is PUT-then-edit
by construction, and a PUT whose buffer already equals `savedSource` is refused
by `shellShouldPutSource` anyway.

The gate is not "a save was requested" but "a save completed and the server holds
exactly these bytes," which is strictly stronger than the review assumed. The
`AbortController` would have been dead code guarding a window that does not
exist.

**Cost if wrong:** a field edit silently replaced by the pre-edit document — the
worst outcome this feature can produce. That is why this one was verified by
reading all five call sites rather than reasoned from the mitigation, after two
earlier fixes on this branch were right about the reported case and wrong one
shape further out.

**Residual, not worth fixing:** two autosave PUTs can overlap if typing continues
through a slow save, and if they resolve out of order `savedSource` ends up
describing the older buffer. That is a Typst-tab staleness, not a field-edit
overwrite, and it self-heals — `savedSource !== source` simply makes the next
debounce save again.

### 57. Finding 5's scheme check belongs on mark urls and argument values, not everywhere — fixed in phase 1 for marks, `set_arg` deferred

Phase 1 closed the `set_line` path: a link mark may not carry a `javascript:`,
`data:`, `vbscript:` or `file:` url. The review named only that path. It is worth
recording that **`set_arg` is a second door**, which the original ruling missed:

- `basic-resume` is safe by construction, not by luck. `src/resume.typ:76-83`'s
  `contact-item` calls `link()` only when `link-type != ""`, and every caller
  passes a literal prefix (`"mailto:"`, `"https://"`, `"https://orcid.org/"`);
  with an empty `link-type` it emits plain text and no link at all. `project()`
  concatenates `"https://" + url`. So `javascript:alert(1)` in a field becomes an
  inert `https://javascript:alert(1)`.
- `acorn-resume` is the counterexample: its `project()` takes `live-url` and
  `repo-url` **raw** into `link()`, as
  `app/services/emitters/acorn_resume.py:42-48` already documents while
  explaining why the emitter uses neither slot (for an unrelated reason — the
  body shows a fixed "Live"/"GitHub" label, so the url text would fail the
  content-survival checks).

**Both halves of what I first concluded here were wrong, and the session I asked
to implement it disproved them rather than complying. Corrected in place, with
the original claims kept visible, because a ruling that quietly improves itself
teaches nobody anything.**

*What I claimed:* nothing Offerfy generates contains a raw-url argument, so the
hazard was "one template choice away"; and the fix was a leading-scheme check on
**any** argument value, explicitly *not* a key-name allowlist, since `live-url`
and `repo-url` are not spellings anyone would have predicted and a key allowlist
is the same whack-a-mole one level up.

*Wrong on reachability.* The emitters are not the only generator.
`app/routers/chat.py:350` injects `template_api_block(*spec)`, which inlines the
package's own `template/main.typ` into the system prompt with the instruction to
copy the example's function names and arguments and "replace only the personal
content". For acorn-resume that block shows the model three raw-url arguments, so
a chat-authored acorn résumé carries `live-url`/`repo-url` as editable string
args — and acorn-resume is one of the `deterministic_pairs()` templates, switched
to in plain language (`templates.py:126`). The choice is already made, in the
prompt. This is the ordinary authoring path, not a hand-pasted edge case.

*Wrong on the fix, in the exact direction I said I cared about.* I asked for the
one way the fix could cost a person their edit, and then proposed it. Run against
the existing `_URL_NOISE`/`_SCHEME` normalisation, a whole-value check refuses
`JavaScript: The Good Parts` (a book, a course and a certificate — a thoroughly
ordinary `name:`), `Data: A Love Story`, and `file: not found (a talk)`. The
noise-stripping means "begins with `javascript:`" is not what the rule ends up
asking. And the obvious repair — a space after the colon means prose — is unsafe,
because `javascript: alert(1)` executes in a viewer and normalises to the *same
shape* as the book title. **No value-only rule can be both safe and prose-safe.**

*What shipped instead*, on `claude/silly-saha-1f5e85`: one shared
`_executable_scheme` helper, two call sites, `set_line` still marks-only, and the
`set_arg` site gated on `is_url_key(unit.key)` — living beside `is_style_key` in
`typst_projection.py`, token-matched rather than substring (so `personal-site`
matches on `site` without dragging in `pages`), style keys excluded, and read off
the source rather than the request so a client cannot rename the field it claims
to be writing. The key set was not guessed: every bare `link(<ident>)` site under
`preview/` was traced back to its enclosing signature, yielding `live-url`,
`repo-url`, `project-url`, `uri`, `link-value`, `homepage`, `destination`,
`email`, `source_code`, plain `url` in eight more packages, and the IR's `Contact`
fields. Three of those — `email`, `source_code`, `personal-site` — a url-ish-name
guess would have missed.

*Why the key gate is the better whack-a-mole*, which is the part I had backwards:
the two rules fail in opposite directions. A key the list misses leaves a field
exactly as exposed as it is today. A value rule that fires on prose takes away an
edit someone legitimately wants. Incomplete beats wrong when the wrong direction
is refusing a person's own résumé text.

*The option that needs no names* is parsing the template, and it was rejected for
cause: it needs Typst evaluated rather than grepped (basic-resume's value reaches
`link()` through `contact-item(link-type: "https://")`, so a syntactic answer is
wrong in both directions), the package may be absent at edit time, and it would
make an edit's acceptance depend on a package download.

*Declined:* adding the whole-value check as a union alongside the key gate. One
space bypasses it, so it buys almost no depth while reopening the prose
false-refusal surface that is the whole reason the value rule lost.

**Cost if wrong:** an executable url in a résumé PDF a person sends to an
employer. The author could already write it by hand in the Typst tab, so what
this adds is exposure for the *reader* of a shared document — but the PoC
compiled, and the PDF carries `/URI (javascript:alert(document.cookie))`, so the
chain is real rather than theoretical.

### 58. Finding 8 named a field that was never built — the labels fix is frontend-only

The review asked to "plumb `label_key` through `parseProjection`". `label_key`
appears **nowhere** in this repository, backend or frontend. There is no such
field to plumb; the projection sends `key`, the Typst argument identifier, and
`StructuredPanel.tsx:242` renders it raw.

So the fix is a frontend mapping from `key` to a localised label with a raw-key
fallback — no API change, which keeps phase 2 to one web deploy instead of two.
Taking the review's wording literally would have added a field to the wire format
to carry information the wire format already carries.

**Cost if wrong:** none to correctness; the cost of getting it wrong the other
way was an unnecessary API deploy and a redundant field in a contract this branch
worked hard to keep narrow.

### 59. The frontend suite had no entry point — added one

Phase 2 measured **503 passed / 0 failed** across 47 files (up from 483/46), and
finding out *how* to measure it took longer than the measurement. There is no
vitest, no jest, no runner config and no `test` script anywhere in this repo; the
47 `*.test.ts` files use Node's built-in runner (`node:test` plus
`node:assert/strict`), which a `vitest run` cannot even collect — it reports "No
test suite found" for all 47 files and exits as if the suite were broken.

So the only way to run the frontend's tests was an invocation recorded nowhere,
which means CI cannot have been running them and every agent that touches this
code has to rediscover it. Added to `apps/frontend/package.json`:

    "test": "node --import tsx --test \"lib/*.test.ts\" \"components/**/*.test.ts\""

**Cost if wrong:** none — it adds a script and changes no build input. The cost of
leaving it was 503 tests that only someone who already knew the trick could run.

### 60. Production runs ~95 Typst packages that no commit pins — recorded, not fixed

Found while verifying ruling 57 and worth separating from it, because it is not
about editing at all.

`.env.production:16` sets
`TYPST_PACKAGE_PATH=/home/ruby0322/offerfy/apps/backend/typst/packages`, which
`starter.resolve_package_path()` reads before falling back to the local tree.
That directory is **gitignored** (`.gitignore:36`,
`apps/backend/typst/packages/preview/*`), and `git ls-files` returns zero files
for acorn-resume. So the ~95 packages production compiles against exist only on
that host: no commit pins their versions, a clean clone cannot reproduce what
prod renders, and a worktree sees only `basic-resume` and `scienceicons` — which
is why this session could not find acorn-resume's source and had to be shown it.

Consequences that bite in order of likelihood: a template's behaviour can change
under the product with no diff to review; a hazard that lives in a package (like
ruling 57's raw `link()`) is not reproducible from the repository; and a host
rebuild loses the template set unless someone remembers to re-fetch it.

Not fixed here — vendoring or lock-pinning ~95 packages is its own piece of work
with its own licence questions, and it has nothing to do with the fields tab.

**Cost if wrong:** nothing, since this ruling changes no code. The cost of not
recording it is the next person concluding, as this session did, that a template
hazard is unreachable because the package is not in the tree they can see.
