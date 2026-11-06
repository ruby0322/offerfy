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
    # 0.2.10 actually exists in the live index -- the real regression the
    # exact-match rule exists to prevent, not just a hypothetical version.
    assert emitter_for("basic-resume", "0.2.10") is None


def test_basic_resume_emitter_declares_no_losses():
    result = emitter_for("basic-resume", "0.2.9").emit(GOLDEN)
    assert result.dropped == () and result.degraded == ()


def test_basic_resume_emitter_pins_the_import_and_the_font():
    src = emitter_for("basic-resume", "0.2.9").emit(GOLDEN).source
    assert src.startswith('#import "@preview/basic-resume:0.2.9"')
    # The font argument is a (latin, cjk) fallback array, not a single
    # conditional string -- see emitters._common.FALLBACK_FONTS -- so this
    # checks for the array literal and both faces by name rather than for
    # a bare quoted string.
    assert "font: (" in src
    assert "New Computer Modern" in src
    assert "Noto Serif CJK TC" in src


def test_emitter_escapes_content_that_would_break_typst():
    hostile = GOLDEN.model_copy(update={"name": 'A "B" \\ C'})
    assert '\\"B\\"' in emitter_for("basic-resume", "0.2.9").emit(hostile).source


def test_emitter_escapes_a_dollar_and_at_sign_inside_a_quoted_markup_phrase():
    # Critical 1: a `"` in markup prose is not a Typst string literal --
    # `markup_value` must escape `$`/`@` that follow an odd-numbered quote
    # too, unlike `escape_typst_markup` (written for already-written
    # source, where copying a quoted span verbatim is correct). `/` is
    # also escaped unconditionally (see markup_value's docstring), so
    # the `/` in "12M/yr" is escaped too.
    src = emitter_for("basic-resume", "0.2.9").emit(GOLDEN).source
    assert r'"LegacyPay \@ \$12M\/yr"' in src


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


# --- ats-friendly-resume: the false-friend fork -----------------------------
#
# ats-friendly-resume forked basic-resume and renamed two of its parameters
# (work's `title:` -> `role:`, and the date helper `dates-helper` ->
# `dates-util`) while keeping everything else -- see
# `app/services/emitters/ats_friendly_resume.py`'s module docstring for the
# full field-by-field derivation this guards.


def test_ats_friendly_emitter_registered_exactly():
    assert emitter_for("ats-friendly-resume", "0.1.1") is not None
    assert emitter_for("ats-friendly-resume", "0.2.0") is None


def test_ats_friendly_uses_dates_util_not_dates_helper():
    src = emitter_for("ats-friendly-resume", "0.1.1").emit(GOLDEN).source
    assert "dates-util(" in src and "dates-helper(" not in src


def test_ats_friendly_passes_role_not_title():
    src = emitter_for("ats-friendly-resume", "0.1.1").emit(GOLDEN).source
    assert "role:" in src and "title:" not in src


def test_ats_friendly_declares_what_its_project_slot_cannot_hold():
    result = emitter_for("ats-friendly-resume", "0.1.1").emit(GOLDEN)
    # project() has no `role` parameter at all in this package (unlike
    # basic-resume's own project() component) -- see resume.typ. Every
    # other ResumeContent field (summary, extras, skills included) has
    # somewhere to go, so this is the only loss.
    assert result.dropped == ("projects[].role",)
    assert result.degraded == ()


def test_ats_friendly_emitter_pins_the_import_and_the_font():
    src = emitter_for("ats-friendly-resume", "0.1.1").emit(GOLDEN).source
    assert src.startswith('#import "@preview/ats-friendly-resume:0.1.1"')
    assert "font: (" in src
    assert "New Computer Modern" in src
    assert "Noto Serif CJK TC" in src


def test_ats_friendly_emitter_escapes_content_that_would_break_typst():
    hostile = GOLDEN.model_copy(update={"name": 'A "B" \\ C'})
    assert '\\"B\\"' in emitter_for("ats-friendly-resume", "0.1.1").emit(hostile).source


def test_ats_friendly_emitter_omits_a_section_with_no_entries():
    src = emitter_for("ats-friendly-resume", "0.1.1").emit(MINIMAL).source
    assert "== Projects" not in src and "#project(" not in src
    assert "== Experience" not in src and "#work(" not in src


def test_ats_friendly_emitter_renders_the_summary():
    # No dedicated summary component in this package either -- same
    # bare-paragraph-under-a-heading technique as basic-resume.
    src = emitter_for("ats-friendly-resume", "0.1.1").emit(GOLDEN).source
    assert "== Summary" in src
    assert GOLDEN.summary in src


def test_ats_friendly_emitter_maps_website_to_portfolio():
    # resume() has no `website` argument -- it has `portfolio` instead
    # (ats-friendly-resume's own renamed slot for the same idea; not one
    # of the two renames the trap is about, but still not a `website:`
    # argument, so Contact.website still needs a destination).
    src = emitter_for("ats-friendly-resume", "0.1.1").emit(GOLDEN).source
    assert f'portfolio: "{GOLDEN.contact.website}"' in src


def test_ats_friendly_renders_a_projects_url_when_dates_are_missing():
    # project()'s own layout (resume.typ) shows `url:` only inside
    # `if url != "" and dates != "" [...]` -- a project with a URL and no
    # dates (GOLDEN.projects[1], see golden_content.py) would otherwise
    # pass `url:` as an argument that the package's own layout then never
    # displays. This checks the fallback link line this emitter adds for
    # exactly that case is present in the source;
    # test_emitters_compile.py's
    # test_registered_emitter_renders_a_dateless_projects_url is the
    # end-to-end proof it survives the real render.
    dateless = GOLDEN.projects[1]
    assert dateless.url and not (dateless.start or dateless.end)
    src = emitter_for("ats-friendly-resume", "0.1.1").emit(GOLDEN).source
    assert f'link("https://" + "{dateless.url}")[#"{dateless.url}"]' in src


def test_ats_friendly_declares_no_project_role_loss_when_none_is_set():
    # emit() must derive `dropped` from the content it was actually
    # handed, not return a constant -- a resume with no project `role`
    # set has nothing to lose there, and GOLDEN alone (where
    # projects[0].role is always set) could never catch a hardcoded
    # tuple that claimed this loss unconditionally.
    roleless = GOLDEN.model_copy(
        update={"projects": [p.model_copy(update={"role": None}) for p in GOLDEN.projects]}
    )
    result = emitter_for("ats-friendly-resume", "0.1.1").emit(roleless)
    assert result.dropped == ()
