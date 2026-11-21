# 讓使用者把自己的 Offerfy 帳號接到自己的 AI 助理

**Date:** 2026-11-20
**Status:** approved — implemented
**Touches:** backend `app/routers/mcp.py` (new), `app/services/agent_keys.py` (new),
`app/services/mcp_tools.py` (new), `app/services/mcp_audit.py` (new),
`app/services/resume_write.py` (new), `app/deps.py`, `app/models.py`,
`app/main.py`, `app/schemas.py`, `app/routers/account.py`,
`app/routers/resumes.py`, `app/services/llm/tools.py`, `app/services/jobs_tools.py`,
`alembic/versions/043_agent_api_keys.py` (new),
`tests/test_agent_keys.py` (new), `tests/test_mcp_transport.py` (new),
`tests/test_mcp_tools.py` (new), `tests/test_agent_export.py` (new),
`tests/test_resume_write.py` (new);
frontend `app/[locale]/settings/page.tsx`,
`components/settings/AgentKeys.tsx` (new), `lib/api.ts`,
`lib/agent-markdown.ts`, `lib/agent-markdown.test.ts`,
`messages/{en,zh-TW,zh-CN}.json`

## 要解決的事

Offerfy 的功能全部鎖在瀏覽器裡。使用者想叫自己的 AI 助理「幫我把履歷的這段改短、
然後看看有什麼職缺符合」，今天唯一的辦法是人自己開網頁，一格一格做。

要的是：**使用者可以把自己的 Offerfy 帳號接到自己的 AI 助理上**，然後那個助理能
代他建履歷、改履歷、調設定（含發布）、匯出 PDF、設定求職偏好、搜職缺。只能碰他
自己的東西。

這不是後端能力的問題 — 那五件事後端全部已經有了，而且全部已經有 cookie 認證的
`/v1` 路由。缺的是三樣：**一個機器拿得住的身分**、**一層 MCP 外殼**、**一套護欄**。

### 目標

- 任何登入使用者能在設定頁自己產生、複製、撤銷 token，不需要聯絡營運。
- 一個遠端 MCP endpoint，claude.ai 的 custom connector、Claude Desktop、
  Claude Code 三者都能連上，不需要使用者裝任何東西。
- 工具只能碰持有 token 的那個使用者自己的資料。跨使用者是 404，不是 403。
- 每一次工具呼叫留下紀錄，使用者自己看得到，尤其是發布。
- 既有的 `/v1` 路由與 cookie 認證**完全不動**。MCP 是第二道門，不是改門鎖。

### 非目標

- 不做 OAuth。見「作法」裡被否決的那條；留給第二批。
- 不做 Claude connector 目錄上架（那件事需要 OAuth）。
- 不碰 `TalentApiKey` 與 `/v1/talent`。那是給招募方讀 talent profile 的另一套
  credential，兩者不共用資料表、不共用 prefix、不共用 hash 命名空間。
- 不做刪除履歷。沒有任何工具能刪掉一份履歷（但代理人可以改寫它的內容，見
  `put_resume_source`）。
- 不做換模板。v1 不開這個工具 — 原因在〈現狀〉那張表的註腳，它不是免費的。
- 不做 PDF 匯入。那會跑模型。
- 不做 guest。MCP 一律只認已登入帳號。

### 反目標（做到了也算失敗）

- **代理人花掉使用者的模型額度。** v1 不開任何會打模型的工具 — 沒有 tailor 改寫、
  沒有 chat、沒有 import、沒有換模板。呼叫端本來就是一個 LLM；再付錢請 Offerfy
  的模型去改呼叫端自己就能改的字，是兩頭都虧。
- **使用者不知道代理人做了什麼。** 特別是發布。
- **token 洩漏等於帳號被接管。** 沒有刪除履歷、沒有改 email、沒有帳號設定、
  沒有用 token 變出更多 token。
- **在 OpenAPI 文件上多開一條路。** `app/routers/talent.py` 的開頭講得很清楚：
  `create_app()` 不傳 `openapi_url`，而 Next 的 rewrite 把 `/api/openapi.json`
  公開出去，所以出現在那份文件裡的路由就是被公開描述的路由。MCP router 一律
  `include_in_schema=False`；MCP 自己有 `tools/list` 做發現，不需要 OpenAPI。
