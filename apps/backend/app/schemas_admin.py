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
    # What the day billed, in micro-USD, priced through
    # app.services.usage.round_cost_uusd so this and enforcement can never
    # disagree about what a round cost.
    llm_cost_uusd: int
    # Cache writes bill at 1.25x the uncached input rate and were invisible
    # until migration 014, so rows written before it report none.
    llm_cache_write_tokens: int
    # Rounds the provider reported no usage for, charged at a fallback. Shown
    # as a count rather than folded into the cost silently: if it climbs, the
    # meter is guessing and nothing else would say so.
    llm_unmeasured_calls: int


class AdminOverview(BaseModel):
    health: AdminHealth
    counts: AdminCounts
    series: list[AdminDayPoint]
    recent_users: list[AdminRecentUser]
    recent_resumes: list[AdminRecentResume]


class AdminUsageSubject(BaseModel):
    """One subject's spend inside the current window.

    `subject_kind` is 'user', 'guest' or 'network'. `email` is set only for
    users -- a guest has none, which is the point of a guest.
    """

    subject_kind: str
    subject_id: str
    email: str | None
    tier: str
    credits_used: float
    uusd_used: int
    turns_used: int
    refusals: int
    unmeasured_rounds: int
    # Only meaningful for a cost-capped tier; null for a guest, who is capped
    # on a count instead.
    limit_credits: float | None
    turn_limit: int | None
    over_limit: bool


class AdminUsageCeiling(BaseModel):
    name: str
    spent_uusd: int
    ceiling_uusd: int | None
    # Null when the ceiling is disabled (0), so the UI shows "off" rather than
    # dividing by zero.
    share: float | None


class AdminUsageTier(BaseModel):
    tier: str
    subjects: int
    uusd_used: int
    credits_used: float
    turns_used: int


class AdminUsageRefusal(BaseModel):
    tier: str
    limit_kind: str
    count: int


class AdminUsageGap(BaseModel):
    """A subject whose counter and whose llm_calls rows disagree.

    Either telemetry was lost -- the chat router's insert is best-effort by
    design -- or something billed without going through the meter. The second
    is the failure a context-manager seam cannot rule out by construction, so
    it has to be visible here.
    """

    subject_kind: str
    subject_id: str
    counter_uusd: int
    llm_call_uusd: int


class AdminUsageOverview(BaseModel):
    window_start: str
    window_end: str
    enforced: bool
    credit_uusd: int
    ceilings: list[AdminUsageCeiling]
    by_tier: list[AdminUsageTier]
    top_subjects: list[AdminUsageSubject]
    refusals: list[AdminUsageRefusal]
    gaps: list[AdminUsageGap]
    # Cost this window by purpose: chat, repair, vision, profile.
    by_purpose: list[tuple[str, int]]


class AdminUserUsage(BaseModel):
    """One user's usage, for their detail page."""

    tier: str
    credits_used: float
    limit_credits: float | None
    turns_used: int
    turn_limit: int | None
    remaining_credits: float | None
    # The split behind limit_credits, both already folded into it. Carried
    # separately so the detail page can show why this person's ceiling is not
    # their tier's, without doing the subtraction in the client.
    tier_credits: int | None
    granted_credits: int
    reset_at: str
    refusals: int
    unmeasured_rounds: int
    # All time, not just this window, so a detail page can show whether this
    # week is typical for them.
    lifetime_uusd: int
    lifetime_turns: int


class AdminQuotaGrant(BaseModel):
    """One grant, active or not.

    The lapsed and revoked ones are shown too: a ceiling that dropped is
    exactly what someone opens this list to explain.
    """

    id: str
    credits: int
    reason: str
    detail: str | None = None
    starts_at: str
    expires_at: str | None = None
    revoked_at: str | None = None
    issued_by: str | None = None
    created_at: str
    # Computed server-side rather than inferred from the dates in the client,
    # because the rule is window overlap and not "valid right now" -- see
    # services.grants.grant_status, which is the one definition both this and
    # the person's own /v1/usage read from.
    status: str
    active: bool


class AdminQuotaGrantCreate(BaseModel):
    credits: int
    reason: str
    detail: str | None = None
    expires_at: str | None = None


class AdminUserListItem(BaseModel):
    id: str
    email: str
    locale: str
    picture: str | None
    created_at: str
    resume_count: int
    # This window's spend, so the list itself answers "who is costing the
    # most" without a drill-down. Ordered by it when order='usage'.
    tier: str
    credits_used: float
    turns_used: int


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
    usage: AdminUserUsage
    grants: list[AdminQuotaGrant]


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
