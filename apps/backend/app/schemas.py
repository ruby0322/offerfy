from typing import Literal

from pydantic import BaseModel, Field


LOCALES = ("en", "zh-TW", "zh-CN")


class ResumeCreate(BaseModel):
    title: str | None = None
    locale: str = "zh-TW"


class ResumeUpdate(BaseModel):
    typst_source: str | None = None
    title: str | None = None


class ResumeOut(BaseModel):
    id: str
    title: str
    typst_source: str
    source: str
    locale: str
    import_status: str = "idle"
    upload_s3_key: str | None = None
    claimed_at: str | None = None
    created_at: str
    updated_at: str | None = None
    published_revision_id: str | None = None
    unpublished_changes: bool = True
    kind: str = "master"
    parent_resume_id: str | None = None
    target_label: str | None = None
    target_context: str | None = None

    model_config = {"from_attributes": True}


class ResumeListItem(BaseModel):
    id: str
    title: str
    source: str
    locale: str
    kind: str = "master"
    parent_resume_id: str | None = None
    updated_at: str | None = None

    model_config = {"from_attributes": True}


class TailorCreate(BaseModel):
    title: str
    target_label: str | None = None
    target_context: str | None = None
    fork_from: Literal["master", "current"] = "master"


class FamilyMember(BaseModel):
    id: str
    title: str
    kind: str
    target_label: str | None = None
    updated_at: str | None = None


class ResumeFamilyOut(BaseModel):
    master: FamilyMember
    tailors: list[FamilyMember]


class MemoryItemOut(BaseModel):
    id: str
    text: str
    status: str
    source_resume_id: str | None = None
    created_at: str


class CompileBody(BaseModel):
    format: str = Field(pattern="^(svg|pdf)$")


class PreviewPages(BaseModel):
    pages: list[str]


class AtsCheck(BaseModel):
    name: str
    passed: bool


class AtsReport(BaseModel):
    checks: list[AtsCheck]


class ChatRequest(BaseModel):
    message: str
    prefer_full_source: bool = False


class ChatMessageOut(BaseModel):
    id: str
    role: str
    content: str

    model_config = {"from_attributes": True}


class ChatResponse(BaseModel):
    message: ChatMessageOut
    messages: list[ChatMessageOut]
    typst_source: str
    applied: bool = False


class AuthMe(BaseModel):
    user: dict | None = None
    guest: bool = True


class AccountOut(BaseModel):
    """The signed-in person's own account profile.

    `email` and `picture` are included read-only, because this is the shape the
    settings form renders and a form that shows some of someone's identity and
    hides the rest is confusing. They are changed by signing in again, not here.

    `display_name` is the editable name; `google_name` is what Google asserted,
    shown as the placeholder so the field can be left empty and still display
    something. Keeping both visible is the only way the precedence is legible.
    """

    email: str
    picture: str | None = None
    google_name: str | None = None
    username: str | None = None
    display_name: str | None = None
    headline: str | None = None
    location: str | None = None
    website: str | None = None
    bio: str | None = None


class AccountUpdate(BaseModel):
    """A partial update. Absent means "leave it"; empty or null means "clear it".

    Every field is a plain string rather than a constrained type: the lengths
    and the character rules live in services.account, which normalises first
    and so can accept a value pydantic would have refused outright (a pasted
    name with a trailing space, a URL with no scheme). Refusals come back as a
    field plus a code the client translates.
    """

    username: str | None = None
    display_name: str | None = None
    headline: str | None = None
    location: str | None = None
    website: str | None = None
    bio: str | None = None


class ClaimResponse(BaseModel):
    claimed: int


class ShareUpdate(BaseModel):
    public: bool


class ShareState(BaseModel):
    public: bool
    token: str | None = None
    published_revision_id: str | None = None
    published_at: str | None = None
    unpublished_changes: bool = True


class RevisionListItem(BaseModel):
    id: str
    created_at: str
    is_published: bool


class RevisionDetail(BaseModel):
    id: str
    created_at: str
    is_published: bool
    typst_source: str


