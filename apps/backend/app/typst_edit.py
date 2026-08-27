def _arg_str(args: dict, key: str) -> str | None:
    if args.get(key) is None:
        return None
    return str(args[key])


def apply_typst_edit(
    source: str, args: dict, *, prefer_full_source: bool = False
) -> str:
    """Apply exactly one Typst source edit.

    Modes (first match, unless ``prefer_full_source``):
    - search and replace (first occurrence): non-empty ``search`` + ``replace``
    - range replace (character offsets): ``start`` + ``end`` + ``replacement``
    - full document write: non-empty ``source``

    Empty ``search``/``replace`` strings are ignored so a full write is not
    blocked when the model fills every schema field. When both a full
    ``source`` snapshot and a patch are present, a patch that actually
    changes the live document wins (stale snapshots are ignored). If the
    patch is a no-op, its ``search`` is missing, or ``start``/``end`` are
    invalid (including schema zeros), a different ``source`` is applied
    instead so full rewrites are not discarded.
    """
    full = _arg_str(args, "source")
    if prefer_full_source and full:
        return full

    search = _arg_str(args, "search")
    replace = _arg_str(args, "replace")
    if search and replace is not None:
        if search not in source:
            if full:
                return full
            raise ValueError("search string not found")
        patched = source.replace(search, replace, 1)
        if patched == source and full and full != source:
            return full
        return patched

    if (
        args.get("start") is not None
        and args.get("end") is not None
        and args.get("replacement") is not None
    ):
        start = int(args["start"])
        end = int(args["end"])
        replacement = str(args["replacement"])
        schema_junk = start == 0 and end == 0 and replacement == ""
        if not schema_junk:
            if start < 0 or end < start or end > len(source):
                if full:
                    return full
                raise ValueError("invalid range")
            patched = source[:start] + replacement + source[end:]
            if patched == source and full and full != source:
                return full
            return patched

    if full:
        return full

    raise ValueError(
        "apply_typst_edit requires source, search+replace, or start+end+replacement"
    )
