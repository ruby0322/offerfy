---
name: Master tailor flow
overview: 每份現有履歷視為 master。Tailor 一律從 master 已存檔的草稿分出，是一份獨立文件；chat 跟著文件走。共用的只有 master 本文（tailor 透過 read_master 讀取），以及已登入使用者確認過的記憶。實作分成後端資料、API、chat 工具，以及前端切換、記憶、dashboard 九個任務。
todos:
  - id: t0-plan-doc
    content: "Task 0: 把本計畫存到 docs/superpowers/plans/2026-10-10-master-tailor.md 並 commit"
    status: in_progress
  - id: t1-schema
    content: "Task 1: Resume family 欄位、MemoryItem 模型、Alembic 009"
    status: pending
  - id: t2-tailor-api
    content: "Task 2: services/family.py 加上 POST tailors、GET family，ResumeOut 補欄位"
    status: pending
  - id: t3-memory-api
    content: "Task 3: services/memory.py 與 routers/memory.py（接受只限登入）"
    status: pending
  - id: t4-chat-tools
    content: "Task 4: 角色卡、read_master、create_tailor、propose_memory、tailor_created SSE"
    status: pending
  - id: t5-fe-api
    content: "Task 5: lib/api.ts 型別與函式、三語系 i18n key"
    status: pending
  - id: t6-switch-ui
    content: "Task 6: ResumeFamilyMenu、建立 tailor 對話框、存檔後切換、輸入框草稿保存"
    status: pending
  - id: t7-memory-ui
    content: "Task 7: ChatMessageCard 記憶卡與 tailor 卡、MemoryPanel、未登入改成登入"
    status: pending
  - id: t8-dashboard
    content: "Task 8: Dashboard 把 tailor 收在所屬 master 底下"
    status: pending
  - id: t9-verify
    content: "Task 9: pytest、eslint、tsc、build、瀏覽器端到端驗證"
    status: pending
isProject: false
---

# Master / Tailor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal：** 使用者可以從任何 master 或 tailor 建立新的 tailor（選單或對話皆可），在同一個 family 的文件之間切換，每份文件保有自己的 chat。只有已登入使用者確認過的記憶會在 family 內共用。

**Architecture：** Tailor 是一列 `resumes`，帶 `kind="tailor"` 和 `parent_resume_id`，所以既有的 revision、chat、分享、claim 都照常運作。每回合的 `instructions` 會從資料庫重算一段角色卡，加上已接受的記憶。對話歷史 `input` 仍然只有這份文件的訊息。新工具由 `chat.py` 透過 `extra_tools` 和 `tool_handlers` 注入 `aiter_llm_turn`。

**Tech Stack：** FastAPI、SQLAlchemy、Alembic、pytest（SQLite in-memory，`ScriptedOpenAI`）、Next 16、React 19、next-intl、Radix `dropdown-menu`。

## Global Constraints

- Locales 只有 `en`、`zh-TW`、`zh-CN`。每個新的 UI 字串都要在三個 `apps/frontend/messages/*.json` 加 key。
- 產品名是 Offerfy。不能聲稱有職缺媒合，也不能聲稱會代為投遞。
- Typst 原始碼字串是唯一的正本。Tailor 就是另一份 Typst 字串。
- Guest 可以建立和切換 tailor。只有接受記憶、刪除記憶需要登入（`require_user`，401）。
- Chat 歷史維持 `chat_messages.resume_id` 一對一。永遠不把另一份文件的訊息帶進 `input`。
- 角色卡和記憶只放在 `instructions`，永遠不寫進 `chat_messages`。
- 後端測試：`cd apps/backend && PATH="$PWD/.tools:$PATH" .venv/bin/pytest -q`。
- 前端檢查：`cd apps/frontend && npx eslint . && npx tsc --noEmit && npm run build`。前端沒有單元測試 runner，UI 行為用瀏覽器驗證。
- 不做：自動萃取記憶、tailor 跟 master 自動同步、接上職缺目錄、合併相似記憶、從 tailor 本文分支新檔、建立當回合就改新 tailor、刪除履歷的 UI（目前沒有刪除 endpoint；這次只靠 ORM cascade 讓之後的刪除能連動）。

## File Structure

後端：

- Modify `apps/backend/app/models.py`：`Resume` family 欄位與 relationship；新增 `MemoryItem`。
- Create `apps/backend/alembic/versions/009_resume_family_memory.py`。
- Create `apps/backend/app/services/family.py`：`family_master`、`family_tailors`、`create_tailor`。
- Create `apps/backend/app/services/memory.py`：提議、接受、略過、列出已接受。
- Create `apps/backend/app/services/role_card.py`：`build_role_card`。
- Create `apps/backend/app/services/family_tools.py`：工具 spec 與 handler。
- Create `apps/backend/app/routers/memory.py`；在 `apps/backend/app/main.py` 註冊。
- Modify `apps/backend/app/routers/resumes.py`、`apps/backend/app/schemas.py`、`apps/backend/app/services/llm.py`、`apps/backend/app/routers/chat.py`。
- Tests：`tests/test_family.py`、`tests/test_memory.py`、`tests/test_chat_family.py`。

前端：

- Modify `apps/frontend/lib/api.ts`、`apps/frontend/lib/chat-tools.ts`。
- Create `apps/frontend/components/editor/ResumeFamilyMenu.tsx`、`CreateTailorDialog.tsx`、`MemoryPanel.tsx`。
- Modify `EditorHeader.tsx`、`EditorShell.tsx`、`EditorChatPanel.tsx`、`ChatMessageCard.tsx`、`app/[locale]/dashboard/page.tsx`、三個 messages 檔案。

---

### Task 0: Save plan into repo

- [ ] 把這個檔案複製到 `docs/superpowers/plans/2026-10-10-master-tailor.md`。
- [ ] Commit：`git add docs/superpowers/plans/2026-10-10-master-tailor.md && git commit -m "docs: plan master and tailor resumes"`

### Task 1: Schema

**Files:** Modify `apps/backend/app/models.py`。Create `apps/backend/alembic/versions/009_resume_family_memory.py`。Test `apps/backend/tests/test_family.py`。

**Produces：** `Resume.kind`、`Resume.parent_resume_id`、`Resume.forked_from_revision_id`、`Resume.target_label`、`Resume.target_context`、`Resume.tailors`、`Resume.memory_items`、`MemoryItem`。

- [ ] **Step 1: Failing test**

```python
# apps/backend/tests/test_family.py
from app.models import MemoryItem, Resume


def test_resume_defaults_to_master(client, db_session):
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    row = db_session.get(Resume, created["id"])
    assert row.kind == "master"
    assert row.parent_resume_id is None


def test_deleting_master_cascades_tailors_and_memory(client, db_session):
    created = client.post("/v1/resumes", json={"locale": "en", "title": "M"}).json()
    master = db_session.get(Resume, created["id"])
    tailor = Resume(
        title="T", typst_source=master.typst_source, source="create",
        locale="en", guest_session_id=master.guest_session_id,
        kind="tailor", parent_resume_id=master.id,
    )
    db_session.add(tailor)
    db_session.flush()
    db_session.add(MemoryItem(master_resume_id=master.id, source_resume_id=tailor.id, text="No photo"))
    db_session.commit()
    tailor_id = tailor.id
    db_session.delete(master)
    db_session.commit()
    assert db_session.get(Resume, tailor_id) is None
    assert db_session.query(MemoryItem).count() == 0
```

