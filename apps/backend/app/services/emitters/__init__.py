"""The emitter protocol and the registry pinning each (package, version)
pair to the function that can turn a `ResumeContent` snapshot into that
package's own Typst source.

A template switch today means asking a model to rewrite the whole document
and hoping it keeps everything -- slow, and free to drop or invent content
on the way (see `app.services.resume_content`'s module docstring). An
emitter removes the model from that path entirely for the handful of
packages one exists for: `emit` is a pure function from structured content
to a complete, compiling Typst file, so the same content always produces
the same document. `Emitter` is a `Protocol` rather than a base class
because Tasks 9-11 each add one more module shaped like this, not a
subclass of anything -- a module with `name`, `version` and `emit` already
satisfies it structurally, so the module itself is the registry's value
(see `basic_resume.py`). Each emitter module imports `EmitResult` lazily,
inside its own `emit()`, rather than at module level -- this `__init__`
imports every emitter module to build the registry below, so a module-level
import the other way would be circular.

Pinned to an exact `(name, version)` pair, never just a name: a package's
API can change between versions (`ats-friendly-resume` renames two of
`basic-resume`'s own parameters -- a later task's whole point), so an
emitter written against one version is never assumed valid for another.
`emitter_for` enforces that exactly -- a floated or bumped version simply
has no emitter, and the model path (`templates.apply_prompt`) remains
correct, if slower, for it.

An emitter also sets its target's font explicitly rather than inheriting
the package's own default: `app.services.starter.PLACEHOLDERS` already
made this call for the starter template -- a package's un-set default can
be a face this host does not ship (`acorn-resume` asks for Calibri) -- and
the emitter path must not reopen that question per package.

`app.jobs.survey_templates` reads `deterministic_pairs()` in place of its
own `EMITTER_PINS` placeholder, so a pair only needs declaring once, here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.services.emitters import basic_resume
from app.services.resume_content import ResumeContent


@dataclass(frozen=True)
class EmitResult:
    """What one `emit()` call produced.

    `dropped` names content the target package has nowhere to put at all;
    `degraded` names content the package can hold but only in a lesser
    form (e.g. a date range collapsed to a single string). Both are empty
    for a package that can hold everything the IR has -- the round-trip
    case `basic_resume`'s emitter is, since it is also the starter's own
    template.
    """

    source: str
    dropped: tuple[str, ...] = ()
    degraded: tuple[str, ...] = ()


class Emitter(Protocol):
    """The shape a per-package emitter module must have. A plain module
    satisfies this structurally -- see `basic_resume.py` for the pattern
    Tasks 9-11 repeat: module-level `name`, `version`, and `emit`."""

    name: str
    version: str

    def emit(self, content: ResumeContent) -> EmitResult: ...


_EMITTERS: dict[tuple[str, str], Emitter] = {
    (basic_resume.name, basic_resume.version): basic_resume,
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
