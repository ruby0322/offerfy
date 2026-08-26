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

    model_config = {"from_attributes": True}


class ResumeListItem(BaseModel):
    id: str
    title: str
    source: str
    locale: str

    model_config = {"from_attributes": True}


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
