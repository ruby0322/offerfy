# 用表單編輯履歷，而 Typst 原始碼仍然是唯一真相

**Date:** 2026-11-19
**Status:** approved — not implemented
**Touches:** backend `app/services/typst_projection.py` (new),
`app/services/typst_intents.py` (new), `app/services/typst_anchors.py` (new),
`app/services/typst_compile.py`, `app/routers/resumes.py`, `app/schemas.py`,
`app/services/emitters/_common.py`, `tests/test_typst_projection.py` (new),
`tests/test_typst_intents.py` (new), `tests/fixtures/typst/` (new, six files)
— **no alembic revision**;
frontend `components/editor/StructuredPanel.tsx` (new),
`components/editor/RichLine.tsx` (new),
`components/editor/FormatPopover.tsx` (new),
`components/editor/PreviewHighlight.tsx` (new),
`components/editor/EditorShell.tsx`, `components/editor/EditorPreview.tsx`,
`lib/api.ts`, `lib/editor-tab.ts`, `messages/{en,zh-TW,zh-CN}.json`

## 要解決的事

今天要改履歷只有兩條路：在 CodeMirror 裡直接寫 Typst，或者請 chat 幫你改。
第一條把使用者當工程師，第二條每一次小修都要付一次模型呼叫、等一次往返，而且
模型有機會改到你沒要它改的地方。

要的是第三條：**一個表單，欄位就是履歷的內容**。改職稱就是改一格字，加一條要
點就是按一個加號，把技能段落搬到工作經歷前面就是按一下箭頭。使用者不需要知道
Typst 存在。

而這件事必須在**不動資料結構**的前提下做到。`Resume.typst_source` 是唯一真相，
照樣是那份文件被編譯、被分享、被下載。表單不是另一種儲存格式，是同一份原始碼
的另一種視圖。

### 目標

- 非技術使用者能改值、加刪搬區塊、設粗體與連結，全程看不到 Typst。
- 每一次寫入都先編譯過才落地；編譯不過就什麼都不寫。
- 每一次結構操作自成一筆 `ResumeRevision`，可以復原。
- 選到某一格時，右邊預覽標出它印在紙上的哪一行。
- 零模型呼叫。這是整個功能的前提，不是最佳化。

### 非目標

- 不取代 chat。chat 處理的是「幫我把這段重寫得更有力」，表單處理的是「把 240ms
  改成 180ms」。
- 不取代原始碼分頁。它留著，變成進階使用者和救火用的那條路。
- 不做即時協作、不做多人游標。
- 不碰 `#grid` / `#table` 這類有自己內部結構的容器（見「不在範圍內」）。

### 反目標（做到了也算失敗）

- **表單裡出現 Typst 語法。** 原型第一版曾經把 `#project(`、`name: "…"`、`)` 各
  自變成一個可編輯的「內文」欄位。使用者在裡面打字，逃脫層會把 `#` 變成 `\#`，
  整個區塊變成一行字面文字。這比沒有這個功能糟。
- **靜默改寫。** 任何「看起來存了，其實把別的地方弄壞了」都算失敗。
- **預覽的 highlight 標錯行。** 標錯比不標更傷信任。

## 原型證明了什麼，又騙了什麼

這份設計前面有一個可操作的原型（瀏覽器內，8 個版本）。它證明了：

- 只認形狀的解析器能處理真實文件：拿本專案作者自己那份 9,943 bytes 的履歷，
  解析出 **104 個編輯單位**（41 個內文行、63 個字串參數），判定 Tier A，表單
  擁有全文 **79%** 的 bytes，其餘是 import、`#show` 的樣式參數、區塊結構與空白。
- **把全部 104 個單位各自重寫一遍，文件一個 byte 都沒變。** 這是整套設計的
  地基不變式。
- 一行一個編輯面（contenteditable）加上選取浮動工具列，粗體與連結是選字套格式，
  而不是把句子切成好幾個輸入框。巢狀（粗體裡的連結）能原樣回去。

它騙的地方有兩個，這份設計要補掉：

1. **紙是假的。** 原型用 CSS 畫了一張像 A4 的紙。真機不需要——`text_layer.py`
   已經為每一頁產生逐行的 span（x / y / width / size / text，而且就在 SVG 的座標
   系裡），`PreviewTextLayer.tsx` 已經把它們疊在 SVG 上做文字選取。highlight 可以
   畫在**真的編譯輸出**上。
2. **文法在瀏覽器裡。** 原型的解析器和序列化器都是 TypeScript，而真正產生文件的
   emitter（`app/services/emitters/`）是 Python。兩份文法會漂開，而且沒有東西會
   攔住它。這是下面選 C 的全部理由。