- [ ] **Step 2:** `pytest tests/test_family.py -q`。預期 FAIL：`ImportError: cannot import name 'MemoryItem'`。

- [ ] **Step 3: Models**。在 `Resume.__table_args__` 加：

```python
        CheckConstraint("kind IN ('master', 'tailor')", name="resume_kind_values"),
        CheckConstraint(
            "(kind = 'master' AND parent_resume_id IS NULL) "
            "OR (kind = 'tailor' AND parent_resume_id IS NOT NULL)",
            name="resume_kind_parent",
        ),
```

在 `published_revision_id` 之後加欄位：

```python
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="master", server_default="master")
    parent_resume_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("resumes.id", ondelete="CASCADE"), nullable=True, index=True
    )
    forked_from_revision_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("resume_revisions.id", ondelete="SET NULL", use_alter=True,
                   name="fk_resumes_forked_from_revision_id"),
        nullable=True,
    )
    target_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    target_context: Mapped[str | None] = mapped_column(Text, nullable=True)
```

在既有 relationship 之後加：

```python
    parent: Mapped["Resume | None"] = relationship(
        remote_side="Resume.id", back_populates="tailors", foreign_keys=[parent_resume_id]
    )
    tailors: Mapped[list["Resume"]] = relationship(
        back_populates="parent", cascade="all, delete-orphan",
        foreign_keys="Resume.parent_resume_id", order_by="Resume.created_at",
    )
    memory_items: Mapped[list["MemoryItem"]] = relationship(
        back_populates="master", cascade="all, delete-orphan",
        foreign_keys="MemoryItem.master_resume_id",
    )
```

在 `ChatMessage` 之後新增：

```python
MEMORY_STATUSES = ("pending", "accepted", "dismissed")


class MemoryItem(Base):
    __tablename__ = "memory_items"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'accepted', 'dismissed')", name="memory_status_values"),
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    master: Mapped[Resume] = relationship(back_populates="memory_items", foreign_keys=[master_resume_id])
```

- [ ] **Step 4: Migration** `009_resume_family_memory.py`（`down_revision = "008_resume_revisions"`）。用 `op.batch_alter_table("resumes")` 加入五個欄位（`kind` 的 `server_default="master"`）、兩個 FK、`ix_resumes_parent_resume_id`，以及兩個 check constraint。再用 `op.create_table("memory_items", ...)` 建立和模型相同的表，加上 `ix_memory_items_master_resume_id`。Downgrade 依相反順序刪除。必要原因：`ensure_schema()` 只會 `create_all`，既有的 `resumes` 表不會自動多出欄位。

- [ ] **Step 5:** `pytest tests/test_family.py -q` PASS。再跑全部 `pytest -q`，必須全綠。

- [ ] **Step 6:** `git commit -m "feat: add resume family columns and memory items"`

### Task 2: Tailor creation and family API

**Files:** Create `apps/backend/app/services/family.py`。Modify `apps/backend/app/schemas.py`、`apps/backend/app/routers/resumes.py`。Test `apps/backend/tests/test_family.py`。

**Produces：**

- `family_master(db, resume) -> Resume`
- `family_tailors(db, master) -> list[Resume]`
- `create_tailor(db, member, *, title, target_label=None, target_context=None) -> Resume`（title 不合法時 raise `ValueError`）
- `POST /v1/resumes/{resume_id}/tailors` 回傳 `ResumeOut`（201）
- `GET /v1/resumes/{resume_id}/family` 回傳 `ResumeFamilyOut`

- [ ] **Step 1: Failing tests**（附加到 `test_family.py`）

```python
def _master(client, title="Master"):
    return client.post("/v1/resumes", json={"locale": "en", "title": title}).json()


def test_create_tailor_copies_master_saved_draft(client):
    master = _master(client)
    edited = master["typst_source"].replace("Your Name", "Ada Lovelace", 1)
    client.put(f"/v1/resumes/{master['id']}", json={"typst_source": edited})
    res = client.post(
        f"/v1/resumes/{master['id']}/tailors",
        json={"title": "Acme SWE", "target_label": "SWE @ Acme", "target_context": "Python, Go"},
    )
    assert res.status_code == 201
    tailor = res.json()
    assert tailor["kind"] == "tailor"
    assert tailor["parent_resume_id"] == master["id"]
    assert tailor["target_label"] == "SWE @ Acme"
    assert "Ada Lovelace" in tailor["typst_source"]
    assert client.get(f"/v1/resumes/{tailor['id']}/messages").json() == []


def test_create_tailor_from_tailor_forks_master_not_tailor(client):
    master = _master(client)
    first = client.post(f"/v1/resumes/{master['id']}/tailors", json={"title": "A"}).json()
    client.put(
        f"/v1/resumes/{first['id']}",
        json={"typst_source": first["typst_source"] + "\n// tailor-only"},
    )
    second = client.post(f"/v1/resumes/{first['id']}/tailors", json={"title": "B"}).json()
    assert second["parent_resume_id"] == master["id"]
    assert "tailor-only" not in second["typst_source"]


def test_create_tailor_requires_title(client):
    master = _master(client)
    assert client.post(f"/v1/resumes/{master['id']}/tailors", json={"title": "  "}).status_code == 400


def test_family_lists_master_and_tailors(client):
    master = _master(client)
    a = client.post(f"/v1/resumes/{master['id']}/tailors", json={"title": "A"}).json()
    body = client.get(f"/v1/resumes/{a['id']}/family").json()
    assert body["master"]["id"] == master["id"]
    assert [row["id"] for row in body["tailors"]] == [a["id"]]


def test_tailor_is_private_to_owner(client):
    master = _master(client)
    client.cookies.clear()
    assert client.post(f"/v1/resumes/{master['id']}/tailors", json={"title": "X"}).status_code == 404


def test_list_resumes_includes_kind_and_parent(client):
    master = _master(client)
    tailor = client.post(f"/v1/resumes/{master['id']}/tailors", json={"title": "A"}).json()
    rows = {row["id"]: row for row in client.get("/v1/resumes").json()}
    assert rows[master["id"]]["kind"] == "master"
    assert rows[tailor["id"]]["parent_resume_id"] == master["id"]
```

- [ ] **Step 2:** `pytest tests/test_family.py -q`。預期 FAIL（404 / KeyError `kind`）。

- [ ] **Step 3: Service** `apps/backend/app/services/family.py`

