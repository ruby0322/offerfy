"""An item inserted into an empty section of an emitter's own document must compile."""

import hashlib

import pytest

from app.schemas_editing import IntentIn
from app.services.emitters import deterministic_pairs, emitter_for
from app.services.typst_compile import compile_typst, typst_available
from app.services.typst_intents import PLACEHOLDER, apply_intent
from app.services.typst_projection import parse_projection
from tests.fixtures.golden_content import GOLDEN

pytestmark = pytest.mark.skipif(not typst_available(), reason="typst binary not available")

PAIRS = sorted(deterministic_pairs())
assert PAIRS, "deterministic_pairs() is empty -- every gate below would collect nothing"


def _section(p, title):
    return next((s for s in p.sections
                 if s.title_unit and p.units[s.title_unit].value == title), None)


@pytest.mark.parametrize("pair", PAIRS)
@pytest.mark.parametrize("title", ["Experience", "Education", "Projects", "Skills"])
def test_first_item_compiles_in_every_emitter(pair, title):
    source = emitter_for(*pair).emit(GOLDEN).source
    sec = _section(parse_projection(source), title)
    if sec is None:
        pytest.skip(f"{pair[0]} writes no `== {title}` section")
    emptied = source[:sec.body_start] + "\n" + source[sec.outer[1]:]
    p = parse_projection(emptied)
    assert not _section(p, title).blocks
    digest = hashlib.sha256(emptied.encode("utf-8")).hexdigest()
    out = apply_intent(emptied, p, IntentIn(
        hash=digest, kind="insert_item", target=_section(p, title).id))
    assert out != emptied
    # every placeholder is in a unit the person can edit, not merely present
    added = parse_projection(out)
    reachable = sum(u.value.count(PLACEHOLDER) for u in added.units.values() if u.editable)
    assert reachable == out.count(PLACEHOLDER) >= 1, (pair[0], title)
    assert compile_typst(out, "pdf").startswith(b"%PDF")