## 三個名詞

這份文件只用這三個詞，意思固定：

- **單位（unit）**：一段可編輯的內容，加上它在原始碼裡的 `[start, end)`。兩種：
  **arg** 是 `key: "值"` 的字串字面值（職稱、公司、日期）；**line** 是一整行內容
  （一條要點、一段內文、一個段落標題），裡面的粗體／連結是它的格式，不是另一個
  單位。
- **投影（projection）**：一份原始碼解析出來的全部單位 + 區塊樹 + 解析報告。
  衍生狀態，隨時可以從原始碼重建。
- **outer span**：一個區塊「擁有」的全部範圍，含結尾換行和它後面的空行。刪除吃
  掉 outer span，新增產生一個同樣形狀的 outer span。這個定義是「刪完不會留下一行
  空的 `-`、連刪兩筆不會把區塊黏在一起」的全部原因。

## 走過的三條路

**A・前端照搬原型。** 解析、序列化、span 位移全在瀏覽器，寫回 `source`，由既有
的 400ms autosave 存檔。後端改動零。代價是文法兩份，而「照這個段落的寫法加一筆」
會變成 TS 在複製原始碼字串，不是重用 emitter 的 `heading_block` / `bullet_lines`。

**B・後端給投影，前端寫 Typst。** 解析在 Python、可以用 pytest 把
`emit(parse(src)) == src` 釘住；但序列化還在 TS，文法還是一半在那邊。工作量接近
C，好處只有一半。

**C・前端完全不碰 Typst（採用）。** 過線的東西不是 Typst 原始碼，而是「文字 ＋
標記」和意圖。前端的工作是 DOM → (文字, 標記)，裡面一個 Typst 字元都沒有，連逃脫
都不用知道。後端把它變成 Typst，用的是跟 emitter 同一份文法，跑 `typst compile`
當閘門，寫 `set_draft_source` 留 revision。

選 C 的理由：原型證明危險的地方全部在文法——逃脫策略、巢狀、`#project` 這種自訂
元件、snippet 要抄現場寫法。那些每一個都該跟 emitter 住在一起、被 pytest 釘住
（後端 98 個測試檔；前端的慣例是 `tsx --test` 手動跑，見 `AGENTS.md` §269）。

## 已經定下的決定

1. **新分頁，不是預設畫面。** 左邊面板變成 chat ／ **欄位** ／ 原始碼，記在既有
   的 `editor-tab.ts`（sessionStorage，per resume id）。第一版只求 Tier A 可用；
   認不出來的文件，使用者還有 chat 和原始碼。*（使用者決定）*
2. **後端擁有 Typst 文法；前端送文字＋標記和意圖。** *（使用者決定）*
3. **投影不進資料庫，每次請求重算；錨點用行程內快取。** `resume_contents` 看起來
   很適合，但它是 `UniqueConstraint(resume_id)` 的一行一份，而 `stored_content()`
   會把 parse 失敗當成「過期」（`app/services/resume_content.py:118-146`）——把投影
   寫進那一格會**擠掉換版型用的快照**，而且是靜默的，下一次 `switch_template` 就得
   重新付一次 `extract_content` 的模型呼叫。對一個以「零模型呼叫」為前提的功能，
   那是最糟的互動。而投影本來就不需要快取：純解析、微秒級、沒有模型也沒有編譯。
   要快取的是**錨點**（它要一次 typst 子程序），用 `templates.py` 的
   `_support_cache` 那種行程內快取，key 是 source hash，容量上限 32 份。
   **所以這個功能不需要 alembic migration。**
   *（我的判斷。原稿寫的是放進 `resume_contents`，是審閱指出它會擠掉 ResumeContent
   的——這條在這個 repo 特別貴，因為沒有測試跑 alembic，唯一的 live Postgres 是 prod。）*
4. **每一次寫入都要先編譯。** 編譯不過就整筆丟棄，回傳未改動的原始碼和原因。
   這條沿用 `switch_template` 已經在做的事：「a deterministic path that can commit
   a broken document is worse than the model path it replaces」。*（我的判斷）*
5. **結構操作 `coalesce=False`，值編輯沿用預設合併。** 預設的 120 秒合併窗口對逐字
   輸入是對的，對「刪掉一整筆工作」是災難：使用者兩秒後想復原，那一筆已經和前面
   的打字合併了。*（我的判斷）*

   **更正（實作時，Task 6）：值編輯也一律 `coalesce=False`。** 決策 10 本來要用
   `hash` 當「這筆 revision 是不是我自己寫的」的判準，實作證明那個判準恆為真，見
   下方決策 10 的更正。所以這裡的「沿用預設合併」沒有安全的實作方式，整個端點改成
   每一次成功的編輯都是獨立一筆 revision。