```python
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Resume
from app.services.revisions import latest_revision, set_draft_source

TITLE_MAX = 255
LABEL_MAX = 255
CONTEXT_MAX = 8000


def family_master(db: Session, resume: Resume) -> Resume:
    if resume.kind == "tailor" and resume.parent_resume_id:
        master = db.get(Resume, resume.parent_resume_id)
        if master is not None:
            return master
    return resume


def family_tailors(db: Session, master: Resume) -> list[Resume]:
    return (
        db.query(Resume)
        .filter(Resume.parent_resume_id == master.id)
        .order_by(Resume.created_at.asc(), Resume.id.asc())
        .all()
    )


def _clip(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped[:limit] or None


def create_tailor(
    db: Session,
    member: Resume,
    *,
    title: str | None,
    target_label: str | None = None,
    target_context: str | None = None,
) -> Resume:
    clean_title = (title or "").strip()
    if not clean_title:
        raise ValueError("Title is required")
    if len(clean_title) > TITLE_MAX:
        raise ValueError("Title is too long")
    master = family_master(db, member)
    forked = latest_revision(db, master.id)
    tailor = Resume(
        title=clean_title,
        typst_source=master.typst_source,
        source="create",
        locale=master.locale,
        user_id=master.user_id,
        guest_session_id=master.guest_session_id,
        kind="tailor",
        parent_resume_id=master.id,
        forked_from_revision_id=forked.id if forked else None,
        target_label=_clip(target_label, LABEL_MAX),
        target_context=_clip(target_context, CONTEXT_MAX),
    )
    db.add(tailor)
    db.flush()
    set_draft_source(db, tailor, master.typst_source)
    return tailor
```

- [ ] **Step 4: Schemas**（`schemas.py`）

```python
class ResumeOut(BaseModel):
    # ...existing fields...
    kind: str = "master"
    parent_resume_id: str | None = None
    target_label: str | None = None
    target_context: str | None = None


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


class FamilyMember(BaseModel):
    id: str
    title: str
    kind: str
    target_label: str | None = None
    updated_at: str | None = None


class ResumeFamilyOut(BaseModel):
    master: FamilyMember
    tailors: list[FamilyMember]
```

- [ ] **Step 5: Router**（`resumes.py`）

`_to_out` 補上 `kind=resume.kind or "master"`、`parent_resume_id`、`target_label`、`target_context`。`list_resumes` 回傳 `ResumeListItem(id=..., title=..., source=..., locale=..., kind=r.kind or "master", parent_resume_id=r.parent_resume_id, updated_at=_iso(r.updated_at))`。新增：

```python
def _member(r: Resume) -> FamilyMember:
    return FamilyMember(
        id=r.id, title=r.title, kind=r.kind or "master",
        target_label=r.target_label, updated_at=_iso(r.updated_at),
    )


@router.post("/v1/resumes/{resume_id}/tailors", response_model=ResumeOut, status_code=201)
def create_tailor_route(
    resume_id: str,
    body: TailorCreate,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    member = _owned_resume(resume_id, request, response, db, user, ensure=True)
    try:
        tailor = create_tailor(
            db, member, title=body.title,
            target_label=body.target_label, target_context=body.target_context,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    out = _to_out(db, tailor)
    db.commit()
    return out


@router.get("/v1/resumes/{resume_id}/family", response_model=ResumeFamilyOut)
def get_family(
    resume_id: str,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    member = _owned_resume(resume_id, request, response, db, user)
    master = family_master(db, member)
    return ResumeFamilyOut(
        master=_member(master),
        tailors=[_member(r) for r in family_tailors(db, master)],
    )
```

需要 import `create_tailor`、`family_master`、`family_tailors`、`TailorCreate`、`FamilyMember`、`ResumeFamilyOut`。

- [ ] **Step 6:** `pytest -q` 全綠。Claim 不必改：`POST /v1/auth/claim` 本來就會搬走 guest 名下的所有 `resumes`，tailor 也在其中。在 `tests/test_family.py` 補一個 claim 測試：用 `_sign` 簽出 session cookie，呼叫 `/v1/auth/claim`，之後 `GET /v1/resumes` 要同時列出 master 和 tailor。

- [ ] **Step 7:** `git commit -m "feat: create tailors from the master draft and list a resume family"`

### Task 3: Memory service and API

**Files:** Create `apps/backend/app/services/memory.py`、`apps/backend/app/routers/memory.py`。Modify `apps/backend/app/main.py`、`apps/backend/app/schemas.py`。Test `apps/backend/tests/test_memory.py`。

**Produces：**

- `normalize_memory_text(text: str) -> str`
- `propose_memory(db, master: Resume, source_resume_id: str | None, text: str) -> tuple[MemoryItem, bool]`
- `accept_memory(db, item) -> MemoryItem`（滿 20 則時 raise `MemoryFullError`，不是 pending 時 raise `ValueError`）
- `dismiss_memory(db, item) -> MemoryItem`
- `accepted_memory_texts(db, master_id: str) -> list[str]`
- `GET /v1/resumes/{id}/memory`、`POST /v1/memory/{id}/accept`、`POST /v1/memory/{id}/dismiss`、`DELETE /v1/memory/{id}`

- [ ] **Step 1: Failing tests**

```python
# apps/backend/tests/test_memory.py
from app.config import get_settings
from app.deps import SESSION_COOKIE, _sign
from app.models import MemoryItem, Resume, User
from app.services.memory import MAX_ACCEPTED, propose_memory


def _guest_master(client):
    return client.post("/v1/resumes", json={"locale": "en", "title": "M"}).json()


def _signed_in(client, db_session):
    user = User(google_sub="sub-mem", email="mem@example.com", locale="en")
    db_session.add(user)
    db_session.commit()
    client.cookies.set(SESSION_COOKIE, _sign(user.id, get_settings().auth_token_secret))
    return user


def test_propose_dedupes_and_lists_family_wide(client, db_session):
    master = _guest_master(client)
    tailor = client.post(f"/v1/resumes/{master['id']}/tailors", json={"title": "T"}).json()
    row = db_session.get(Resume, master["id"])
    first, created1 = propose_memory(db_session, row, tailor["id"], "  No   photo ")
    again, created2 = propose_memory(db_session, row, master["id"], "no photo")
    db_session.commit()
    assert created1 and not created2 and first.id == again.id
    assert first.text == "No photo"
    listed = client.get(f"/v1/resumes/{tailor['id']}/memory").json()
    assert [item["text"] for item in listed] == ["No photo"]
    assert listed[0]["status"] == "pending"


def test_guest_cannot_accept_but_can_dismiss(client, db_session):
    master = _guest_master(client)
    item, _ = propose_memory(db_session, db_session.get(Resume, master["id"]), master["id"], "No photo")
    db_session.commit()
    assert client.post(f"/v1/memory/{item.id}/accept").status_code == 401
    assert db_session.get(MemoryItem, item.id).status == "pending"
    assert client.post(f"/v1/memory/{item.id}/dismiss").json()["status"] == "dismissed"


def test_signed_in_owner_accepts_after_claim_and_cap(client, db_session):
    master = _guest_master(client)
    row = db_session.get(Resume, master["id"])
    item, _ = propose_memory(db_session, row, master["id"], "Keep one page")
    db_session.commit()
    _signed_in(client, db_session)
    assert client.post("/v1/auth/claim").json()["claimed"] == 1
    assert client.post(f"/v1/memory/{item.id}/accept").json()["status"] == "accepted"
    for i in range(MAX_ACCEPTED - 1):
        extra, _ = propose_memory(db_session, row, master["id"], f"Fact {i}")
        db_session.commit()
        assert client.post(f"/v1/memory/{extra.id}/accept").status_code == 200
    over, _ = propose_memory(db_session, row, master["id"], "One too many")
    db_session.commit()
    assert client.post(f"/v1/memory/{over.id}/accept").status_code == 409
    assert client.delete(f"/v1/memory/{item.id}").status_code == 204
    assert client.post(f"/v1/memory/{over.id}/accept").status_code == 200


def test_other_user_cannot_touch_memory(client, db_session):
    master = _guest_master(client)
    item, _ = propose_memory(db_session, db_session.get(Resume, master["id"]), master["id"], "X")
    db_session.commit()
    client.cookies.clear()
    _signed_in(client, db_session)
    assert client.post(f"/v1/memory/{item.id}/accept").status_code == 404
```

