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
    assert "targets" not in report
    assert "visual" not in report


@needs_typst
def test_target_reports_a_wrapped_bullet_as_two_rows():
    report = geometry_report(starter() + f"\n- {WRAPPED_BULLET}\n", [WRAPPED_BULLET])
    target = report["targets"][0]
    assert target["found"] is True
    assert target["wrapped"] is True
    assert target["rows"] == 2
    assert target["page"] == 1
    # Three quarters of the second line is used, so trimming a few words is
    # not enough to pull it back onto one.
    assert 0.5 < target["last_row_fill"] < 1.0


@needs_typst
def test_target_reports_room_left_on_a_single_line_bullet():
    report = geometry_report(starter(), ["Replace this bullet with an achievement"])
    target = report["targets"][0]
    assert target["found"] is True
    assert target["wrapped"] is False
    assert target["rows"] == 1
    # Over half the column is still free, which is the answer to "can I say
    # more here without it wrapping".
    assert target["spare_pt"] > 200


@needs_typst
def test_target_accepts_the_bullet_marker_the_source_uses():
    report = geometry_report(starter(), ["- Replace this bullet with an achievement"])
    assert report["targets"][0]["found"] is True


@needs_typst
def test_target_that_is_not_in_the_document_says_so():
    report = geometry_report(starter(), ["nowhere to be found in this document"])
    target = report["targets"][0]
    assert target["found"] is False
    assert "hint" in target
    assert "wrapped" not in target


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

    report = await read_layout_report(
        starter(), {"targets": ["Replace this bullet"], "questions": ["Crowded?"]}
    )

    assert report["pages"] == 1
    assert report["targets"][0]["found"] is True
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


@needs_typst
@pytest.mark.anyio
async def test_read_layout_caps_targets_and_questions(openai_enabled, monkeypatch):
    stub = StubDecisions({"answers": []})
    monkeypatch.setattr("app.services.layout.httpx.AsyncClient", stub)
    monkeypatch.setattr("app.services.layout._render_pages", lambda source: [b"png"])

    report = await read_layout_report(
        starter(),
        {
            "targets": [f"target {i}" for i in range(12)],
            "questions": [f"question {i}?" for i in range(12)],
        },
    )

    assert len(report["targets"]) == 6
    assert len(stub.calls[0]["body"]["questions"]) == 6


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


# --- what the quoted text is allowed to look like ---------------------------


@needs_typst
def test_a_target_quoted_from_the_source_matches_the_typeset_rendering():
    # Typst turns ' into a right single quote on the page. A target copied from
    # the source therefore differs from the rendering by one character, which
    # used to fail the exact match, fall through to the prefix search, and
    # return an answer about whatever fragment happened to fit one row.
    bullet = "Shipped Offerfy's inspector -- it reads the page, not the source"
    report = geometry_report(starter() + f"\n- {bullet}\n", [bullet])
    target = report["targets"][0]
    assert target["found"] is True
    assert "match" not in target  # matched as given, no prefix fallback


@needs_typst
def test_a_quoted_fragment_answers_about_the_whole_bullet():
    # The caller asks "does this bullet fit on one line" but quotes a fragment.
    # Scoped to the fragment, a quote ending inside the first rendered line
    # reported "1 row, not wrapped" about a bullet that plainly wraps.
    report = geometry_report(
        starter() + f"\n- {WRAPPED_BULLET}\n",
        [
            WRAPPED_BULLET,
            "Built and shipped an end-to-end data platform",  # fits row 1 alone
            "eleven minutes",  # sits in the middle of the item
        ],
    )
    answers = report["targets"]
    assert [t["found"] for t in answers] == [True, True, True]
    # Every one of them is an answer about the same item, so they agree.
    assert {t["rows"] for t in answers} == {2}
    assert {t["wrapped"] for t in answers} == {True}
    assert len({t["spare_pt"] for t in answers}) == 1


@needs_typst
def test_a_full_width_row_is_not_reported_as_a_fitting_line():
    # A row that ran to the column edge and broke measures a few points past
    # it, because pdfminer counts the advance of the last glyph before the
    # break. That signature must not read as "fits, with no room left".
    report = geometry_report(
        starter() + f"\n- {WRAPPED_BULLET}\n", ["Built and shipped an end-to-end"]
    )
    target = report["targets"][0]
    assert target["wrapped"] is True
    assert target["spare_pt"] > 0  # room on the item's last row, not -3.3


@needs_typst
def test_a_cjk_target_spanning_a_line_break_is_found():
    # Rows join with a space because a Latin break falls between words. CJK has
    # no word spaces, so that space used to land inside a word and the target
    # could never be found -- and the prefix fallback could not help, because it
    # splits on spaces too.
    bullet = (
        "\u8ca0\u8cac\u5f8c\u7aef\u8207 agent \u5de5\u5177\u8a2d\u8a08\uff0c"
        "\u628a\u6392\u7248\u5224\u65ad\u5f9e\u6a21\u578b\u624b\u4e0a\u62ff\u56de\u4f86"
        "\u4ea4\u7d66\u5e7e\u4f55\u8a08\u7b97\uff0c\u6210\u672c\u5f9e\u6bcf\u6b21\u63a8"
        "\u7406\u964d\u5230\u96f6\uff0c\u4e26\u4e14\u7b54\u6848\u662f\u7cbe\u78ba\u7684"
        "\u800c\u4e0d\u662f\u6a5f\u7387\u7684"
    )
    report = geometry_report(starter() + f"\n- {bullet}\n", [bullet])
    target = report["targets"][0]
    assert target["found"] is True
    assert target["wrapped"] is True
    assert target["rows"] == 2


@needs_typst
def test_a_continuation_is_found_from_the_line_above_not_its_own_first_letter():
    # A wrapped plain paragraph resumes at the left margin, so the indent test
    # cannot see it, and every word here is capitalised, so a "starts
    # lowercase" test cannot either. What settles it is that the row above ran
    # to the right edge of the column.
    para = " ".join(["Offerfy"] * 26) + "."
    source = starter() + "\n\n" + para + "\n"
    whole = geometry_report(source, [para])["targets"][0]
    fragment = geometry_report(source, [" ".join(["Offerfy"] * 5)])["targets"][0]
    assert whole["rows"] == 2 and whole["wrapped"] is True
    # The fragment sits entirely inside the first row; the answer is still
    # about the paragraph.
    assert fragment["rows"] == whole["rows"]
    assert fragment["wrapped"] is True


@needs_typst
def test_an_ambiguous_quote_says_how_many_places_it_matched():
    phrase = "Shipped the layout inspector"
    source = starter() + (
        f"\n- {phrase} early and it fits"
        f"\n- {phrase} again much later with a great deal more text after it so that "
        "this one definitely wraps onto a second rendered line\n"
    )
    target = geometry_report(source, [phrase])["targets"][0]
    assert target["occurrences"] == 2
    assert "first" in target["hint"]


def test_a_full_width_row_means_the_text_continues():
    # The unit behind the fix: a row reaching the column edge is a row whose
    # text did not finish, whatever the next row starts with.
    full = row(1, "ends exactly at the column edge", body_x1=559.0)
    short = row(1, "ends early", body_x1=200.0)
    capitalised = row(1, "Offerfy Offerfy", y0=688.0)
    assert _is_continuation([full, capitalised], 1, 36.0, 10.0) is True
    assert _is_continuation([short, capitalised], 1, 36.0, 10.0) is False