6. **寫入前驗證那一段 byte。** 任何意圖都帶著它認為那個單位現在是什麼；伺服器比
   對不符就拒絕並要求重建投影。原型在這裡被咬過一次：兩個 listener 握著不同世代的
   span，一次按鍵寫了兩次，把「地點」的值寫進了 `#let github` 那一行。*（我的判斷）*
7. **訪客一樣能用。** `owner_context` 已經處理 `GuestSession`，不另外開路。*（我的判斷）*
8. **`/edit` 回傳新的 `typst_source`，而且欄位分頁開著的時候要擋住 autosave。**
   這是這份設計最容易靜默吃掉使用者編輯的地方：`EditorShell` 有一個 400ms 的
   debounce 會把 `source` state `PUT` 回去（`EditorShell.tsx:932-950`），還有
   `flushSave()` 會在離開編輯器、切換履歷、開新 tailor 時再 `PUT` 一次
   （`EditorShell.tsx:1273-1296`）。欄位分頁寫完之後，那個 buffer 還是**編輯前**的
   文件——第一次 `flushSave` 就會把它蓋回去，而且因為落在 `set_draft_source` 的
   120 秒合併窗口裡（`revisions.py:32-64`），歷史也救不回來。這正好是反目標 2
   「靜默改寫」，而且走的是原稿完全沒提到的路。做法照抄 import／tailor 既有的那一
   套：`skipTimers` + 游標 ref + `importBlocksSave`（`EditorShell.tsx:470-530`、
   `912-930`），欄位分頁拿到 source 的擁有權時就擋掉 autosave，意圖回來的新 source
   直接 adopt。*（我的判斷；是審閱指出的）*
9. **值編輯在「提交」時才寫，不是每個按鍵。** 提交 = 離開那一格、按 Enter，或
   停手 2 秒。理由是成本：一次編輯如果同時觸發「閘門編譯 + `/preview`（一次 SVG
   加一次 PDF）+ 2 秒後的 `/compile` + 錨點的 `typst eval`」，那是**五個 typst
   子程序**，而且都在同步 handler 裡握著 DB session——這支 API 已經被同步 handler
   的 pool 耗盡整支打掛過一次。所以 `/edit` 的回應**同時帶回預覽頁和錨點**，一次
   編輯一個往返一次編譯；`/edit` 必須是 `async def`，編譯走
   `asyncio.to_thread`（照 `switch_template` 和 chat 的既有慣例），而且不要跨編譯
   持有 session。*（我的判斷；是審閱算出那五個子程序的）*
10. **只有在「上一筆 revision 就是這個分頁自己寫的」時才合併。** 預設的
    `set_draft_source` 會合併進 120 秒內的最後一筆 revision，不管那筆是誰寫的
    （`revisions.py:44-57`）——所以一次 chat 回覆、import 或換版型之後兩分鐘內的
    欄位編輯，會就地改寫**那一筆**，不可復原。不需要為此加 provenance 欄位：意圖
    已經帶著 `hash`，伺服器只要在 `hash` 等於最後一筆 revision 的 `content_hash`
    時才允許合併，否則 `coalesce=False`。**一樣不需要 migration。**
    *（我的判斷；是審閱指出這個窗口不分來源的）*

    **更正（實作時，Task 6）：這個判準不管用，目標改用 `coalesce=False` 達成。**
    `set_draft_source` 每次寫入都讓最後一筆 revision 的 `content_hash` 等於當下的
    來源，而一個不陳舊的請求的 `hash` 依定義也等於當下的來源——所以
    `hash == latest.content_hash` 對每一個合法請求都成立，永遠分不出那筆 revision
    是誰寫的。implementer 照字面實作後，本決策自己的測試就以 `assert 2 == 3` 失敗，
    這是這條規則不可能成立的證明，不是測試寫錯。
    **決策 10 要保護的事不變**（絕不就地改寫別人的 revision），改用無條件
    `coalesce=False` 達成：代價是每一次成功的欄位編輯多一筆 revision。真要恢復合併
    需要 provenance 欄位，那是另一次變更。*（我的裁定）*