- [ ] **Step 2:** `pytest tests/test_memory.py -q`。預期 FAIL：`ModuleNotFoundError: app.services.memory`。

- [ ] **Step 3: Service** `apps/backend/app/services/memory.py`

```python
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import MemoryItem, Resume

MAX_TEXT = 280
MAX_ACCEPTED = 20
LIVE = ("pending", "accepted")


class MemoryFullError(Exception):
    pass


def normalize_memory_text(text: str) -> str:
    return " ".join((text or "").split())


def propose_memory(
    db: Session, master: Resume, source_resume_id: str | None, text: str
) -> tuple[MemoryItem, bool]:
    clean = normalize_memory_text(text)
    if not clean:
        raise ValueError("Memory text is required")
    if len(clean) > MAX_TEXT:
        raise ValueError(f"Memory text must be at most {MAX_TEXT} characters")
    folded = clean.casefold()
    live = (
        db.query(MemoryItem)
        .filter(MemoryItem.master_resume_id == master.id, MemoryItem.status.in_(LIVE))
        .all()
    )
    for item in live:
        if item.text.casefold() == folded:
            return item, False
    item = MemoryItem(master_resume_id=master.id, source_resume_id=source_resume_id, text=clean)
    db.add(item)
    db.flush()
    return item, True


def accepted_count(db: Session, master_id: str) -> int:
    return (
        db.query(MemoryItem)
        .filter(MemoryItem.master_resume_id == master_id, MemoryItem.status == "accepted")
        .count()
    )


def accept_memory(db: Session, item: MemoryItem) -> MemoryItem:
    if item.status == "accepted":
        return item
    if item.status != "pending":
        raise ValueError("Only pending memory can be accepted")
    if accepted_count(db, item.master_resume_id) >= MAX_ACCEPTED:
        raise MemoryFullError
    item.status = "accepted"
    item.decided_at = datetime.now(timezone.utc)
    db.flush()
    return item


def dismiss_memory(db: Session, item: MemoryItem) -> MemoryItem:
    if item.status == "pending":
        item.status = "dismissed"
        item.decided_at = datetime.now(timezone.utc)
        db.flush()
    return item


def list_live_memory(db: Session, master_id: str) -> list[MemoryItem]:
    return (
        db.query(MemoryItem)
        .filter(MemoryItem.master_resume_id == master_id, MemoryItem.status.in_(LIVE))
        .order_by(MemoryItem.created_at.asc(), MemoryItem.id.asc())
        .all()
    )


def accepted_memory_texts(db: Session, master_id: str) -> list[str]:
    return [item.text for item in list_live_memory(db, master_id) if item.status == "accepted"]
```

- [ ] **Step 4: Schema**：`MemoryItemOut(id: str, text: str, status: str, source_resume_id: str | None, created_at: str)`。

- [ ] **Step 5: Router** `apps/backend/app/routers/memory.py`

```python
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import Response as RawResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user, load_resume_for_owner, owner_context, require_user
from app.models import MemoryItem, Resume, User
from app.schemas import MemoryItemOut
from app.services.family import family_master
from app.services.memory import MemoryFullError, accept_memory, dismiss_memory, list_live_memory

router = APIRouter()


def _out(item: MemoryItem) -> MemoryItemOut:
    created = item.created_at.isoformat() if item.created_at else ""
    return MemoryItemOut(
        id=item.id, text=item.text, status=item.status,
        source_resume_id=item.source_resume_id, created_at=created,
    )


def _item_for_owner(item_id, request, response, db, user, *, user_only: bool) -> MemoryItem:
    item = db.get(MemoryItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    if user_only:
        load_resume_for_owner(item.master_resume_id, request, db, user, None)
    else:
        u, guest = owner_context(request, response, db, user, ensure=False)
        load_resume_for_owner(item.master_resume_id, request, db, u, guest)
    return item


@router.get("/v1/resumes/{resume_id}/memory", response_model=list[MemoryItemOut])
def list_memory(resume_id: str, request: Request, response: Response,
                db: Session = Depends(get_db), user: User | None = Depends(get_current_user)):
    u, guest = owner_context(request, response, db, user, ensure=False)
    member = load_resume_for_owner(resume_id, request, db, u, guest)
    master = family_master(db, member)
    return [_out(i) for i in list_live_memory(db, master.id)]


@router.post("/v1/memory/{item_id}/accept", response_model=MemoryItemOut)
def accept(item_id: str, request: Request, response: Response,
           db: Session = Depends(get_db), user: User = Depends(require_user)):
    item = _item_for_owner(item_id, request, response, db, user, user_only=True)
    try:
        accept_memory(db, item)
    except MemoryFullError as exc:
        raise HTTPException(status_code=409, detail="Memory is full") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _out(item)


@router.post("/v1/memory/{item_id}/dismiss", response_model=MemoryItemOut)
def dismiss(item_id: str, request: Request, response: Response,
            db: Session = Depends(get_db), user: User | None = Depends(get_current_user)):
    item = _item_for_owner(item_id, request, response, db, user, user_only=False)
    dismiss_memory(db, item)
    return _out(item)


@router.delete("/v1/memory/{item_id}", status_code=204)
def delete(item_id: str, request: Request, response: Response,
           db: Session = Depends(get_db), user: User = Depends(require_user)):
    item = _item_for_owner(item_id, request, response, db, user, user_only=True)
    if item.status != "accepted":
        raise HTTPException(status_code=400, detail="Only accepted memory can be deleted")
    db.delete(item)
    return RawResponse(status_code=204)
```

在 `main.py` 註冊：`from app.routers import ..., memory` 和 `application.include_router(memory.router)`。

- [ ] **Step 6:** `pytest -q` 全綠。
- [ ] **Step 7:** `git commit -m "feat: add confirm-before-use family memory"`

### Task 4: Chat role card and family tools

**Files:** Create `apps/backend/app/services/role_card.py`、`apps/backend/app/services/family_tools.py`。Modify `apps/backend/app/services/llm.py`、`apps/backend/app/routers/chat.py`。Test `apps/backend/tests/test_chat_family.py`。

**Consumes：** Task 2 的 `family_master`、`create_tailor`；Task 3 的 `propose_memory`、`accepted_memory_texts`。

