from pathlib import Path

import pytest

from app.services.layout import (
    MAX_SCORES,
    _is_continuation,
    SCORE_RUBRICS,
    SPARSE_PAGE_FILL,
    _findings,
    compile_digest,
    decisions_url,
    geometry_report,
    read_layout_report,
    vision_report,
)
from app.services.typst_compile import typst_available

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

STARTER = Path(__file__).resolve().parents[1] / "typst" / "starter.typ"

# Long enough that basic-resume wraps it onto a second line at A4 width.
WRAPPED_BULLET = (
    "Built and shipped an end-to-end data platform that cut nightly batch "
    "runtime from six hours to eleven minutes, which is a deliberately long "
    "sentence so that it wraps onto a second line in the rendered PDF"
)

needs_typst = pytest.mark.skipif(not typst_available(), reason="Typst CLI is required")


def starter() -> str:
    return STARTER.read_text(encoding="utf-8")


def padded(count: int, tail: str = "") -> str:
    bullets = "".join(f"- Padding bullet {i}\n" for i in range(count))
    return starter() + "\n" + bullets + tail


# --- geometry ---------------------------------------------------------------


@needs_typst
def test_starter_is_one_sparse_page_with_nothing_to_report():
    report = geometry_report(starter())
    assert report["pages"] == 1
    assert report["page_size_pt"] == [595, 842]
    assert report["rows_per_page"] == [9]
    # The starter barely covers a fifth of the page. That number is the point:
    # it is how the model learns there is room without being shown the page.
    assert 0.1 < report["page_fill"][0] < 0.3
    assert report["findings"] == []
    assert "visual" not in report
    # Every rendered item comes back, so nothing has to be looked up by text.
    heads = [item["head"] for item in report["items"]]
    assert "Your Name" in heads
    assert all(item["rows"] == 1 for item in report["items"])
@needs_typst
def test_unbreakable_content_is_reported_as_right_margin_overflow():
    report = geometry_report(starter() + "\n- Tag: " + "A" * 200 + "\n")
    kinds = [finding["kind"] for finding in report["findings"]]
    assert "right_margin_overflow" in kinds
    overflow = next(f for f in report["findings"] if f["kind"] == "right_margin_overflow")
    assert overflow["pt"] > 100


@needs_typst
def test_ordinary_wrapped_text_is_not_reported_as_overflow():
    # A wrapped line's bbox runs a few points past the column because pdfminer
    # counts the advance of the last glyph before the break. That must not read
    # as a spill, or every long bullet would be a finding.
    report = geometry_report(starter() + f"\n- {WRAPPED_BULLET}\n")
    assert [f["kind"] for f in report["findings"]] == []


@needs_typst
def test_a_nearly_empty_last_page_is_reported():
    report = geometry_report(padded(36))
    assert report["pages"] == 2
    assert report["page_fill"][-1] < SPARSE_PAGE_FILL
    finding = next(f for f in report["findings"] if f["kind"] == "last_page_nearly_empty")
    assert finding["page"] == 2
    assert finding["rows"] == report["rows_per_page"][-1]


@needs_typst
def test_a_heading_that_merely_ends_the_document_is_not_a_finding():
    # The last page's final heading is where the resume stops, not a heading
    # stranded by a page break.
    report = geometry_report(padded(40, "\n== Skills\n\n- One skill line\n"))
    assert report["pages"] == 2
    assert "heading_orphaned_at_page_end" not in [f["kind"] for f in report["findings"]]


@needs_typst
def test_a_page_break_inside_a_paragraph_is_reported():
    long_para = (
        "This is one long plain paragraph that must span many rendered lines so "
        "that the page break has no choice but to fall somewhere inside it, and "
        "it keeps going with more words about nothing in particular until it is "
        "comfortably taller than the space that remains at the bottom of the "
        "page, and then some more words after that for good measure. "
    )
    # 24 repetitions is three pages' worth, so the break cannot avoid landing
    # inside the paragraph however Typst nudges its widows and orphans.
    report = geometry_report(padded(38, "\n" + long_para * 24 + "\n"))
    assert report["pages"] >= 3
    split = next(f for f in report["findings"] if f["kind"] == "item_split_across_pages")
    assert split["page"] >= 2
    assert split["text"]


def test_a_source_that_does_not_compile_returns_an_error_not_an_exception():
    report = geometry_report("#this-is-not-typst(")
    assert "error" in report
    assert "pages" not in report