11. **樣式參數進表單，但分組收起來。** 認得的內容 key（`title`、`company`、
    `institution`、日期⋯）是欄位；`font`、`paper`、`lang`、`margin`、`*-color`、
    `*-position`、`*-font`、`size`、`leading` 這些用名字判定的樣式 key 收進一個預設
    折疊的「版面設定」區；其餘不認得的 key 照樣是可編欄位，標籤就用原始 key。
    不認得的 key **不影響 Tier**。理由：allowlist 會把 Tier A 縮到只有四個有 emitter
    的版型，denylist 不分組則會讓 `accent-color` 和 `font` 混在職稱旁邊。分組之後
    這些其實是功能——不用碰 Typst 就能改強調色。*（我的判斷；是審閱指出原稿只給了
    兩個例子、沒給規則的）*

## 投影

`GET /v1/resumes/{id}/projection` →

```jsonc
{
  "hash": "9f2c…",              // revisions.source_hash(typst_source)
  "tier": "A",                   // A 全可編 / B 有唯讀 / C 無可編單位
  "sections": [                  // 區塊樹，表單直接照它畫
    { "id": "s3", "title_unit": "u12", "outer": [1830, 4460],
      "items": [
        { "id": "s3i0", "kind": "comp", "name": "work",
          "outer": [1852, 2410], "content_end": 2398,
          "args": ["u13", "u14", "u15", "u16", "u17"],
          "bullets": [{ "id": "s3i0b0", "outer": [2180, 2290], "unit": "u18" }],
          "notes": [{ "outer": [2290, 2340], "text": "// - 這行被註解掉了" }] }
      ] }
  ],
  "units": {
    "u13": { "kind": "arg", "key": "title", "label_key": "field.title",
             "span": [1875, 1902], "value": "後端工程師", "editable": true },
    "u18": { "kind": "line", "role": "bullet", "span": [2182, 2289],
             "text": "把訂單查詢的 p95 從 1.8s 壓到 240ms",
             "marks": [{ "kind": "strong", "start": 7, "end": 10 }],
             "opaque": [], "editable": true }
  },
  "report": { "structures": {"#import": 1, "#set": 1, "#pagebreak": 1, "comment": 1},
              "skipped_args": {"font": 1, "lang": 1},
              "lints": ["github 的值看起來是標籤而不是網址"] }
}
```

要點：

- **單位的值不是 Typst。** `arg` 給解好逃脫的字串；`line` 給純文字加上標記陣列
  （`strong` / `emph` / `link`，offset 是純文字座標）。
- **所有 offset 都是 code point，不是 byte。** 這份文件前面幾處寫「byte」是鬆散的用詞
  *（2026-11-19 執行時裁定）*：解析器和意圖處理器全程切的是 Python `str`，JSON 載的是
  文字而不是位元組，換成 UTF-8 byte offset 只會讓每次切片多一次 encode／decode。
  **前端必須用 code point 索引**（`Array.from(text)`，不是 `.length` / `.slice`）：
  CJK 在 UTF-16 下剛好一致，但 emoji 這類 astral 字元一個 code point 等於兩個 UTF-16
  單位，marks 的 offset 就會差。這條要有測試，fixture 裡要有一個 astral 字元。認不出來的行內構造列在
  `opaque` 裡並讓 `editable: false`，**絕不假裝它是內文**。
- `label_key` 是 i18n key，不是中文字串。三語在前端的 `messages/*.json` 裡。
- `report` 是給產品判斷覆蓋率用的，表單**不顯示**文件設定和註解——它們不能編也
  不是內容。但它們被算進去了，沒有被偷偷忽略。
- **不快取。** 投影是 `typst_source` 的純函式，每次請求重算（見決定 3）。前端手上
  那份靠 `hash` 自我驗證：hash 不等於伺服器算出來的就是過期，重抓。

## 意圖 API

`POST /v1/resumes/{id}/edit`，一次一個意圖，body 一律帶 `hash` 和 `expect`：

```jsonc
{ "hash": "9f2c…",           // 前端手上投影的 hash
  "unit": "u18",
  "expect": "把訂單查詢的 p95 從 1.8s 壓到 240ms",   // 它認為那個單位現在是什麼
  "intent": { "kind": "set_line", "text": "把訂單查詢的 p95 壓到 180ms",
              "marks": [{ "kind": "strong", "start": 0, "end": 4 }] } }
```

意圖清單（第一版）：

| kind | 參數 | 做什麼 |
|---|---|---|
| `set_arg` | `value` | 換掉一個字串字面值，過 `typst_value` 逃脫 |
| `set_line` | `text`, `marks` | 用一份文字＋標記重新產生這一行的 Typst |
| `insert_bullet` | `after`（區塊 id） | 在那個區塊的 `content_end` 插一條 |
| `insert_item` | `section`（段落 id） | 抄那個段落最後一個區塊的形狀，字串值換成佔位（見下）|
| `insert_section` | — | 標題和第一筆一起生 |
| `delete` | `target`（區塊／bullet／段落 id） | 吃掉 outer span；刪到最後一個子項時連標題一起刪 |
| `move_section` | `section`, `dir` | 兩塊 outer 對調，一次 splice |

