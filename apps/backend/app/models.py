import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    google_sub: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    locale: Mapped[str] = mapped_column(String(16), nullable=False, default="zh-TW")
    picture: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    resumes: Mapped[list["Resume"]] = relationship(back_populates="user")


class GuestSession(Base):
    __tablename__ = "guest_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    locale: Mapped[str] = mapped_column(String(16), nullable=False, default="zh-TW")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    resumes: Mapped[list["Resume"]] = relationship(back_populates="guest_session")
    rate_events: Mapped[list["RateEvent"]] = relationship(back_populates="guest_session")


class Resume(Base):
    __tablename__ = "resumes"
    __table_args__ = (
        CheckConstraint(
            "(guest_session_id IS NOT NULL AND user_id IS NULL) "
            "OR (guest_session_id IS NULL AND user_id IS NOT NULL)",
            name="resume_owner_xor",
        ),
        CheckConstraint(
            "source IN ('create', 'upload')",
            name="resume_source_values",
        ),
        CheckConstraint(
            "import_status IN ('idle', 'pending', 'done', 'failed')",
            name="resume_import_status_values",
        ),
        UniqueConstraint("id", name="resumes_id_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="Untitled")
    typst_source: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    locale: Mapped[str] = mapped_column(String(16), nullable=False, default="zh-TW")
    guest_session_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("guest_sessions.id"), nullable=True
    )
    user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    upload_s3_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    import_status: Mapped[str] = mapped_column(String(16), nullable=False, default="idle")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    guest_session: Mapped[GuestSession | None] = relationship(back_populates="resumes")
    user: Mapped[User | None] = relationship(back_populates="resumes")
    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="resume", cascade="all, delete-orphan"
    )
    share: Mapped["ResumeShare | None"] = relationship(
        back_populates="resume", uselist=False, cascade="all, delete-orphan"
    )


class ResumeShare(Base):
    __tablename__ = "resume_shares"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    resume_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("resumes.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    token: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    resume: Mapped[Resume] = relationship(back_populates="share")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    resume_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("resumes.id"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    resume: Mapped[Resume] = relationship(back_populates="messages")


class RateEvent(Base):
    __tablename__ = "rate_events"
    __table_args__ = (
        CheckConstraint("kind IN ('chat', 'export')", name="rate_event_kind_values"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    guest_session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("guest_sessions.id"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    guest_session: Mapped[GuestSession] = relationship(back_populates="rate_events")


JOB_SOURCES = ("greenhouse", "lever", "ashby", "taiwanjobs")


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(
            "source IN ('greenhouse', 'lever', 'ashby', 'taiwanjobs')",
            name="job_source_values",
        ),
        UniqueConstraint("source", "source_id", name="jobs_source_source_id_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[str] = mapped_column(String(255), nullable=False)
    company: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    location: Mapped[str | None] = mapped_column(String(512), nullable=True)
    remote: Mapped[bool | None] = mapped_column(nullable=True)
    apply_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    description_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    description_html: Mapped[str] = mapped_column(Text, nullable=False, default="")
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)
    spotlight_score: Mapped[float] = mapped_column(nullable=False, default=0.0)


class JobIngestRun(Base):
    __tablename__ = "job_ingest_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    board_slug: Mapped[str | None] = mapped_column(String(255), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ok_count: Mapped[int] = mapped_column(nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(nullable=False, default=0)
    upserted_count: Mapped[int] = mapped_column(nullable=False, default=0)
    expired_count: Mapped[int] = mapped_column(nullable=False, default=0)
    error_snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ok")