# --- findings, on rows built by hand ---------------------------------------
#
# Typst's own widow and orphan handling makes some of these hard to provoke
# with real source, and the detector should be pinned down regardless of which
# template the user switches to.


def row(page, text, *, x0=36.0, x1=300.0, y0=700.0, size=10.0, body_x1=None):
    return {
        "page": page,
        "page_width": 595.0,
        "page_height": 842.0,
        "text": text,
        "x0": x0,
        "x1": x1,
        "body_x1": x1 if body_x1 is None else body_x1,
        "y0": y0,
        "y1": y0 + 12.0,
        "size": size,
    }


def test_an_indented_first_row_on_a_new_page_is_a_split_item():
    rows = [
        row(1, "• a bullet that starts here", y0=100.0),
        row(2, "and finishes over here", x0=49.0, y0=800.0),
        row(2, "• the next bullet", y0=780.0),
        row(2, "• and another", y0=760.0),
    ]
    found = _findings(rows, [0.99, 0.9])
    assert [f["kind"] for f in found] == ["item_split_across_pages"]
    assert found[0]["page"] == 2


def test_a_new_bullet_at_the_top_of_a_page_is_not_a_split_item():
    rows = [
        row(1, "• a bullet", y0=100.0),
        row(2, "• another bullet", y0=800.0),
        row(2, "• a third bullet", y0=780.0),
    ]
    assert _findings(rows, [0.99, 0.9]) == []


def test_a_heading_stranded_at_the_foot_of_a_page_is_reported():
    rows = [
        row(1, "Experience", y0=700.0, size=12.0),
        row(1, "• something", y0=680.0),
        row(1, "• something else", y0=660.0),
        row(1, "Skills", y0=40.0, size=12.0),
        row(2, "• Python, Typst", y0=800.0),
        row(2, "• and more", y0=780.0),
    ]
    found = _findings(rows, [0.99, 0.9])
    assert [f["kind"] for f in found] == ["heading_orphaned_at_page_end"]
    assert found[0]["text"] == "Skills"
    assert found[0]["rows_after"] == 0


def test_a_right_aligned_date_does_not_count_toward_the_right_edge():
    # The row extends to the right margin only because the date sits there.
    rows = [
        row(1, "Job Title 2023-07 - Present", x1=559.0, body_x1=120.0),
        row(1, "• a bullet", y0=680.0),
    ]
    assert _findings(rows, [0.5]) == []


def test_findings_are_capped_so_the_report_stays_small():
    rows = [row(1, "• x" * 3, y0=700.0 - index, body_x1=900.0) for index in range(20)]
    found = _findings(rows, [0.99])
    assert len(found) == 7
    assert found[-1] == {"kind": "truncated", "dropped": 14}


# --- the vision side --------------------------------------------------------


class StubResponse:
    def __init__(self, payload, status_code=200):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class StubDecisions:
    """Stands in for httpx.AsyncClient for the /v1/decisions call."""

    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.calls: list[dict] = []

    def __call__(self, *args, **kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, headers=None, json=None):
        self.calls.append({"url": url, "headers": headers or {}, "body": json or {}})
        return StubResponse(self.payload, self.status_code)


@pytest.fixture
def stub_pages(monkeypatch):
    monkeypatch.setattr(
        "app.services.layout._render_pages", lambda source: [b"\x89PNG-one", b"\x89PNG-two"]
    )


@pytest.mark.parametrize(
    "base,expected",
    [
        ("https://api.openai.com/v1", "https://api.openai.com/v1/decisions"),
        ("https://api.openai.com/v1/", "https://api.openai.com/v1/decisions"),
        ("https://api.openai.com", "https://api.openai.com/v1/decisions"),
        ("https://proxy.test/v1/responses", "https://proxy.test/v1/decisions"),
        ("https://proxy.test/v1/chat/completions", "https://proxy.test/v1/decisions"),
        ("https://proxy.test/v1/decisions", "https://proxy.test/v1/decisions"),
    ],
)
def test_decisions_url(base, expected):
    assert decisions_url(base) == expected


@pytest.mark.anyio
async def test_vision_is_skipped_when_no_key_is_configured(stub_pages):
    report = await vision_report("anything", ["Does the page look crowded?"])
    assert report == {"skipped": "llm_not_configured"}