回應一律是
`{hash, typst_source, projection, preview: {pages, text_layers, bands}}`：

- `typst_source` 讓 `EditorShell` 直接 adopt，取代它手上那份過期的 buffer（決定 8）。
  沒有這一欄，第一次 `flushSave` 就會把編輯蓋掉。
- `preview` 和意圖共用同一次編譯（決定 9），所以一次編輯一個往返一次編譯，而不是
  三次。
- 前端拿新投影取代舊的，**但正在編輯的那個單位不重繪**（否則游標會跳）。

### 新增區塊的形狀從哪來

**主路徑是抄現場**：複製那個段落最後一個區塊的原始碼，把每個字串字面值換成佔位。
這條能一般化到全部 97 個版型和手寫的文件。

emitter 的 helper（`_common.py` 的 `heading_block` / `bullet_lines` / `dates_expr`）
**只在沒有東西可抄的時候**用——段落是空的，而且這份文件的 `(name, version)` 剛好有
emitter。原稿把這兩條寫成了矛盾的話，實際上它們產生的東西不一樣：`dates_expr` 吐
`dates-helper(...)`，但 `ats-friendly-resume` 把它改名叫 `dates-util`，`acorn-resume`
根本沒有這個函式（這就是 `date_range_expr` 存在的原因），而 97 個版型裡只有 4 個有
emitter。所以 emitter 是備援，不是主路徑。

`insert_section` 只支援 `== ` 當段落標題的文件。不是這種結構的文件，結構操作標成
不支援（值編輯照樣可用）。

**更正（實作時，Task 11）：完全沒有標題的文件改成允許追加。** 原本的拒絕在實作後
產生了一個死路：Task 11 加的「刪除區塊」按鈕刪掉最後一個段落的最後一筆之後，文件
就沒有 `== ` 標題了，於是 `insert_section` 拒絕，使用者**再也加不回一個段落**。
裁定：移除死路而不是守住它——`insert_section` 可以在沒有任何標題的文件上追加
`== 新段落`，連帶「唯一一個有標題的段落不能刪」那個拒絕也一起拿掉。
但**本條的原意仍然成立**：這條拒絕本來是為了避免把 `== ` 塞進「用別種慣例組織段落」
的文件裡，產生混用的結構。所以判準改成看證據而不是看有無標題：報告裡數到
`=` 或 `===` 標題（別種慣例的明確證據）就拒絕；什麼標題都沒有就允許。

**再更正（同一天，審閱指出上面這條還是留了一個死路）：沒有 `== ` 段落時一律允許
追加，不看其他標題。** 上面那條判準分不出兩種長得一樣的文件：一份從來沒有 `== `
段落、用 `= ` 當段落標題的（該拒絕），和一份本來有 `== ` 段落、剛剛被刪掉最後一個、
只剩下 `= 姓名` 的（該允許）。在「要不要追加」的那一刻，兩者的證據完全相同。
`= 姓名` 配 `== ` 段落是最常見的履歷寫法，所以第二種不是邊角案例。
裁定：接受較小的代價。允許追加的代價是「用別種慣例的文件會多一個可能格式不對的
`== 新段落`」——在預覽裡立刻看得見，而且現在刪得掉；拒絕的代價是把人困在死路裡，
而且困住的正是「不想碰 Typst」的那種使用者，也就是這整個功能存在的理由。
用別種慣例的文件本來就只會投影出很少東西，多一個標題不會讓它更糟多少。
*（我的裁定。這等於回到我第一版的寬鬆行為，但這次知道代價換到了什麼。）*

拒絕的情況有三種，各自有自己的 code，前端照 code 顯示：
`stale_hash`（投影過期 → 重建）、`expect_mismatch`（那段 byte 不是它以為的那段 →
重建）、`compile_failed`（帶 typst 的 stderr 第一行）。

### 逃脫策略跟著原文

`set_line` 產生 Typst 時，**canonical 逃脫集之外，這一段原本就逃脫過的字元繼續
逃脫**。所以 `Projects & Achievements` 的裸 `&` 和 `Systems Analysis \& Design`
的 `\&` 都能原封不動回去。這是「每個模板寫法不一樣」的直接解法：不要訂一套全域
規則，照每一段自己的寫法走。投影要把這個集合帶給意圖處理器（放在單位裡，前端不
需要看懂）。

