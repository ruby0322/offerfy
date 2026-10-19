from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Resume
from app.services.family import family_master
from app.services.memory import accepted_memory_texts


def build_role_card(db: Session, resume: Resume) -> str:
    master = family_master(db, resume)
    lines = ["", "", "Document context (rebuilt every turn, not part of the chat):"]
    if resume.kind == "tailor":
        lines.append(
            f"- You are editing a TAILOR resume titled {resume.title!r}, "
            f"forked from the master {master.title!r}."
        )
        if resume.target_label:
            lines.append(f"- Target role for this tailor: {resume.target_label}")
        if resume.target_context:
            lines.append("- Job description, for this tailor only:\n" + resume.target_context)
        lines.append(
            "- Call read_master to see the master resume. "
            "You cannot edit the master from here."
        )
        lines.append(
            "- create_tailor defaults to copying the master. Pass fork_from='current' "
            "when the user wants the new tailor based on THIS tailor's draft, for "
            "example 'make another one from this version'."
        )
    else:
        lines.append(
            f"- You are editing the MASTER resume titled {resume.title!r}. Tailors fork from it."
        )
    lines += [
        "- apply_typst_edit changes only this document.",
        "- create_tailor makes a new tailor from a saved draft. Call it at most "
        "once per turn. Ask for the job title or description first when it is unclear. "
        "Do not edit the new tailor in this turn; tell the user it will open next.",
        "- propose_memory only suggests a durable preference or fact about the person "
        "(never job-specific wording). It takes effect only after a signed-in user accepts it.",
    ]
    accepted = accepted_memory_texts(db, master.id)
    if accepted:
        lines.append("Accepted memory for every resume in this family:")
        lines += [f"- {text}" for text in accepted]
    return "\n".join(lines)
