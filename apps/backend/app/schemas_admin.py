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
