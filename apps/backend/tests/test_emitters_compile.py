"""Compile every registered emitter's own output through the real binary.

`tests/test_emitters.py` checks what an emitter wrote; this checks what
the writing actually produces -- gated on `typst_available()`, matching
`tests/test_import_qa.py`'s pattern (that module documents why: a compile
that exits 0 can still have silently lost content, which only rendering
and re-reading the page can catch).

Parametrized over `deterministic_pairs()` rather than naming
`("basic-resume", "0.2.9")` directly: this is the one test a later
emitter (Tasks 9-11) must not be able to forget to add itself to, and the
registry already enumerates exactly what to iterate.
"""

from io import BytesIO

import pytest
from pdfminer.high_level import extract_pages

from app.services.ats import extract_pdf_text
from app.services.emitters import deterministic_pairs, emitter_for
from app.services.import_qa import verify_render
from app.services.typst_compile import compile_typst, typst_available
from tests.fixtures.golden_content import GOLDEN

pytestmark = pytest.mark.skipif(not typst_available(), reason="typst binary not available")

PAIRS = sorted(deterministic_pairs())


@pytest.mark.parametrize("pair", PAIRS)
def test_registered_emitter_output_compiles_in_one_page(pair):
    name, version = pair
    source = emitter_for(name, version).emit(GOLDEN).source
    pdf_bytes = compile_typst(source, "pdf")
    pages = sum(1 for _ in extract_pages(BytesIO(pdf_bytes)))
    assert pages == 1


@pytest.mark.parametrize("pair", PAIRS)
def test_registered_emitter_output_keeps_the_content(pair):
    name, version = pair
    source = emitter_for(name, version).emit(GOLDEN).source
    report = verify_render(source)
    assert report["ok"] is True, report


@pytest.mark.parametrize("pair", PAIRS)
def test_registered_emitter_output_preserves_a_literal_backslash(pair):
    # Critical 1's end-to-end proof: `verify_render` above normalises both
    # sides of its comparison, which strips every backslash on both sides
    # (see import_qa._normalise and its own module docstring) -- it
    # cannot see a swallowed backslash by construction, coverage-threshold
    # or not. This reads the actual rendered PDF text back and checks the
    # literal, unescaped substring from GOLDEN's own config-path bullet
    # survived character for character.
    name, version = pair
    source = emitter_for(name, version).emit(GOLDEN).source
    pdf_bytes = compile_typst(source, "pdf")
    pdf_text = extract_pdf_text(pdf_bytes)
    assert "C:\\configs\\prod" in pdf_text
