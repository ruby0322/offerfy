"""Probe a Typst template's data loader so it can be inlined.

Offerfy compiles a résumé as a single file in an empty temp directory, so a
template that reads a sidecar data file via `yaml("x.yaml")` / `toml(...)` /
`json(...)` can never compile there -- nothing may reference a file beside
it. Rewriting that call into the Typst literal for the file's own contents
removes the sidecar dependency entirely, recovering templates that would
otherwise be unusable.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any

import yaml

from app.services.typst_escape import typst_value

_LOADER_CALL = re.compile(r'\b(yaml|toml|json)\(\s*"([^"]+)"\s*\)')

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