**Produces：** `aiter_llm_turn` / `aiter_chat_edit` 新增 keyword 參數 `extra_tools: list[dict] | None`、`tool_handlers: dict[str, Callable[[dict], str]] | None`。`build_role_card(db, resume) -> str`。`family_tool_specs(resume) -> list[dict]`、`family_tool_handlers(db, resume) -> dict`。新的 SSE 事件 `{"type": "tailor_created", "resume_id": str, "title": str}`。

- [ ] **Step 1: Failing tests** `apps/backend/tests/test_chat_family.py`。沿用 `tests/test_chat.py` 的寫法：從那裡 import `post_chat`、`_system_blobs`，並以 `monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted)` 注入。

```python
import json

from app.models import MemoryItem
from tests.openai_script import ScriptedOpenAI, llm_message, tool_call
from tests.test_chat import _system_blobs, post_chat


def _patch(monkeypatch, script):
    scripted = ScriptedOpenAI(script)
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted)
    return scripted


def test_create_tailor_from_tailor_chat_emits_event_and_keeps_chats_apart(client, openai_enabled, monkeypatch):
    master = client.post("/v1/resumes", json={"locale": "en", "title": "M"}).json()
    first = client.post(f"/v1/resumes/{master['id']}/tailors", json={"title": "A"}).json()
    _patch(monkeypatch, [
        llm_message(tool_calls=[tool_call("create_tailor", {
            "title": "Beta PM", "target_label": "PM @ Beta", "target_context": "Roadmaps",
        }, "ct-1")]),
        llm_message(content="Created the Beta tailor."),
    ])
    result = post_chat(client, first["id"], json={"message": "Make one for the Beta PM role"})
    created = next(e for e in result["events"] if e["type"] == "tailor_created")
    new = client.get(f"/v1/resumes/{created['resume_id']}").json()
    assert new["parent_resume_id"] == master["id"]
    assert new["target_context"] == "Roadmaps"
    assert client.get(f"/v1/resumes/{new['id']}/messages").json() == []
    assert any(r["role"] == "assistant" for r in client.get(f"/v1/resumes/{first['id']}/messages").json())


def test_create_tailor_once_per_turn(client, openai_enabled, monkeypatch):
    master = client.post("/v1/resumes", json={"locale": "en"}).json()
    _patch(monkeypatch, [
        llm_message(tool_calls=[
            tool_call("create_tailor", {"title": "One"}, "a"),
            tool_call("create_tailor", {"title": "Two"}, "b"),
        ]),
        llm_message(content="Done."),
    ])
    result = post_chat(client, master["id"], json={"message": "two tailors"})
    assert [e["type"] for e in result["events"]].count("tailor_created") == 1
    assert len(client.get(f"/v1/resumes/{master['id']}/family").json()["tailors"]) == 1


def test_role_card_and_accepted_memory_in_instructions_only(client, openai_enabled, monkeypatch, db_session):
    master = client.post("/v1/resumes", json={"locale": "en", "title": "M"}).json()
    tailor = client.post(f"/v1/resumes/{master['id']}/tailors",
                         json={"title": "T", "target_label": "SWE @ Acme", "target_context": "Kafka"}).json()
    db_session.add(MemoryItem(master_resume_id=master["id"], text="Never list GPA", status="accepted"))
    db_session.add(MemoryItem(master_resume_id=master["id"], text="Pending thing", status="pending"))
    db_session.commit()
    scripted = _patch(monkeypatch, [llm_message(content="ok")])
    post_chat(client, tailor["id"], json={"message": "hi"})
    systems = _system_blobs(scripted.requests)
    assert "TAILOR" in systems and "SWE @ Acme" in systems and "Kafka" in systems
    assert "Never list GPA" in systems
    assert "Pending thing" not in systems
    tools = {t.get("name") for t in scripted.requests[0]["tools"]}
    assert {"read_master", "create_tailor", "propose_memory"} <= tools
    for row in client.get(f"/v1/resumes/{tailor['id']}/messages").json():
        assert "Never list GPA" not in row["content"]


def test_master_chat_has_no_read_master(client, openai_enabled, monkeypatch):
    master = client.post("/v1/resumes", json={"locale": "en"}).json()
    scripted = _patch(monkeypatch, [llm_message(content="ok")])
    post_chat(client, master["id"], json={"message": "hi"})
    tools = {t.get("name") for t in scripted.requests[0]["tools"]}
    assert "read_master" not in tools and "create_tailor" in tools


def test_read_master_returns_master_source(client, openai_enabled, monkeypatch):
    master = client.post("/v1/resumes", json={"locale": "en"}).json()
    client.put(f"/v1/resumes/{master['id']}",
               json={"typst_source": master["typst_source"] + "\n// master-marker"})
    tailor = client.post(f"/v1/resumes/{master['id']}/tailors", json={"title": "T"}).json()
    client.put(f"/v1/resumes/{master['id']}",
               json={"typst_source": master["typst_source"] + "\n// master-v2"})
    _patch(monkeypatch, [
        llm_message(tool_calls=[tool_call("read_master", {}, "rm")]),
        llm_message(content="read"),
    ])
    post_chat(client, tailor["id"], json={"message": "what is in master"})
    rows = [json.loads(r["content"]) for r in client.get(f"/v1/resumes/{tailor['id']}/messages").json()
            if r["role"] == "tool"]
    assert "master-v2" in rows[0]["result"]["source"]


def test_guest_propose_memory_says_login_required(client, openai_enabled, monkeypatch, db_session):
    master = client.post("/v1/resumes", json={"locale": "en"}).json()
    _patch(monkeypatch, [
        llm_message(tool_calls=[tool_call("propose_memory", {"text": "Prefers one page"}, "pm")]),
        llm_message(content="Suggested."),
    ])
    post_chat(client, master["id"], json={"message": "remember I like one page"})
    item = db_session.query(MemoryItem).one()
    assert item.status == "pending"
    rows = [json.loads(r["content"]) for r in client.get(f"/v1/resumes/{master['id']}/messages").json()
            if r["role"] == "tool"]
    assert rows[0]["result"]["requires_login"] is True
    assert rows[0]["result"]["item_id"] == item.id
```

- [ ] **Step 2:** `pytest tests/test_chat_family.py -q`。預期 FAIL：缺少 `tailor_created`，`tools` 裡也沒有 `create_tailor`。

- [ ] **Step 3: `llm.py`**

- 把 `SYSTEM_PROMPT` 裡的 `"You may call only read_typst, apply_typst_edit, read_ats, and web_search. "` 改成 `"Call only the tools you are given. "`。
- `aiter_llm_turn` 與 `aiter_chat_edit` 都加上 keyword 參數 `extra_tools: list[dict] | None = None`、`tool_handlers: dict[str, Callable[[dict], str]] | None = None`。`aiter_chat_edit` 原樣往下傳。`from typing import Any, Callable`。
- 在 request body 裡：`"tools": TOOLS + list(extra_tools or [])`。
- 在 dispatch 的最後一個 `else:` 之前插入：

```python
                    elif tool_handlers and name in tool_handlers:
                        result = tool_handlers[name](args)
```

- 把 fallback 的錯誤換成 `{"error": f"unknown tool {name}"}`。

- [ ] **Step 4: `role_card.py`**

