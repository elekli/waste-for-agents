# Agent-first RSS 訂閱層 — Design Spec

**日期:** 2026-06-25(2026-06-26 更新:5 個開放問題經 elek 拍板)
**狀態:** Draft — brainstorming 收斂後的設計,5 個開放問題已定案,待 spec-reviewer 審 + gap-verification
**設計文件(策略 source of truth):** `~/.claude/plans/https-blogtrottr-com-expressive-reef.md`
**上層 HANDOFF:** `~/.claude/plans/agent-watch-service-HANDOFF.md`
**前一份實作計畫(Batch 1, 已交付):** `docs/superpowers/plans/2026-06-21-waste-for-agents-mvp.md`

---

## Context — wedge 怎麼收斂到 RSS

Batch 1 建好 MVP(store/diff/scheduler/server/twinkle/http_json)後,用真實資料壓測 diff 引擎時撞到一個會動搖原 wedge 的發現,並由此收斂出這份設計:

1. **「監看另一個 MCP」是死路。** 實測 YouBike「每1分」即時源:透過 Twinkle Hub `query_rows` 拿到的是它 16 天前 normalise 後凍住的 cache,直打上游 JSON 卻是此刻值。根因不是「慢」,是**hub 的 cache 延遲不透明且不可控**——`ly-bills` 一樣中招,只是它本來就慢,延遲偽裝成「還沒變」看不出來。更根本地:**MCP 的本質是 pull-on-demand,「監看一個 pull-on-demand 介面」是範疇錯誤**。要即時就繞過 MCP 回源頭,MCP 那層始終可繞過、不是產品。

2. **收斂後的 wedge:** agent-first 的「一更新就想知道」訂閱層,載體是 **RSS 與有 API 的平台 feed**,不碰爬蟲(維護 treadmill)、不監看 MCP。

3. **RSS 是 wedge 入口,不是護城河。** RSS 監看是紅海(Blogtrottr 等),單一 adapter 的技術差異(MCP 介面、結構化 diff、游標)都可複製。RSS 的角色是**最容易讓 agent 開發者上手、dogfood、驗證需求**的載體。第一個 dogfood 場景已定:**elek 自己監看 Hacker News**(設計文件記錄的第一手痛點:用 Gemini 監看 HN 很難用、它不主動用 RSS)。

4. **agent-first 的真定義(本輪銳化):** 不是「有 MCP 介面」,而是 **agent 能在不經過使用者逐步操作的前提下,自主完成「發現服務 → 訂閱某 feed → 持續取得通知」的閉環**。這條直接塑形了 feed discovery 與計費 gate 的設計。

---

## 目標 / Non-goals

**目標:** 讓既有的 `source → scheduler → diff → store → list_changes` 管線新增一個 RSS 來源,並補上「agent 自主訂閱閉環」需要的最小機制(feed discovery + API key 認證 + 計費 gate 骨架)。dogfood = 監看 HN。

**Non-goals(本 MVP 不做):**
- HTTP conditional GET(ETag/If-Modified-Since)——列**第一個 fast-follow**,觸發點 = 有使用者把 `interval_s` 調到高頻。
- 真金流(綠界/Stripe)、帳號/訂閱 UI、自動開通——計費只做**機制骨架**,dogfood 用手動開額度。
- 撤稿偵測(RSS `removed`)——抑制,不報。
- 爬蟲 / 無 RSS 網頁 adapter、webhook 推送(v2)、多實例 / Postgres 遷移。

---

## 架構

**核心原則:最大化複用既有已測管線。新東西要嘛是新 adapter、要嘛是 watch 上的宣告式 policy,不切進引擎核心(brainstorming 拍板的方案 1)。**

### 元件分層(複用 vs 新增)

