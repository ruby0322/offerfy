"""Pure-Python checks for the emitter registry and the basic-resume emitter.

No typst binary needed here -- these only check what the emitter *wrote*,
not whether it compiles. `tests/test_emitters_compile.py` is the gated
module that runs the emitted source through the real compiler.
"""

from app.services.emitters import emitter_for
from tests.fixtures.golden_content import GOLDEN, MINIMAL


def test_emitter_for_matches_name_and_version_exactly():
    assert emitter_for("basic-resume", "0.2.9") is not None
    assert emitter_for("basic-resume", "0.3.0") is None  # a floated version falls back


def test_basic_resume_emitter_declares_no_losses():
    result = emitter_for("basic-resume", "0.2.9").emit(GOLDEN)
    assert result.dropped == () and result.degraded == ()


def test_basic_resume_emitter_pins_the_import_and_the_font():
    src = emitter_for("basic-resume", "0.2.9").emit(GOLDEN).source
    assert src.startswith('#import "@preview/basic-resume:0.2.9"')
    assert 'font: "' in src


def test_emitter_escapes_content_that_would_break_typst():
    hostile = GOLDEN.model_copy(update={"name": 'A "B" \\ C'})
    assert '\\"B\\"' in emitter_for("basic-resume", "0.2.9").emit(hostile).source


def test_emitter_omits_a_section_with_no_entries():
    # Review Focus 4: the untouched starter has sections we must not emit empty
    src = emitter_for("basic-resume", "0.2.9").emit(MINIMAL).source
    assert "== Projects" not in src and "#project(" not in src


def test_basic_resume_emitter_renders_the_summary():
    # basic-resume has no dedicated summary component -- this is the one
    # field the package only holds via a plain `== heading` + paragraph,
    # and the only way `test_basic_resume_emitter_declares_no_losses` can
    # be an honest claim about it is a fixture that actually sets it.
    src = emitter_for("basic-resume", "0.2.9").emit(GOLDEN).source
    assert "== Summary" in src
    assert GOLDEN.summary in src


def test_basic_resume_emitter_maps_website_to_personal_site():
    # resume.with() has no `website` argument -- Contact.website has
    # nowhere to go but the package's own `personal-site`.
    src = emitter_for("basic-resume", "0.2.9").emit(GOLDEN).source
    assert f'personal-site: "{GOLDEN.contact.website}"' in src