```python
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Resume
from app.services.family import family_master
from app.services.memory import accepted_memory_texts


def build_role_card(db: Session, resume: Resume) -> str:
    master = family_master(db, resume)
    lines = ["", "", "Document context (rebuilt every turn, not part of the chat):"]
    if resume.kind == "tailor":
        lines.append(f"- You are editing a TAILOR resume titled {resume.title!r}, forked from the master {master.title!r}.")
        if resume.target_label:
            lines.append(f"- Target role for this tailor: {resume.target_label}")
        if resume.target_context:
            lines.append("- Job description, for this tailor only:\n" + resume.target_context)
        lines.append("- Call read_master to see the master resume. You cannot edit the master from here.")
    else:
        lines.append(f"- You are editing the MASTER resume titled {resume.title!r}. Tailors fork from it.")
    lines += [
        "- apply_typst_edit changes only this document.",
        "- create_tailor makes a new tailor from the master's saved draft. Call it at most once per turn. "
        "Ask for the job title or description first when it is unclear. Do not edit the new tailor in this turn; "
        "tell the user it will open next.",
        "- propose_memory only suggests a durable preference or fact about the person (never job-specific "
        "wording). It takes effect only after a signed-in user accepts it.",
    ]
    accepted = accepted_memory_texts(db, master.id)
    if accepted:
        lines.append("Accepted memory for every resume in this family:")
        lines += [f"- {text}" for text in accepted]
    return "\n".join(lines)
```

- [ ] **Step 5: `family_tools.py`**

```python
from __future__ import annotations

import json
from typing import Callable

from sqlalchemy.orm import Session

from app.models import Resume
from app.services.family import create_tailor, family_master
from app.services.memory import propose_memory

READ_MASTER_TOOL = "read_master"
CREATE_TAILOR_TOOL = "create_tailor"
PROPOSE_MEMORY_TOOL = "propose_memory"

_READ_MASTER = {
    "type": "function", "name": READ_MASTER_TOOL,
    "description": "Return the master resume's current saved Typst source (read-only).",
    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
}
_CREATE_TAILOR = {
    "type": "function", "name": CREATE_TAILOR_TOOL,
    "description": "Create a new tailor resume copied from the master's saved draft. At most once per turn.",
    "parameters": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "target_label": {"type": "string"},
            "target_context": {"type": "string"},
        },
        "required": ["title"],
        "additionalProperties": False,
    },
}
_PROPOSE_MEMORY = {
    "type": "function", "name": PROPOSE_MEMORY_TOOL,
    "description": "Suggest one durable fact or preference about the person (max 280 chars). Needs user confirmation.",
    "parameters": {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
        "additionalProperties": False,
    },
}


def family_tool_specs(resume: Resume) -> list[dict]:
    specs = [_CREATE_TAILOR, _PROPOSE_MEMORY]
    if resume.kind == "tailor":
        specs.insert(0, _READ_MASTER)
    return specs


def family_tool_handlers(db: Session, resume: Resume) -> dict[str, Callable[[dict], str]]:
    created: list[str] = []

    def read_master(_args: dict) -> str:
        master = family_master(db, resume)
        return json.dumps({"title": master.title, "source": master.typst_source}, ensure_ascii=False)

    def make_tailor(args: dict) -> str:
        if created:
            return json.dumps({"error": "Only one tailor per turn. Tell the user to ask again."})
        try:
            tailor = create_tailor(
                db, resume, title=args.get("title"),
                target_label=args.get("target_label"), target_context=args.get("target_context"),
            )
        except ValueError as exc:
            return json.dumps({"error": str(exc)})
        db.commit()
        created.append(tailor.id)
        return json.dumps(
            {"ok": True, "tailor_id": tailor.id, "title": tailor.title, "target_label": tailor.target_label},
            ensure_ascii=False,
        )

    def remember(args: dict) -> str:
        master = family_master(db, resume)
        try:
            item, is_new = propose_memory(db, master, resume.id, str(args.get("text") or ""))
        except ValueError as exc:
            return json.dumps({"error": str(exc)})
        db.commit()
        requires_login = resume.user_id is None
        return json.dumps({
            "ok": True, "item_id": item.id, "text": item.text, "status": item.status,
            "created": is_new, "requires_login": requires_login,
            "note": "Pending until the user accepts it." + (" The user must sign in first." if requires_login else ""),
        }, ensure_ascii=False)

    handlers: dict[str, Callable[[dict], str]] = {
        CREATE_TAILOR_TOOL: make_tailor,
        PROPOSE_MEMORY_TOOL: remember,
    }
    if resume.kind == "tailor":
        handlers[READ_MASTER_TOOL] = read_master
    return handlers
```

- [ ] **Step 6: `chat.py`**

- 在 `extra = ...` 和 template spec 之後加：`extra += build_role_card(db, resume)`。接著建立 `family_specs = family_tool_specs(resume)` 和 `family_handlers = family_tool_handlers(db, resume)`，**每個 request 只建立一次**，這樣「每回合最多一份」也涵蓋 repair loop。
- 兩次 `aiter_chat_edit(...)` 呼叫都傳 `extra_tools=family_specs, tool_handlers=family_handlers`。
- 新增 helper：

```python
def _tailor_created(trace: dict) -> dict | None:
    if trace.get("name") != "create_tailor":
        return None
    result = trace.get("result")
    if isinstance(result, dict) and result.get("tailor_id"):
        return {"type": "tailor_created", "resume_id": result["tailor_id"], "title": result.get("title") or ""}
    return None
```

- 在兩個 `kind == "tool"` 分支裡，送出 `tool` SSE 之後：`created = _tailor_created(event["trace"])`；若有值就 `yield _sse(created)`。

- [ ] **Step 7:** `pytest -q` 全綠，包含既有的 `tests/test_chat.py`。
- [ ] **Step 8:** `git commit -m "feat: tailor and memory tools in chat with per-document role card"`

### Task 5: Frontend API and copy

**Files:** Modify `apps/frontend/lib/api.ts`、`apps/frontend/messages/{en,zh-TW,zh-CN}.json`。

**Produces（TypeScript）：**

```ts
export type ResumeKind = "master" | "tailor";
// Resume 加上：kind?: ResumeKind; parent_resume_id?: string | null;
//              target_label?: string | null; target_context?: string | null;
export type FamilyMember = { id: string; title: string; kind: ResumeKind; target_label?: string | null; updated_at?: string | null };
export type ResumeFamily = { master: FamilyMember; tailors: FamilyMember[] };
export type MemoryStatus = "pending" | "accepted" | "dismissed";
export type MemoryItem = { id: string; text: string; status: MemoryStatus; source_resume_id?: string | null; created_at: string };

export async function createTailor(id: string, payload: { title: string; target_label?: string; target_context?: string }): Promise<Resume> {
  return asResume(await apiJson<unknown>(`/api/v1/resumes/${id}/tailors`, { method: "POST", body: JSON.stringify(payload) }));
}
export async function getResumeFamily(id: string): Promise<ResumeFamily> {
  return apiJson<ResumeFamily>(`/api/v1/resumes/${id}/family`);
}
export async function listMemory(id: string): Promise<MemoryItem[]> {
  return apiJson<MemoryItem[]>(`/api/v1/resumes/${id}/memory`);
}
export async function acceptMemory(itemId: string): Promise<MemoryItem> {
  return apiJson<MemoryItem>(`/api/v1/memory/${itemId}/accept`, { method: "POST" });
}
export async function dismissMemory(itemId: string): Promise<MemoryItem> {
  return apiJson<MemoryItem>(`/api/v1/memory/${itemId}/dismiss`, { method: "POST" });
}
export async function deleteMemory(itemId: string): Promise<void> {
  await apiFetch(`/api/v1/memory/${itemId}`, { method: "DELETE" });
}
```

