from pydantic import BaseModel


class AdminHealth(BaseModel):
    api: str
    database: str
    s3_configured: bool


class AdminCounts(BaseModel):
    users: int
    guest_sessions: int
    resumes: int
    resumes_create: int
    resumes_upload: int
    resumes_guest: int
    resumes_user: int
    chat_messages_24h: int
    chat_messages_7d: int
    guest_rate_chat_24h: int
    guest_rate_export_24h: int


class AdminRecentUser(BaseModel):
    id: str
    email: str
    locale: str
    created_at: str
    resume_count: int


class AdminRecentResume(BaseModel):
    id: str
    title: str
    source: str
    import_status: str
    owner_kind: str
    owner_label: str
    created_at: str
    message_count: int


class AdminDayPoint(BaseModel):
    date: str
    users: int
    resumes_create: int
    resumes_upload: int
    chats: int
    guest_rate_chat: int
    guest_rate_export: int
    llm_calls: int
    llm_input_tokens: int
    llm_cached_tokens: int
    llm_output_tokens: int
    llm_reasoning_tokens: int
    # cached / input, over the calls that reported both numbers -- which
    # excludes every vision call and any proxy that drops
    # `input_tokens_details`. None on a day where nothing reported both,
    # because that is "no data", not a 0% hit rate. This is the number that
    # says whether prompt caching works at all through whatever sits in front
    # of the model, so a call whose cache status went unreported must not
    # dilute it.
    llm_cache_hit_rate: float | None


class AdminOverview(BaseModel):
    health: AdminHealth
    counts: AdminCounts
    series: list[AdminDayPoint]
    recent_users: list[AdminRecentUser]
    recent_resumes: list[AdminRecentResume]


class AdminUserListItem(BaseModel):
    id: str
    email: str
    locale: str
    picture: str | None
    created_at: str
    resume_count: int


class AdminUserList(BaseModel):
    items: list[AdminUserListItem]
    total: int


class AdminUserResume(BaseModel):
    id: str
    title: str
    source: str
    import_status: str
    created_at: str
    message_count: int


class AdminUserDetail(BaseModel):
    id: str
    email: str
    google_sub: str
    locale: str
    picture: str | None
    created_at: str
    resumes: list[AdminUserResume]


class AdminResumeListItem(BaseModel):
    id: str
    title: str
    source: str
    import_status: str
    locale: str
    owner_kind: str
    owner_id: str
    owner_label: str
    claimed_at: str | None
    created_at: str
    message_count: int


class AdminResumeList(BaseModel):
    items: list[AdminResumeListItem]
    total: int


class AdminResumeDetail(AdminResumeListItem):
    typst_source: str


class AdminChatMessage(BaseModel):
    id: str
    role: str
    content: str
    created_at: str


class AdminChatMessageList(BaseModel):
    items: list[AdminChatMessage]


class AdminTalentObligation(BaseModel):
    """One recipient we still owe a stop-use notice for one withdrawal."""

    user_id: str
    user_email: str
    withdrawn_at: str
    consent_event_id: str
    api_key_id: str | None = None
    recipient_label: str
    recipient_contact: str
    first_disclosed_at: str
    last_disclosed_at: str
    disclosure_count: int


class AdminTalentObligationList(BaseModel):
    items: list[AdminTalentObligation]
    total: int


class AdminStopUseNoticeCreate(BaseModel):
    recipient_contact: str
    api_key_id: str | None = None
    detail: str | None = None


class AdminStopUseNotice(BaseModel):
    id: str
    user_id: str | None = None
    recipient_contact: str
    channel: str
    sent_by: str
    detail: str | None = None
    created_at: str


class AdminDisclosure(BaseModel):
    id: str
    user_id: str | None = None
    api_key_id: str | None = None
    recipient_label: str
    recipient_contact: str
    scope: str
    profile_content_hash: str
    policy_version: str
    route: str
    created_at: str


class AdminDisclosureList(BaseModel):
    items: list[AdminDisclosure]
    total: int


class AdminTalentKeyCreate(BaseModel):
    label: str
    contact: str
    expires_in_days: int | None = None


class AdminTalentKey(BaseModel):
    id: str
    label: str
    contact: str
    created_by: str
    created_at: str
    expires_at: str | None = None
    revoked_at: str | None = None
    last_used_at: str | None = None
    disclosure_count: int = 0


class AdminTalentKeyCreated(AdminTalentKey):
    """The only response that ever carries the raw token."""

    token: str


class AdminTalentKeyList(BaseModel):
    items: list[AdminTalentKey]
    total: int
