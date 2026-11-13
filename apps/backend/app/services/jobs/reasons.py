"""One reason and one optional gap per surviving posting, and the validator
that stops a sentence claiming experience the resume does not have.

This module produces and validates user-visible match prose and does nothing
else. Scoring is `score.py`; choosing the survivors and building the digest
are later tasks.

Why the validator is stricter than `profile.py`'s. Everything `profile.py`
writes is shown to an employer as a summary the person can read and correct.
This is the only free text shown to a person as a claim about THEIR OWN
history, written by a model, with no one checking it before it lands in their
inbox. An unverifiable sentence is worse than no sentence. So the model does
not just write a reason: it must name what it cites (`resume_cites`, from the
resume; `posting_cites`, from the posting), the cite must be findable in the
resume facts (`ResumeFacts.skills_text` / `search_text`, which are columns, not
functions) and the cite must actually appear in the sentence it vouches for. A
gap is a claim that the person LACKS something, so it must name what
(`gap_missing`) and what it names must not be on the resume.

The model is not asked to "write a reason". It is told which dimension is weak:
a low `requirements_met` beside three high components localises the gap, and
the prompt carries all four component probabilities for exactly that.

Two different things are called an "item" here, and the difference is the
point. `validate` DROPS A `Prose` RECORD: a reason citing experience the resume
does not have comes back in `dropped` as (job_id, "uncited") -- or
"undeclared" for an unaccounted-for name, or "narrated" for a history claim --
and no `Prose` is returned for it. It does NOT return a `Prose` with an empty-string reason. The
digest item, built later from a `Scored` plus an OPTIONAL `Prose`, keeps its
tier and score and simply has no sentence. Do not "fix" this into blank
reasons: the caller would then have to filter them out again, and a blank
string is how a missing sentence gets rendered as an empty paragraph.

A rejected gap is gentler: the reason is a separate claim, so it survives with
`gap=None`.

Length is clamped, never rejected (the discipline of `resume_facts.clamp_payload`):
truncating loses a clause, rejecting loses the sentence. Cites are checked
against the clamped text, because that is what the person reads.

WHAT IS GUARANTEED AND WHAT IS NOT. Read this before trusting the output,
because the two kinds of check here are not the same kind of promise.

EXACT, decided by comparing strings and nothing else:
  * every declared resume cite is present in the resume facts, and at least
    one of them appears in the sentence it vouches for;
  * every declared posting cite corresponds to a value of THIS posting --
    whenever `check` is given the posting's field values, which `write_reasons`
    always is (values only: a cite of "Requirements" must not match a label);
  * `gap_missing` is named, at most GAP_MISSING_CHARS long, absent from the
    resume, and actually mentioned in the gap;
  * at most MAX_ITEMS records are kept, and duplicates and unknown job ids go.
  WHICH postings are sent is the caller's cap (Task 5), not this module's.

BEST EFFORT, two heuristics over free prose, neither of them a guarantee:
  * the NAME SCAN (`_undeclared_names`) looks for name-like ASCII tokens the
    model did not declare -- capitalised, inner capital, digit, + or #, two
    characters or more, plus "R" and "C". Position excuses nothing; only
    `_STOPWORDS` membership does. A LOWERCASE technology the model neither
    declares nor capitalises ("you led terraform migrations") is invisible to
    it, as is any non-ASCII name, and a word in `_STOPWORDS` is exempt even if
    a company is called that.
  * the CLAIM SHAPE check (`_claims_history` for a reason,
    `_asserts_possession` for a gap) looks for a second-person subject with a
    past-tense or perfect verb, or beside a quantity. It is what catches the
    claims that name nothing at all -- "You managed 40 engineers.", "You saved
    $5M." -- which no name scan could ever see. It knows a fixed verb list and
    a three-word window, so an unusual construction ("you Docker-ized Acme")
    walks through it.
Together they stop the sentences a model actually writes; a determined
adversary is not the threat model, and neither heuristic should be described
to anyone as a guarantee that the prose is true.

The scan is split by field. In a `reason`, name-like tokens from the posting's
company and location are exempt (a city or an employer is not a claim about
the person) but tokens from its TITLE and REQUIREMENTS are not (titles carry
technology names; a posting cite never exempts anything): a requirement
mirrored back as the person's experience is the likeliest invented claim, so it
must be declared and on the resume. That company/location exemption covers a
MENTION, not an ATTRIBUTION: Docker, Snowflake and Terraform are all
employers, so an exempt token loses its exemption when a possessive stands
immediately before it or "experience"/"work" immediately after. "...fits
Terraform." is the employer; "Your Terraform work..." is a history. In the
`gap`, the gap_missing token is allowed wherever it stands -- naming a
requirement the person lacks is what that field is for. It is held to
`gap_missing` (named, at most GAP_MISSING_CHARS long, absent from the resume,
in the gap); an unbounded gap_missing would exempt a whole phrase.

The gap IS also scanned for any other name-like token (exempting gap_missing,
and the company and location as mentions), and must contain an absence or
demand marker (`_ABSENCE`), since "You have shipped Kubernetes in prod." names
only the gap token. A marker alone proves nothing, though -- a demand verb
says nothing about the person -- so a gap asserting POSSESSION is rejected
whatever marker it also carries. NEGATION is the exception and it matters:
"You have not worked with Kubernetes." is the correct way to write a gap, and
an earlier round rejected it, so a negator between the subject and the verb
lets the sentence through.

The two fields are held to different standards on purpose. A reason may say
what the posting wants and which of the person's declared, on-resume items
match it, in the present tense ("you already work in Python"); it may not
narrate their history, tenure or achievements, because "Since 2019 you have
built Python services at Acme" is unverifiable even when Python and Acme are
both on the resume -- the year never is. A gap must not describe what the
person does at all, in any tense. What stays uncatchable: a gap that frames
the gap_missing token ITSELF as something the person has done in words the
verb list does not cover ("Missing docker; you led docker swarm" -- the
lowercase token is legitimately exempt there). The prompt is the only defence.

Fewer than five is allowed and so is none: the honest empty state is only
honest if the model is permitted to produce it.

One provider call per digest, over the survivors only. A provider fault sets
`error` and leaves `prose` empty; the caller keeps its tiers, scores and order
and renders the rows bare.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from app.config import get_settings
from app.services.jobs.score import (
    ScoreFailed,
    Scored,
    facts_text,
    record_round,
    requirements,
)
from app.services.llm.providers.openai import output_text, responses_url
from app.services.metering import metered, system_guard
from app.services.resume_facts import PROFILE_FALLBACK_MODEL, PROFILE_TIMEOUT, cut, usage_of

logger = logging.getLogger(__name__)

# Fixed, not the person's choice: nobody is watching the call. The same cheap
# OpenAI model resume_facts falls back to.
REASON_MODEL = PROFILE_FALLBACK_MODEL

REASON_CHARS = 240
GAP_CHARS = 160
MAX_ITEMS = 5
MIN_CITE_CHARS = 2
# gap_missing names ONE requirement, not a sentence. Unbounded, it exempts
# every name inside itself from the scan ("Kubernetes production deployments at
# Google Scale"), so it is capped and a longer one rejects the gap.
GAP_MISSING_CHARS = 40

PROMPT = (
    "You write the one-line reasons a person sees beside job postings that "
    "matched their resume. Each posting comes with probabilities for four "
    "dimensions (requirements_met, seniority_fit, location_fit, direction_fit); "
    "a low one beside high ones says where the gap is, so write about that "
    "dimension and not a generic one. "
    "You may return fewer than five items, and you may return none: return "
    "only the postings you can write an honest reason for. "
    "Every reason must cite something from the resume facts AND something from "
    "the posting, and you must list each cite exactly as written in "
    "resume_cites and posting_cites; each resume cite must also appear in the "
    "reason's own words. "
    "Write the reason in the PRESENT TENSE, about what the posting asks for and "
    "which of the resume's own items match it. Never narrate the person's "
    "history: do not write what they did, where they worked, how long they have "
    "done it, how many people they led, or what they achieved. \"Python, which "
    "this role centres on, is on your resume\" is right; \"you have six years of "
    "Python\" and \"you led a team of 40\" are rejected even when the resume "
    "names Python, because the years and the headcount are claims of their own. "
    "Completeness is required: every skill, employer, institution or technology "
    "named in the reason must appear in resume_cites. Refer to the posting by its "
    "company or location, or just say \"this role\": do not name technology "
    "words from the title or the requirements unless the resume has them and "
    "you list them. A skill or requirement the person does not have never goes "
    "in the reason: say it in gap and gap_missing. A name you did not list is "
    "rejected. "
    "gap is the single most important thing the posting asks for that the "
    "resume does not show, and it is null when there is none; when it is not "
    "null, put the missing thing, as the posting words it, in gap_missing and "
    "repeat it in the gap. A gap says what is ABSENT or what the posting asks "
    "for (not, no, missing, without, lacks, new to, the posting wants); it never "
    "describes anything the person has done, and it names nothing else. "
    "Never write a gap for something the resume lists. "
    "Never invent experience: do not claim a skill, employer, title, years or "
    "result that the resume facts do not state, and never guess what the "
    "person did. "
    "Write at most one sentence per reason, in the language of the posting."
)


class ProseItem(BaseModel):
    """What the model returns per posting, before the validator has seen it."""

    job_id: str
    reason: str
    gap: str | None = None
    gap_missing: str | None = None
    resume_cites: list[str] = []
    posting_cites: list[str] = []


class _ProseBatch(BaseModel):
    items: list[ProseItem]


@dataclass(frozen=True)
class Prose:
    job_id: str
    reason: str
    gap: str | None


@dataclass
class ProseRun:
    prose: list[Prose] = field(default_factory=list)
    dropped: list[tuple[str, str]] = field(default_factory=list)
    # Job ids whose gap was suppressed (manufactured or unverifiable). Not in
    # `dropped`, which means "whole record removed"; the reason survived.
    gaps_rejected: list[str] = field(default_factory=list)
    error: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    model: str = ""


def _schema() -> dict[str, Any]:
    """The response schema, written out rather than derived from pydantic.

    Strict structured output requires every property in `required` and
    additionalProperties false throughout. `_ProseBatch` still validates the
    result: this is the wire contract and that is the truth.
    """
    text = {"type": "string"}
    maybe = {"type": ["string", "null"]}
    cites = {"type": "array", "items": {"type": "string"}}
    item = {
        "type": "object",
        "additionalProperties": False,
        "required": ["job_id", "reason", "gap", "gap_missing", "resume_cites", "posting_cites"],
        "properties": {
            "job_id": text,
            "reason": text,
            "gap": maybe,
            "gap_missing": maybe,
            "resume_cites": cites,
            "posting_cites": cites,
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["items"],
        "properties": {"items": {"type": "array", "items": item}},
    }


# --- the validator -----------------------------------------------------------


def _mentions(haystack: str, needle: str) -> bool:
    """Whole-word containment, case-folded.

    The boundary is ASCII-only on purpose: `\\w` would count a CJK character
    as a word character and refuse "Python" inside "Python開發". "java" is
    not vouched for by "javascript".
    """
    needle = needle.strip().casefold()
    if len(needle) < MIN_CITE_CHARS:
        return False
    return bool(
        re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", haystack.casefold())
    )


def _on_resume(facts: Any, cite: str) -> bool:
    skills = getattr(facts, "skills_text", "") or ""
    search = getattr(facts, "search_text", "") or ""
    token = cite.strip().casefold()
    if len(token) < MIN_CITE_CHARS:
        return False
    return f"|{token}|" in skills.casefold() or _mentions(search, token)


@dataclass(frozen=True)
class PostingText:
    """The field VALUES of one posting, as shown to the model -- no labels.

    Kept apart because the scan treats them differently. Company and location
    are not claims about the person, so a name found there is exempt where it
    is MENTIONED -- but not where it is attributed to the person, since an
    employer may be called Terraform. The title
    is NOT: it routinely contains technology names ("Senior Terraform
    Engineer"), so a title word in a `reason` must be declared and on the
    resume. Requirements likewise: a requirement mirrored back as the person's
    experience is the most likely invented claim. In a `gap` the gap_missing
    token is exactly what the field is for.
    """

    company: str
    title: str
    location: str
    requirements: str

    @property
    def header(self) -> str:
        return "\n".join([self.company, self.title, self.location])

    @property
    def everything(self) -> str:
        return "\n".join([self.header, self.requirements])


@dataclass
class Checked:
    kept: list[Prose] = field(default_factory=list)
    dropped: list[tuple[str, str]] = field(default_factory=list)
    gaps_rejected: list[str] = field(default_factory=list)


# An ASCII token that looks like a name or a technology: capitalised, or
# carrying an inner capital, a digit, or + / #. Two characters is the minimum,
# because "Go", "JS" and "C#" are languages; "R" and "C" are the one-character
# cases worth naming, and a bare "I" or "A" is not. Lowercase technologies
# ("kubernetes") are NOT caught -- a known limitation, see the module docstring.
_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9+#]+|(?<![A-Za-z0-9])[RC](?![A-Za-z0-9])")

# Capitalisation is not excused by POSITION: "Terraform and Python work fits"
# and "Your Python work fits. Terraform shipped in prod." both put an invented
# claim where a sentence begins. A capitalised token is excused only by being
# one of these words: function words, sentence-openers, and the vocabulary of
# describing a posting and a match. No employer or technology is plausibly
# called any of them.
#
# The list is long on purpose, and that is a change of strategy. While it was
# the ONLY thing standing between an invented claim and the person's inbox it
# had to stay short, and it ate ordinary prose ("Based in Berlin, you already
# work in Python" was dropped over "Based"). The construction that actually
# carries an invented history -- a second-person subject with a past-tense verb
# -- is now caught by `_claims_history`, which does not care which word opens
# the sentence, so the opener list can be generous without reopening route 2.
_STOPWORDS = frozenset(
    """
    you your yours the this that these those they them their there here his
    her hers its our ours and but for nor not with without from into onto over
    under after before while when where what which who whom whose why how
    because although though however also both each every all any some most
    many much few more less least other another such same than then still yet
    about across along around between during through upon within against among
    has have had was were are been being can could shall should will would
    may might must does did doing having given despite besides beyond
    nothing none neither either missing lacks lacking never new absent
    unfamiliar seems looks matches fits experience background work working
    role roles team teams years year posting resume
    only just enough plenty several nearly almost mostly largely likely
    clearly exactly fewer little
    one two three four five six seven eight nine ten eleven twelve
    strong good great direct close ready able senior junior
    remote onsite hybrid backend frontend
    based since fully join looking combining building leading located hiring
    seeking offers requires excellent solid perfect ideal relevant proven
    extensive deep recent previous previously currently current plus fit
    match matching suits overall especially ideally probably very really
    first multiple various someone company mid lead staff principal entry
    candidates applicants familiarity knowledge proficiency expertise
    am an as at be by do he if in is it me my no of on or so to up us we vs
    cv cvs
    requirements responsibilities duties tasks skills tools technologies
    languages frameworks platforms systems services products projects
    engineers developers managers leaders analysts scientists designers
    people employer employers companies city office location region country
    position job opening opportunity vacancy listing description title level
    seniority intern graduate anyone everyone nobody
    now today soon often usually typically mainly primarily partly partially
    entirely wholly highly quite rather pretty truly simply merely basically
    essentially effectively ultimately finally second third next last
    numerous others rest whole entire half double triple single sole major
    minor primary secondary full early late high low past present above below
    recently formerly presently roughly approximately like unlike via per
    once using considering hands significant substantial demonstrated expect
    aligned pairing overlap perhaps maybe generally broadly directly notably
    particularly
    """.split()
)

# A possessive immediately before, or "experience"/"work" immediately after,
# turns a MENTION into an ATTRIBUTION. See `_undeclared_names`.
_POSSESSIVES = frozenset({"your", "my", "his", "her", "their", "its", "our"})
_ATTRIBUTIVE_NOUNS = frozenset({"experience", "work"})
_WORD_BEFORE = re.compile(r"([A-Za-z']+)\s*$")
_WORD_AFTER = re.compile(r"^\s*([A-Za-z']+)")


def _attributed(text: str, start: int, end: int) -> bool:
    """Is the token at [start:end) framed as the PERSON's, not the posting's?

    A company or a city named after a technology (Docker, MongoDB, Snowflake,
    Terraform are all employers) would otherwise launder the claim: "Your
    Python and Terraform work fits Terraform." The mention at the end is the
    employer; "Your ... Terraform work" is a claim about a history.
    """
    before = _WORD_BEFORE.search(text[:start])
    if before and before.group(1).casefold() in _POSSESSIVES:
        return True
    after = _WORD_AFTER.match(text[end:])
    return bool(after and after.group(1).casefold() in _ATTRIBUTIVE_NOUNS)


def _undeclared_names(
    text: str, allowed: list[str], facts: Any, exempt: str = ""
) -> list[str]:
    """Name-like tokens in the sentence that the resume, the declared cites
    and the posting cites all fail to account for.

    `allowed` exempts a token wherever it stands (it is on the resume or
    declared). `exempt` -- the posting's company and location -- exempts it
    only as a mention: an attributed one is still a claim about the person.
    """
    found = []
    for match in _TOKEN.finditer(text):
        token = match.group(0)
        # A stopword is excused however it is written: "CV" and "IT" are the
        # two-letter all-caps cases that the inner-capital rule would flag.
        if token.casefold() in _STOPWORDS:
            continue
        looks_named = (
            token[0].isupper()
            or any(c.isupper() for c in token[1:])
            or any(c.isdigit() or c in "+#" for c in token)
        )
        if not looks_named:
            continue
        if _on_resume(facts, token) or any(_mentions(c, token) for c in allowed):
            continue
        if (
            exempt
            and _mentions(exempt, token)
            and not _attributed(text, match.start(), match.end())
        ):
            continue
        found.append(token)
    return found


def check(
    items: list[ProseItem],
    *,
    facts: Any,
    scored_ids: set[str],
    postings: dict[str, PostingText] | None = None,
) -> Checked:
    """`validate` plus the ids whose gap was suppressed.

    `postings` maps job_id to the posting's field values. When given, a posting
    cite must be found in them (correspondence) and name-like tokens from the
    posting's company and location are exempt from the undeclared scan where
    they are mentioned rather than attributed to the person (title and
    requirements are never exempt). Posting cites prove the sentence is about THIS
    posting; they never exempt a token, since that is a different question from
    whether a word is a claim about the person. Without it the posting half only checks that
    something was declared (presence) and nothing is exempt.

    The cap (MAX_ITEMS) is enforced here: the prompt asks for at most five, but
    a prompt is not a bound. The caller still owns WHICH postings are sent.
    """
    out = Checked()
    seen: set[str] = set()
    for item in items:
        job_id = item.job_id
        if job_id not in scored_ids:
            out.dropped.append((job_id, "unknown_job"))
            continue
        if job_id in seen:
            out.dropped.append((job_id, "duplicate"))
            continue
        reason = cut(item.reason, REASON_CHARS)
        if not reason:
            out.dropped.append((job_id, "empty"))
            continue
        declared = [c for c in item.resume_cites if c.strip()]
        # EVERY declared cite must be on the resume. Filtering to the valid
        # ones and vouching with any of them would let "Python and Rust" ship
        # on Python's credit while Rust is silently discarded.
        if not declared or not all(_on_resume(facts, c) for c in declared):
            out.dropped.append((job_id, "uncited"))
            continue
        # A cite only counts if the sentence says it, else it is decoration.
        if not any(_mentions(reason, c) for c in declared):
            out.dropped.append((job_id, "uncited"))
            continue
        posting_cites = [c for c in item.posting_cites if len(c.strip()) >= MIN_CITE_CHARS]
        # Company and location only; see PostingText for why not the title.
        exempt = ""
        if postings is not None:
            posting = postings.get(job_id)
            posting_cites = [c for c in posting_cites if posting and _mentions(posting.everything, c)]
            exempt = "\n".join([posting.company, posting.location]) if posting else ""
        if not posting_cites:
            out.dropped.append((job_id, "uncited"))
            continue
        # Completeness is asked of the model in the prompt; this is the cheap
        # check that it kept the promise for name-like tokens.
        if _undeclared_names(reason, declared, facts, exempt=exempt):
            out.dropped.append((job_id, "undeclared"))
            continue
        # Shape, not vocabulary: a narrated history names nothing the scan
        # above can see ("You managed 40 engineers.").
        if _claims_history(reason):
            out.dropped.append((job_id, "narrated"))
            continue
        seen.add(job_id)
        if len(out.kept) >= MAX_ITEMS:
            out.dropped.append((job_id, "over_cap"))
            continue
        gap = _checked_gap(item, facts, exempt)
        if gap is None and (item.gap or "").strip():
            out.gaps_rejected.append(job_id)
        out.kept.append(Prose(job_id=job_id, reason=reason, gap=gap))
    return out


def validate(
    items: list[ProseItem], *, facts: Any, scored_ids: set[str]
) -> tuple[list[Prose], list[tuple[str, str]]]:
    """Keep the prose that can be checked; list the rest with why.

    Pure. Returns (kept, dropped) where dropped is (job_id, why). A dropped
    record is dropped from the PROSE list only -- see the module docstring.
    """
    checked = check(items, facts=facts, scored_ids=scored_ids)
    return checked.kept, checked.dropped


# Words that frame the gap as an absence or as the posting's demand. A gap
# that has none is describing the person, which is the claim this module exists
# to stop. ASCII words are matched whole; the CJK ones as substrings.
_ABSENCE = re.compile(
    r"(?<![a-z])(?:not|no|none|nothing|never|missing|without|lack\w*|fewer|new|"
    r"absent|unfamiliar|wants?|requires?|needs?|expects?|seeks?|asks?)(?![a-z])"
    r"|n't|沒有|缺|未|無|不",
    re.IGNORECASE,
)


# --- claims about the person -------------------------------------------------
#
# The name scan asks WHICH WORDS a sentence uses. This asks WHAT SHAPE it is,
# which is the only way to see a claim that names nothing: "You managed 40
# engineers.", "You have 12 years of it.", "You saved $5M." carry no
# capitalised token at all, so no stopword list could ever reach them.
#
# The shape is a second-person subject followed, within a few words, by a
# past-tense or perfect verb, or by a quantity. A NEGATOR in between cancels
# it: "you have not worked with Kubernetes" is the correct form for a gap, and
# an earlier round rejected it, which was backwards.

# How far after the subject a verb still counts ("you have already shipped").
_CLAIM_LOOKAHEAD = 3
# Quantities only count right beside the subject ("you managed 40", "you: 12").
_QUANTITY_LOOKAHEAD = 2

# A word, a quantity, or a money/percentage figure. "node.js" and "mid-level"
# hold together; a sentence-final "." does not join the word before it, or
# "something you run." would not be the verb "run".
_WORD = re.compile(
    r"\$\d[\d.,]*|\d[\d.,]*%?|[A-Za-z][A-Za-z'+#]*(?:[.\-][A-Za-z0-9'+#]+)*"
)
_SUBJECTS = frozenset({"you", "you've", "you'd", "you're", "you'll"})
_NEGATORS = frozenset(
    """
    not no never neither nor nothing none without hardly rarely barely
    lack lacks lacking few fewer little yet unlikely
    """.split()
)
# Before a bare "has/have shipped", these say the clause is about somebody
# else -- the posting's ideal candidate, not this person.
_ELIDED_GUARDS = frozenset(
    """
    who whom whoever that they we someone anyone everyone nobody person people
    candidate candidates applicant applicants engineer engineers one
    """.split()
)
_PERFECT = frozenset({"have", "has", "had", "having"})
# An auxiliary carries its negator AFTER it ("you have not worked"), so finding
# one is not yet a claim: the next word decides.
_AUXILIARIES = _PERFECT | {"been", "being", "was", "were", "are", "is", "do", "does", "did"}

# Past tense, perfect, and the present-tense verbs of HAVING. Narrating any of
# these about the person is a claim about their history, in a reason or a gap.
_PAST_CLAIM_VERBS = frozenset(
    """
    have has had been was were
    led shipped built ran drove driven managed mentored saved cut hold holds
    held worked used wrote written deployed delivered designed developed owned
    scaled maintained launched founded created migrated architected grew
    taught earned won achieved improved reduced increased automated refactored
    oversaw supervised spearheaded implemented integrated knew did done
    """.split()
)
# A gap has no business describing what the person DOES either: a gap names an
# absence, so the present tense is a claim there even though a reason may use
# it ("you already work in Python" is a fair reason, not a fair gap).
_PRESENT_CLAIM_VERBS = frozenset(
    """
    are is lead leads ship ships build builds drive drives run runs manage
    manages own owns deliver delivers design designs develop develops write
    writes work works use uses bring brings know knows scale scales maintain
    maintains deploy deploys do does mentor mentors hold
    """.split()
)
_GAP_CLAIM_VERBS = _PAST_CLAIM_VERBS | _PRESENT_CLAIM_VERBS

# "your Docker experience", "your production Docker expertise". Up to two words
# in between, which is why "your resume shows no Kubernetes experience" (four,
# and negated) is not caught by it.
_ATTRIBUTED_NOUN = re.compile(
    r"(?<![a-z])your\s+((?:[A-Za-z0-9+#.-]+\s+){1,2})"
    r"(?:experience|background|history|expertise|proficiency|skills)(?![a-z])",
    re.IGNORECASE,
)


def _claim_words(text: str) -> list[str]:
    return [w.replace("’", "'").casefold() for w in _WORD.findall(text)]


def _negates(word: str) -> bool:
    return word in _NEGATORS or "n't" in word


def _is_quantity(word: str) -> bool:
    return word[0].isdigit() or word[0] == "$" or word.endswith("%")


def _claim_at(words: list[str], i: int, verbs: frozenset[str], quantities: bool) -> bool:
    """Does the subject at `words[i]` carry a positive claim after it?"""
    # "you've"/"you'd" ARE the auxiliary; "you're" is the copula.
    contracted = {"you've": "have", "you'd": "had", "you're": "are"}.get(words[i])
    window = ([contracted] if contracted else []) + words[i + 1 :]
    for distance, word in enumerate(window[:_CLAIM_LOOKAHEAD]):
        if _negates(word):
            return False
        if word in verbs:
            following = window[distance + 1] if distance + 1 < len(window) else ""
            if word in _AUXILIARIES and following and _negates(following):
                return False
            return True
        if quantities and distance < _QUANTITY_LOOKAHEAD and _is_quantity(word):
            return True
    return False


def _asserts_about_person(text: str, verbs: frozenset[str], *, elided: bool) -> bool:
    words = _claim_words(text)
    subject_seen = False
    for i, word in enumerate(words):
        if word in _SUBJECTS:
            subject_seen = True
            if _claim_at(words, i, verbs, quantities=True):
                return True
        elif elided and subject_seen and word in _PERFECT:
            # "You need Kubernetes experience and have shipped it in prod."
            # Only after the person has been made the subject, or "Initech has
            # shipped Docker" would read as a claim about them.
            if i and words[i - 1] in _ELIDED_GUARDS:
                continue
            if _claim_at(words, i, verbs, quantities=False):
                return True
    return False


def _claims_history(reason: str) -> bool:
    """Does the reason narrate the person's history, tenure or achievements?

    A reason may say what the posting wants and which of the person's declared,
    on-resume items match it, in the present tense. It may not say what they
    did: "you worked at Initech", "you have 12 years", "you saved $5M" are
    unverifiable even when every name in them is on the resume, and the year or
    the headcount is never on the resume at all.
    """
    return _asserts_about_person(reason, _PAST_CLAIM_VERBS, elided=False)


def _asserts_possession(gap: str) -> bool:
    """Does the gap assert that the person HAS something?

    A gap's job is to name an absence, so possession is always wrong there --
    whatever absence or demand marker the sentence also carries, since a demand
    verb ("the posting wants X") says nothing about the person.
    """
    if _asserts_about_person(gap, _GAP_CLAIM_VERBS, elided=True):
        return True
    for match in _ATTRIBUTED_NOUN.finditer(gap):
        if not any(_negates(w) for w in _claim_words(match.group(1))):
            return True
    return False


def _checked_gap(item: ProseItem, facts: Any, exempt: str = "") -> str | None:
    gap = cut(item.gap, GAP_CHARS)
    missing = (item.gap_missing or "").strip()
    if not gap or not missing:
        return None
    # One requirement, not a phrase: a long gap_missing exempts every name
    # inside it from the scan below.
    if len(missing) > GAP_MISSING_CHARS:
        return None
    # The gap says the person lacks `missing`; if the resume has it, that is
    # a manufactured claim about their history.
    if _on_resume(facts, missing):
        return None
    if not _mentions(gap, missing):
        return None
    if not _ABSENCE.search(gap):
        return None
    # A gap must never assert that the person HAS something.
    if _asserts_possession(gap):
        return None
    # Nothing else name-like may appear: "...you led Terraform migrations".
    # gap_missing is exempt wherever it stands -- naming the requirement the
    # person lacks is the field's purpose -- while the company and the location
    # are exempt only as mentions.
    if _undeclared_names(gap, [missing], facts, exempt=exempt):
        return None
    return gap


# --- the call ----------------------------------------------------------------


class _CallFailed(Exception):
    """A provider fault. Carries whatever usage the response reported: a
    response that bills and then fails must still reach the ledger."""

    def __init__(self, message: str, usage: dict | None = None):
        super().__init__(message)
        self.usage = usage or {}


def _posting_text(job: Any) -> PostingText:
    return PostingText(
        company=str(job.company),
        title=str(job.title),
        location=str(job.location or ""),
        requirements=requirements(getattr(job, "description_text", "")),
    )


def _posting_block(scored: Scored, posting: PostingText) -> str:
    comps = ", ".join(f"{k}={v:.2f}" for k, v in scored.components.items())
    return "\n".join(
        [
            f"job_id: {scored.job_id}",
            f"Title: {posting.title}",
            f"Company: {posting.company}",
            f"Location: {posting.location or 'not stated'}",
            f"Tier: {scored.tier}",
            f"Probabilities: {comps}",
            "Requirements:",
            posting.requirements,
        ]
    )


def _request_body(model: str, resume_text: str, blocks: list[str]) -> dict[str, Any]:
    parts = [PROMPT, "", "RESUME FACTS:", resume_text, "", f"POSTINGS (at most {MAX_ITEMS} items):"]
    parts += ["\n---\n".join(blocks)]
    return {
        "model": model,
        "input": [{"role": "user", "content": "\n".join(parts)}],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "match_prose",
                "strict": True,
                "schema": _schema(),
            }
        },
    }


async def _write_call(*, url: str, headers: dict, body: dict) -> tuple[dict, dict]:
    """The one billable call. Returns (response body, usage).

    Usage is read off the body before anything is parsed, so every
    `_CallFailed` raised after a response arrived carries it.
    """
    try:
        async with httpx.AsyncClient(timeout=PROFILE_TIMEOUT) as client:
            resp = await client.post(url, headers=headers, json=body)
            try:
                data = resp.json()
            except ValueError:
                data = None
    except httpx.HTTPError as exc:
        logger.warning("digest prose transport failed: %s", exc.__class__.__name__)
        raise _CallFailed("provider_error") from None
    usage = usage_of(data) if isinstance(data, dict) else {}
    if resp.status_code >= 400:
        logger.warning("digest prose failed status=%s", resp.status_code)
        raise _CallFailed("provider_error", usage)
    if not isinstance(data, dict):
        raise _CallFailed("unparseable", usage)
    return data, usage


def _record(db, meter: Any, model: str, usage: dict, *, user=None, facts=None) -> None:
    """The counter and the telemetry row, through score.record_round."""
    if not any(v is not None for v in usage.values()):
        return
    record_round(
        db,
        meter=meter,
        user=user,
        facts=facts,
        event={
            "kind": "usage",
            "purpose": "digest",
            "model": model,
            "round_index": 0,
            "input_tokens": usage.get("input_tokens"),
            "cached_tokens": usage.get("cached_tokens"),
            "cache_write_tokens": None,
            "output_tokens": usage.get("output_tokens"),
            "reasoning_tokens": None,
        },
    )


async def write_reasons(db, *, user, facts, scored: list[Scored], jobs: list[Any]) -> ProseRun:
    """One call for the whole digest. Never raises on a provider fault."""
    run = ProseRun(model=REASON_MODEL)
    by_id = {str(getattr(j, "id", "")): j for j in jobs}
    survivors = [s for s in scored if s.job_id in by_id]
    if not survivors:
        return run
    settings = get_settings()
    if not (settings.openai_api_key or "").strip():
        run.error = "llm_not_configured"
        return run
    try:
        resume_text = facts_text(facts)
    except ScoreFailed as exc:
        run.error = str(exc)
        return run

    posting_texts = {s.job_id: _posting_text(by_id[s.job_id]) for s in survivors}
    blocks = [_posting_block(s, posting_texts[s.job_id]) for s in survivors]
    body = _request_body(REASON_MODEL, resume_text, blocks)
    url = responses_url(settings.openai_base_url or "https://api.openai.com/v1")
    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }
    guard = system_guard(
        user=user,
        purpose="digest",
        reason="weekly digest prose; runs from the build job with no HTTP caller to refuse",
    )
    async with metered(guard) as meter:
        try:
            data, usage = await _write_call(url=url, headers=headers, body=body)
        except _CallFailed as exc:
            # A failed call may still have been billed.
            _record(db, meter, REASON_MODEL, exc.usage, user=user, facts=facts)
            run.usage, run.error = exc.usage, str(exc)
            return run
        # Recorded before the answer is read: a response that bills and then
        # fails to parse must not vanish from the ledger.
        _record(db, meter, REASON_MODEL, usage, user=user, facts=facts)
    run.usage = usage

    output = data.get("output") if isinstance(data.get("output"), list) else []
    text = output_text(data, output)
    if not text.strip():
        run.error = "no_output"
        return run
    try:
        batch = _ProseBatch.model_validate_json(text)
    except ValidationError as exc:
        # Where it failed, never what it said: the content is personal data.
        logger.warning(
            "digest prose rejected: %s",
            "; ".join(".".join(str(p) for p in err["loc"]) for err in exc.errors()),
        )
        run.error = "invalid_payload"
        return run
    checked = check(
        batch.items,
        facts=facts,
        scored_ids={s.job_id for s in survivors},
        postings=posting_texts,
    )
    run.prose, run.dropped, run.gaps_rejected = (
        checked.kept, checked.dropped, checked.gaps_rejected,
    )
    return run
