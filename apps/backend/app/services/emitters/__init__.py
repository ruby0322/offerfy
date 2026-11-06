"""The emitter registry: which (package, version) pair has a function
that can turn a `ResumeContent` snapshot into that package's own Typst
source, deterministically.

A template switch today means asking a model to rewrite the whole document
and hoping it keeps everything -- slow, and free to drop or invent content
on the way (see `app.services.resume_content`'s module docstring). An
emitter removes the model from that path entirely for the handful of
packages one exists for: `emit` is a pure function from structured content
to a complete, compiling Typst file, so the same content always produces
the same document.

The contract itself (`EmitResult`, `Emitter`) lives in `base.py`, not
here -- this module's own job is building the registry, which means
importing every emitter module, and a module that both defines the thing
emitter modules depend on *and* imports them would be circular the moment
an emitter module needed that definition back. See `base.py`'s docstring.

Pinned to an exact `(name, version)` pair, never just a name: a package's
API can change between versions (`ats-friendly-resume` renames two of
`basic-resume`'s own parameters -- a later task's whole point), so an
emitter written against one version is never assumed valid for another.
`emitter_for` enforces that exactly -- a floated or bumped version simply
has no emitter, and the model path (`templates.apply_prompt`) remains
correct, if slower, for it.

`app.jobs.survey_templates` reads `deterministic_pairs()` in place of its
own `EMITTER_PINS` placeholder, so a pair only needs declaring once, here.
"""

from __future__ import annotations

from app.services.emitters import acorn_resume, ats_friendly_resume, basic_resume
from app.services.emitters.base import EmitResult, Emitter

__all__ = ["EmitResult", "Emitter", "deterministic_pairs", "emitter_for"]

_EMITTERS: dict[tuple[str, str], Emitter] = {
    (basic_resume.name, basic_resume.version): basic_resume,
    (ats_friendly_resume.name, ats_friendly_resume.version): ats_friendly_resume,
    (acorn_resume.name, acorn_resume.version): acorn_resume,
}


def emitter_for(name: str, version: str) -> Emitter | None:
    """The emitter for this exact (name, version) pair, or `None` -- a
    package with no emitter, or one pinned to a different version, falls
    back to the model path. See the module docstring for why the match is
    exact rather than by name alone."""
    return _EMITTERS.get((name, version))


def deterministic_pairs() -> frozenset[tuple[str, str]]:
    """Every (name, version) pair this registry can emit deterministically
    -- what `templates.list_templates()`'s `deterministic` field reports,
    and what `survey_templates.survey_pairs()` pins a survey row to."""
    return frozenset(_EMITTERS)
