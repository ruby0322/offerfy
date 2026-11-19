"""The synthetic Typst fixtures every structured-editing task is tested against.

All names, contacts and employers in them are invented. This file only proves
they compile and that each one still contains the construct it exists to force.
"""

import re
from pathlib import Path

import pytest

from app.services.typst_compile import compile_typst, typst_available

pytestmark = pytest.mark.skipif(not typst_available(), reason="typst binary not available")

FIXTURES = Path(__file__).parent / "fixtures" / "typst"
NAMES = ["synthetic-en", "synthetic-zh", "synthetic-marks",
         "synthetic-tier-b", "synthetic-tier-c", "synthetic-long"]


@pytest.mark.parametrize("name", NAMES)
def test_fixture_compiles(name):
    pdf = compile_typst((FIXTURES / f"{name}.typ").read_text(encoding="utf-8"), "pdf")
    assert pdf.startswith(b"%PDF")


def test_marks_fixture_covers_every_inline_case():
    src = (FIXTURES / "synthetic-marks.typ").read_text(encoding="utf-8")
    assert re.search(r"^- .*\n// - .*\n- ", src, re.M)  # comment between two bullets
    assert re.search(r"\*[^*\n]*#link\(\"[^\"]+\"\)\[[^\]]+\][^*\n]*\*", src)  # link in bold
    assert re.search(r"#link\(\"[^\"]+\"\)\[[^\]]+\]", src)
    assert re.search(r"\[nested .*\]", src)  # nested content block
    heading = r"^== .*\*[^*\n]+\*.*#link\(\"[^\"]+\"\)\[[^\]]+\]"
    assert re.search(heading, src, re.M)  # bold and a link inside a section heading
    for escape in ["\\&", "\\@", "\\*", "\\~", "\\$"]:
        assert escape in src


def test_en_fixture_has_a_dead_let_and_a_used_one():
    src = (FIXTURES / "synthetic-en.typ").read_text(encoding="utf-8")
    assert "author: name," in src          # used through one hop
    assert "#let phone =" in src
    assert src.count("phone") == 1         # defined, never referenced


def test_tier_b_fixture_has_a_grid_and_an_unparsed_call_inside_a_bullet():
    src = (FIXTURES / "synthetic-tier-b.typ").read_text(encoding="utf-8")
    assert "#grid(" in src
    assert re.search(r"^- .*#footnote\[", src, re.M)  # opaque run inside a live bullet


def test_tier_c_fixture_is_data_driven_with_nothing_a_form_could_edit():
    src = (FIXTURES / "synthetic-tier-c.typ").read_text(encoding="utf-8")
    assert "#let data = (" in src and "#for " in src
    assert not re.search(r"#(work|edu|project)\(", src)
    assert not re.search(r"^== ", src, re.M)
    assert not re.search(r"^- ", src, re.M)
