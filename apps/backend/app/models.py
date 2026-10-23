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
        CheckConstraint("kind IN ('master', 'tailor')", name="resume_kind_values"),
        CheckConstraint(
            "(kind = 'master' AND parent_resume_id IS NULL) "
            "OR (kind = 'tailor' AND parent_resume_id IS NOT NULL)",
            name="resume_kind_parent",
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
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    published_revision_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey(
            "resume_revisions.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_resumes_published_revision_id",
        ),
        nullable=True,
    )
    kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default="master", server_default="master"
    )
    parent_resume_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("resumes.id", ondelete="CASCADE"), nullable=True, index=True
    )
    forked_from_revision_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey(
            "resume_revisions.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_resumes_forked_from_revision_id",
        ),
        nullable=True,
    )
    target_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    target_context: Mapped[str | None] = mapped_column(Text, nullable=True)

    guest_session: Mapped[GuestSession | None] = relationship(back_populates="resumes")
    user: Mapped[User | None] = relationship(back_populates="resumes")
    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="resume", cascade="all, delete-orphan"
    )
    share: Mapped["ResumeShare | None"] = relationship(
        back_populates="resume", uselist=False, cascade="all, delete-orphan"
    )
    revisions: Mapped[list["ResumeRevision"]] = relationship(
        back_populates="resume",
        cascade="all, delete-orphan",
        foreign_keys="ResumeRevision.resume_id",
    )
    published_revision: Mapped["ResumeRevision | None"] = relationship(
        foreign_keys=[published_revision_id],
        post_update=True,
        uselist=False,
    )
    parent: Mapped["Resume | None"] = relationship(
        remote_side="Resume.id",
        back_populates="tailors",
        foreign_keys=[parent_resume_id],
    )
    tailors: Mapped[list["Resume"]] = relationship(
        back_populates="parent",
        cascade="all, delete-orphan",
        foreign_keys="Resume.parent_resume_id",
        order_by="Resume.created_at",
    )
    memory_items: Mapped[list["MemoryItem"]] = relationship(
        back_populates="master",
        cascade="all, delete-orphan",
        foreign_keys="MemoryItem.master_resume_id",
    )


class ResumeRevision(Base):
    __tablename__ = "resume_revisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    resume_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("resumes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    typst_source: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    resume: Mapped["Resume"] = relationship(
        back_populates="revisions",
        foreign_keys=[resume_id],
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


class MemoryItem(Base):
    __tablename__ = "memory_items"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'accepted', 'dismissed')",
            name="memory_status_values",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    master_resume_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("resumes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_resume_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("resumes.id", ondelete="SET NULL"), nullable=True
    )
    text: Mapped[str] = mapped_column(String(280), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    master: Mapped[Resume] = relationship(
        back_populates="memory_items", foreign_keys=[master_resume_id]
    )


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


LLM_CALL_PURPOSES = ("chat", "repair", "vision")


class LlmCall(Base):
    """One model call, with what it billed.

    Written from the chat router, never from the service that makes the call:
    `aiter_llm_turn` has no Session and keeps it that way. A row is inserted per
    round even when the provider reports no usage at all, because
    OPENAI_BASE_URL may be a proxy that omits it -- and knowing the spend is
    unmeasurable is itself the measurement.
    """

    __tablename__ = "llm_calls"
    __table_args__ = (
        CheckConstraint(
            "purpose IN ('chat', 'repair', 'vision')", name="llm_call_purpose_values"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, index=True
    )
    resume_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("resumes.id", ondelete="SET NULL"), nullable=True
    )
    purpose: Mapped[str] = mapped_column(String(16), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    # 0-based round within one turn. The repair loop restarts the count, so a
    # round_index only means something alongside its purpose.
    round_index: Mapped[int] = mapped_column(nullable=False, default=0)
    prefer_full_source: Mapped[bool] = mapped_column(nullable=False, default=False)
    # Nullable on purpose: a proxy may report part of the usage block, or none.
    input_tokens: Mapped[int | None] = mapped_column(nullable=True)
    cached_tokens: Mapped[int | None] = mapped_column(nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(nullable=True)
    reasoning_tokens: Mapped[int | None] = mapped_column(nullable=True)
    # Length of the Typst source entering this round, to correlate cost with
    # document size.
    source_chars: Mapped[int] = mapped_column(nullable=False, default=0)


CONSENT_PURPOSES = ("service", "talent_introduction")
CONSENT_ACTIONS = ("granted", "withdrawn")


class ConsentEvent(Base):
    """One consent decision, as the person it belongs to made it.

    Append-only. A withdrawal is a new row with action='withdrawn', never an
    UPDATE or a DELETE, because the question we have to be able to answer is
    not "do they agree now" but "what did they agree to, when, and in which
    language". Current state is the latest row per (subject, purpose) -- read
    it through app.services.consent, never by querying this table directly.

    There is deliberately no hash of the text they saw. The policy lives in the
    frontend's message files, which this container cannot read, so storing one
    would mean keeping a second copy of every version here. policy_version
    plus the git history of messages/*.json answers the same question.

    guest_session_id exists but is never written today: a guest sees no legal
    notice anywhere in the product, so a 'granted' row for one would be
    evidence of a consent that never happened. Guest work is processed as
    necessary to the service they asked for, and the ledger picks them up at
    /v1/auth/claim -- the moment the data first becomes identifiable.

    Both subject columns are nullable and ON DELETE SET NULL, so erasing an
    account leaves the consent record standing with no subject. That is the
    point: we must be able to show consent for data we no longer hold.
    """

    __tablename__ = "consent_events"
    __table_args__ = (
        CheckConstraint(
            "purpose IN ('service', 'talent_introduction')",
            name="consent_purpose_values",
        ),
        CheckConstraint(
            "action IN ('granted', 'withdrawn')",
            name="consent_action_values",
        ),
        # Not an XOR: both NULL is the erased-subject case above. Only both
        # set at once is nonsense.
        CheckConstraint(
            "NOT (user_id IS NOT NULL AND guest_session_id IS NOT NULL)",
            name="consent_subject_not_both",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    guest_session_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("guest_sessions.id", ondelete="SET NULL"), nullable=True
    )
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    # The language they actually read the notice in, not their current preference.
    locale: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    # Human-readable context only, e.g. 'claimed=3'. Never parsed by code.
    detail: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, index=True
    )