### 字串參數放不了格式

`title: "*x*"` 在 Typst 裡會把星號原樣印出來。所以 `arg` 單位的
`editable_marks: false`，前端的工具列整排變灰並說明原因。要讓職稱能有粗體，得版型
那邊把那個參數改成收內容區塊 `[…]`，那是版型 API 的事，不是編輯器能決定的。

## 版面行 highlight

這是這份設計裡唯一需要新機制的地方，而且**三條路都實測過了**（2026-11-19，
typst 0.15.1，拿作者本人的履歷）：

| 做法 | 結果 |
|---|---|
| 按順序配（解析器的單位順序 vs text layer 的閱讀順序） | **不可用。** 內文行只有 33/35 單調遞增；字串參數只有 37/62——同一列的左右欄被 pdfminer 打亂，而且一行可以被 12 個參數共用 |
| 用字串比對找行 | **不可用。** 41 個內文行有 6 個對不到（連結的中括號、`\&`、字形替換），而且這條路的教訓已經寫在 `layout.py` 的 docstring 裡：五個 bug，沒有一個在幾何 |
| 插 `#metadata(…)<label>` 到影子副本 | **不可用。** 2 頁變 3 頁 |
| **在影子副本貼純標籤 `<uNN>`** | **採用。** 2 頁 vs 2 頁、每頁行數一致、**最大位移 0.0000pt**、文字零差異 |

機制：

1. 取 `typst_source`，在每個 `line` 單位的 `end` 貼一個 ` <uNN>`（純標籤是
   metadata，不是 content，所以不佔版面）。這是記憶體裡的影子副本，**不會被存、
   不會被分享**。
2. 跑一次
   `typst eval 'query(selector(label("u1")).or(label("u2"))…).map(it => (id: str(it.label), p: it.location().page(), y: it.location().position().y.pt()))' --in <shadow>`
   → 每個單位落在哪一頁的哪個 y。實測回傳
   `[{"id":"f8","p":1,"x":36.0,"y":88.59}, …]`。
3. 把它和既有的 `text_layer` 行 rect 交叉：某一行屬於「上一個單位的 y」到「這個
   單位的 y」之間的那個單位。純區間判斷，沒有字串比對。
4. 前端收到 `{unit_id: [{page, y, height}]}`，在 `PreviewTextLayer` 旁邊畫一層
   絕對定位的 band。

兩個已知限制，要寫在程式碼註解裡：

- `typst query` 子命令在 0.15.1 已經 deprecated，要用 `typst eval`。
- `query(heading)` 回傳 6，而文件裡只有 5 個 `== `——套件把姓名也排成了 heading。
  **所以不能用元素型別盲目按順序配**，必須是我們自己貼的標籤。
- 字串參數（職稱／公司／日期）不是 `line`，貼不了標籤。它們的 highlight 退回
  「這個區塊的那幾列」：用前後兩個 `line` 單位的 y 夾出區塊的帶狀範圍。

這一步多一次 typst 呼叫。只在欄位分頁開著的時候跑，而且和投影共用同一個
`derived_from_hash` 快取。

## 使用者看到什麼

```
┌─ 左面板（分頁）────────────┐  ┌─ 右面板 ──────────────┐
│ chat │ 欄位 │ 原始碼      │  │  預覽（既有 SVG + text  │
├────────────────────────────┤  │  layer）＋ 新的 band 層 │
│ 基本資料                   │  │                        │
│   姓名  [林宜蓁        ]   │  │  ┌──────────────────┐  │
│   Email [yichen@…      ]   │  │  │ 林宜蓁           │  │
│ ─────────── 工作經歷 ↑↓ ✕ │  │  │ ████████████████ │← │
│  ┌ #work ─────────── 刪除 ┐│  │  │ 後端工程師  2023 │  │
│  │ 職稱 [後端工程師      ]││  │  └──────────────────┘  │
│  │ 期間 [2023-03][Present]││  │                        │
│  │ 要點 1                 ││  └────────────────────────┘
│  │ ┌────────────────────┐ ││
│  │ │把訂單查詢的 p95 從 │ ││  選到一格 → 右邊標出它印在
│  │ │1.8s 壓到 240ms     │ ││  紙上的那一行（青色）
│  │ └────────────────────┘ ││  按刪除 → 標出整塊要消失的
│  │ ＋ 加一條要點          ││  範圍（紅色）
│  └────────────────────────┘│
│  ＋ 照這個段落的寫法加一筆 │
└────────────────────────────┘
```