`ChatStreamEvent` 加上 `| { type: "tailor_created"; resume_id: string; title: string }`。

- [ ] 在三個語系檔的 `editor` 底下加 key（zh-TW 範例；en / zh-CN 意思相同）：
  - `familyMaster`: "主履歷"、`familyTailor`: "客製版"、`familyNewTailor`: "建立新的客製版"
  - `tailorDialogTitle`: "從主履歷建立客製版"、`tailorTitle`: "名稱"、`tailorTargetLabel`: "職缺名稱（選填）"、`tailorTargetContext`: "職缺說明（選填）"、`tailorCreate`: "建立"、`tailorCreating`: "建立中…"、`tailorCreateError`: "無法建立客製版。"
  - `toolReadMaster`: "讀取主履歷"、`toolCreateTailor`: "建立客製版"、`toolCreateTailorOpen`: "開啟 {title}"
  - `toolProposeMemory`: "建議記住"、`memoryRemember`: "記住"、`memoryDismiss`: "不用"、`memoryRemembered`: "已記住"、`memoryDismissed`: "已略過"、`memoryLogin`: "登入以使用此功能"、`memoryFull`: "記憶已滿 20 則，請先刪除一則。"
  - `memoryPanel`: "記憶"、`memoryPending`: "待確認"、`memoryAccepted`: "已記住"、`memoryEmpty`: "還沒有記憶。對話中 AI 可以建議要記住的偏好。"、`memoryDelete`: "刪除"
  - `switchBlocked`: "等這一回合結束再切換。"
- [ ] `cd apps/frontend && npx tsc --noEmit && npx eslint lib/api.ts` 乾淨。
- [ ] `git commit -m "feat: frontend api and copy for tailors and memory"`

### Task 6: Family menu and switching

**Files:** Create `components/editor/ResumeFamilyMenu.tsx`、`components/editor/CreateTailorDialog.tsx`。Modify `EditorHeader.tsx`、`EditorShell.tsx`。

**Consumes：** Task 5 的 `getResumeFamily`、`createTailor`、`ResumeFamily`。

- [ ] **`ResumeFamilyMenu`**（Radix `@/components/ui/dropdown-menu`）。Props：`family: ResumeFamily | null`、`currentId: string`、`disabled: boolean`、`onSelect(id: string)`、`onNewTailor()`、`labels`。觸發按鈕顯示目前標題，加上 `familyMaster`/`familyTailor` badge。選單內容：master 一列、分隔線、各 tailor 一列（標題加 `target_label`），目前那份打勾，最後一列是「建立新的客製版」。`disabled` 時觸發按鈕顯示 tooltip `switchBlocked`。
- [ ] **`CreateTailorDialog`**：專案沒有 dialog primitive，所以用一個固定定位的 `role="dialog"` 面板，內含 `Input`（標題，必填，預設 `${masterTitle} – `）、`Input`（職缺名稱）、`Textarea`（職缺說明）、取消與建立按鈕。按 Esc 關閉，開啟時 focus 標題欄位。送出時呼叫 `onCreate({title, target_label, target_context})`。
- [ ] **`EditorHeader`**：把純文字的 `title` `<span>` 換成 `familyMenu?: ReactNode` slot（沒有傳入時保留原本的 span）。
- [ ] **`EditorShell`**：
  - 新 state：`family`、`showTailorDialog`。初次載入時呼叫 `getResumeFamily(resumeId)`，與 `getResume` 並行（不阻擋畫面；失敗就不顯示選單）。
  - `const router = useRouter()`，來自 `@/i18n/navigation`。
  - `async function flushSave()`：若 `!sendingRef.current`，呼叫 `await putResumeSource(resumeId, { typst_source: source })`，期間把 `skipTimers.current` 設為 true。
  - `async function onSwitchResume(id)`：若 `sendingRef.current || id === resumeId` 就 return；`await flushSave()`；呼叫 `saveComposerDraft(resumeId, draft)`；`router.push(`/editor/${id}`)`。
  - `async function onCreateTailor(payload)`：`await flushSave()`；`const tailor = await createTailor(resumeId, payload)`；`router.push(`/editor/${tailor.id}`)`。後端會自動解析回 master，所以在 tailor 上建立也會從 master 分出。
  - 在 `sendChat` 的 handler 裡：`if (event.type === "tailor_created") { pendingTailorRef.current = event.resume_id; void getResumeFamily(resumeId).then(setFamily); return; }`。在 `sendUserChat` 的 `finally` 裡，等 `sendingRef.current = false` 之後：`const next = pendingTailorRef.current; pendingTailorRef.current = null; if (next) router.push(`/editor/${next}`);`。這樣回覆會留在原本的 chat，新文件在 `done` 之後才打開。
  - 輸入框草稿：寫一個小 helper（放在 `EditorShell.tsx` 的 component 外面）

```ts
const DRAFT_KEY = (id: string) => `offerfy:composer:${id}`;
function loadComposerDraft(id: string): string {
  try { return sessionStorage.getItem(DRAFT_KEY(id)) ?? ""; } catch { return ""; }
}
function saveComposerDraft(id: string, value: string) {
  try { value ? sessionStorage.setItem(DRAFT_KEY(id), value) : sessionStorage.removeItem(DRAFT_KEY(id)); } catch { /* private mode */ }
}
```

  `draft` 初始值用 `loadComposerDraft(resumeId)`；每次 `setDraft` 時也呼叫 `saveComposerDraft`；`onChat` 送出後清掉。
  - 在 tailor 上時，標題列的「已發布 / 分享」按鈕維持不變（每份文件各自分享）。
- [ ] `npx tsc --noEmit && npx eslint components/editor` 乾淨。
- [ ] `git commit -m "feat: switch between master and tailors from the editor header"`

### Task 7: Memory and tailor cards, memory panel

**Files:** Modify `ChatMessageCard.tsx`、`EditorChatPanel.tsx`、`EditorShell.tsx`、`lib/chat-tools.ts`。Create `components/editor/MemoryPanel.tsx`。

- [ ] **`chat-tools.ts`**：新增

```ts
export function memoryProposal(payload: ToolPayload | null): { itemId: string; text: string } | null {
  if (!payload || payload.name !== "propose_memory") return null;
  const r = payload.result as { item_id?: unknown; text?: unknown } | null;
  return r && typeof r.item_id === "string" && typeof r.text === "string" ? { itemId: r.item_id, text: r.text } : null;
}
export function createdTailor(payload: ToolPayload | null): { id: string; title: string } | null {
  if (!payload || payload.name !== "create_tailor") return null;
  const r = payload.result as { tailor_id?: unknown; title?: unknown } | null;
  return r && typeof r.tailor_id === "string" ? { id: r.tailor_id, title: String(r.title ?? "") } : null;
}
```

