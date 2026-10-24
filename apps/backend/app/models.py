import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
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
    # Google hands us a name under the scope we already request. Stored because
    # a name inferred from Typst by regex or by a model is a guess, and a wrong
    # name on a profile an employer reads is worse than no name at all.
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Which resume feeds this person's talent profile. Required because kind
    # defaults to 'master' for every resume, so "the master resume" is not a
    # thing a user has one of. It lives here rather than on TalentProfile so
    # the choice survives a withdrawal, which deletes the profile.
    #
    # use_alter is mandatory: resumes.user_id already points back at this
    # table, and create_all cannot otherwise order the two.
    profile_resume_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey(
            "resumes.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_users_profile_resume_id",
        ),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    resumes: Mapped[list["Resume"]] = relationship(
        back_populates="user", foreign_keys="Resume.user_id"
    )


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
    # foreign_keys is explicit on both sides: users.profile_resume_id is a
    # second path between these two tables, so the owner join is ambiguous
    # without it.
    user: Mapped[User | None] = relationship(
        back_populates="resumes", foreign_keys="Resume.user_id"
    )
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
            # taiwanjobs is retired but stays listed: historical rows carry it.
            "source IN ('greenhouse', 'lever', 'ashby', 'smartrecruiters', "
            "'workday', 'taiwanjobs')",
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
    # One id shared by every row of a single ingest pass. Nullable because rows
    # written before migration 014 predate the concept; the admin ingest view
    # skips those rather than inventing a pass for them.
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    board_slug: Mapped[str | None] = mapped_column(String(255), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, index=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ok_count: Mapped[int] = mapped_column(nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(nullable=False, default=0)
    upserted_count: Mapped[int] = mapped_column(nullable=False, default=0)
    expired_count: Mapped[int] = mapped_column(nullable=False, default=0)
    error_snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ok")


LLM_CALL_PURPOSES = ("chat", "repair", "vision", "profile")


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
            "purpose IN ('chat', 'repair', 'vision', 'profile')",
            name="llm_call_purpose_values",
        ),
        # Not an XOR: a row whose subject has been erased has neither, the same
        # case ConsentEvent documents for its own two subject columns. Only both
        # at once is nonsense.
        CheckConstraint(
            "NOT (user_id IS NOT NULL AND guest_session_id IS NOT NULL)",
            name="llm_call_subject_not_both",
        ),
        # app.services.usage filters on (subject, created_at) together on every
        # chat turn, so the composite is the index that matters; it also covers
        # the leading column, which is why neither has index=True of its own.
        Index("ix_llm_calls_user_window", "user_id", "created_at"),
        Index("ix_llm_calls_guest_window", "guest_session_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, index=True
    )
    resume_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("resumes.id", ondelete="SET NULL"), nullable=True
    )
    # Who the spend belongs to, copied in at write time rather than reached
    # through resume_id -- the same denormalisation DisclosureEvent makes for
    # recipient_label, and for the same reason: the row has to still answer the
    # question after the thing it pointed at has moved or gone. Here there are
    # two concrete ways it moves, and both of them reset a cost quota:
    #
    #   (a) resume_id is ON DELETE SET NULL, and deleting a resume is a
    #       supported one-click action. Spend joined through it disappears.
    #   (b) /v1/auth/claim sets resume.user_id and clears guest_session_id, so
    #       every round a guest already spent would retroactively join to the
    #       account they just signed up with -- charging a new account for the
    #       trial it converted from.
    #
    # Written by the chat router and the profile builder; nullable because a
    # subject can be erased, and SET NULL so erasing one leaves the spend
    # recorded with no owner.
    #
    # Both FKs are named, and the names must equal migration 014's. Postgres
    # auto-names an unnamed one `llm_calls_user_id_fkey` while the migration
    # writes `fk_llm_calls_user_id`, and ensure_schema() builds this table from
    # these models in production -- so leaving them unnamed means a later
    # drop_constraint finds nothing on exactly the databases that were never
    # migrated. Same trap TalentProfile.__table_args__ already documents;
    # caught here by diffing `\d llm_calls` between a migrated database and a
    # create_all one.
    user_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="SET NULL", name="fk_llm_calls_user_id"),
        nullable=True,
    )
    guest_session_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey(
            "guest_sessions.id",
            ondelete="SET NULL",
            name="fk_llm_calls_guest_session_id",
        ),
        nullable=True,
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
    # Billed separately, at 1.25x the uncached input rate on gpt-5.6-terra (the
    # 30m TTL; 12h is 2.5x). Unlike cached_tokens and reasoning_tokens this is
    # NOT a subset of anything else in this row -- it is its own charge on top
    # of the read, and it measured 20-45% of a turn. Nullable like its
    # siblings: a proxy may not report it.
    cache_write_tokens: Mapped[int | None] = mapped_column(nullable=True)
    # Length of the Typst source entering this round, to correlate cost with
    # document size.
    source_chars: Mapped[int] = mapped_column(nullable=False, default=0)


