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
