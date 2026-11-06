"""Compile the basic-resume emitter's own output through the real binary.

`tests/test_emitters.py` checks what the emitter wrote; this checks what
the writing actually produces -- gated on `typst_available()`, matching
`tests/test_import_qa.py`'s pattern (that module documents why: a compile
that exits 0 can still have silently lost content, which only rendering
and re-reading the page can catch).
"""

from io import BytesIO

import pytest
from pdfminer.high_level import extract_pages

from app.services.emitters import emitter_for
from app.services.import_qa import verify_render
from app.services.typst_compile import compile_typst, typst_available
from tests.fixtures.golden_content import GOLDEN

pytestmark = pytest.mark.skipif(not typst_available(), reason="typst binary not available")


def test_basic_resume_emitter_output_compiles_in_one_page():
    source = emitter_for("basic-resume", "0.2.9").emit(GOLDEN).source
    pdf_bytes = compile_typst(source, "pdf")
    pages = sum(1 for _ in extract_pages(BytesIO(pdf_bytes)))
    assert pages == 1


def test_basic_resume_emitter_output_keeps_the_content():
    source = emitter_for("basic-resume", "0.2.9").emit(GOLDEN).source
    report = verify_render(source)
    assert report["ok"] is True, report
