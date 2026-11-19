"""Where each line unit of a résumé sits on the rendered page.

The projection says which spans of the source are editable; this says where they
land. A unit is tagged with a bare Typst label in a throwaway copy of the source,
`typst eval` asks the compiler where each label ended up, and the text layer of the
real document supplies the lines. A bare label is layout-neutral (it is a zero-size
tag on the preceding element), which `#metadata(...)` is not.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException

from app.services.revisions import source_hash
from app.services.starter import resolve_font_paths, resolve_package_path
from app.services.text_layer import extract_text_layer
from app.services.typst_compile import compile_typst, typst_binary
from app.services.typst_projection import Projection, Unit

logger = logging.getLogger(__name__)

_CACHE_SIZE = 32
# Keyed by the source alone. In production `projection` and `layers` are both derived
# from `source`, so the source determines the answer; a caller passing a hand-made
# projection (a test) must change the source too, or it reads another call's result.
_cache: dict[str, dict[str, list["Band"]]] = {}
# A unit's lines are consecutive: a line further than this many line heights below the
# previous band is a different thing (the next job's heading), even if its text recurs.
_LINE_GAP = 1.8
# Two spans closer than this in y are one row (a left column and a right column).
_SAME_ROW = 0.5


@dataclass(frozen=True)
class Band:
    page: int          # 0-based, parallel to the preview's pages
    y: float
    height: float


def _line_units(projection: Projection) -> list[Unit]:
    return [u for u in projection.units.values() if u.role]


def _prefix(source: str) -> str:
    """A label namespace the person's own source does not use.

    The labels are ours, but they share a namespace with every `<label>` the résumé
    already has; a collision would silently move an anchor onto the person's element.
    """
    k = 0
    while f"<ofy{'x' * k}-" in source:
        k += 1
    return f"ofy{'x' * k}-"


# A trailing line comment is opaque to the end of its unit, so it is inside the span;
# a label written after it would be part of the comment and never exist.
_TRAILING_COMMENT = re.compile(r"\s+//[^\n]*$")


def _label_at(source: str, unit: Unit) -> int:
    start, end = unit.span
    found = _TRAILING_COMMENT.search(source, start, end)
    return found.start() if found else end


def label_shadow(source: str, projection: Projection) -> str:
    """`source` with ` <ofy-uNN>` added to every line unit, laid out identically.

    Descending, so the offsets of the units still to be labelled stay valid. An end
    marker follows the document so the caller can tell the shadow paginated like it.
    """
    prefix = _prefix(source)
    out = source
    for unit in sorted(_line_units(projection), key=lambda u: -u.span[1]):
        at = _label_at(source, unit)
        out = out[:at] + f" <{prefix}{unit.id}>" + out[at:]
    return out + f"\n#metadata(none) <{prefix}end>\n"


def _eval_anchors(shadow: str, ids: list[str] | None = None, prefix: str | None = None
                  ) -> tuple[dict[str, tuple[int, float]], int | None]:
    """(unit id -> (page, y), page count of the shadow) from one `typst eval`."""
    if prefix is None:
        # Ours is the longest `ofyxx-` whose end marker is in the shadow (see `_prefix`).
        found = re.findall(r"<(ofyx*-)end>", shadow)
        if not found:
            return {}, None
        prefix = max(found, key=len)
    if ids is None:
        ids = sorted(set(re.findall(rf"<{re.escape(prefix)}(u\d+)>", shadow)))
    if not ids:
        return {}, None
    binary = typst_binary()
    if binary is None:
        raise HTTPException(status_code=503, detail="Typst is not available")
    names = [f"{prefix}{i}" for i in ids]
    selector = f'selector(label("{names[0]}"))' + "".join(
        f'.or(label("{n}"))' for n in names[1:])
    expr = (f'(end: query(label("{prefix}end")).map(it => it.location().page()), '
            f"rows: query({selector}).map(it => (id: str(it.label), "
            "p: it.location().page(), y: it.location().position().y.pt())))")
    with tempfile.TemporaryDirectory(prefix="offerfy-typst-") as tmp:
        path = Path(tmp) / "resume.typ"
        path.write_text(shadow, encoding="utf-8")
        cmd = [binary, "eval", "--root", tmp, "--format", "json",
               "--package-path", resolve_package_path()]
        for font_path in resolve_font_paths():
            cmd.extend(["--font-path", font_path])
        cmd.extend([expr, "--in", str(path)])
        try:
            done = subprocess.run(cmd, capture_output=True, check=False, timeout=30)
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(status_code=504, detail="Typst eval timed out") from exc
    if done.returncode != 0:
        err = (done.stderr or done.stdout or b"").decode("utf-8", errors="replace")
        raise HTTPException(status_code=400, detail=f"Typst eval failed: {err[-2000:]}")
    out = json.loads(done.stdout)
    rows = {row["id"].removeprefix(prefix): (int(row["p"]) - 1, float(row["y"]))
            for row in out["rows"]}
    missing = sorted(set(ids) - set(rows))
    if missing:
        logger.warning("typst eval returned no anchor for %s", missing)
    end = out["end"][0] if out["end"] else None
    return rows, end


def anchor_positions(shadow: str, ids: list[str] | None = None,
                     prefix: str | None = None) -> dict[str, tuple[int, float]]:
    """unit id -> (0-based page, y in points), read from the labels in `shadow`."""
    return _eval_anchors(shadow, ids, prefix)[0]


def _squash(text: str) -> str:
    """Letters and digits only: hyphenation, smart quotes and the bullet glyph are the
    compiler's doing, not the unit's text, so they must not decide whether a line fits."""
    return re.sub(r"[\W_]+", "", text)


def _gap(y: float, span: dict) -> float:
    """How far the anchor's y is from a text-layer line (0 when it is inside it)."""
    return max(span["y"] - y, y - (span["y"] + span["height"]), 0.0)