- 一條要點是**一個** contenteditable，不是一排輸入框。選一段字，旁邊浮出
  `B ／ I ／ 連結`；鍵盤是 `⌘B` / `⌘I` / `⌘K`，`⌘K` 直接把網址欄打開、游標已經
  在裡面，Enter 套上去。點既有的連結會浮出同一條、網址已填好，可以改或移除。
- 浮動工具列開網址欄的那一刻瀏覽器會丟掉選取，所以**開啟時要把 Range 複製一份，
  套用前還原**。這是整個手勢成立的關鍵細節。
- Tier B 的唯讀單位照樣顯示內容，但不給編，並說明原因（「這一段的格式序列化回不去
  原樣」）。Tier C 的文件不給開這個分頁，直接說為什麼並指向 chat／原始碼。
- 三語都要有。設計與驗收都用 zh-TW：CJK 標籤寬約 1.5 倍，英文先做一定會在中文下
  換行。

## 不變式與測試

這些是值得用測試釘住的，不是註解：

1. **投影往返**：對任一份原始碼，把每個單位用它自己的值重新序列化回去，文件逐
   byte 相同。（原型在一份真履歷的 104 個單位上成立；pytest 跑下面那組合成
   fixture。）
2. **`emit(parse(src)) == src`**：對四個有 emitter 的 (name, version)，從
   `ResumeContent` emit 出來的文件，解析回來再序列化，等於原文。這條把解析器和
   emitter 釘在同一份文法上，是 snippet 生成正確性的唯一保證。
3. **逃脫策略往返**：含 `\&` 與裸 `&`、`\@`、`\*`、`\~`、CJK、`//` 的單位，
   `set_line` 之後逐 byte 相同。
4. **outer span 的空白守恆**：連續五次「加一條再刪掉」之後文件 byte 不變。
   （原型驗過，931 bytes 回到一個 byte 不差。）
5. **標籤不動版面**：貼標籤的影子副本編譯出來，頁數、每頁行數、每行座標與原文
   相同。這條是 highlight 正確性的前提，也是未來升級 typst 版本時第一個會壞的
   東西——所以要有測試。
6. **編譯閘門**：故意送一個會壞的 `set_arg`（值裡有裸的 `"`），斷言原始碼沒變、
   revision 沒長、回傳 `compile_failed`。
7. **expect 守衛**：送一個 `expect` 和現況不符的意圖，斷言拒絕且原始碼沒變。
8. **不合併進別人的 revision**：先寫一筆（模擬 chat 的）revision，120 秒內送一個值
   編輯意圖，斷言長出**新的一筆**而不是就地改寫前一筆（決定 10）。
9. **autosave 蓋不掉欄位分頁的寫入**：守衛邏輯抽成 `lib/` 裡的純函式
   （「欄位分頁持有 source 時，debounce 和 flushSave 都不送」），用 `tsx --test` 測。
   這個 repo 沒有前端互動測試，所以不要把這條寄託在手動點擊上。

## 測試 fixture

原型和它驗證過的那份履歷都不在 repo 裡，而那份履歷是真人的個資（email、電話、
雇主），不進 git。所以第一步要先造一組**合成**的 fixture，放
`apps/backend/tests/fixtures/typst/`，名字、聯絡方式、公司全部是編的
*（使用者決定：做合成的，真履歷留在本機只用來量覆蓋率）*：

| 檔案 | 要逼出什麼 |
|---|---|
| `synthetic-en.typ` | `#let` 間接（有人用的 + 死綁定各一）、`#show: resume.with(...)` 含 `font` 的 tuple 與 `accent-color`／`paper`／`lang`／`author-position`、`#set par`、`#import`、`#pagebreak()`、`#align(left)[…]` 包一段內文、四個以上 `== ` 段落（其中一個標題含**裸 `&`**）、`#work`／`#edu`／`#project` 三種元件（`#project` 證明只認形狀）、`dates-helper(start-date:, end-date:)` 和一個 `dates: ""`、左右兩欄的參數 |
| `synthetic-zh.typ` | 同樣的構造但 CJK 內容，驗 zh-TW 的寬度與斷行 |
| `synthetic-marks.typ` | 行內標記的全部情況：`*粗體*`、`_斜體_`、`#link("url")[文字]`、**粗體裡的連結**、`[巢狀內容區塊]`、`\&`／`\@`／`\*`／`\~`／`\$` 的逃脫、一條被 `//` 註解掉的 bullet **夾在兩條 bullet 中間**（證明 bullet 串會跨過它而不是被切斷）、開頭是 `*標籤：*` 的技能行 |
| `synthetic-tier-b.typ` | 含 `#grid` 和一個序列化回不去的構造，驗唯讀路徑和 Tier B 判定 |
| `synthetic-tier-c.typ` | 沒有任何可編單位，驗分頁不給開 |
| `synthetic-en.units.json` | golden：單位列表（id、kind、key、span、值）。*（2026-11-19 執行時裁定：它的內容是 byte offset 的 span，不可能手寫，所以它是解析器產生的 snapshot，歸第三步而不是第一步；第一步的驗收基準是 fixture 本身編得過、而且涵蓋這張表的每一格。）* |

