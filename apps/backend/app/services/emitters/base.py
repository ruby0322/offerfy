"""The emitter contract -- `EmitResult` and `Emitter` -- kept in its own
module, separate from the registry (`__init__.py`).

`__init__.py` has two different jobs: define what an emitter is, and
import every emitter module to build the registry of them. Doing both in
one module is what made the registry's own circularity self-inflicted --
each emitter module needs the contract, and `__init__.py` needs every
emitter module, so whichever one imported the other first would find it
only half-built. Splitting the contract out breaks that: this module
imports nothing from any emitter module, ever (only `resume_content`), so
every emitter module can import `EmitResult`/`Emitter` from here with a
plain top-level import, and `__init__.py` can import every emitter module
the same way. The dependency only ever points one direction: `__init__.py`
-> an emitter module -> `base.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.services.resume_content import ResumeContent


@dataclass(frozen=True)
class EmitResult:
    """What one `emit()` call produced.

    `dropped` names content the target package has nowhere to put at
    all; `degraded` names content the package can hold but only in a
    lesser form (e.g. a date range collapsed to a single string). Both
    are empty for a package that can hold everything the IR has --
    `basic_resume`'s emitter is that case, since it is also the starter's
    own template.

    Entries in either tuple are **stable dot-path field identifiers into
    `ResumeContent`** -- e.g. `"summary"`, `"contact.website"`,
    `"projects[].dates"` -- never free-form English. This is a
    three-locale product whose consumer is a UI notice, not a log line:
    only the consumer should own the wording a person reads, and it can
    only do that from an identifier stable enough to look up, not from a
    sentence an emitter made up on the spot. An emitter reports the path;
    mapping a path to copy is the caller's job, not this one's.
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