@pytest.mark.anyio
async def test_vision_sends_inline_images_and_typed_questions(
    openai_enabled, monkeypatch, stub_pages
):
    stub = StubDecisions(
        {
            "answers": [
                {"type": "predicate", "name": "q1", "probability": 0.91},
                {"type": "predicate", "name": "q2", "probability": 0.04},
            ],
            "usage": {"input_tokens": 4834},
        }
    )
    monkeypatch.setattr("app.services.layout.httpx.AsyncClient", stub)
    questions = ["Does page 1 look crowded?", "Is the right column misaligned?"]

    report = await vision_report("source", questions)

    call = stub.calls[0]
    assert call["url"] == "https://api.openai.com/v1/decisions"
    assert call["body"]["model"] == "gpt-6-luna"
    assert [q["type"] for q in call["body"]["questions"]] == ["predicate", "predicate"]
    assert [q["instructions"] for q in call["body"]["questions"]] == questions
    content = call["body"]["input"][0]["content"]
    images = [part for part in content if part["type"] == "input_image"]
    # Inline base64: the Decisions endpoint takes no hosted URL and no file_id,
    # which is the whole reason this feature needs no object storage.
    assert len(images) == 2
    assert all(part["image_url"].startswith("data:image/png;base64,") for part in images)
    assert report["pages_seen"] == [1, 2]
    assert report["input_tokens"] == 4834
    assert report["answers"] == [
        {"question": questions[0], "probability": 0.91},
        {"question": questions[1], "probability": 0.04},
    ]


@pytest.mark.anyio
async def test_vision_only_shows_the_first_few_pages(openai_enabled, monkeypatch):
    monkeypatch.setattr("app.services.layout._render_pages", lambda source: [b"png"] * 7)
    stub = StubDecisions({"answers": []})
    monkeypatch.setattr("app.services.layout.httpx.AsyncClient", stub)

    report = await vision_report("source", ["Anything?"])

    assert report["pages_seen"] == [1, 2, 3, 4]
    assert report["pages_skipped"] == 3
    content = stub.calls[0]["body"]["input"][0]["content"]
    assert len([part for part in content if part["type"] == "input_image"]) == 4


@pytest.mark.anyio
async def test_a_refusal_is_not_read_as_a_low_probability(
    openai_enabled, monkeypatch, stub_pages
):
    stub = StubDecisions(
        {"answers": [{"type": "refusal", "name": "q1", "reason": "cannot judge"}]}
    )
    monkeypatch.setattr("app.services.layout.httpx.AsyncClient", stub)

    report = await vision_report("source", ["Is this person employable?"])

    answer = report["answers"][0]
    assert answer["refused"] is True
    assert answer["reason"] == "cannot judge"
    assert "probability" not in answer


@pytest.mark.anyio
async def test_a_failed_decisions_call_is_reported_not_raised(
    openai_enabled, monkeypatch, stub_pages
):
    stub = StubDecisions({"error": {"message": "Unknown endpoint /v1/decisions"}}, 404)
    monkeypatch.setattr("app.services.layout.httpx.AsyncClient", stub)

    report = await vision_report("source", ["Does it look crowded?"])

    assert report["error"] == "Unknown endpoint /v1/decisions"
    assert report["model"] == "gpt-6-luna"


@needs_typst
@pytest.mark.anyio
async def test_read_layout_keeps_the_geometry_when_the_vision_call_fails(
    openai_enabled, monkeypatch
):
    stub = StubDecisions({"error": {"message": "nope"}}, 500)
    monkeypatch.setattr("app.services.layout.httpx.AsyncClient", stub)

    report = await read_layout_report(starter(), {"questions": ["Crowded?"]})

    assert report["pages"] == 1
    assert report["items"]
    assert report["visual"]["error"] == "nope"


@needs_typst
@pytest.mark.anyio
async def test_read_layout_does_not_render_when_no_questions_are_asked(monkeypatch):
    def fail(source):
        raise AssertionError("rendered a PNG for a geometry-only request")

    monkeypatch.setattr("app.services.layout._render_pages", fail)
    report = await read_layout_report(starter(), {})
    assert report["pages"] == 1
    assert "visual" not in report
# --- the digest an edit carries home ----------------------------------------


@needs_typst
def test_compile_digest_reports_the_page_from_the_compile_it_already_did():
    digest = compile_digest(padded(36))
    assert digest["compile"] == {"ok": True}
    layout = digest["layout"]
    assert layout["pages"] == 2
    assert layout["page_fill"][-1] < SPARSE_PAGE_FILL
    assert [f["kind"] for f in layout["findings"]] == ["last_page_nearly_empty"]
    # Deliberately smaller than the full report: this rides on every edit.
    assert set(layout) == {"pages", "page_fill", "findings"}