錨點那條不釘座標（升級 typst 就會變），只釘**版面同一性**：貼標籤前後的頁數、每頁
行數、每行座標相同。

原型的解析器（TypeScript）連同已發佈的 POC 頁面一起放
`docs/superpowers/prototypes/2026-11-19-structured-typst-editing/`，當第一步的參考
實作——不是要跑它，是要能對著移植，而不是從一段文字重新推導文法。

## 工作順序

1. **合成 fixture + golden dump**（見上），然後**後端解析器**
   （`typst_projection.py`）+ 不變式 1。只認形狀：`#ident(...)` 裡的
   `key: "字串字面值"`、`#let` 一跳間接、行內標記遞迴、認不出來的行永遠不變成可編
   單位。這一塊是全部風險所在，先做完、先釘死。fixture 要先存在，否則這一步沒有
   驗收基準。
2. **投影端點**（純函式，不快取）+ Tier 判定 + 解析報告 + 樣式 key 的分組規則
   （決定 11）。此時可以用 curl 量真實覆蓋率。**沒有 migration。**
3. **意圖處理器**（`typst_intents.py`）：`set_arg` / `set_line` 先行，含逃脫策略、
   expect 守衛、編譯閘門、合併規則（決定 10）、`async def` + `asyncio.to_thread`。
   不變式 3、6、7、8。
4. **前端欄位分頁**：區塊樹、arg 輸入框、`RichLine` contenteditable、三語，**以及
   autosave 守衛和 source adopt（決定 8）**。只接 `set_arg` / `set_line`，提交時機照
   決定 9。不變式 9。**此時已經可以給人用——但沒有決定 8 就不行，那會吃掉編輯。**
5. **結構意圖**：insert / delete / move，含 outer span 的空白正規化。不變式 4。
   前端補加號、刪除的兩段式確認、段落箭頭。
6. **格式**：浮動工具列、`⌘B/⌘I/⌘K`、Range 還原、巢狀。
7. **highlight**：`typst_anchors.py` + 影子副本 + `typst eval` + band 層。
   不變式 5。
8. **空段落的備援 snippet**：沒有區塊可抄的時候，用 emitter 的 helper
   （`_common.py` 的 `heading_block` / `bullet_lines` / `dates_expr`），只對有 emitter
   的 `(name, version)` 生效。同時補不變式 2，把解析器和 emitter 釘在同一份文法上。
   排在最後，因為主路徑（抄現場）在第 5 步就已經涵蓋了全部 97 個版型。

每一步結束時 `source` 都還是唯一真相，所以任何一步停下來都不會留下半個功能。

## 不在範圍內

- `#grid` / `#table` 和其他有內部結構的容器。它們會被標成「沒認出來的構造」，
  讓那份文件掉到 Tier B。認得它們是下一個題目。
  *（2026-11-19 執行時補的規則：元件裡的**位置參數字串**也算在這一類——`#tag("…")`、
  `#cvitem("職稱", "公司")` 這種寫法在 97 個版型裡很常見，而它們是人要編的內容。
  所以「元件內未被投影的內容」要記進 `report.skipped_args["(positional)"]` 並且
  **強制 Tier B**。沒有這條，這種文件會判 Tier A——宣稱每個內容單位都可編，而表單
  其實少顯示了半份履歷，而且不會有任何東西失敗。）*
- 多行段落合併成一段。現在一行就是一個單位。
- 把欄位分頁變成預設畫面。那要先有線上文件的 Tier 分布數據。
- 自動分頁位置的回饋（「這條要點把你擠到第二頁」）。`layout.py` 已經能算，但那是
  另一個功能。

## 留給實作者的

- 投影 payload 在很長的文件上要不要分頁。（9.9KB 的履歷產生約 12KB 的 JSON，
  先不處理。）
- `label_key` 的命名與 fallback：認不出來的 `key` 直接顯示原字串，還是顯示
  `field.unknown` 加上原 key。
- 影子副本的 `typst eval` 要不要和預覽的兩次編譯合併成一次子程序呼叫。
- 浮動工具列在手機寬度（約 400px）的落點。