def _assign(units: list[Unit], positions: dict[str, tuple[int, float]],
            layers: list[dict]) -> dict[str, list[Band]]:
    """Each unit gets the line its anchor is on, then the consecutive lines under it
    that are pieces of its own text, up to the next anchor.

    The label's position is where its unit STARTS, and falls inside its first line (a
    heading or list item reports the top of its block, a paragraph its baseline), so
    the first line is simply the one nearest the anchor and needs no tuned slack. Past
    that the unit's lines are consecutive in flow, so the walk takes only a line that
    is the next one: between two anchors sit things no unit owns (a job title, a
    right-hand date column), and a unit's text recurring in one of them (a city, a
    year, a former employer) must not claim it. Adjacency is judged against the unit's
    own last band, never iteration order: the layer lists spans by text block, so a
    right-hand column arrives after everything beside it.
    """
    anchored = sorted((u for u in units if u.id in positions),
                      key=lambda u: (positions[u.id], u.span[0]))
    bands: dict[str, list[Band]] = {u.id: [] for u in units}
    tops = [min((s["y"] for s in layer["spans"]), default=0.0) for layer in layers]
    for n, unit in enumerate(anchored):
        page_a, y_a = positions[unit.id]
        if page_a >= len(layers) or not layers[page_a]["spans"]:
            continue
        want = _squash(unit.value)
        nearest = min(_gap(y_a, s) for s in layers[page_a]["spans"])
        best = sorted((s for s in layers[page_a]["spans"] if _gap(y_a, s) == nearest),
                      key=lambda s: s["x"])
        first = next((s for s in best if _squash(s["text"]) in want), best[0])
        last = Band(page_a, first["y"], first["height"])
        bands[unit.id].append(last)
        end = positions[anchored[n + 1].id] if n + 1 < len(anchored) else None
        stop = False
        for page in range(page_a, len(layers)):
            for span in sorted(layers[page]["spans"], key=lambda s: (s["y"], s["x"])):
                if (page, span["y"]) <= (last.page, last.y + _SAME_ROW):
                    continue
                if end is not None and (page, span["y"] + span["height"]) > end:
                    stop = True
                    break
                text = _squash(span["text"])
                if not text or text not in want:
                    continue
                if page == last.page:
                    adjacent = span["y"] - last.y <= _LINE_GAP * last.height
                elif page == last.page + 1:
                    adjacent = span["y"] - tops[page] <= _LINE_GAP * span["height"]
                else:
                    adjacent = False
                if adjacent:
                    last = Band(page, span["y"], span["height"])
                    bands[unit.id].append(last)
            if stop:
                break
    return bands


def _arg_blocks(projection: Projection) -> list[tuple[tuple[int, int], list[str]]]:
    """(outer span, argument unit ids) of each block that owns content arguments.

    A block whose arguments are all layout settings is left out: `font` and `paper`
    print nothing, so such a block occupies no row, has no row to highlight and cannot
    be mistaken for the block that does.
    """
    out = []
    for section in projection.sections:
        for block in section.blocks:
            ids = [i for i in block.args
                   if i in projection.units and not projection.units[i].style]
            if ids:
                out.append((block.outer, ids))
    return out