def test_compile_digest_reports_a_broken_compile_without_a_layout():
    digest = compile_digest("#this-is-not-typst(")
    assert digest["compile"]["ok"] is False
    assert "layout" not in digest


def test_compile_digest_skips_when_typst_is_missing(monkeypatch):
    monkeypatch.setattr("app.services.typst_compile.typst_available", lambda: False)
    assert compile_digest("anything") == {"compile": {"ok": True, "skipped": True}}


# --- page scoping -----------------------------------------------------------


@pytest.mark.anyio
async def test_questions_can_be_scoped_to_one_page(openai_enabled, monkeypatch):
    monkeypatch.setattr(
        "app.services.layout._render_pages", lambda source: [b"one", b"two", b"three"]
    )
    stub = StubDecisions({"answers": []})
    monkeypatch.setattr("app.services.layout.httpx.AsyncClient", stub)

    report = await vision_report("source", ["Does it look unfinished?"], [2])

    assert report["pages_seen"] == [2]
    content = stub.calls[0]["body"]["input"][0]["content"]
    labels = [part["text"] for part in content if part["type"] == "input_text"]
    # The label must carry the real page number, or an answer about page 2
    # reads as an answer about page 1.
    assert "Page 2 of 3:" in labels
    assert len([part for part in content if part["type"] == "input_image"]) == 1


@pytest.mark.anyio
async def test_out_of_range_pages_fall_back_to_the_whole_document(
    openai_enabled, monkeypatch
):
    monkeypatch.setattr("app.services.layout._render_pages", lambda source: [b"one", b"two"])
    stub = StubDecisions({"answers": []})
    monkeypatch.setattr("app.services.layout.httpx.AsyncClient", stub)

    report = await vision_report("source", ["Anything?"], [7, 9])

    assert report["pages_seen"] == [1, 2]


# --- score rubrics ----------------------------------------------------------


def test_every_rubric_is_an_ordered_scale_with_described_levels():
    # A score is only interpretable if its levels are ordered and distinct, and
    # the model's answer depends on how they are worded, so they are checked.
    assert set(SCORE_RUBRICS) == {"density", "hierarchy"}
    for name, rubric in SCORE_RUBRICS.items():
        assert rubric["instructions"].endswith("?"), name
        labels = [level["label"] for level in rubric["levels"]]
        assert len(labels) == 4, name
        assert len(set(labels)) == 4, name
        for level in rubric["levels"]:
            assert level["description"].strip(), name


@pytest.mark.anyio
async def test_scores_are_sent_as_score_questions_with_their_rubric(
    openai_enabled, monkeypatch, stub_pages
):
    stub = StubDecisions({"answers": []})
    monkeypatch.setattr("app.services.layout.httpx.AsyncClient", stub)

    await vision_report("source", [], rubrics=["density", "hierarchy"])

    sent = stub.calls[0]["body"]["questions"]
    assert [q["type"] for q in sent] == ["score", "score"]
    assert [q["name"] for q in sent] == ["density", "hierarchy"]
    assert sent[0]["levels"] == SCORE_RUBRICS["density"]["levels"]
    assert sent[0]["instructions"] == SCORE_RUBRICS["density"]["instructions"]


@pytest.mark.anyio
async def test_predicates_and_scores_ride_in_one_request(
    openai_enabled, monkeypatch, stub_pages
):
    stub = StubDecisions({"answers": []})
    monkeypatch.setattr("app.services.layout.httpx.AsyncClient", stub)

    await vision_report("source", ["Is it crowded?"], rubrics=["density"])

    sent = stub.calls[0]["body"]["questions"]
    # One request, so the page images are billed once however many ride along.
    assert [q["type"] for q in sent] == ["predicate", "score"]
    assert len(stub.calls) == 1


