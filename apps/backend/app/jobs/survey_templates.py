"""Survey Typst Universe's `cv` category and commit the subset the picker can
actually offer.

`template_probe.probe_template` reports only whether a template's own vendor
example compiled -- it deliberately stops there (see its module docstring).
That leaves four gates to this job: a font the probe environment doesn't
have installed (any warning, not just an uninstallable one -- the picker
can't ship a face it never installs), a résumé that runs past two pages, a
thumbnail the registry doesn't actually serve, and a denylist for `cv`
packages that are not résumés at all (cover letters, Japanese rirekisho
forms, an icon library).

Which (name, version) gets probed is not simply "whatever `latest_cv_
packages()` says is newest". The starter and system prompt pin a handful of
packages to a specific version (`app/services/starter._render`,
`app/services/llm.SYSTEM_PROMPT`), and the emitter registry
(`app.services.emitters.deterministic_pairs()`) pins whichever versions it
has an emitter for. A survey built only from the index's latest would mark
the pinned version unsupported the moment the index moves on, and Task 4's
switch endpoint would then reject the very template Offerfy ships by
default. So this job surveys the index's latest per package, unioned with
every pinned pair, and offers the pinned version when a package has both --
one row per package either way.

Font gate 2 below is measured differently once a pair has an emitter: an
emitter sets its target's font explicitly (see
`app/services/emitters/__init__.py`'s module docstring), so the question
worth asking is whether *that* output warns, not whether the vendor
example's un-set default does. `_emitter_font_warnings` compiles the
emitter's own output against a sample built right here -- never against
`tests/fixtures/golden_content.GOLDEN`, since an app module must not import
a `tests/` fixture -- and only the content's *shape* matters for a font
warning (it comes from the explicit `font:` argument and the package's own
icon usage, never from the text), so this sample does not need to be
hostile the way that fixture is.

Usage (compose):
  docker compose run --rm backend python -m app.jobs.survey_templates --out data/template_support.json

Run on a host with the `typst` binary and network access -- the generated
file should be committed as-is, never hand-edited.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from app.services.emitters import deterministic_pairs, emitter_for
from app.services.resume_content import (
    Contact,
    EducationEntry,
    ProjectEntry,
    ResumeContent,
    SkillGroup,
    WorkEntry,
)
from app.services.template_probe import (
    INSTALLABLE_FONTS,
    font_warnings as compiled_font_warnings,
    probe_template,
    unavailable_fonts,
)
from app.services.templates import (
    SUPPORT_PATH,
    fetch_index,
    fetch_registry_thumbnail,
    latest_cv_packages,
    package_root,
    prefetch_one,
    thumbnail_relpath,
)
from app.services.typst_compile import compile_typst_with_stderr, typst_binary

# `cv`-category packages that are not résumés at all -- two cover letters,
# three Japanese rirekisho forms, one icon library -- so no amount of
# compiling cleanly makes them something the picker should offer.
DENYLIST: dict[str, str] = {
    "exp-cover-letter": "cover letter, not a resume",
    "modernpro-coverletter": "cover letter, not a resume",
    "hadronic-rirekisho": "Japanese rirekisho form, not a resume",
    "rirekisho-ofa": "Japanese rirekisho form, not a resume",
    "shokumu-keirekisho-ofa": "Japanese rirekisho form, not a resume",
    "scienceicons": "icon library, not a resume template",
}


def survey_pairs(index: list[dict[str, Any]]) -> list[tuple[str, str, dict[str, Any]]]:
    """One (name, version, index_row) per package to survey: the latest
    `cv` row per package, with any name the emitter registry pins pointed
    at its pinned version instead. The index row still carries the declared
    thumbnail path used by `_thumbnail_ok` -- which is normally valid for a
    pinned version a patch or two behind the index's latest too.
    """
    latest = {str(row.get("name") or ""): row for row in latest_cv_packages(index)}
    pins = dict(deterministic_pairs())
    names = sorted(set(latest) | set(pins))
    pairs: list[tuple[str, str, dict[str, Any]]] = []
    for name in names:
        row = latest.get(name, {})
        version = pins.get(name) or str(row.get("version") or "")
        if version:
            pairs.append((name, version, row))
    return pairs


def _emitter_sample_content() -> ResumeContent:
    """A representative snapshot for probing an emitter's own output below
    -- not a stand-in for `tests/fixtures/golden_content.GOLDEN`, which
    this app module must not import. It only needs to exercise every
    section an emitter can produce (contact fields, one of each entry
    type, a skill group); see the module docstring for why the words in it
    don't matter for a font warning.
    """
    return ResumeContent(
        name="Sample Name",
        contact=Contact(
            email="sample@example.com",
            phone="+1 555 0100",
            location="City, Country",
            github="github.com/sample",
            linkedin="linkedin.com/in/sample",
            website="sample.dev",
        ),
        summary="A short summary line.",
        education=[
            EducationEntry(
                institution="Sample University",
                degree="B.S., Sample Field",
                location="City, Country",
                start="2018-09",
                end="2022-06",
                bullets=["Sample coursework bullet"],
            )
        ],
        work=[
            WorkEntry(
                title="Sample Title",
                company="Sample Company",
                location="City, Country",
                start="2022-07",
                end="Present",
                bullets=["Sample achievement bullet"],
            )
        ],
        projects=[
            ProjectEntry(
                name="Sample Project",
                role="Contributor",
                url="github.com/sample/project",
                start="2021-01",
                end="2021-12",
                bullets=["Sample project bullet"],
            )
        ],
        skills=[SkillGroup(category="Sample Skills", items=["Alpha", "Beta"])],
    )


def _emitter_font_warnings(name: str, version: str) -> tuple[str, ...] | None:
    """Font warnings from compiling the emitter's own output for this pair,
    or `None` when there is no emitter -- telling `survey_row` to keep
    using the vendor example's own warnings instead. See the module
    docstring for why gate 2 must ask this question once an emitter
    exists, rather than the vendor example's.

    A compile failure here counts as a clean pass on fonts specifically
    (no warning text to report) -- gate 1 (`probe.ok`) is what already
    catches a template that cannot compile at all; this function answers
    only the font question.
    """
    emitter = emitter_for(name, version)
    if emitter is None:
        return None
    source = emitter.emit(_emitter_sample_content()).source
    try:
        _pdf_bytes, stderr = compile_typst_with_stderr(source, "pdf")
    except HTTPException:
        return ()
    return compiled_font_warnings(stderr)


def _declared_thumbnail(name: str, version: str, row: dict[str, Any], *, root: Path) -> str | None:
    """The thumbnail path this package declares, preferring the live
    index row -- which also covers a pinned version a patch or two behind
    the index's latest, since the declared path is normally unchanged
    across patches -- and falling back to the pinned version's own
    extracted typst.toml, already on disk after `prefetch_one`, when the
    index has no row for this name at all.

    That fallback is what keeps an emitter-pinned package's thumbnail gate
    from failing merely because the package dropped out of
    `latest_cv_packages()` -- a stale or changed `cv` category tag, a
    rename -- which is exactly the kind of index drift the pin exists to
    survive in the first place (see the module docstring and
    `survey_pairs`). Without it, `row` would be `{}`, `thumbnail_relpath`
    would return `None`, and the gate would fail with "no thumbnail" even
    for a package whose own typst.toml declares a perfectly good one.
    """
    declared = thumbnail_relpath(row)
    if declared:
        return declared
    toml_path = root / "preview" / name / version / "typst.toml"
    if not toml_path.is_file():
        return None
    try:
        data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    template = data.get("template") if isinstance(data, dict) else None
    if isinstance(template, dict):
        thumb = template.get("thumbnail")
        if isinstance(thumb, str) and thumb.strip():
            return thumb.strip()
    return None


def _thumbnail_ok(name: str, version: str, row: dict[str, Any], *, root: Path) -> bool:
    """Whether the registry actually serves the thumbnail this package
    declares. A package with no declared thumbnail at all -- neither in
    the index row nor its own typst.toml -- has nothing to check here and
    fails this gate, even if some file elsewhere in its tarball happens to
    be named thumbnail.png, since that is not what `declared` names.
    """
    declared = _declared_thumbnail(name, version, row, root=root)
    if not declared:
        return False
    return fetch_registry_thumbnail(name, version, declared, root=root) is not None


def survey_row(name: str, version: str, row: dict[str, Any], *, root: Path) -> dict[str, Any]:
    """Probe one (name, version) and apply every gate `probe_template`
    deliberately leaves to this job -- denylist, fonts, page count, then
    the thumbnail fetch -- cheapest and most-likely-to-fail first, so a
    template that's already denylisted or over budget on fonts/pages never
    costs a network round trip for its thumbnail.
    """
    probe = probe_template(name, version, root=root)
    ok, reason = probe.ok, probe.reason
    # Once a pair has an emitter, gate 2 asks about *its* output instead of
    # the vendor example's -- see the module docstring and
    # `_emitter_font_warnings`. `None` means no emitter claims this pair,
    # so the vendor example's own warnings (and split) still govern.
    emitter_warnings = _emitter_font_warnings(name, version)
    warnings = probe.font_warnings if emitter_warnings is None else emitter_warnings
    installable = tuple(font for font in warnings if font in INSTALLABLE_FONTS)
    if ok and name in DENYLIST:
        ok, reason = False, DENYLIST[name]
    if ok and warnings:
        ok, reason = False, f"font warning: {warnings[0]}"
    if ok and probe.pages > 2:
        ok, reason = False, f"{probe.pages} pages"
    if ok and not _thumbnail_ok(name, version, row, root=root):
        ok, reason = False, "no thumbnail"
    return {
        "name": name,
        "version": version,
        "ok": ok,
        "pages": probe.pages,
        "needs_inline_data": probe.needs_inline_data,
        "font_warnings": list(warnings),
        # The classification `font_warnings` alone can't recover: which of
        # those warnings names a font the pool could ship by installing it
        # (`installable_fonts`) versus one nobody can legally or practically
        # bundle (`unavailable_fonts`). Gate 2 above still rejects a
        # template on *any* warning, but recording the split here is what
        # makes the to-install set recoverable from the committed file
        # instead of only by re-running the survey.
        "installable_fonts": list(installable),
        "unavailable_fonts": list(unavailable_fonts(warnings)),
        "reason": reason,
    }


def _typst_version(binary: str | None) -> str:
    if binary is None:
        return ""
    try:
        completed = subprocess.run(
            [binary, "--version"], capture_output=True, text=True, timeout=10, check=False
        )
    except Exception:
        return ""
    return (completed.stdout or completed.stderr or "").strip()


def run_survey(*, root: Path | None = None) -> dict[str, Any]:
    base = root or package_root()
    index = fetch_index()
    rows: list[dict[str, Any]] = []
    for name, version, index_row in survey_pairs(index):
        prefetch_one(name, version, root=base)
        row = survey_row(name, version, index_row, root=base)
        if not row["ok"]:
            print(f"{name}:{version} unsupported -- {row['reason']}", file=sys.stderr)
        rows.append(row)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "typst_version": _typst_version(typst_binary()),
        "rows": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.jobs.survey_templates")
    parser.add_argument(
        "--out",
        default=str(SUPPORT_PATH),
        help="where to write the generated support list",
    )
    args = parser.parse_args(argv)

    result = run_survey()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    ok_count = sum(1 for row in result["rows"] if row["ok"])
    print(f"rows={len(result['rows'])} ok={ok_count} out={out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