- [ ] **`ChatMessageCard`**：新增 props `memoryById?: Record<string, MemoryItem>`、`signedIn?: boolean`、`loginHref?: string`、`onAcceptMemory?(id)`、`onDismissMemory?(id)`、`onOpenResume?(id)`。在 `toolHeadline` 加上 `read_master`（`toolReadMaster`）、`create_tailor`（`toolCreateTailor`，detail 用標題）、`propose_memory`（`toolProposeMemory`，detail 用文字）。在 tool 分支裡：
  - `create_tailor` 成功時顯示一個連結樣式的按鈕 `toolCreateTailorOpen`，呼叫 `onOpenResume(id)`。
  - `propose_memory`：狀態優先取 `memoryById[itemId]?.status`；找不到時當作 `"pending"`。`accepted` 顯示 `memoryRemembered`，`dismissed` 顯示 `memoryDismissed`，或是在項目不見時（已刪除）也顯示 `memoryDismissed`。`pending` 時：已登入顯示 `memoryRemember` 和 `memoryDismiss` 兩顆按鈕；未登入顯示 `<Link href={loginHref}>{t("memoryLogin")}</Link>`（來自 `@/i18n/navigation`）加上 `memoryDismiss`。
- [ ] **`EditorChatPanel`**：把新 props 原樣傳給 `ChatMessageCard`。
- [ ] **`MemoryPanel`**：一個從標題列按鈕（`memoryPanel`，顯示 pending 數量 badge）打開的 popover，用 `dropdown-menu` 的 content 搭配自訂內容實作。分成「待確認」「已記住」兩區；沒有任何項目時顯示 `memoryEmpty`。Pending 列的按鈕和聊天卡片相同（未登入時同樣是登入連結）。已記住的列在已登入時有 `memoryDelete`。
- [ ] **`EditorShell`**：
  - `memory` state（`MemoryItem[]`），從 `listMemory(resumeId)` 載入；`memoryById` 用 `useMemo` 算。
  - 在 `sendChat` 裡，如果 tool 訊息的名稱是 `propose_memory`，就重新抓 `listMemory`。
  - `onAcceptMemory(id)`：`acceptMemory`；`ApiError` 409 時顯示 `memoryFull`；401 時 `setSignedIn(false)`。成功或失敗後都重新抓 memory。
  - `onDismissMemory(id)` 和 `onDeleteMemory(id)` 用同樣的模式。
  - `loginHref = `/login?next=/editor/${resumeId}``（和 `EditorSettingsPanel.tsx` 一樣）。登入後既有的 `claimResumes()` 會在載入時執行，pending 項目會留著，按鈕也會變成「記住」。
- [ ] `npx tsc --noEmit && npx eslint components/editor lib` 乾淨。
- [ ] `git commit -m "feat: confirm memory from chat and the memory panel; sign-in gate for guests"`

### Task 8: Dashboard grouping

**Files:** Modify `apps/frontend/app/[locale]/dashboard/page.tsx`，以及三個語系檔（`dashboard.tailorCount`: "{count} 份客製版"）。

- [ ] 以 `parent_resume_id` 分組：master 依原順序列出；每個 master 列底下縮排列出它的 tailor（標題加上 `/editor/{id}` 的「開啟」）。找不到 master 的孤兒 tailor 列在最後。

```ts
const masters = resumes.filter((r) => (r.kind ?? "master") === "master");
const byParent = new Map<string, Resume[]>();
for (const r of resumes) {
  if (r.kind === "tailor" && r.parent_resume_id) {
    byParent.set(r.parent_resume_id, [...(byParent.get(r.parent_resume_id) ?? []), r]);
  }
}
const orphans = resumes.filter((r) => r.kind === "tailor" && !masters.some((m) => m.id === r.parent_resume_id));
```

- [ ] `npx tsc --noEmit && npx eslint "app/[locale]/dashboard"` 乾淨。
- [ ] `git commit -m "feat: group tailors under their master on the dashboard"`

### Task 9: Verification

- [ ] 跑 Alembic：在 dev Postgres（`docker compose`）執行 `alembic upgrade head`，然後 `alembic downgrade -1` 和 `alembic upgrade head`。
- [ ] `cd apps/backend && PATH="$PWD/.tools:$PATH" .venv/bin/pytest -q`，全部通過。
- [ ] `cd apps/frontend && npx eslint . && npx tsc --noEmit && npm run build`，乾淨無錯。
- [ ] 瀏覽器（dev server，先以 guest，再登入 Google）：
  1. 建立一份 master，編輯姓名，從選單建立 tailor，填入職缺說明。確認 tailor 帶有編輯後的姓名，chat 是空的，選單顯示 master 加一份 tailor。
  2. 在 tailor 輸入一段沒送出的 chat 草稿，切到 master 再切回來，草稿還在。兩邊的 chat 歷史不同。
  3. 在 tailor 的 chat 裡請 AI「再做一份給 Beta PM」。回覆留在第一份 tailor；回合結束後新 tailor 自動打開，chat 是空的；它的本文等於 master，不等於第一份 tailor。
  4. 在 master 的 chat 裡用對話建立 tailor，得到相同結果。
  5. Chat 回合進行中，選單不可按，tooltip 可見。
  6. Guest：請 AI 記住一個偏好。卡片顯示「登入以使用此功能」和「不用」；直接呼叫 accept 回傳 401。登入後回到編輯器（claim），同一張卡片變成「記住」。按下後，記憶面板列為已記住；在另一份 tailor 送出一則訊息，dev log 裡（或直接觀察回覆）確認模型有遵守。
  7. 在記憶面板刪除已接受的記憶，下一回合不再遵守。
  8. Dashboard 在登入後把 tailor 縮排列在 master 底下。
  9. 回歸檢查：既有的 master 的 Typst 編輯、預覽、ATS 欄、版本歷史還原、分享/發布、模板套用都能正常運作。
  10. 分別在桌面寬度和約 390px 手機寬度檢查標題列，選單和記憶按鈕不會溢出。
- [ ] 修掉所有 lint 錯誤，再 commit 任何修正。

## Self-review notes

- 設計涵蓋檢查：family 欄位（T1）、從 master 已存檔草稿 fork 並在 tailor 上解析回 master（T2）、family 選單與存檔後切換（T6）、草稿保存（T6）、dashboard 分組（T8）、角色卡只放 instructions（T4）、tailor 才有 read_master（T4）、兩種 chat 都能 create_tailor，每回合一份，`done` 之後才打開（T4、T6）、提議後確認、去重、上限 20（T3）、guest 擋在登入（T3、T7）、claim 後 pending 保留（T3 測試、T9）、ORM cascade 連動刪除（T1）。
- 名稱一致性：`family_master`、`create_tailor`、`propose_memory`、`accepted_memory_texts`、`build_role_card`、`family_tool_specs`、`family_tool_handlers`、SSE `tailor_created { resume_id, title }`，前端 `createTailor`、`getResumeFamily`、`listMemory`、`acceptMemory`、`dismissMemory`、`deleteMemory`，前後一致。
