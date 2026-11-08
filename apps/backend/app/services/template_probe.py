"""Probe a Typst template so the picker can know, ahead of time, whether it
actually compiles.

Offerfy compiles a résumé as a single file in an empty temp directory, so a
template that reads a sidecar data file via `yaml("x.yaml")` / `toml(...)` /
`json(...)` can never compile there -- nothing may reference a file beside
it. Rewriting that call into the Typst literal for the file's own contents
removes the sidecar dependency entirely, recovering templates that would
otherwise be unusable.

The other way a template silently fails is quieter: a font the package
expects (an icon face, a particular sans/serif) is not installed in this
environment. Typst does not error on that -- it warns and substitutes a
fallback, so the résumé still "compiles" but ships tofu or the wrong face.
`probe_template` surfaces every such warning so a later gate (Task 3) can
decide, per font, whether the pool could ship it or the template should be
dropped. Deciding that gate is out of scope here: this module only reports
the facts a template's vendor example produced.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

import yaml
from fastapi import HTTPException
from pdfminer.high_level import extract_pages

from app.services.templates import PACKAGE_NAME_RE, template_example_source
from app.services.typst_compile import compile_typst_with_stderr
from app.services.typst_escape import typst_value

_LOADER_CALL = re.compile(r'\b(yaml|toml|json)\(\s*"([^"]+)"\s*\)')
_FONT_WARNING = re.compile(r"unknown font family: (.+)")

# The measured set of free families the template pool asks for: fonts a
# support survey could ship by installing them, as opposed to a proprietary
# face (Calibri, Helvetica Neue) nobody can legally bundle. Anything a
# template warns about that is not in this set counts as unavailable --
# see `unavailable_fonts`.
INSTALLABLE_FONTS: frozenset[str] = frozenset(
    {
        "font awesome 6 brands",
        "font awesome 6 free",
        "font awesome 6 free solid",
        "font awesome 7 brands",
        "font awesome 7 free",
        "font awesome 7 free solid",
        "source sans 3",
        "source sans pro",
        "source serif 4",
        "source han sans",
        "source han serif",
        "pt serif",
        "inria sans",
        "fira sans",
        "eb garamond",
        "libre baskerville",
        "inter",
        "roboto",
        "open sans",
        "montserrat",
        "cabin",
        "alegreya",
        "cantarell",
        "xcharter",
        "linux libertine",
        "linux biolinum",
        "academicons",
        "lxgw wenkai gb",
        "harano aji gothic",
        "harano aji mincho",
    }
)

_LOADERS: dict[str, Any] = {
    "yaml": yaml.safe_load,
    "toml": tomllib.loads,
    "json": json.loads,
}


def inline_data_files(source: str, base: Path) -> tuple[str, tuple[str, ...]]:
    """Rewrite every `yaml(...)` / `toml(...)` / `json(...)` call whose
    argument is a literal path into the Typst literal for that file's
    contents, read relative to `base`. Returns the rewritten source and the
    relative paths it inlined.

    A call whose argument isn't a literal string -- e.g. `json(sys.inputs
    .at("CV", default: "cv.json"))` computing the path at compile time, as
    polycv and porygon do -- never matches the regex, so it is left alone by
    design: there is no path to resolve ahead of compilation. A literal path
    that is missing or fails to parse is also left untouched rather than
    raising, so a survey over many templates can keep going.
    """
    inlined: list[str] = []

    def replace(match: re.Match[str]) -> str:
        loader, rel_path = match.group(1), match.group(2)
        path = base / rel_path
        if not path.is_file():
            return match.group(0)
        try:
            data = _LOADERS[loader](path.read_text(encoding="utf-8"))
        except Exception:
            return match.group(0)
        inlined.append(rel_path)
        return typst_value(data)

    out = _LOADER_CALL.sub(replace, source)
    return out, tuple(inlined)


def font_warnings(stderr: str) -> tuple[str, ...]:
    """Every `unknown font family: <name>` warning the compiler printed, in
    the order it printed them. Typst always lowercases the family name in
    this warning regardless of how the source cased it, so the result is
    already in the same case `INSTALLABLE_FONTS` uses."""
    return tuple(match.group(1).strip() for match in _FONT_WARNING.finditer(stderr or ""))


def unavailable_fonts(warnings: tuple[str, ...]) -> tuple[str, ...]:
    """The subset of `warnings` a support survey could not fix by installing
    a font -- i.e. not in `INSTALLABLE_FONTS`."""
    return tuple(font for font in warnings if font not in INSTALLABLE_FONTS)


@dataclass(frozen=True)
class SupportRow:
    """What a template probe found. `ok` means only "the vendor example
    compiled" -- it says nothing about fonts, page count or a denylist.
    Those gates belong to the survey job (Task 3), which reads this row's
    `font_warnings` and `pages` to apply them; re-deciding them here would
    let the two disagree.
    """

    name: str
    version: str
    ok: bool
    pages: int
    needs_inline_data: bool
    font_warnings: tuple[str, ...]
    installable_fonts: tuple[str, ...]
    reason: str


def _blank_row(name: str, version: str, reason: str, *, needs_inline_data: bool = False) -> SupportRow:
    return SupportRow(
        name=name,
        version=version,
        ok=False,
        pages=0,
        needs_inline_data=needs_inline_data,
        font_warnings=(),
        installable_fonts=(),
        reason=reason,
    )


def _template_dir(name: str, version: str, root: Path) -> Path:
    """The directory the vendor example lives in, so a sidecar data file's
    relative path can be resolved against it. Mirrors
    `templates.template_example_source`'s own reading of `typst.toml`'s
    `[template].path` (default `"template"`) -- most packages use the
    default, but a few (grotesk-cv, lavandula, signal-resume, ...) do not.
    """
    dest = root / "preview" / name / version
    toml_path = dest / "typst.toml"
    rel_dir = "template"
    if toml_path.is_file():
        try:
            data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        template = data.get("template") if isinstance(data, dict) else None
        if isinstance(template, dict):
            rel_dir = str(template.get("path") or "template")
    return dest / rel_dir


def probe_template(name: str, version: str, *, root: Path) -> SupportRow:
    """Compile a template's own vendor example as Offerfy would -- a single
    file, no sidecar data -- and report what happened.

    A "file not found" failure naming a sidecar data file gets one retry
    through `inline_data_files`, which is exactly the recovery Offerfy's
    real compile path would also need. Anything else that fails to compile
    is reported as failed outright: there is no second recovery to try.
    """
    if not PACKAGE_NAME_RE.fullmatch(name):
        return _blank_row(name, version, "invalid name")

    source = template_example_source(name, version, root=root)
    if source is None:
        return _blank_row(name, version, "no example")

    needs_inline_data = False
    try:
        pdf_bytes, stderr = compile_typst_with_stderr(source, "pdf")
    except HTTPException as exc:
        if "file not found" not in str(exc.detail):
            return _blank_row(name, version, "compile failed")
        retried_source, inlined = inline_data_files(source, _template_dir(name, version, root))
        if not inlined:
            return _blank_row(name, version, "compile failed")
        needs_inline_data = True
        try:
            pdf_bytes, stderr = compile_typst_with_stderr(retried_source, "pdf")
        except HTTPException:
            return _blank_row(name, version, "compile failed", needs_inline_data=True)

    warnings = font_warnings(stderr)
    installable = tuple(font for font in warnings if font in INSTALLABLE_FONTS)
    pages = sum(1 for _ in extract_pages(BytesIO(pdf_bytes)))
    return SupportRow(
        name=name,
        version=version,
        ok=True,
        pages=pages,
        needs_inline_data=needs_inline_data,
        font_warnings=warnings,
        installable_fonts=installable,
        reason="ok",
    )