- **`put_resume_source` 留下編譯不過的文件，或蓋掉瀏覽器那邊的編輯。** 既有的
  `PUT /v1/resumes/{id}` 兩個都會發生，所以這個工具不能直接包它。

## 現狀：能力分別落在哪裡

| 要的能力 | 現有路由 | 認證 | 會打模型嗎 |
|---|---|---|---|
| 建履歷（空白起稿） | `POST /v1/resumes` | cookie 或 guest | 否 |
| 建履歷（從 PDF 匯入） | `POST /v1/resumes/upload` + `/import` | 同上 | **會** |
| 改履歷（結構化欄位） | `POST /v1/resumes/{id}/edit` | 同上 | 否 |
| 改履歷（整份原始碼） | `PUT /v1/resumes/{id}` | 同上 | 否，但**沒有編譯檢查也沒有 hash 防撞** |
| 改標題 | `PUT /v1/resumes/{id}` | 同上 | 否 |
| 換模板 | `POST /v1/resumes/{id}/template` | 同上 | **會**（註 1） |
| 建立 tailor 分支 | `POST /v1/resumes/{id}/tailors` | 同上 | 否（純複製） |
| 發布 | `POST .../publish` → **只 pin，不產生連結**（註 2） | 要登入帳號 | 否 |
| 產生公開連結 | `PUT .../share` with `public: true` | 要登入帳號 | 否 |
| 匯出 PDF | `GET /v1/resumes/{id}/export` | cookie 或 guest | 否 |
| 求職偏好 | `GET`/`PUT /v1/me/job-preferences` | 要登入帳號 | 否 |
| 搜職缺（公開目錄） | `GET /v1/jobs` | **無** | 否 |
| 配對清單（算過分數） | `job_scores` 快取，由 sweep 填 | 要登入帳號 | 否（讀快取） |

**註 1：換模板不是免費的。** `switch_template`（`resumes.py:594`）只有在
`(name, version)` 有註冊 emitter 時才走確定性路徑，而 emitter 只有四個；其他情況
回 `mode: "llm"` 並且什麼都不改。而且就算走 emitter，若還沒有內容快取，它會在
`preflight`/`metered` 裡跑 `extract_content`（`resumes.py:639`）— 那是
`tests/test_metering.py` 計費清單上的函式。所以這個工具不是花使用者的錢，就是對
大部分模板靜默無作用。兩種都不能接受，v1 不開。

**註 2：`publish` 與公開連結是兩道門。** `POST .../publish` 呼叫
`pin_current_draft`，只設 `published_revision_id`；`build_share_state` 的
`public` 是看有沒有 `ResumeShare` 列，所以對一份沒分享過的履歷呼叫它會**產生不了
任何連結**。連結是 `PUT .../share` 建的（它在沒 pin 時會順手 pin）。

**而 `published_revision_id` 是 talent profile 的第三道閘。**
`profile.readable_profiles_query`（`profile.py:277-286`）要求
`Resume.published_revision_id.isnot(None)`，所以 pin 一份履歷會把開了
`talent_introduction` 同意、而且 `profile_resume_id` 指向它的使用者，重新開放給
持有 credential 的招募方。刪掉 share 列不會收回這件事，因為 `published_revision_id`
還在。

另外兩件從這張表掉出來的事：

- **`GET /v1/jobs` 本來就是公開的。** `search_jobs` 其實不需要 token。還是放在同一個
  MCP server 裡，因為使用者要的是一個連接器涵蓋全部，而不是兩個。
- **`llm/tools.py` 已經有一套工具介面，裡面已經有一個叫 `search_jobs` 的工具。**
  見〈設計〉第 4 節第一段。

## 作法

### 被選上的：自助 bearer token + 遠端 MCP endpoint

使用者到 `/settings`，按「產生 token」，拿到一串只顯示一次的 `ofa_…`，貼進
Claude 的 connector 設定（claude.ai 的 **Request headers** 欄位、Desktop、或
Claude Code 的 `--header`）。後端新增一張 user-scoped 的 key 表、一個 bearer-only
的 dependency、一個 `POST /mcp`。

