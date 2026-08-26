def _arg_str(args: dict, key: str) -> str | None:
    if args.get(key) is None:
        return None
    return str(args[key])


def apply_typst_edit(source: str, args: dict) -> str:
    """Apply exactly one Typst source edit.

    Modes (first match):
    - search and replace (first occurrence): non-empty ``search`` + ``replace``
    - range replace (character offsets): ``start`` + ``end`` + ``replacement``
    - full document write: non-empty ``source``

    Empty ``search``/``replace`` strings are ignored so a full write is not
    blocked when the model fills every schema field. When both a full
    ``source`` snapshot and a patch are present, the patch is applied to the
    live document — not the (often stale) snapshot.
    """
    search = _arg_str(args, "search")
    replace = _arg_str(args, "replace")
    if search and replace is not None:
        if search not in source:
            raise ValueError("search string not found")
        return source.replace(search, replace, 1)

    if (
        args.get("start") is not None
        and args.get("end") is not None
        and args.get("replacement") is not None
    ):
        start = int(args["start"])
        end = int(args["end"])
        if start < 0 or end < start or end > len(source):
            raise ValueError("invalid range")
        return source[:start] + str(args["replacement"]) + source[end:]

    full = _arg_str(args, "source")
    if full:
        return full

    raise ValueError(
        "apply_typst_edit requires source, search+replace, or start+end+replacement"
    )
