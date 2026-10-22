# Render layout inspection (`read_layout`)

**Date:** 2026-10-22
**Status:** implemented (geometry + vision), vision side runs on a public-beta endpoint

## Goal

Let the chat agent answer questions about the *rendered* resume — did this bullet
wrap, what did the page break cut, how full is the last page, does the page look
crowded — instead of guessing from Typst source.

## The two costs this was waiting on, and why neither was real

The feature had been parked on two assumptions. Both turned out to be wrong.

**"We need object storage to serve a public image URL."** We do not. The
Decisions endpoint takes images as inline base64 data URLs and
[explicitly rejects](https://developers.openai.com/api/docs/guides/decisions)
`file_id` inputs, with the guide excluding hosted HTTP(S) URLs as well. The bytes
ride in the request body. And nothing has to produce a PDF first:
`compile_typst_pages(source, "png", ppi=...)` already existed for share-card OG
images ([shares.py](../../../apps/backend/app/routers/shares.py)), so rendering
is one call with no new infrastructure.

**"The LLM calls are expensive."** They are not, at any of the options:

| route | input tokens / page | $ / page | $ / 1,000 pages |
| --- | --- | --- | --- |
| Decisions, `gpt-6-luna` @ $0.10/1M, output free | ~2,417 | $0.00024 | $0.24 |
| the same page straight to `gpt-5.6-terra` @ $2/1M | ~2,417 | $0.0048 | $4.80 |
| Decisions at 110 ppi | ~1,427 | $0.00014 | $0.14 |

A4 at 144 ppi is 1191x1684 px, so `ceil(1191/32) * ceil(1684/32)` = 2,014 patches
of 32px — inside the 2,500-patch `high` budget, so the page is billed whole
rather than shrunk — and 2,014 x 1.2 = 2,417 billable tokens
([images and vision](https://developers.openai.com/api/docs/guides/images-vision)).

So Decisions is not what made this affordable; it was already affordable. What it
buys is worth having anyway:

1. **Image tokens stay out of the main conversation.** The chat loop re-sends its
   accumulated input every round at Terra prices. An image in that history costs
   ~2.4k tokens *per remaining round*. A side-channel call pays once.
2. **Typed answers.** `predicate` returns a probability, and a refusal is its own
   answer type rather than a confident-sounding "no". No JSON schema to police.
3. **~10x faster** than the Responses API, per OpenAI.

Note what it does *not* buy: `gpt-6-luna` costs the same $0.10/1M input on the
Responses API. Decisions drops output charges and the $0.01/1M cached-input rate,
which on
[one published comparison](https://www.beri.net/article/openai-decisions-api-public-beta-gpt-6-luna-pricing-cache-discount-vs-jev-clef-zdr-residency)
is $50 vs $55 per million short calls — and *four times worse* if every call
repeats a long cached rubric. Our calls carry a fresh image and no shared prefix,
which is the shape where it wins.

## The actual design decision: geometry first, vision only for what is left

Most of what was wanted here is not a vision problem. The PDF knows it exactly,
and `pdfminer` is already a dependency parsing the same bytes for ATS checks.

| question | answered by | cost | accuracy |
| --- | --- | --- | --- |
| how many pages | row table | 0 | exact |
| did this text wrap, onto how many lines | row table | 0 | exact |
| which page is it on, did the break split it | row table | 0 | exact |
| how much room is left on its last line | row table | 0 | exact |
| content past the right margin | row table | 0 | exact |
| how full is each page | row table | 0 | exact |
| does this page look crowded or unbalanced | Decisions + vision | $0.00024/page | a judgement |

This split is not only about cost. `gpt-6-luna` is a compact model whose
[weakest axis is OCR](https://playground.roboflow.com/models/openai/gpt-6-luna)
(59.8%, #30 of 47; 69.7% average across vision tasks, #21 of 63). "Did this
bullet wrap" is simultaneously the question it is most likely to get wrong and
the one a bounding box answers perfectly. Asking the model would be paying for a
worse answer.

## Implementation

[`app/services/layout.py`](../../../apps/backend/app/services/layout.py).

**Rows, not containers.** pdfminer regularly files the second line of a wrapped
bullet under a `LTTextContainer` of its own, so a container's line count is not a
wrap count. Lines are regrouped into *visual rows* by vertical overlap, which
also folds a right-aligned date onto the title row it belongs to, the way a
reader sees it. Each row keeps `body_x1` — the main column's own right edge — so
a row that reaches the right margin only because of its date does not read as a
full-width line.

**Targets.** `targets` are snippets of rendered text. Rows are joined into one
searchable string with a span per row, so a snippet's match maps back to the rows
it covers: one row means it fits, two means it wrapped, two pages means the break
split it. A row ending in a hyphen joins without a space, since that hyphen came
from the line breaker and is not in the source. An exact match is tried first,
then successively shorter prefixes down to four words.

**Findings** are computed, not asked for: `item_split_across_pages`,
`heading_orphaned_at_page_end`, `right_margin_overflow`,
`last_page_nearly_empty`. Two tolerances matter:

- Overflow needs a tolerance of 0.6em. pdfminer's bbox for a wrapped line
  includes the advance of the last glyph before the break, which puts ordinary
  wrapped text ~3.7pt past the column; a real spill (a 200-character token)
  measured 1,010pt.
- A heading is only "orphaned" if a page follows it. Without that condition, the
  last heading of every resume was a finding, because a document ending looks
  identical to a stranded heading from inside one page.

**Vision** is opt-in per call: no `questions`, no raster, no HTTP, nothing
billed. With questions, pages render at `VISION_PPI`, up to `MAX_VISION_PAGES`,
and go out as one request — independent questions share one input, so the pages
are billed once however many ride along. `input_tokens` comes back in the report,
since input is the entire bill on this endpoint.

**It never raises.** A 404 from a proxy that has no `/v1/decisions`, a transport
error, a refusal — each comes back as a field, because the geometry half of the
report is still the answer to most of the question.

**Two question shapes.** A `question` is free-form and yes/no, answered with a
probability. A `score` names one of the fixed rubrics in `SCORE_RUBRICS` and
comes back graded 0–3, with its level labels attached so the number can be read.
Both ride in one request, so the page images are billed once.

**Every edit carries the page home.** `apply_typst_edit` already compiles a PDF
to check the edit when `verify_compile` is on, which the chat router always sets,
so `compile_digest()` derives a small digest — `pages`, `page_fill`, `findings` —
from those same bytes and attaches it to the edit result beside `compile`. No
second compile, and more importantly no extra agent round: a round of the main
model re-sends the whole accumulated input at $2/1M, roughly $0.016, which is
~66x the vision call it would be spending that round to make. The digest is
deliberately narrower than `geometry_report` because it rides along on every
single edit.

## Live verification and calibration (2026-10-22)

Nine real calls against `https://api.openai.com/v1/decisions`, `gpt-6-luna`, on
three documents whose ground truth comes from the geometry report: **A** sparse
(1 page, `page_fill` 0.21), **B** dense (1 page, 0.99, containing exactly one
two-line bullet), **C** two-page (0.99 / 0.13). Total cost **$0.0024**;
latency 493 to 2,241ms.

### Easy questions saturate; hard ones do not

The first battery was four easy questions on A and came back `1.0, 0.0, 1.0,
0.0`, all correct, which looked like the endpoint only emits booleans. **That
conclusion was wrong** — it was a property of the questions. The hard battery on
B grades:

| question | probability | truth |
| --- | --- | --- |
| Is the vertical spacing between sections consistent? | **0.13** | true — **miss** |
| Does this page have too much empty space? | 0.01 | false |
| Would this page read better split onto two pages? | 0.97 | subjective |
| Are the right-hand dates aligned to the same edge? | 1.0 | true |
| Is there exactly **one** two-line bullet? | 1.0 | true |
| Is there exactly **two** two-line bullets? | 0.0 | false |
| Is this resume ready to send to an employer? | 0.0 | beyond the image |

So `probability` is usable as a degree, not only as a threshold.

**It counted, and it did not simply agree.** Asked in the same request whether
there was exactly one and exactly two two-line bullets, it answered 1.0 and 0.0;
the geometry says one. A model pattern-matching the question's premise would
have said yes to both.

**The one real miss was spacing consistency** — 0.13 for a page whose spacing is
provably uniform (11.02pt before both section headings, 8.37pt after each,
8.83pt between every bullet, measured off the PDF). Fine-grained spacing
judgement is not something to trust this model with, and it is also something the
row geometry answers exactly, so there is no reason to ask it.

### It is deterministic

Three identical requests returned byte-identical probabilities (`0.13, 0.01,
0.97, 1.0, 1.0, 0.0, 0.0`, three times over). Retrying buys nothing; there is no
variance to average out.

### Prefer `score` over `choice` for a verdict

A `score` question with four ordered density levels behaved well: A scored 0.0
("Sparse", confidence 1.0) and B scored 1.79 with `Dense=0.73`.

A `choice` question in the *same request* as B's score, asking for the biggest
layout problem, answered **`too_sparse` at 0.88, confidence 0.84** — about the
page it had just scored `Dense`. One request contradicting itself, with high
confidence on the wrong half. Read `confidence` as how clear-cut the question
is, not as whether the answer is right.

### One request answers about its whole input (fixed)

Asked to score C's density across both pages, it returned a single document-wide
**0.05 ("Sparse"), confidence 0.95** — dominated by the near-empty second page,
for a document whose first page is 99% full. Hence `pages` on the tool, measured
before and after:

| input | score | confidence | input tokens |
| --- | --- | --- | --- |
| both pages (before) | 0.05, "Sparse" | 0.95 | 4,241 |
| page 1 only (0.99 full) | **1.32**, between Comfortable and Dense | 0.46 | 2,219 |
| page 2 only (0.09 full) | **0.0**, "Sparse" | 1.0 | 2,219 |

Scoping halves the tokens and is the difference between a right and a wrong
answer. Note the confidences: 0.46 on the genuinely borderline page, 0.95 on the
blended answer that was wrong.

### Score rubrics: two kept, one measured and thrown away

`score` questions use fixed server-side rubrics, not levels the agent writes.
Two reasons, both measured above: the answer depends heavily on how the levels
are worded (the `choice` contradiction), and a fixed scale is the only way two
calls are comparable — which is what makes "did my edit improve this"
answerable. The model is deterministic, so a fixed rubric yields a stable
number. Free-form judgements still have `questions`.

Four documents, one request each, all three rubrics in every request:

| document | geometry | `density` | `hierarchy` | `balance` |
| --- | --- | --- | --- | --- |
| A sparse | 1 page, fill 0.21 | **0.0** Airy (1.0) | 2.79 Strong (0.79) | 0.03 Lopsided (0.97) |
| B dense | 1 page, fill 0.99 | **1.30** Comfortable (0.40) | 2.83 Strong (0.83) | 0.92 Uneven (0.92) |
| C page 2 only | 6 rows, fill 0.09 | **0.0** Airy (1.0) | **0.23** Flat (0.77) | 0.01 Lopsided (0.99) |
| D one font size, no headings | 1 page, fill 0.42 | 0.0 Airy (1.0) | **0.01** Flat (0.99) | 0.02 Lopsided (0.98) |

`density` and `hierarchy` discriminate. `hierarchy` is the clearest: 2.8
("Strong") for the real template, 0.01 ("Flat") for a document set in a single
size with no headings, and 0.23 for a stray second page that carries no heading
at all. That is a real judgement about the rendered page, and not one the row
geometry makes easily.

**`balance` was removed.** It answered 0.01–0.92 with 0.92–0.99 confidence on
*every* document, including a perfectly ordinary resume. A left-aligned single
column is left-heavy by construction, so the rubric could not discriminate — and
acting on it means adding a right-hand column, which would break the
`single_column` check in `ats.py`. The part of balance that is a real resume
problem, vertical distribution, is already measured exactly by `page_fill` and
the findings. A rubric that cannot discriminate and whose advice fights another
feature is worse than no rubric.

### Fixed rubrics beat free-form wording

The payoff, measured on the same document before and after an edit that added 30
bullets (`page_fill` 0.21 to 0.94):

| | before | after |
| --- | --- | --- |
| `density` | 0.0 Airy (conf 1.0) | **0.65** Comfortable (conf 0.61) |
| `hierarchy` | 2.79 Strong | 2.82 Strong — unchanged, correctly |
| predicate "Does this page look unfinished?" | 0.95 | **1.0** |

The rubric moved the right way and the unrelated rubric held still. The
free-form predicate went the *wrong* way: it grew more certain the page looked
"unfinished" as the page filled up — almost certainly because it read
"unfinished" as placeholder content ("Padding bullet 7", "University Name"),
which is true and is not what was being asked. **Word a `question` about what is
visible on the page, not about a quality the model has to interpret.** That is
exactly the ambiguity a rubric's described levels remove.

### Wire format

Verified against the real response, which is richer than the guide shows and
reports a cache write on an endpoint whose pricing has no cache line:
`{"input_tokens": 2695, "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 2069, "cache_write_tokens_details": {"ttl_30m_tokens": 2069}}, "output_tokens": 0, "total_tokens": 2695}`.
The bill is `input_tokens`: 2,695 x $0.10/1M = **$0.00027** for one page plus
seven questions, of which ~2,417 is the image and ~278 the questions — matching
the patch arithmetic above. A second identical request was charged the same, with
`cached_tokens: 0`, consistent with there being no cache-read discount here.

## Risks

- **Public beta.** The endpoint opened 2026-10-06 with GA "in the coming weeks",
  one supported model, and no published rate limits. `openai_decisions_model` is
  a setting so a rename is a config change, and a failed call degrades to
  geometry.
- **A proxy `OPENAI_BASE_URL`.** `decisions_url()` derives the path the same way
  `_responses_url()` does, but a proxy that does not forward the endpoint will
  404. That is handled, not prevented.
- **PII.** Resume pages are personal data. The endpoint supports ZDR and HIPAA,
  and US / EEA+CH residency, but both need separate eligibility — confirm the
  org's configuration before relying on it in production.
- **Continuation detection is script-dependent.** A wrapped CJK paragraph that
  resumes at the left margin is indistinguishable from a new paragraph by
  geometry. Passing the text as a `target` answers it exactly; the automatic
  `item_split_across_pages` finding will miss it.

## Not done

- **No caller-defined score levels, and no `choice` at all.** `scores` selects
  from `SCORE_RUBRICS` by name. Custom levels would make two calls
  incomparable and put the wording — the thing the measurements say matters
  most — in the hands of whoever is writing the prompt. `choice` is not
  supported on the evidence above.
- **No frontend control.** The tool is agent-only; the chat card reports it.
- **Spacing and alignment judgements are not worth asking the model for.** The
  one measured miss is also the thing geometry answers exactly. If those
  judgements are wanted, extend the row analysis, not the question list.