**claude.ai 吃得下使用者自填的 header 這件事查證過了**（2026-11-20，
[Claude docs](https://claude.com/docs/connectors/custom/remote-mcp)、
[Help Center 11175166](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp)、
[Claude Code docs](https://code.claude.com/docs/en/mcp)）。custom connector 的
**Request headers** 區塊就是為 API key / bearer token 這種固定憑證設計的。早期
那個「只能填 OAuth client id/secret」的
[GitHub issue #112](https://github.com/anthropics/claude-ai-mcp/issues/112) 已經過時。
**這是整條路線的前提，實作前再確認一次。**

### 被否決的：自己當 OAuth 2.1 authorization server

MCP 遠端伺服器的規範做法。使用者點「連接」→ Google 登入 → 按同意，不用複製貼上
任何東西；也是上架 Claude connector 目錄的唯一條件。

否決的理由不是它不好，是**順序**。工具層與護欄 — 發布的稽核、每把 token 的額度
上限、匯出的簽名連結、`put_resume_source` 的編譯閘 — 兩條路一模一樣，而風險全在
那裡。OAuth 是一塊邊界乾淨的額外工程（authorization / token endpoint、PKCE、
refresh token、dynamic client registration、protected resource metadata、同意
畫面），在一個目前只有 HMAC cookie 的 codebase 裡等於長出一整套新的授權機制，
工量是本案的兩三倍。

RFC 9728 允許同一個 resource 同時公告多種認證方式，所以第二批補 OAuth 時，工具層
與護欄一行都不用改。**本設計的結構必須留著這個出口**：認證只由 dependency 決定，
工具函式永遠只拿到一個已經解出來的 `User`，不知道他是怎麼被認出來的。

### 被否決的：發一個跑在使用者電腦上的 stdio 套件

`npx @offerfy/mcp`，在本機跑，拿使用者的 token 去打現有的 `/v1`。credential 的工
跟被選上的那條一樣多（還是要那張表、那個 UI、那套撤銷），另外多一個要版控、要
發布、要支援的套件，而且 **claude.ai 完全用不了** — stdio 只能在桌面和 CLI。

## 設計

### 1. credential：`agent_api_keys`

照抄 `TalentApiKey` 的形狀，但是綁 user 而不是綁組織。

```
agent_api_keys
  id           String(36)  pk
  user_id      String(36)  fk users.id, ondelete CASCADE, indexed
  key_hash     String(64)  unique, not null
  label        String(255) not null     -- 使用者自己取的名字
  created_at   DateTime(tz) not null
  last_used_at DateTime(tz) null
  revoked_at   DateTime(tz) null
  expires_at   DateTime(tz) null
```

- **prefix `ofa_`**，與 talent 的 `ofk_` 區隔。讓洩漏的 token 在 log 裡可 grep，
  也讓 dependency 不碰資料庫就能擋掉不是我們發的東西。
- **unsalted SHA-256**，理由與 `TalentApiKey`、`GuestSession` 完全相同：secret 是
  `secrets.token_urlsafe(32)` 的 256 bits，不是人選的密碼，拉伸對猜測沒有幫助，
  而查詢保持單一 indexed equality。`talent_keys.py` 的 docstring 已經寫過「不要改成
  bcrypt」，這裡一樣。
- **不共用 `hash_talent_key`。** 兩種不同的 secret 不該共用 hash 命名空間 —
  這條規則是 `talent_keys.py` 自己定的，照辦。
- `user_id` 上 CASCADE：帳號刪掉，token 跟著消失。
- **每人最多 5 把活著的 key。** 不是容量問題，是撤銷問題：一個不知道自己有幾把
  鑰匙的人不會想到要撤銷。超過就 400，要他先撤一把。

新檔 `app/services/agent_keys.py`，介面對齊 `talent_keys.py`：
`new_agent_key()`、`hash_agent_key()`、`is_live()`、
`mint_agent_key(db, user, label, expires_in_days=None)` → `(key, raw)`、
`key_for_token(db, raw) -> AgentApiKey | None`。

### 2. 認證：`require_agent_caller`

`app/deps.py` 新增，形狀照 `require_talent_caller`，差別是回傳使用者：

```python
def require_agent_caller(
    request: Request, db: Session = Depends(get_db)
) -> tuple[User, AgentApiKey]:
```

三條規則，照 talent 的理由辦：

1. **沒有 fallthrough。** session cookie、guest cookie、admin 帳號在這裡全部 401。
   唯一能開這道門的是 `ofa_` token。
2. **每一種失敗都是同一個 401、同一句話。** 缺少、格式錯、不存在、已撤銷、已過期
   不是呼叫者的事。
3. **讀也是寫。** 更新 `last_used_at`。沒人看得到在被用的 key 就是沒人想到要撤銷
   的 key。**這個 stamp 跟稽核列一起走第 6 節的獨立 session** — 寫在請求的交易裡
   的話，任何拋出 `HTTPException` 的工具都會把它一起 rollback 掉。

**不要把認證做進 MCP 的 JSON-RPC 層。** 認證只在這個 dependency 裡，工具函式拿到
的永遠是一個解好的 `User`。這是第二批接 OAuth 時不用改工具層的原因。

### 3. transport：`POST /mcp`

新 router `app/routers/mcp.py`，`include_in_schema=False`，
`dependencies=[Depends(require_agent_caller)]`。

**手寫 JSON-RPC envelope，不引入 `mcp` Python SDK。** 三個理由：

1. 官方 SDK 帶一整個 ASGI app（`streamable_http_app`），它想自己管掛載、session
   與 SSE；我們只需要 `initialize`、`tools/list`、`tools/call` 三個 method。
2. 它的 session 層會把 pool 連線釘住。`app/deps.py` 的 `resolve_user` 當初就是為了
   這件事才從 `get_current_user` 拆出來的：FastAPI 的 yield dependency 會把 session
   握到回應結束，對一條 stream 來說就是握一輩子。這個坑剛補好，不要再踩。
3. 為了大約 150 行的 envelope，在一個只有 14 行 `requirements.txt` 的專案裡多一個
   依賴，不划算。

**只回 `application/json`，不開 SSE。** Streamable HTTP 允許伺服器用單一 JSON 回應
取代開 stream，而我們沒有任何要主動推給客戶端的東西。沒有 `GET /mcp`、沒有 session
id、沒有斷線重連。**實作時第一件事是拿規範確認這一段**（protocol version、
`initialize` 的 capability 欄位、錯誤碼、`tools/call` 的 content block 形狀），本設計
寫的是形狀，不是逐字的 wire format。

錯誤對應：工具內部拋的 `HTTPException` 轉成 MCP 的 tool error（`isError: true` 加
一段文字），不是 JSON-RPC 層的錯誤 — 讓代理人看得懂「這份文件被改過了，重讀一次」
而不是看到一個傳輸層失敗。

### 4. 工具：十五個

**工具規格重用 `app/services/llm/tools.py`，不另外定義一套。** 那個檔案的
docstring 自己說它是「the tool surface, written once in a provider-neutral shape」，
每個 provider 一個翻譯點；`jobs_tools.py` 裡已經有一個叫 `search_jobs` 的工具，
有自己的參數、自己的精簡輸出投影、自己的上限，而 `app/services/jobs/search.py:5`
說 `filtered_query` 被抽出來就是為了讓那個工具能呼叫它。

所以 MCP 是**第三個翻譯目標**：`tools/list` 由那份 provider-neutral 規格產生，
handler 直接重用。同一個 codebase 裡有兩個同名、參數不同、輸出不同的 `search_jobs`
是維護災難，不是細節。履歷側的工具是新的，照同樣的形狀寫進同一個地方。

| 工具 | 包的是 | 備註 |
|---|---|---|
| `search_jobs` | `jobs_tools` 現有的 | 重用，不重寫 |
| `get_job` | `jobs_tools.read_job` | 同上 |
| `list_matches` | `job_scores` 快取 | 算過分數的個人化清單；讀快取，不花錢。可重用 `jobs_tools.read_my_job_profile` 的組裝 |
| `list_resumes` | 列表查詢 | 只有這個 user 的 |
| `create_resume` | `_new_resume` + `generate_starter` | 空白起稿 |
| `get_resume` | `parse_projection` | **回 projection，不只回原始碼**：代理人要靠 unit id 才能下 intent |
| `edit_resume` | `apply_intent` + 編譯 | 單格編輯，見下面的兩段式 |
| `put_resume_source` | 新的 `services/resume_write.py` | 整份原始碼，**帶編譯閘與 hash 防撞**，見下 |
| `create_tailor` | `services/family.py` 的複製 | 純複製，不跑模型 |
| `set_resume_title` | `PUT /v1/resumes/{id}` 的標題分支 | 只有標題。不含換模板（註 1） |
| `publish_resume` | `PUT .../share` with `public: true` | **走 share 那道門**，因為那才是會產生連結的門 |
| `unpublish_resume` | `PUT .../share` with `public: false` | 刪 share 列。**不清 `published_revision_id`** — 跟網站一致 |
| `export_resume` | 見第 5 節 | 回一個短效簽名連結 |
| `get_job_preferences` | `current_or_seeded` | |
| `set_job_preferences` | `put_job_preferences` 的邏輯 | **整份取代，不是 patch** — 既有語意，工具說明要講清楚 |

#### `edit_resume` 是兩段式，這是它安全的原因

代理人先 `get_resume` 拿到 `projection`（裡面有 `hash` 和每一格的 `unit` id、現值），
再送 `{hash, kind, unit, expect, value}`。`hash` 不符就 409 `stale_hash`，`expect`
不符就擋下來。代理人不能憑空指一個位置，也不能在不知道現值的情況下覆蓋。既有的
`/edit` 路由已經做完全部的檢查，工具層只是把它叫起來。

intent 種類只有 `set_arg | set_line | insert_bullet | insert_item`
（`app/schemas_editing.py:72`）— 沒有刪除、沒有重排。這就是為什麼需要下一個工具。

#### `put_resume_source` 是內容進得來、東西刪得掉的唯一路

既有的 `PUT /v1/resumes/{id}`（`resumes.py:471`）直接 `set_draft_source`，**沒有
編譯檢查，也沒有 hash 防撞**。照包會違反反目標。

新檔 `app/services/resume_write.py`，把 `/edit` 已經證明過的順序抽成一個可重用的
函式：

1. 比對 `hash`，不符就 409 `stale_hash`。
2. `db.commit()` 放掉 pool 連線 — 編譯是最多三個 30 秒的子行程，握著 slot 會重演
   `app/db.py:37-84` 記錄的那次 pool 耗盡（連 `/healthz` 一起掛掉）。
3. `asyncio.to_thread` 跑編譯。不過就 400，什麼都不寫。
4. `db.refresh(resume)`，再比一次 hash — 編譯期間 chat 或另一個分頁可能寫進來。
5. `set_draft_source(..., coalesce=False)`。

**網站那支 `PUT` 不動。** 它是既有行為，改它會動到編輯器的原始碼分頁，不在本案
範圍。兩條寫入路徑安全程度不同是已知的，記在這裡。

#### 工具函式的形狀

`(db, user, args) -> dict`，**但允許 `async def`，也允許在中途 `db.commit()`**。
`put_resume_source` 與 `edit_resume` 兩者都必須如此，理由在上面第 2 步。

**而且本案包含把 router internals 抽成真正的 service。** 下列目標今天是 handler，
帶 `Request`/`Response`/`Depends` 簽名，不是能直接呼叫的函式：`_filtered_query`
（`routers/jobs.py:121`）、`_new_resume`（`resumes.py:222`）、`list_resumes`
（`:320`）、`publish_resume`（`:731`）、`put_job_preferences`
（`routers/preferences.py:58`）。抽出來之後既有路由改成呼叫同一個 service。

**這是工量估計裡最大的變數。** 一週是在「抽取順利」的前提下；抽到一半發現某個
handler 的邏輯跟 request 綁太緊，就會變成兩週。

#### 工具說明（description）是這個功能的 UI

代理人只讀得到工具說明，所以：

- `set_job_preferences` 要寫明它會清掉沒帶的欄位。
- `search_jobs` 要寫明 Offerfy 不代投遞。
- **`publish_resume` 要寫明兩件事**：它產生一條任何人都能看的公開連結；而且如果
  使用者開了「開放配對」並把 profile 指向這份履歷，它也會把那份 profile 重新開放
  給持有 credential 的招募方。
- `unpublish_resume` 要寫明它收回公開連結，但**不會**收回上面那件事 — 要收回得
  到網站上關掉「開放配對」。
- `put_resume_source` 要寫明它整份覆蓋，而且需要先用 `get_resume` 拿 hash。

這些句子要跟 `lib/agent-markdown.ts` 的講法一致。

### 5. 匯出：短效簽名連結，不加資料表

MCP 的工具回應是文字，塞不了 PDF。`export_resume` 回
`{url, expires_at, filename}`。

token 用 `app/deps.py` 已經有的 `_sign` / `_unsign`（HMAC-SHA256 +
`auth_token_secret`）簽 `resume_id|expiry`，**不新增資料表**。新路由
`GET /v1/resumes/export/{token}`，公開、`include_in_schema=False`，驗簽與過期之後
編譯並回 PDF。

- **10 分鐘。** 夠代理人下載，不夠當成分享連結用。
- **不是 share token。** `shares.py` 的 token 是使用者自願公開的狀態，有一張表、
  有 revoke、會出現在 OG image 和 talent profile 裡。匯出連結是一次性的管道。
- 走既有的 `compile_typst`，所以逾時與缺 typst 的處理完全不變。

### 6. 護欄：`agent_tool_calls`，寫在自己的交易裡

一張表做兩件事，照 `DisclosureEvent` 的前例：

```
agent_tool_calls
  id          String(36)   pk
  key_id      String(36)   fk agent_api_keys.id, ondelete CASCADE
  user_id     String(36)   fk users.id, ondelete CASCADE, indexed
  tool        String(64)   not null
  resume_id   String(36)   null
  outcome     String(32)   not null   -- ok | refused | error
  created_at  DateTime(tz) not null, indexed
```

**稽核列與 `last_used_at` 都走 `get_session_factory()` 開的獨立 session，不是請求
那一個。** `get_db`（`app/db.py:131-141`）在任何例外時 `rollback`，所以寫在請求
交易裡的稽核列會跟著拋出的 `HTTPException` 一起消失 — 正好漏掉使用者最想看的拒絕
與錯誤，而且一把只produce錯誤的 key 永遠到不了額度上限。`app/services/metering.py`
已經為同樣的理由 import `get_session_factory`，照它辦。

新檔 `app/services/mcp_audit.py`，一個 context manager 包住每次 `tools/call`：
進去先數額度，出來不論成敗寫一列。

- **額度：每把 key 每小時 300 次呼叫。** 數法照 `app/routers/talent.py` 的
  `_check_budget`：在做任何事之前先數，超過就整個拒絕。不借用
  `rate_limit.RateEvent` — 它要求一個 guest session，而且在非 production 會 no-op。
- **稽核：** 使用者在 `/settings` 看得到每把 key 最近做了什麼。發布是這張表存在
  的主要理由。
- 不記參數。參數裡有履歷內容。只記做了什麼、對哪一份、結果如何。
- 「拒絕時什麼都不寫」這條（第 10 節的測試）**不包含稽核列本身** — 被拒絕這件事
  一定要留下紀錄。

### 7. 設定頁

`app/[locale]/settings/page.tsx` 加一段，新元件
`components/settings/AgentKeys.tsx`：

- 列出活著的 key：label、建立時間、最後使用時間、撤銷按鈕。
- 「產生」→ 輸入 label →**明文只出現這一次**，附複製按鈕，關掉就再也看不到。
- 每把 key 下面一行「最近的動作」，點開看 `agent_tool_calls`。
- 連接說明：MCP endpoint 的網址，以及「貼到 claude.ai 的 Request headers，格式是
  `Authorization: Bearer ofa_…`，Bearer 後面有一個空格」。
- 如果使用者開了「開放配對」，這一段要有一行說明：代理人發布履歷時會連帶重新
  開放 talent profile。

新路由掛在 `account.py`（它已經是 `/v1/me/account` 的家）：
`GET`/`POST /v1/me/agent-keys`、`DELETE /v1/me/agent-keys/{id}`、
`GET /v1/me/agent-keys/{id}/calls`。這些是給網頁用的，走 cookie 認證，**不是**
走 `require_agent_caller` — 代理人不能用 token 去變出更多 token。

**用 zh-TW 設計與驗收，不是英文。** CJK 標籤寬約 1.5 倍、看起來也重，固定寬度會
折行、實心按鈕會擠。三個語系的字串都要加。

### 8. 要改的對外文案

這件事本身會讓已經出去的句子變成假的，所以它是設計的一部分，不是收尾。

| 位置 | 現在寫什麼 | 怎麼改 |
|---|---|---|
| `lib/agent-markdown.ts:106` | 「There is no public agent API, OAuth authorization server for machines, or self-serve API keys」 | 改成：有一個使用者自助的 MCP server，認證是使用者自己產生的 token；**沒有** machine OAuth authorization server（這半句還是真的） |
| `lib/agent-markdown.ts:107` | 「Google sign-in is for humans…not a scoped machine OAuth API」 | 留著。還是真的 |
| `lib/agent-markdown.ts:175` | contact 頁的「no self-serve API key desk」 | 改掉，指向設定頁 |
| `lib/agent-markdown.test.ts:42` | `assert.match(body, /no public agent API/i)` | 改成斷言新句子。**這個測試是故意把那句話釘住的**，所以改它要是一個看得見的動作 |
| `app/deps.py:166`、`app/models.py:1635`、`app/services/talent_keys.py:4` | docstring 說那三句「must stay true」 | 改寫成：talent credential 仍然只有營運能發；自助的是另一張表、另一個 prefix、另一道門 |

`llms.txt` 另外要加一段「怎麼連」。terms 與 privacy 完全沒提到這件事，所以沒有
法律承諾要動 — 查過了。

### 9. migration 與部署

- **`alembic/versions/043_agent_api_keys.py`**，head 目前是 `042_job_scores`。
  一支 revision 建兩張表。
- **`alembic upgrade head` 要在任何 reload 之前。** `create_all()` 每次 API 啟動
  都會跑，所以從一棵有新 model 的工作樹 HUP 下去，會在 prod 建出沒有 migration
  的表。
- **本案加新路由，所以 API 先、web 後。** 舊前端不會呼叫新路由，不對稱只往一邊
  倒。順序：`alembic upgrade head` → `npm run build:public` → `kill -HUP` API →
  roll web。
- 沒有 CI。全部手動，照 `AGENTS.md`。
- **`/api` 前綴只在公開那一側。** 在 `:8001` 上路由是裸的，所以本機驗 MCP 要打
  `http://127.0.0.1:8001/mcp`，不是 `/api/mcp`。

### 10. 測試

- `test_agent_keys.py` — mint / hash / live / 撤銷 / 過期 / 五把上限 / prefix 不符
  不查庫。
- `test_mcp_transport.py` — 沒有 Authorization 是 401；cookie 是 401；guest cookie
  是 401；talent 的 `ofk_` token 是 401（兩套 credential 不互通，這條必須有測試）；
  `initialize` / `tools/list` / `tools/call` 的形狀；`tools/list` 的內容跟
  `llm/tools.py` 的規格一致（不是第二份硬寫的清單）。
- `test_mcp_tools.py` — 每個工具一個 happy path；**跨使用者一律 404**；
  `edit_resume` 的 `stale_hash` 與 `expect` 不符；額度超過時拒絕且除了稽核列以外
  什麼都不寫；**工具拋錯時稽核列仍然留下**（這條是第 6 節那個決定的證據）。
- `test_resume_write.py` — `put_resume_source` 的 hash 不符、編譯不過時什麼都不寫、
  編譯期間有人寫進來時 409。
- `test_agent_export.py` — 簽名正確、過期被拒、別人的 resume_id 簽不出來。
- 既有的 `test_metering.py::test_billable_call_sites_are_wrapped` 是一份清單，列出
  每一個會計費的呼叫點。**v1 不開會打模型的工具，所以這份清單不該因為本案而長。
  如果它長了，就是有人開了一個不該開的工具。**

## 已經決定的事（使用者拍的）

1. **只做自助 token，不做 OAuth。** OAuth 留給第二批。
2. **代理人可以發布。** 使用者被告知「發布會連帶把 talent profile 重新開放給持有
   credential 的招募方（只有開了『開放配對』的人會這樣）」之後，選擇**照做、不特別
   處理**。所以工具不為這件事設閘，改成在工具說明、設定頁與稽核紀錄裡把它講明白。
3. **開整份原始碼寫入**，但要補上既有 `PUT` 沒有的編譯閘與 hash 防撞。
   這是內容進得來、東西刪得掉的路。
4. **職缺查詢兩種都給** — 關鍵字搜尋，加上算過分數的配對清單。
5. **tailor 分支要給** — 純複製，不跑模型。

## 留給實作者的

- MCP 的 wire format 細節（protocol version 字串、`initialize` 的 capability 欄位、
  錯誤碼對應、`tools/call` 的 content block 形狀）去規範拿。
- 工具說明的英文逐字稿。要跟 `agent-markdown.ts` 的講法一致。
- `search_jobs` / `list_matches` 回哪些欄位。`jobs_tools.py` 已經有精簡投影，照它。
- 設定頁那一段的視覺。照既有的 settings 區塊。
- 額度 300/hr 與上限 5 把 key 是起始值，不是結論。沒有實際用量可以參考。
- `list_matches` 要不要帶分頁。先不要，有上限就好。