```
┌─ 對外介面:server.py (FastMCP + FastAPI) ───────────────────┐
│   · auth middleware (API key + rate limit) ........ [新]    │
│   · create_watch  + feed discovery ................ [改]    │
│   · list_changes  + 計量 gate ..................... [改]    │
│   · list_watches / delete_watch ................... 既有    │
└─────────────────────────────────────────────────────────────┘
┌─ 監看引擎(全部既有,只加宣告式 policy) ─────────────────────┐
│   scheduler.py → diff.py → store.py                         │
│   · diff 多讀 watch policy: source_kind(dataset|rolling)   │
│       rolling_window: added 對 seen-set、removed 抑制 (F2)  │
│   · store 加欄位: watch policy / seen-set / 輪計數 / keys   │
└─────────────────────────────────────────────────────────────┘
┌─ 來源 adapter(Source protocol,簽名不變) ──────────────────┐
│   · sources/rss.py ................................ [新]    │
│       feedparser → 穩定 id → content 轉 pinned MD          │
│   · sources/http_json.py / twinkle.py ............. 不動    │
└─────────────────────────────────────────────────────────────┘
```

### 三條資料流

```
建立  agent ─(auth)→ create_watch(source="rss", {url 或 site})
            └→ [若給 site: feed discovery 抓 HTML 找 feed link]
            └→ store 建 watch{ policy: source_kind=rolling_window, free_rounds=2 }

監看  scheduler tick ─→ rss.fetch(url) ─→ 正規化(穩定 id + HTML→MD)
            └→ diff(reference, new, 讀 policy.source_kind) ─→ record_run 原子寫 events
               · rolling_window: reference = 累積 seen-set,非最後窗口(F2)

通知  agent ─(auth)→ list_changes(cursor)
            └→ events_since(cursor) + per-watch 額度 gate
            └→ 交付額度內 events;超額 withhold + 附「如何升級」結構化訊息
```

---

## 確認的設計決策(brainstorming rationale)

| # | 決策 | 理由 |
|---|------|------|
| 1 | **抑制 `removed`**,RSS watch 只報 `added`+`modified` | RSS 是滾動窗口,舊文掉出 ≠ 刪除;直接套既有「完整集合」diff 會系統性誤報。撤稿偵測延後。 |
| 2 | **穩定 `id`**:`guid`/`atom:id` → `link` → `hash(title+published)`,key 預設 `["id"]` | 識別髒活關在 adapter 內,對外暴露乾淨穩定 id(開箱即用,符合 agent-first)。 |
| 3 | **feedparser 當解析底層** | externalize RSS/Atom 格式地獄(兩套 schema、編碼、id 統一、日期),符合「能不碰維護則不碰」。 |
| 4 | 預設 ignore 重生成時間戳(`updated`),modified 由內容欄位驅動,`published` 保留;`ignore_columns` 可覆寫 | 真編輯必動內容欄位 → 照樣觸發 modified;光 `updated` 跳是噪音(YouBike `srcUpdateTime` 教訓)。 |
| 5 | **conditional GET 後置**,預設 poll 間隔小時級(如 3600s) | RSS 是內容發布、非即時告警,訂閱者沒有分鐘級需求;poll 稀疏 → conditional GET 是過早優化。觸發點 = interval 被調高頻。 |
| 6 | **content 轉 pinned Markdown** | 保留連結/結構(agent 常需 content 內連結決定下一步)、去 HTML 噪音、LLM 原生友善。**前提:pin 轉換器版本確保 determinism**。 |
| — | **計費 gate 從 create 移到「持續通知」** | create + 首次變化通知免費 → agent 無付款能力也能自主建 watch + 嚐到價值;gate 延後到「證明持續有價值」。PLG。 |
| — | **計量單位 = 輪次(round),非篇數**(elek 定案) | 計費輪 = 一次**產生新內容(`added`)的 diff 週期**;前 2 輪免費,第 3 輪起 gated。「舊文換連結」(只產 `modified`、無 `added`)**不計輪**。見「計費 gate」段。 |
| — | **橫切關注點走宣告式 watch policy(方案1)** | `source_kind`(gap-verification F2 將原 `suppress_removed` bool 升為此語意軸)、計量都降為 watch 屬性,引擎只讀屬性、不認得「RSS」或「計費」,保持通用乾淨。 |
| — | **db: 現在 SQLite,上線先遷 Neon,需擴展再升 Supabase**(elek 定案) | Neon 只需純 Postgres、DX 佳、零 infra 起步;真要 auth/storage/realtime 再升 Supabase。不為還沒有的量先付架構稅。 |
| — | **金流: 暫只接台灣支付,傾向綠界**(elek 定案) | elek 無法在台灣為 Stripe 營業、亦無美國/其他 Stripe 國家帳戶(除非走 Stripe Atlas);台灣選項中綠界近期 DX 改善明顯。真金流後置。 |

