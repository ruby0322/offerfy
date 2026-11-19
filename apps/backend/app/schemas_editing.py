"""Wire models for structured Typst editing.

The Out models mirror `services.typst_projection`'s dataclasses field for field.
Spans and offsets are code points, carried as plain integers; nothing here converts
them. A `span` is a two-item list in JSON.
"""

from pydantic import BaseModel

from app.schemas import PreviewPages


class MarkOut(BaseModel):
    kind: str
    start: int
    end: int
    url: str | None = None


class UnitOut(BaseModel):
    id: str
    kind: str
    span: tuple[int, int]
    value: str
    editable: bool
    key: str | None = None
    style: bool = False
    role: str | None = None
    marks: list[MarkOut] = []
    opaque: list[str] = []
    escapes: list[str] = []


class BlockOut(BaseModel):
    id: str
    kind: str
    name: str | None
    outer: tuple[int, int]
    content_end: int
    args: list[str] = []
    unit: str | None = None
    bullets: list["BlockOut"] = []
    notes: list[tuple[int, int]] = []
    body: tuple[int, int] | None = None
    body_units: list[str] = []


class SectionOut(BaseModel):
    id: str
    title_unit: str | None
    outer: tuple[int, int]
    body_start: int
    blocks: list[BlockOut]


class ReportOut(BaseModel):
    structures: dict[str, int]
    skipped_args: dict[str, int]
    lints: list[str]


class ProjectionOut(BaseModel):
    hash: str
    tier: str
    sections: list[SectionOut]
    units: dict[str, UnitOut]
    report: ReportOut


class IntentIn(BaseModel):
    hash: str
    kind: str  # set_arg | set_line | insert_bullet | insert_item
    #            | insert_section | delete | move_section
    unit: str | None = None  # set_arg, set_line
    expect: str | None = None  # set_arg, set_line: the value the client believes it has
    value: str | None = None  # set_arg
    text: str | None = None  # set_line
    marks: list[MarkOut] = []  # set_line
    target: str | None = None  # insert_bullet, insert_item, delete, move_section
    dir: str | None = None  # move_section: "up" | "down"
    # The words a new entry is written with, in the person's own language. They are
    # content, not Typst, and `apply_intent` has no locale -- so the caller that does
    # brings them, and the server's CJK defaults stand in when it does not. Validated
    # like any other value (`typst_intents._word`).
    placeholder: str | None = None  # insert_bullet, insert_item, insert_section
    title: str | None = None  # insert_section


class EditOut(BaseModel):
    hash: str
    typst_source: str
    projection: ProjectionOut
    preview: PreviewPages