@pytest.mark.anyio
async def test_a_score_answer_carries_its_scale_back(
    openai_enabled, monkeypatch, stub_pages
):
    stub = StubDecisions(
        {
            "answers": [
                {
                    "type": "score",
                    "name": "density",
                    "score": 1.79,
                    "confidence": 0.71,
                    "probabilities": [
                        {"value": 0, "label": "Airy", "probability": 0.02},
                        {"value": 1, "label": "Comfortable", "probability": 0.21},
                        {"value": 2, "label": "Dense", "probability": 0.73},
                        {"value": 3, "label": "Cramped", "probability": 0.04},
                    ],
                }
            ]
        }
    )
    monkeypatch.setattr("app.services.layout.httpx.AsyncClient", stub)

    report = await vision_report("source", [], rubrics=["density"])

    answer = report["answers"][0]
    # Identified by rubric, not by a question nobody asked.
    assert answer["rubric"] == "density"
    assert "question" not in answer
    assert answer["score"] == 1.79
    assert answer["confidence"] == 0.71
    # A bare 1.79 means nothing without the scale it sits on.
    assert answer["levels"] == [
        {"label": "Airy", "probability": 0.02},
        {"label": "Comfortable", "probability": 0.21},
        {"label": "Dense", "probability": 0.73},
        {"label": "Cramped", "probability": 0.04},
    ]


@pytest.mark.anyio
async def test_a_refused_score_is_not_a_zero(openai_enabled, monkeypatch, stub_pages):
    stub = StubDecisions({"answers": [{"type": "refusal", "name": "hierarchy"}]})
    monkeypatch.setattr("app.services.layout.httpx.AsyncClient", stub)

    report = await vision_report("source", [], rubrics=["hierarchy"])

    assert report["answers"][0] == {"rubric": "hierarchy", "refused": True}


@needs_typst
@pytest.mark.anyio
async def test_scores_alone_still_run_the_vision_call(openai_enabled, monkeypatch):
    stub = StubDecisions({"answers": []})
    monkeypatch.setattr("app.services.layout.httpx.AsyncClient", stub)
    monkeypatch.setattr("app.services.layout._render_pages", lambda source: [b"png"])

    report = await read_layout_report(starter(), {"scores": ["hierarchy"]})

    assert [q["name"] for q in stub.calls[0]["body"]["questions"]] == ["hierarchy"]
    assert report["visual"]["pages_seen"] == [1]


@needs_typst
@pytest.mark.anyio
async def test_unknown_rubrics_are_dropped_and_duplicates_collapse(
    openai_enabled, monkeypatch
):
    stub = StubDecisions({"answers": []})
    monkeypatch.setattr("app.services.layout.httpx.AsyncClient", stub)
    monkeypatch.setattr("app.services.layout._render_pages", lambda source: [b"png"])

    report = await read_layout_report(
        starter(),
        {"scores": ["density", "density", "vibes", "hierarchy", "balance"]},
    )

    # "vibes" is not a rubric and "balance" was removed on the evidence; both
    # are dropped rather than guessed at.
    assert [q["name"] for q in stub.calls[0]["body"]["questions"]] == [
        "density",
        "hierarchy",
    ]
    assert len(stub.calls[0]["body"]["questions"]) <= MAX_SCORES
    assert "visual" in report


@needs_typst
@pytest.mark.anyio
async def test_a_bogus_rubric_on_its_own_asks_nothing(openai_enabled, monkeypatch):
    def fail(source):
        raise AssertionError("rendered a PNG for a request with nothing to ask")

    monkeypatch.setattr("app.services.layout._render_pages", fail)
    report = await read_layout_report(starter(), {"scores": ["vibes"]})
    assert "visual" not in report
def test_a_full_width_row_means_the_text_continues():
    # The unit behind the fix: a row reaching the column edge is a row whose
    # text did not finish, whatever the next row starts with.
    full = row(1, "ends exactly at the column edge", body_x1=559.0)
    short = row(1, "ends early", body_x1=200.0)
    capitalised = row(1, "Offerfy Offerfy", y0=688.0)
    assert _is_continuation([full, capitalised], 1, 36.0, 10.0) is True
    assert _is_continuation([short, capitalised], 1, 36.0, 10.0) is False
# --- when geometry cannot answer, say so and point at the vision path -------

TWO_COLUMN = """#set page(paper: "a4", margin: 36pt)
#set text(size: 10pt)
#grid(columns: (1fr, 1fr), gutter: 24pt,
[Left column text that is long enough that it wraps onto a second rendered line inside its own narrow column],
[Right column text that also runs long enough to wrap onto a second line of its own])
"""


# --- items: the whole report, with nothing looked up by text ----------------


@needs_typst
def test_every_item_is_listed_with_its_own_geometry():
    source = starter().replace(
        "- Replace this bullet with an achievement",
        f"- Short one\n- {WRAPPED_BULLET}\n- Another short one",
    )
    items = geometry_report(source)["items"]
    bullets = [item for item in items if item["head"].startswith("\u2022")]
    assert [item["rows"] for item in bullets] == [1, 2, 1]
    # The wrapped one reports the room left on its *last* line, which is what
    # tells the caller how much to cut.
    assert bullets[1]["spare_pt"] > 0
    assert bullets[0]["spare_pt"] > bullets[1]["spare_pt"]
    assert bullets[1]["head"].startswith("\u2022 Built and shipped")


