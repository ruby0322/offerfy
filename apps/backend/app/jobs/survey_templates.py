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
`app/services/llm.SYSTEM_PROMPT`), and Task 8's emitter registry will pin
more. A survey built only from the index's latest would mark the pinned
version unsupported the moment the index moves on, and Task 4's switch
endpoint would then reject the very template Offerfy ships by default. So
this job surveys the index's latest per package, unioned with every pinned
pair, and offers the pinned version when a package has both -- one row per
package either way. `EMITTER_PINS` below stands in for Task 8's
`emitters.deterministic_pairs()`, which does not exist yet; Task 8 replaces
this constant with that call.

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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.template_probe import probe_template
from app.services.templates import (
    SUPPORT_PATH,
    fetch_index,
    fetch_registry_thumbnail,
    latest_cv_packages,
    package_root,
    prefetch_one,
    thumbnail_relpath,
)
from app.services.typst_compile import typst_binary

# Stands in for Task 8's emitters.deterministic_pairs(): the (package,
# version) pairs the starter and system prompt are pinned to today. See the
# module docstring for why the survey must offer these versions rather than
# the index's latest.
EMITTER_PINS: dict[str, str] = {
    "basic-resume": "0.2.9",
    "ats-friendly-resume": "0.1.1",
    "acorn-resume": "0.1.0",
    "clean-print-cv": "0.1.0",
}

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
    `cv` row per package, with any name in `EMITTER_PINS` pointed at its
    pinned version instead. The index row still carries the declared
    thumbnail path used by `_thumbnail_ok` -- which is normally valid for a
    pinned version a patch or two behind the index's latest too.
    """
    latest = {str(row.get("name") or ""): row for row in latest_cv_packages(index)}
    names = sorted(set(latest) | set(EMITTER_PINS))
    pairs: list[tuple[str, str, dict[str, Any]]] = []
    for name in names:
        row = latest.get(name, {})
        version = EMITTER_PINS.get(name) or str(row.get("version") or "")
        if version:
            pairs.append((name, version, row))
    return pairs


def _thumbnail_ok(name: str, version: str, row: dict[str, Any], *, root: Path) -> bool:
    """Whether the registry actually serves the thumbnail this package
    declares. A package with no `[template].thumbnail` in its typst.toml
    has nothing to check here and fails this gate -- even if some file
    elsewhere in its tarball happens to be named thumbnail.png, since that
    is not what `declared` names.
    """
    declared = thumbnail_relpath(row)
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
    if ok and name in DENYLIST:
        ok, reason = False, DENYLIST[name]
    if ok and probe.font_warnings:
        ok, reason = False, f"font warning: {probe.font_warnings[0]}"
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
        "font_warnings": list(probe.font_warnings),
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