---

## 資料模型(store schema 改動)

既有 `watches` / `snapshots` / `change_events` 表保留。改動:

**`watches` 加欄位(宣告式 policy + 計量 + 歸屬):**
- `source_kind TEXT DEFAULT 'dataset'` — `dataset`(現行:diff 對最後 snapshot、removed 為真)或 `rolling_window`(RSS:added 對 seen-set、modified 對最後已知內容、removed 抑制)。**取代原單一 `suppress_removed` bool**(gap-verification F2:三個行為綁在一起,該升為一個語意軸)。
- `free_rounds INTEGER DEFAULT 2` — 免費**計費輪**額度(elek 定案 2 輪;計量單位是輪次非篇數,見計費 gate)。
- `delivered_rounds INTEGER DEFAULT 0` — 已交付的計費輪計數,gate 用。
- `api_key_id` — watch 歸屬的 key(濫用面 + 計費歸戶)。

**gap-verification 牽出的新增(F2/F3/F4,精確 schema 待 C-stub/C-perwatch 拍板):**
- **seen-set 持久化(F2):** rolling_window watch 要存「每個曾見 id → 最後已知狀態 + 版本戳」,不只最後窗口。`snapshots` 單列模型不夠,需擴(per-watch seen 映射或 `seen_entries` 表)。
- **run 歸屬(F4):** `change_events` 加 `run_seq`(per-watch 遞增),讓 gate 按輪聚合計數。
- **withhold 標記(F3,採 C-stub 時):** `change_events` 加 `withheld INTEGER DEFAULT 0` + per-watch replay 路徑;付費後翻旗補拿。
- **版本戳(F5):** snapshot 連帶存轉換器 + feedparser 版本,版本不符該輪不以內容 diff 觸發 modified。

**新表 `api_keys`:**
- `id` / `key_hash`(只存 hash,不存明文) / `tier`(free|paid) / `rate_limit` / `created_at`。

**後置(不進 MVP):** per-watch `etag` / `last_modified`(conditional GET)。

> store 介面維持不漏 SQLite 細節(現已做到),遷 Postgres 時換實作不動 caller。

---

## RSS adapter(`sources/rss.py`)

`Source` protocol 簽名不變:`async fetch(query) -> list[Row]`。`query = {url, ...}`。

1. **抓取:** 既有 httpx GET feed url(沿用 http_json 的薄網路層精神)。
2. **解析:** bytes → `feedparser.parse`。具名錯誤 `RssFetchError`(連線/解析/形狀失敗一律具名,不靜默吞)。
3. **穩定 id(決策2):** `entry.id`(feedparser 已統一 guid/atom:id)→ `entry.link` → `hash(title+published)`。保證每筆有非空 `id`。
4. **content 轉 Markdown(決策6):** entry content/summary 的 HTML → Markdown,用 **pinned 版本**的 library(候選 markdownify / html2text)。
5. **正規化成固定 schema rows:** `id` / `title` / `link` / `published` / `author` / `summary` / `content`(MD)。缺欄位補空字串,所有值 stringify(對齊 http_json)。

**id fallback 的固有限制(誠實標註):** 落到 `hash(title+published)` 的 feed,標題被編輯 → hash 變 → 那篇被當「滾出 + 新出現」而非 modified。有 guid 的 feed(多數)不受影響。

---

## 計費 gate(機制骨架)

**計量單位 = 計費輪(round),非篇數(elek 定案)。** 一個計費輪 = **一次產生新內容(`added` 事件)的 diff 週期**(scheduler tick / `record_run`)。

- **為什麼是 diff 週期而非 `list_changes` 呼叫:** agent 可任意 batch 呼叫 `list_changes`,呼叫次數不能當計費單位;但「有幾次更新帶來新文」是內容側的客觀事實,綁在 `record_run` 上才穩。
- **什麼算一輪、什麼不算:**
  - 該週期有 ≥1 `added` 事件(新文)→ **計一輪**,`delivered_rounds++`。
  - 只有 `modified`(如舊文換連結、metadata churn)、無 `added` → **不計輪**,且不受 gate 影響(那不是「新內容」,白嫖也沒漏到沒交付過的東西)。
  - 0 事件 → 不計、不交付。