@needs_typst
def test_a_nested_list_is_three_items_not_one():
    # Typst draws a bullet, a triangle and a dash for nesting levels one, two
    # and three, indenting each further. Reading the indent alone made every
    # child a continuation of its parent and merged the list into one item.
    source = starter().replace(
        "- Replace this bullet with an achievement",
        "- Parent bullet, short\n  - Child one\n    - Grandchild one",
    )
    items = geometry_report(source)["items"]
    tail = [item for item in items if item["rows"] or True][-3:]
    assert [item["rows"] for item in tail] == [1, 1, 1]
    assert [item["head"][0] for item in tail] == ["\u2022", "\u2023", "\u2013"]


@needs_typst
def test_a_wrapped_item_after_a_nested_list_still_wraps():
    # The guard against markers must not swallow real continuations: the second
    # line of a wrapped item is indented exactly like a child bullet, and is
    # told apart by having no marker of its own.
    source = starter().replace(
        "- Replace this bullet with an achievement",
        f"- Parent bullet, short\n  - Child one\n- {WRAPPED_BULLET}",
    )
    items = geometry_report(source)["items"]
    assert items[-1]["rows"] == 2
    assert items[-1]["head"].startswith("\u2022 Built and shipped")


@needs_typst
def test_a_continuation_is_found_from_the_line_above_not_its_own_first_letter():
    # A wrapped plain paragraph resumes at the left margin, so the indent says
    # nothing, and every word here is capitalised, so "starts lowercase" says
    # nothing either. What settles it is that the row above ran to the edge.
    para = " ".join(["Offerfy"] * 26) + "."
    items = geometry_report(starter() + "\n\n" + para + "\n")["items"]
    assert items[-1]["rows"] == 2
    assert items[-1]["head"].startswith("Offerfy Offerfy")


@needs_typst
def test_a_cjk_item_that_wraps_is_two_rows():
    # No string is compared any more, so CJK needs no special handling at all.
    bullet = (
        "\u8ca0\u8cac\u5f8c\u7aef\u8207 agent \u5de5\u5177\u8a2d\u8a08\uff0c"
        "\u628a\u6392\u7248\u5224\u65ad\u5f9e\u6a21\u578b\u624b\u4e0a\u62ff\u56de\u4f86"
        "\u4ea4\u7d66\u5e7e\u4f55\u8a08\u7b97\uff0c\u6210\u672c\u5f9e\u6bcf\u6b21\u63a8"
        "\u7406\u964d\u5230\u96f6\uff0c\u4e26\u4e14\u7b54\u6848\u662f\u7cbe\u78ba\u7684"
        "\u800c\u4e0d\u662f\u6a5f\u7387\u7684"
    )
    items = geometry_report(starter() + f"\n- {bullet}\n")["items"]
    assert items[-1]["rows"] == 2


@needs_typst
def test_an_item_split_by_a_page_break_says_which_pages():
    long_para = (
        "This is one long plain paragraph that must span many rendered lines so "
        "that the page break has no choice but to fall somewhere inside it, and "
        "it keeps going with more words about nothing in particular until it is "
        "comfortably taller than the space that remains at the bottom of the "
        "page, and then some more words after that for good measure. "
    )
    items = geometry_report(padded(38, "\n" + long_para * 24 + "\n"))["items"]
    split = [item for item in items if "split_across_pages" in item]
    assert split
    assert split[0]["split_across_pages"][1] == split[0]["split_across_pages"][0] + 1


@needs_typst
def test_side_by_side_text_lists_no_items_instead_of_wrong_ones():
    report = geometry_report(TWO_COLUMN)
    assert report["geometry"] == "unreliable"
    assert "side by side" in report["reason"]
    assert "questions" in report["hint"]
    assert "items" not in report
    # Page-level facts survive; nothing that needs one column does.
    assert report["pages"] == 1
    assert report["findings"] == []


@needs_typst
def test_a_single_column_resume_lists_items_and_is_not_declared_unreliable():
    for source in (starter(), starter() + f"\n- {WRAPPED_BULLET}\n", padded(36)):
        report = geometry_report(source)
        assert "geometry" not in report, source[:40]
        assert report["items"]