USAGE_SUBJECT_KINDS = ("user", "guest", "network")


class UsageCounter(Base):
    """One subject's spend inside one weekly window. The quota's source of truth.

    Separate from LlmCall on purpose, and the reason is durability rather than
    shape. `_persist_usage` in the chat router is wrapped in try/except by
    design -- telemetry may be lost, the work may not -- which was right while
    LlmCall was only telemetry and became a contradiction the moment a budget
    read from it: a lost telemetry row would be a free turn. So LlmCall keeps
    its best-effort writes and its per-round detail for admin and cost
    analysis, and this table carries the number the limit is enforced against.

    The two are reconciled rather than trusted. A persistent gap means either
    telemetry was lost or something billed without going through the meter --
    the second being the failure a context-manager seam cannot rule out by
    construction, so it has to be observable instead.

    Incremented with a single atomic INSERT .. ON CONFLICT DO UPDATE ..
    RETURNING through app.services.metering, which is the only writer. The
    RETURNING is not decoration: enforcement used to read-then-decide with no
    lock, so two concurrent turns could each see the last credit.

    No foreign key on subject_id, for three reasons: a counter has to outlive
    an erased subject (the spend happened), it is pruned on its own schedule
    rather than with the subject, and 'network' subjects -- the guest IP HMAC
    that stops a cookie reset from minting a fresh allowance -- have no table
    to point at.

    Weekly only. The hourly burst guard still counts LlmCall rows: a per-week
    counter cannot answer "turns in the last hour", and burst is an abuse guard
    rather than an allowance, so best-effort is the right durability for it.
    Do not try to unify them.
    """

    __tablename__ = "usage_counters"
    __table_args__ = (
        CheckConstraint(
            "subject_kind IN ('user', 'guest', 'network')",
            name="usage_counter_subject_kinds",
        ),
        CheckConstraint(
            "uusd >= 0 AND turns >= 0 AND unmeasured >= 0",
            name="usage_counter_non_negative",
        ),
        # The ceilings sum every subject for one window, so the window leads.
        # The primary key already serves the per-subject lookup. Must match
        # migration 015 by name: ensure_schema() builds this from here.
        Index("ix_usage_counters_window", "window_start", "subject_kind"),
    )

    # Composite natural key. No surrogate id: the UPSERT conflict target *is*
    # the identity, and a surrogate would let the same subject-window exist
    # twice.
    subject_kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    # 36 for a uuid subject, shorter for a truncated network HMAC.
    subject_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # Monday 00:00 Asia/Taipei, stored as UTC. Derived from the clock by
    # app.services.usage.window_start -- never written by a scheduled job.
    window_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True
    )

    uusd: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    # Chat turns started. Counted here rather than re-derived because the count
    # is what caps a guest, and it must be as durable as the cost side.
    turns: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    # Rounds the provider reported no usage for, charged at a fallback. Carried
    # so the meter can say when it is guessing.
    unmeasured: Mapped[int] = mapped_column(
        nullable=False, default=0, server_default="0"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


USAGE_LIMIT_KINDS = (
    "weekly_turns",
    "weekly_credits",
    "burst",
    "global_ceiling",
    "guest_pool",
)


class UsageRefusal(Base):
    """One time we told someone no, and which limit said so.

    Append-only, like ConsentEvent and DisclosureEvent. Nothing was written
    when a request was refused before this table, which left the only questions
    that matter for sizing the tiers unanswerable:

      - How often does a guest reach the wall? Is four edits the right number?
      - Does a signed-in person reach 20 credits? **This one decides whether a
        promise in the Terms is true.** The matching bonus is lawfully a
        thank-you only while the signed-in tier is independently sufficient,
        and that claim currently rests on an estimate of how many edits
        polishing a page takes. If registered users are hitting the wall, the
        claim is false and the tiers have to change.
      - Which limit fires most? Each of the five implies a different fix.
      - Is the hourly burst cap catching honest use? 15 an hour was a guess.

    This is the one table in the usage work that exists purely to watch people
    rather than to serve them, so it is kept to the minimum that answers those
    questions: no message content, no resume content, no IP, no user agent. It
    is named in the privacy policy and deleted after 90 days.

    Deliberately NOT a per-person conversion funnel. "Did this guest sign in
    after hitting the wall" would need a stored link from a guest session to
    the account it became, and /v1/auth/claim destroys that link on purpose --
    it nulls resumes.guest_session_id and records consent against the user
    alone. Rebuilding it would mean adding personal-data linkage for an
    analytics question. The rate of guest refusals against the rate of claims
    over the same days answers it well enough to size a tier, and costs
    nothing.

    No foreign key on subject_id, for the reasons UsageCounter gives: it has to
    outlive an erased subject, it is pruned on its own schedule, and a
    'network' subject has no table to point at.
    """

    __tablename__ = "usage_refusals"
    __table_args__ = (
        CheckConstraint(
            "subject_kind IN ('user', 'guest', 'network')",
            name="usage_refusal_subject_kinds",
        ),
        CheckConstraint(
            "limit_kind IN ('weekly_turns', 'weekly_credits', 'burst', "
            "'global_ceiling', 'guest_pool')",
            name="usage_refusal_limit_kinds",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, index=True
    )
    subject_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(64), nullable=False)
    tier: Mapped[str] = mapped_column(String(16), nullable=False)
    limit_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    purpose: Mapped[str] = mapped_column(String(16), nullable=False)
    # What they had spent when they were refused, so a refusal can be read
    # without joining back to a counter that has since moved on or been pruned.
    used_uusd: Mapped[int] = mapped_column(nullable=False, default=0)
    turns_used: Mapped[int] = mapped_column(nullable=False, default=0)


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


TALENT_PROFILE_SCHEMA_VERSION = 1


class TalentProfile(Base):
    """The structured profile we may hand to an employer, for one person.

    Keyed on the user, not on a resume: consent_events' subject is a user, so
    a withdrawal is one row against one profile rather than a fan-out over
    however many documents they happen to own. The resume only says where the
    content came from.

    Derived state, not a ledger. Unlike ConsentEvent and DisclosureEvent this
    row is updated in place and deleted outright -- on withdrawal, when the
    source resume goes, or when the person points somewhere else. The audit
    trail lives in those two append-only tables; this is a cache of a document
    we were told we may show.

    It holds no contact details by construction. There is no column for one,
    the payload schema has no field for one, and services.profile.scrub_contacts
    strips them from the text fields anyway. A reader who has this still cannot
    reach the person except through Offerfy.
    """

    __tablename__ = "talent_profiles"
    __table_args__ = (
        # Named here rather than unique=True on the column, so that this and
        # alembic produce the same object. unique=True plus index=True renders
        # as one UNIQUE INDEX, while the migration writes a named UNIQUE
        # constraint and a plain index -- and ensure_schema() builds from these
        # models in production, so the two shapes have to agree or a later
        # drop_constraint finds nothing.
        UniqueConstraint("user_id", name="talent_profiles_user_id_key"),
        CheckConstraint(
            "years_experience IS NULL OR (years_experience >= 0 AND years_experience <= 60)",
            name="talent_profile_years_range",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # CASCADE, not SET NULL as the ledgers use: this is a copy of personal
    # data, not evidence that it was collected, so erasing the account must
    # erase it.
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Which resume, and which revision of it, this was built from. CASCADE
    # because a profile with no source resume is unsourced -- deleting the
    # resume is one more automatic kill switch.
    source_resume_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("resumes.id", ondelete="CASCADE"), nullable=False
    )
    source_revision_id: Mapped[str] = mapped_column(String(36), nullable=False)
    # Hash of everything that fed the build: the revision, its source, and the
    # accepted memory items. Staleness is computed by comparing this and the
    # revision pointer, so the publish path needs no code to invalidate
    # anything.
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    schema_version: Mapped[int] = mapped_column(
        nullable=False, default=TALENT_PROFILE_SCHEMA_VERSION, server_default="1"
    )
    # Text, not JSON: disclosure_events.profile_content_hash is the evidence
    # that a recipient holds this exact payload, so the bytes have to be stable
    # across a write-read round trip. Postgres JSONB normalises key order and
    # numeric form and would break that. Nothing queries inside the blob --
    # the columns below exist for that. Same idiom as ResumeRevision.
    profile_json: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    # Denormalised for the list endpoint, never a second source of truth:
    # services.profile rebuilds every one of these from profile_json on write.
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    headline: Mapped[str | None] = mapped_column(String(255), nullable=True)
    years_experience: Mapped[int | None] = mapped_column(nullable=True)
    # Lowercased free text for the q filter, same plain-LIKE approach the jobs
    # router already uses.
    search_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Pipe-delimited and lowercased, '|python|go|', so LIKE '%|java|%' cannot
    # match 'javascript'.
    skills_text: Mapped[str] = mapped_column(Text, nullable=False, default="")

    built_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, index=True
    )
    model: Mapped[str] = mapped_column(String(128), nullable=False, default="")

    user: Mapped[User] = relationship(foreign_keys=[user_id])


class TalentApiKey(Base):
    """A credential we issue to one named organisation, by hand.

    Not self-serve, and no route anywhere mints one except the admin endpoint:
    apps/frontend/lib/agent-markdown.ts tells every agent that reads it there
    is no public agent API, no machine OAuth server and no self-serve API
    keys, and all three of those statements have to stay true.

    key_hash follows GuestSession.key_hash: unsalted SHA-256 of the raw token.
    Correct here for the same reason it is there -- the secret is 256 bits from
    secrets.token_urlsafe, not a guessable password, so stretching buys nothing
    and lookup stays one indexed equality. Do not "fix" this to bcrypt; it
    would break the lookup without making the token harder to guess.

    revoked_at and last_used_at are mutable, unlike the ledgers below. This is
    credential state, not a record of what happened.
    """

    __tablename__ = "talent_api_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    # Who holds it, in words, so a disclosure row can name them to the person
    # whose profile they read.
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    contact: Mapped[str] = mapped_column(String(320), nullable=False)
    created_by: Mapped[str] = mapped_column(String(320), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


DISCLOSURE_PURPOSES = ("talent_introduction",)
DISCLOSURE_SCOPES = ("summary", "full")


class DisclosureEvent(Base):
    """One person's profile, handed to one recipient, once.

    Append-only, like ConsentEvent and for the same reason: this is the
    evidence behind "we will require anyone who already has it to stop using
    it". You cannot keep that promise without being able to list who they are.

    A row is one disclosure of one data subject to one recipient -- not one
    HTTP request. A list page returning twenty profiles writes up to twenty
    rows, because the obligation is per (recipient, subject) and a per-request
    row cannot answer the only question that matters at withdrawal time.

    recipient_label and recipient_contact are copied in at write time rather
    than read through api_key_id, so the row still names a human to contact
    after the key is revoked or deleted. Both subject columns are nullable and
    ON DELETE SET NULL: an erased account, or a deleted key, leaves the record
    standing. profile_content_hash pins which payload they hold, which is why
    talent_profiles.profile_json is stored as stable bytes.

    There is deliberately no mutable state column for whether the obligation
    has been discharged. That is derived -- see
    app.services.disclosure.pending_obligations -- and discharge is itself
    recorded by appending a StopUseNotice.
    """

    __tablename__ = "disclosure_events"
    __table_args__ = (
        CheckConstraint(
            "purpose IN ('talent_introduction')", name="disclosure_purpose_values"
        ),
        CheckConstraint(
            "scope IN ('summary', 'full')", name="disclosure_scope_values"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    api_key_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("talent_api_keys.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    recipient_label: Mapped[str] = mapped_column(String(255), nullable=False)
    recipient_contact: Mapped[str] = mapped_column(String(320), nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    profile_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    # Which endpoint handed it over, for reading the ledger back later.
    route: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, index=True
    )


STOP_USE_CHANNELS = ("manual_email",)


class StopUseNotice(Base):
    """We told one recipient to stop using one person's profile.

    Append-only, and the other half of the withdrawal promise. channel is
    'manual_email' because that is the truth: there is no mailer in this
    codebase, so an operator sends it and records that they did -- the same way
    the privacy policy already handles access and deletion requests.

    Deliberately not a flag on DisclosureEvent. A discharge is a thing that
    happened at a time, by someone, and re-granting then withdrawing again
    reopens the obligation rather than reusing the old answer.
    """

    __tablename__ = "stop_use_notices"
    __table_args__ = (
        CheckConstraint(
            "channel IN ('manual_email')", name="stop_use_channel_values"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    api_key_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("talent_api_keys.id", ondelete="SET NULL"), nullable=True
    )
    recipient_contact: Mapped[str] = mapped_column(String(320), nullable=False)
    # Which withdrawal this discharges, when we can still see it.
    withdrawn_consent_event_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("consent_events.id", ondelete="SET NULL"), nullable=True
    )
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    sent_by: Mapped[str] = mapped_column(String(320), nullable=False)
    detail: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, index=True
    )