class PublicShareOut(BaseModel):
    title: str
    locale: str


class UsageOut(BaseModel):
    """One caller's weekly AI allowance.

    Credits, never micro-USD: the ledger is money so that cheap edits cost
    less, but a published allowance that is re-denominated every time a
    provider changes a price is not an allowance. `approx_edits_left` is the
    number people actually read, derived from their own trailing cost per
    turn.

    Both halves are nullable because the tiers are capped differently -- a
    guest on a turn count has no credit limit, and the account tiers have no
    turn limit. Exactly one side is set.
    """

    tier: Literal["guest", "registered", "matching"]
    # False in development, so a client can say "not enforced here" rather
    # than drawing an empty meter.
    enforced: bool
    # Both ends of the window, so a page can name the period it is reporting
    # on without re-deriving "the most recent Monday in Taipei" in the client.
    # Getting that rule wrong in a second place is how a meter comes to
    # disagree with itself about which week it is showing.
    period_start: str
    reset_at: str
    limit_credits: float | None
    used_credits: float
    remaining_credits: float | None
    # The split behind limit_credits. tier_credits is the published number for
    # this tier; granted_credits is what this person has been granted on top of
    # it. Sent so a meter can say "20 + 50" rather than an unexplained 70 --
    # /v1/usage/tiers deliberately knows nothing about grants, so a client
    # comparing the two would otherwise see a contradiction.
    tier_credits: int | None
    granted_credits: int
    # Every grant this person has held, newest first; empty for a guest, who
    # cannot hold one. Carried on the balance rather than its own endpoint so a
    # ceiling and its explanation can never be fetched a week apart.
    grants: list["UsageGrantOut"] = []
    turn_limit: int | None
    turns_used: int
    turns_remaining: int | None
    approx_edits_left: int | None
    exhausted: bool
    # Rounds the provider reported no usage for, charged at a fallback. Shown
    # because a meter that is guessing should say so.
    unmeasured_rounds: int


class UsageGrantOut(BaseModel):
    """One grant, as the person who holds it may see it.

    Deliberately narrower than the admin shape. `detail` is an operator's note
    written for whoever reads it in six months -- shorthand, ticket numbers, a
    cohort name -- and `issued_by` is a colleague's address; neither is written
    with this reader in mind, so neither is sent. What is left is what someone
    needs to understand their own ceiling: how much, roughly why, and for how
    long.

    Expired and revoked grants are included. Someone whose allowance dropped is
    owed the reason, and the same argument that puts them on the admin page
    puts them here.
    """

    credits: int
    # The enum, not prose. The client renders it in its own words, because the
    # admin wording ('Manual') means nothing to the person who received it.
    reason: str
    status: Literal["active", "scheduled", "expired", "revoked"]
    starts_at: str
    expires_at: str | None


class UsageTierOut(BaseModel):
    """One published tier. The shape the public /usage page renders."""

    name: Literal["guest", "registered", "matching"]
    # Exactly one of these is set, because the tiers are capped differently:
    # a guest on a count of AI edits, an account on cost. See the design spec.
    credits: int | None
    turns: int | None
    # Derived here so the page does not have to know the credit peg.
    approx_edits: int
    can_publish: bool


class UsageTiersOut(BaseModel):
    """Every tier, for the public page.

    Public and subject-free on purpose: the page has to render the same table
    for a crawler and for a signed-out visitor as for a signed-in one.

    It exists so the page never holds a copy of the numbers. A published
    allowance that has drifted from the enforced one is the worst bug this page
    could have, and fetching makes that drift impossible rather than merely
    tested for. On this host it is also the cheaper thing to operate: tuning
    the tiers is then a code change plus a zero-downtime API reload, where a
    number baked into the message files would need a frontend rebuild and ~40s
    of downtime (AGENTS.md section 4).
    """

    tiers: list[UsageTierOut]
    # Monday 00:00 Asia/Taipei, so the page can say when the week turns over
    # without re-deriving the rule.
    reset_at: str
    enforced: bool