def _rows_between(after: Band | None, before: Band | None,
                  layers: list[dict]) -> list[Band]:
    """One band per rendered row strictly between two anchored bands, columns merged.

    `after` is the bottom of the band above, `before` the top of the band below; either
    may be None, which means "from the first row" / "to the last row".
    """
    if after is not None and before is not None and (
            (after.page, after.y + after.height) > (before.page, before.y)):
        # Document order and rendered order disagree, which a two-column layout can do.
        # Then this is not a bracket and there is nothing sound to draw (anti-goal 3).
        return []
    first = after.page if after else 0
    last = before.page if before else len(layers) - 1
    out: list[Band] = []
    for page in range(max(first, 0), min(last, len(layers) - 1) + 1):
        for span in sorted(layers[page]["spans"], key=lambda s: (s["y"], s["x"])):
            if after is not None and (page, span["y"]) <= (after.page,
                                                           after.y + _SAME_ROW):
                continue
            if before is not None and (page, span["y"] + span["height"]) > (before.page,
                                                                            before.y):
                continue
            y, height = float(span["y"]), float(span["height"])
            if out and out[-1].page == page and y - out[-1].y <= _SAME_ROW:
                # A left column and a right column of one row are one band: the band has
                # no x, it is the row.
                top = min(out[-1].y, y)
                bottom = max(out[-1].y + out[-1].height, y + height)
                out[-1] = Band(page, top, bottom - top)
            else:
                out.append(Band(page, y, height))
    return out


def _arg_bands(source: str, projection: Projection, bands: dict[str, list[Band]],
               layers: list[dict]) -> dict[str, list[Band]]:
    """A band for every content string argument: the rows of the block it belongs to.

    A string argument (a job title, a company, a date) is not a line, so it cannot carry
    a label -- which is why the design gives it a fallback: "the rows of this block",
    bracketed by the y of the anchored line units before and after it. Without it the
    highlight was missing from most of the form, since string arguments outnumber line
    units on a real résumé, and an armed delete on an entry with no bullets drew no band
    at all while telling the person what it would take.

    The bracket is the nearest *placed* line unit before the block and the nearest one at
    or after its start -- its own first bullet, when it has one, so a block's band is its
    heading rows and not its bullets. Both ends may be absent (a block before any line
    unit, or after the last one); with neither there is nothing to bracket and nothing is
    emitted. Nothing is emitted either when a second block that may print sits inside the
    same bracket: its rows are in there too and nothing here tells them apart, so no band
    is better than a band over two entries (anti-goal 3).
    """
    placed = [u for u in sorted(_line_units(projection), key=lambda u: u.span[0])
              if bands.get(u.id)]
    blocks = _arg_blocks(projection)
    starts = [outer[0] for outer, _ in blocks]
    out: dict[str, list[Band]] = {}
    for outer, ids in blocks:
        lo = next((u for u in reversed(placed) if u.span[1] <= outer[0]), None)
        hi = next((u for u in placed if u.span[0] >= outer[0]), None)
        if lo is None and hi is None:
            continue
        gap = (lo.span[1] if lo else -1, hi.span[0] if hi else len(source))
        if sum(1 for s in starts if gap[0] <= s < gap[1]) != 1:
            continue
        rows = _rows_between(bands[lo.id][-1] if lo else None,
                             bands[hi.id][0] if hi else None, layers)
        if not rows:
            continue
        for uid in ids:
            out[uid] = rows
    return out


def anchor_bands(source: str, projection: Projection,
                 layers: list[dict] | None = None) -> dict[str, list[Band]]:
    """unit id -> one Band per rendered line the unit occupies; `{}` when unknown.

    Line units are placed by their own label; a string argument has no label and falls
    back to the rows of its block (`_arg_bands`), which is also what the delete highlight
    needs for an entry with no bullets.

    `layers` is the text layer of `source` when the caller already compiled it.
    """
    key = source_hash(source)
    if key in _cache:
        return {uid: list(rows) for uid, rows in _cache[key].items()}
    units = _line_units(projection)
    positions, end = _eval_anchors(
        label_shadow(source, projection), [u.id for u in units], _prefix(source))
    if layers is None:
        layers = extract_text_layer(compile_typst(source, "pdf"))
    if not positions or end != len(layers):
        # Nothing found, or the shadow paginated differently from the document: anchors
        # over the wrong pages are worse than none (cf. `compile_preview_pages`).
        if positions:
            logger.warning("shadow has %s pages, document %s", end, len(layers))
        return {}
    bands = _assign(units, positions, layers)
    bands.update(_arg_bands(source, projection, bands, layers))
    if len(_cache) >= _CACHE_SIZE:
        _cache.pop(next(iter(_cache)))
    _cache[key] = bands
    return {uid: list(rows) for uid, rows in bands.items()}
