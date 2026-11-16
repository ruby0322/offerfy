from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Resume
from app.services.family import family_master
from app.services.jobs.applications import job_id_for_tailor
from app.services.memory import accepted_memory_texts
from app.services.templates import parse_preview_spec


def build_role_card(db: Session, resume: Resume) -> str:
    master = family_master(db, resume)
    lines = ["", "", "Document context (rebuilt every turn, not part of the chat):"]
    spec = parse_preview_spec(resume.typst_source)
    if spec:
        lines.append(f"- This document currently uses @preview/{spec[0]}:{spec[1]}.")
    if resume.kind == "tailor":
        lines.append(
            f"- You are editing a TAILOR resume titled {resume.title!r}, "
            f"forked from the master {master.title!r}."
        )
        if resume.target_label:
            lines.append(f"- Target role for this tailor: {resume.target_label}")
        # The posting, by id, instead of a copy of its description. A tailor
        # created from a posting carries no target_label or target_context at
        # all -- only the applications row knows which job it was for -- so
        # without this the card said "no target role" for the one path that
        # always has one. By id rather than inline because read_job fetches it
        # on demand, while a description pasted here is re-sent on every turn
        # of the conversation for as long as it lasts.
        job_id = job_id_for_tailor(db, resume)
        if job_id:
            lines.append(
                f"- This tailor is for Offerfy posting job_id {job_id!r}. Call "
                "read_job with that id to read the role; do not ask the user "
                "to paste the description."
            )
        if resume.target_context:
            lines.append("- Job description, for this tailor only:\n" + resume.target_context)
        if job_id or resume.target_label or resume.target_context:
            lines.append(
                "- That target is what this tailor is for. Work from it rather "
                "than searching the catalogue again, unless the user asks for "
                "a different role."
            )
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
        "- search_jobs and read_job read Offerfy's own job catalogue. Use "
        "them, not web_search, when the user asks about roles: a posting "
        "found on the open web has no job_id and no apply_url you can trust, "
        "and Offerfy cannot show it to them afterwards. Read a posting with "
        "read_job before tailoring for it, and tailor from its description "
        "rather than from its title.",
        "- map_jd_requirements records what a posting asks for next to what "
        "this resume says about each point, and the person sees it as a "
        "table. It describes the document, never the person: no score, no "
        "total, no percentage, and no verdict on how strong a candidate they "
        "are. Call it once per turn, after reading the posting.",
        "- Propose or edit, and the difference is what they asked for. A "
        "specific request ('shorten this bullet', 'change the job title to "
        "X') is yours to carry out: use apply_typst_edit and do it. An "
        "open-ended one ('make this better', 'is this good enough for that "
        "role?'), or a request with more than one reasonable answer, goes "
        "through propose_resume_actions instead, so they see the wording "
        "before it is in their resume and accept the one they want. Use the "
        "same rule when you are genuinely unsure an edit is what they meant: "
        "a proposal they apply in one tap costs them less than an edit they "
        "have to undo. Do not propose something they already decided on.",
        "- propose_resume_actions records what the person could do, and "
        "after map_jd_requirements it should answer every requirement the "
        "table marked partly_in_resume or not_in_resume. A change they can "
        "apply in one click needs a "
        "`before` copied verbatim from the source read_typst gave you and "
        "appearing in it exactly once, because their browser applies it by "
        "finding that span -- no second call to you. When you cannot write "
        "the text yourself, leave `before` and `after` out, say what you "
        "would need, and give `asks`: short replies in their own words they "
        "can send in one tap. An open suggestion is the honest answer to a "
        "gap; inventing experience to fill one is not. The reply says per "
        "action whether it matched; read the source again and correct the "
        "ones that did not. Neither tool changes the resume: apply_typst_edit "
        "is still the only thing that does, and it is for what the person "
        "asks for, not for what you proposed.",
        "- When you have called those two, say the headline in a sentence or "
        "two and stop. The cards carry the detail; repeating them as prose "
        "doubles the conversation and says nothing new.",
        "- Never invent a posting, an employer or an apply_url, and never "
        "state a count or a date no tool returned. The catalogue is refreshed "
        "on a timer, so it is current rather than live to the minute; "
        "catalog_refreshed_at is when a posting was last confirmed.",
    ]
    if resume.user_id is not None:
        lines.append(
            "- read_my_job_profile is this person's own job context on "
            "Offerfy: the roles and places they said they want, how many live "
            "postings match, what they have saved or applied to, and their "
            "weekly digest when one exists. Read it before asking them what "
            "they are looking for."
        )
    lines += [
        "- Call propose_memory when the user states something lasting, and "
        "especially when they say never or always to do something. Only part of "
        "a long conversation is replayed to you, and a thing the document does "
        "not do leaves no trace in it to find later -- accepted memory is "
        "replayed in full on every turn, so that is where such a rule has to "
        "live to survive.",
    ]
    accepted = accepted_memory_texts(db, master.id)
    if accepted:
        lines.append("Accepted memory for every resume in this family:")
        lines += [f"- {text}" for text in accepted]
    return "\n".join(lines)