class ConsentUpdate(BaseModel):
    granted: bool


class ConsentState(BaseModel):
    # One key per purpose the person controls themselves, so the settings page
    # does not have to know the purpose list.
    consents: dict[str, bool]
    policy_version: str


# Caps on the payload. They bound what one read hands over.
#
# Enforced by services.profile.clamp_payload, NOT by Field(max_length=...) on
# the fields below. A length constraint here would make pydantic *reject* a
# payload that is one bullet point too long, and the whole profile would be
# thrown away -- which is exactly what happened to the first real resume this
# ran on: one role had five highlights where the cap was four. These are
# limits, not a contract the provider has to satisfy, so they truncate.
TALENT_SKILL_LIMIT = 40
TALENT_ROLE_LIMIT = 12
TALENT_EDUCATION_LIMIT = 6
TALENT_HIGHLIGHT_LIMIT = 4
TALENT_LOCATION_LIMIT = 4
TALENT_LANGUAGE_LIMIT = 6
TALENT_HEADLINE_CHARS = 120
TALENT_SUMMARY_CHARS = 600
TALENT_NAME_CHARS = 255
TALENT_YEARS_MAX = 60
TALENT_SUMMARY_SKILLS = 12


class TalentRole(BaseModel):
    employer: str | None = None
    title: str | None = None
    # YYYY-MM, or Present/至今/现在 -- the convention llm.SYSTEM_PROMPT already
    # enforces inside the documents. Kept as written: a date we could not parse
    # is better shown verbatim than dropped.
    start: str | None = None
    end: str | None = None
    highlights: list[str] = Field(default_factory=list)


class TalentEducation(BaseModel):
    institution: str | None = None
    credential: str | None = None
    field: str | None = None
    end: str | None = None


class TalentProfilePayload(BaseModel):
    """What an employer may be shown, stored as talent_profiles.profile_json.

    There is deliberately no email, phone, URL, address or social-handle field,
    and none may be added: introductions go through Offerfy, so a reader who
    has this payload still cannot contact the person directly. Three things
    enforce that -- this schema having nowhere to put one, the prompt saying
    not to, and services.profile.scrub_contacts removing them from the text
    fields regardless.
    """

    schema_version: int = 1
    display_name: str | None = None
    headline: str | None = None
    summary: str | None = None
    years_experience: int | None = None
    # City or region only. A street address is a contact detail.
    locations: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    roles: list[TalentRole] = Field(default_factory=list)
    education: list[TalentEducation] = Field(default_factory=list)


class TalentProfileDetail(BaseModel):
    """One profile as a credentialed reader sees it.

    The identifier is the profile's own id, never the user's: handing an
    external reader a user id makes it correlatable with anything that ever
    leaks one.
    """

    profile_id: str
    updated_at: str
    profile: TalentProfilePayload


class TalentProfileSummary(BaseModel):
    """Enough to decide whom to open, and no more."""

    profile_id: str
    display_name: str | None = None
    headline: str | None = None
    years_experience: int | None = None
    locations: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    updated_at: str


class TalentProfileSummaryList(BaseModel):
    items: list[TalentProfileSummary]
    # Counted before the per-candidate consent filter, so it is advisory. A
    # page can be shorter than limit; see services.profile.readable_profiles.
    total: int
    limit: int
    offset: int


class TalentProfileSourceUpdate(BaseModel):
    resume_id: str | None = None


class TalentProfileStatus(BaseModel):
    """The owner's view: what we hold, where it came from, who has read it."""

    consent_granted: bool
    source_resume_id: str | None = None
    source_resume_title: str | None = None
    has_published_revision: bool = False
    profile: TalentProfilePayload | None = None
    stale: bool = False
    built_at: str | None = None
    disclosure_count: int = 0
    last_disclosed_at: str | None = None
    # Why the last build attempted by this request failed, when one was
    # attempted and failed. Not stored: setting the source attempts a build
    # opportunistically and must not refuse the choice, so without this the
    # person sees "no profile yet" and cannot tell that anything went wrong.
    build_error: str | None = None