- **前 2 輪免費(`free_rounds=2`):**
  - **輪 1 = 訂閱 baseline。** RSS agent-first 要「訂完馬上抓到第一批內容」,所以 RSS watch 的 baseline **必須當成第一個可交付計費輪**,把當下 feed 全部條目以 `added` 發出(⚠️ 這與通用引擎「baseline 靜默」語意衝突,見下方 gap-verification 標記)。
  - **輪 2 = 第一次帶新內容的更新。** 免費。
  - **輪 3 起 = gated。**

**gate 行為(gate 點 = `list_changes` 交付時,read 路徑帶計量,已接受):**
- 交付某輪的 `added` 事件前檢查 `delivered_rounds < free_rounds`(或 tier=paid 無上限)。
- **額度內:** 正常交付,該輪 `delivered_rounds++`。
- **超額:** **withhold 該輪的 `added` 事件(不從 store 刪除)**,改回結構化升級訊息(placeholder:「此 watch 免費額度用完,請至 X 開通」),讓 agent 轉達背後的人。
- **硬 invariant:** withhold 只延遲交付、**不得永久遺失**;付費後(手動把 tier 改 paid)能補拿。

> ⚠️ **gap-verification 必逼點(baseline 交付 × 不變式 #1):** 現有不變式 #1 寫「首輪 baseline 不產 added」,但 elek 要的「訂完馬上拿第一批」要求 RSS baseline **要**產 added。兩者衝突,需在 gap-verification 裁決:是 (a) 讓 RSS watch policy 覆寫 baseline 行為(baseline 全條目當 added、計為輪 1),還是 (b) 維持 baseline 靜默、另開一條「初始內容交付」路徑。傾向 (a)(較簡單、語意一致:agent 看到的就是「新出現的內容」),但這會改寫不變式 #1 的措辭,不可偷改引擎核心。

---

## Feed discovery

`create_watch` 的 `query.url` 收到的可能是 feed url,也可能是網站首頁 url(agent 只知道「訂 X 的 blog」)。

- 若 url 直接是 feed(Content-Type 或解析成功)→ 直接用。
- 否則抓該 HTML,找 `<link rel="alternate" type="application/rss+xml|atom+xml">` → 取 feed url。
- 找不到 → 具名錯誤回 agent(「該頁無可發現的 feed」),不靜默失敗。

技術輕量:一次 HTML 抓取 + 一個 link 解析。直接服務「agent 自主」目標。

---

## 認證(API key + rate limit)

- 所有對外 MCP 呼叫(create/list/delete)經 auth middleware 驗 API key。
- key 可**自助免費發放**(free tier)+ rate limit,擋裸端點濫用(現況端點未授權,TODOS 已記)。
- 付費 = 把 key tier 調 paid(dogfood 手動)。
- **onboarding(路徑1 人類先發現):** 官網/README 給「複製一段 prompt 啟動」,prompt 內含 free key 取得方式 + create_watch 指引。
- **onboarding(路徑2 agent 先發現):** agent 撞 gate 時拿到結構化升級訊息,轉達使用者去開通。

---

## 本機落地物 + teardown(清理原則)

elek 要求:不在本機留散落的實作伴生物。

- **所有落地物集中在單一 data dir**(如 `~/.waste-for-agents/`):SQLite 檔、任何 cache、(後置)litestream 備份。不寫進 cwd 或散處。
- spec/README 明列**落地物清單** + 提供 `teardown` 指令(刪整個 data dir),遷移時一鍵清乾淨。

---

## 錯誤處理

延續既有「zero silent failures / every error has a name」:
- `RssFetchError`(抓取/解析/形狀)、feed discovery 失敗、auth 失敗(401)、gate withhold(非錯誤,結構化訊息)。
- 錯誤經 `last_error` 對外前 **scrub + 截長**(對齊 twinkle:不洩 secret、不帶無界上游內容)。
- 單一 watch 失敗隔離,不影響其他 watch(既有 scheduler 行為)。

---

## 測試策略 + Invariant 清單(gap-verification 輸入)

**測試:** 沿用既有慣例——純函式解析層 unit(無網路、CI 安全)+ token-free live e2e(gated by 環境變數,真打 HN feed)。新增 RSS 專屬:滾動窗口 diff、id fallback、HTML→MD determinism、gate withhold。

**spec→plan 之間跑 `/gap-verification`,逼以下不變式檢查設計、再下放為 property 測試來源:**

1. **新文必報** — id **不在 watch 累積 seen-set** 的 entry 必產 `added`。baseline 不再是特例:seen-set 初始為空 → baseline 全條目皆「未見過」→ 全部 added、計為免費輪 1(見 gap-verification 結果 F1)。**這條取代原「baseline 靜默」措辭。**
2. **滾出不報、不污染** — 舊文掉出窗口不報 removed,且不影響其他 entry 的 added/modified 判定。
3. **窗口滑動交互(最易出 bug)** — 一邊滑進新文、一邊滑出舊文,要同時「新文必報」+「滾出不報」+「重浮現的舊文不誤判成 added」。
4. **id 跨輪穩定** — 同一篇兩輪算出相同 `id`,否則同時偽 removed(被抑制看不到)+偽 added。
5. **重現不誤報** — baseline 已有、內容沒變 → 0 event。
6. **正規化 determinism** — 同一份 HTML 每次轉出位元級相同的 Markdown(pin 轉換器);否則 library 升級偽報整個 feed modified。
7. **gate 只延遲不遺失** — withhold 的 event 付費後可補拿,絕不永久消失。
8. **輪次計量正確** — 只有「產生 ≥1 `added` 的 diff 週期」遞增 `delivered_rounds`;只產 `modified`(舊文換連結)或 0 事件的週期不遞增、不被 gate。同一週期計恰一次,不因 `list_changes` 被呼叫幾次而重複計。
9. **交付恰一次 + 游標單調**(intent-derived,原 8 條漏列)— 每個 change_event 隨游標前進至多交付一次,游標前進不跳過未交付(且未 withhold)的事件、不重複交付已交付的。withhold 是唯一例外,且被獨立追蹤以供補拿。**這條是 metering + withhold 引入的最硬條件,被全域游標架構威脅(見 F3)。**
10. **跨 session 狀態持久**(intent-derived,原 8 條漏列)— `seen-set`、`delivered_rounds`、withhold 標記、游標語意全部 survive 進程重啟(必須落 SQLite,不可只在記憶體)。產品價值「跨 session 記得讀到哪」直接靠這條。

---

## Gap-Verification 結果(2026-06-26)

對本 spec 跑計畫階段 gap-verification(主入口:設計階段抽不變式 + 查架構守不守得住,**全程無 code**)。已對照實際引擎(`diff.py` / `store.py` / `server.py`)接地,非憑空。摘要:**1 個衝突裁決 + 3 個 Critical 設計漏洞**——都在寫 RSS code 前抓到。

### F1 — baseline × 不變式 #1 衝突:用 seen-set 模型統一(elek 指定裁決)

**問題:** elek 要「訂閱完馬上抓第一批內容」要求 baseline 產 `added`,但原不變式 #1 寫「baseline 靜默」。
**裁決:** 採 spec 原列的選項 (a),但**重構掉特例**——`added` 偵測改對「watch 累積 seen-set」而非「最後一個 snapshot」。如此 baseline 不再是特例:seen-set 空 → 全條目皆未見 → 全 added、計免費輪 1。語意統一、agent 看到的就是「新出現的內容」。不偷改引擎核心:這是 RSS watch policy 的明確行為(見 F2 的 policy 軸)。
**邊界:** baseline feed 為空(0 條)→ 0 added → 依不變式 #8 不計輪。即「空 feed 不吃免費輪」,對 agent 友善,刻意保留。

### F2 — Critical:rolling-window 的 `added` 不能對「最後 snapshot」判(重浮現假 added)

**接地證據:** `store.py` 的 `snapshots` 表是 **PK watch_id 單列** = old 只有最後一個窗口;`diff.py` 的 `added` = key 不在 old。
**漏洞路徑(違反不變式 #3/#5):** baseline {A,B,C} → 輪2 feed {B,C,D}(A 滾出,suppress_removed 吃掉)→ 輪3 feed {C,D,**A**}(A 重浮現)。A 不在「最後 snapshot {B,C,D}」→ **被判 added**。但 A 早在 baseline 交付過。**雙重傷害:**(i)假 added 騙 agent「新文」;(ii)假 added 讓輪3 變成「計費輪」→ **多扣一個免費輪 / 多收一次錢**,計量直接錯。
**根因:** 「diff 對最後 snapshot」對**完整資料集**(twinkle/http_json 每次拉全集)是對的;對**滾動窗口**(RSS 舊文合法消失又可能回來)是錯的。同一個 `diff_rows` 套兩種來源語意 → 範疇錯誤。
**修法(寫進實作計畫):**
- RSS watch 的 `added` 對 **watch 累積 seen-id-set** 判;`modified` 對「該 id 最後已知內容」判;`removed` 抑制。
- 這是新的 **watch policy 軸**,不是單一 bool。`suppress_removed` 不夠——它和「added 對 seen-set 判」「保留滾出 id 的最後內容」三者綁在一起。建議升為 `source_kind: dataset | rolling_window`(dataset = 現行語意;rolling_window = RSS)。
- **儲存衝擊:** 要保留「每個曾見 id 的最後已知狀態」,不只窗口。`snapshots` 單列模型要擴成 per-watch 的 seen-id → last-state 映射(或加 `seen_ids` 表)。**未決:** seen-set 無界成長 → 需 bounding(留最後 K 個 id 或 T 天),極舊重浮現可接受假 added。列 fast-follow。

### F3 — Critical:全域游標 vs per-watch 計費 gate 根本不相容

**接地證據:** `store.py` 的 `events_since(cursor)` 是 **全域** `WHERE id > ? ORDER BY id`,單一全域 event-id 游標,跨所有 watch;`server.py` 的 `list_changes` 一次回所有 watch 的事件。但計費 gate 是 **per-watch**(`delivered_rounds` per watch)。
**漏洞(威脅不變式 #7/#9):** withhold watch A 的輪3 事件、但 watch B 事件 id 更高 → 全域游標若前進去交付 B,就**永久跳過 A 的 withheld 事件**(違反 #7「不遺失」);若為了 A 卡住游標,則 **B 也一起被擋**(多 watch agent 全卡)。單一全域游標**無法**同時做到「per-watch 擋」+「不誤傷其他 watch」+「withheld 不丟」。
**修法(二選一,寫進計畫;這是計畫階段最該裁決的架構決策):**
- **(C-stub) 保留全域游標,gated 事件交付成 upgrade-stub。** 游標照常前進,但 gated watch 的事件 `detail` 換成「免費額度用完,付費後 `replay_watch(id)` 補拿」;原事件留 store,標 `withheld=1`;付費後經**獨立於游標的 per-watch replay 路徑**補回。優點:保住「單次全域拉取」的優雅 + SessionStart `/changes` 鏡像不動;且 F6(modified-only 仍免費)自然成立。代價:agent 要懂「付費後 replay」(stub 內含指引)。
- **(C-perwatch) 游標改 per-watch。** `list_changes(watch_id, cursor)`,gated watch 的串流卡在 gate、其他不受影響。語意最乾淨,但**改 API**(現為單一全域 `since`)+ `/changes` 鏡像要跟著改。
- **傾向 C-stub**:改動小、保全域拉取體驗、且讓 F6 乾淨收斂。但 stub 的「付費後要主動 replay」是新的 agent 互動契約,需在實作確認 agent 接得住。

### F4 — 計量需要 run 歸屬(輪次無法只靠 created_at 切)

**接地證據:** `change_events` 只有 `created_at`(同一 run 內多事件同時間戳),無 run 分組。但「輪」= 一次 `record_run`。
**修法:** `change_events` 加 `run_seq`(per-watch 遞增)或 `run_id`,讓 gate 能「按輪」聚合與計數。靠 created_at 切輪脆弱(同秒多 run / 時鐘跳)。不變式 #8(同輪計恰一次)依賴這個。

### F5 — determinism 破口:轉換器/解析器**版本升級**會靜默偽報整個 feed

**問題:** 不變式 #6 要 HTML→MD 位元級穩定;但 pin 版本**被 bump**(依賴更新)時,全 feed 重新 MD → 內容字串全變 → 每條 `modified` 假爆。`feedparser` 版本變動同理會改 id 衍生 → 假 removed(被抑制)+假 added。
**修法:** snapshot 連同存「轉換器 + feedparser 版本」;版本不符時,該輪**不以內容 diff 觸發 modified**(視為重新 baseline、靜默),避免升級日炸出整片假 modified。pin `markdownify`/`html2text` 與 `feedparser`。列實作必做。

### F6 — 開放項收斂:gate exhausted 後「只 modified 的舊文更新」仍免費

原 spec 留的開放項,在 F3 裁決後收斂:**採 C-stub 則自然成立**——gate 只認 `added` 事件,modified-only 輪不計、不 gate,故 exhausted 後照常免費交付(且無洩漏:那些 id 的內容早在免費輪給過)。**採 C-perwatch 則不成立**(串流卡在 gate、後續 modified 觸不到)。→ 這是傾向 C-stub 的第二個理由。

### 未決清單(禁止靜默留白)

- **seen-set bounding 策略**(F2):無界成長的上限與淘汰法,極舊重浮現的可接受誤差。fast-follow。
- **C-stub vs C-perwatch 最終拍板**(F3):兩者都解,但 agent 互動契約不同。建議 writing-plans 階段定。
- **replay 契約細節**(C-stub):付費後 agent 怎麼觸發 replay、replay 是否也走計量、stub 訊息結構。
- **gate 原子性**(實作層,非設計漏洞但要記):`delivered_rounds` 在 read 路徑遞增,並行 `list_changes` 需 per-watch 序列化(現有 RLock 全域序列化已足夠,但遷 Postgres 後要改 `UPDATE ... WHERE delivered_rounds < free_rounds` 原子判斷)。
- **單一 RLock 吞吐**(`store.py`):大 feed 的 record_run 阻塞 list_changes;MVP 可接受,scale 時記。

### 試用回報(gap-verification skill 本身)

命中判準準:這份計畫同時帶「跨狀態不變式 + 並行(scheduler×read gate)+ 已知衝突」,是該套的典型。產出抓到 2 個 spec 自列 8 條**沒涵蓋**的 intent-derived 不變式(#9 交付恰一次、#10 跨 session 持久),以及 2 個只有對照實際 `store.py` 才看得出的 Critical 架構漏洞(F2 單列 snapshot、F3 全域游標)——即「不變式來自 intent、設計判定要接地可抽查」兩條硬規則的價值。並行未升級到 TLA+:交錯空間小(單一 RLock 全序列化),手推狀態表已足,符合升級條件「命中才做」。

---

## MVP scope 切分

**進 MVP:** RSS adapter(decisions 1–4, 6)、feed discovery、API key 認證 + rate limit、計費 gate 骨架(計量 + withhold + 升級訊息,手動開額度)、單一 data dir + teardown、HN dogfood、上述測試 + gap-verification。

**後置:** conditional GET(fast-follow #1)、真金流(台灣支付,傾向綠界)、Postgres 遷移(先 Neon)、撤稿偵測、webhook。

---

## 已定案的決策(2026-06-26 elek 拍板)

1. **計量單位 = 計費輪(round),非篇數。** 前 2 輪免費(輪 1 = 訂閱 baseline 抓第一批、輪 2 = 第一次帶新內容的更新),第 3 輪起 gated。只產 `modified`(舊文換連結)的週期不計輪。→ 已寫進「計費 gate」段 + 不變式 #8。
2. **金流:暫只接台灣支付,傾向綠界。** elek 無法在台灣為 Stripe 營業、無美國/其他 Stripe 國家帳戶(除非 Stripe Atlas);綠界近期 DX 改善明顯。真金流後置。
3. **上線 db:先 Neon,需擴展再升 Supabase。** Neon 只需純 Postgres、零 infra 起步。
4. **`free_rounds` 預設 = 2。** 計量改為輪次後,原 `free_notifications`(按篇)汰除,改 `free_rounds`(按輪)。
5. **Linear:拆成 Copyists team issues 追蹤**(key 在 `~/.env` 的 `LINEAR_API_KEY_COPYISTS`)。→ 列入下一步。

## 仍開放(非本輪 5 題)

- 需求 n>1 從哪找(elek 以外、今天就想要 agent-first RSS 的人)——dogfood HN 跑通後驗。
- gate 已 exhausted 後,「只產 modified 的舊文更新」要不要仍免費交付?目前傾向**是**(不計輪、不 gate,因內容早在免費輪交付過、無洩漏),但留給 gap-verification 確認無漏洞。
