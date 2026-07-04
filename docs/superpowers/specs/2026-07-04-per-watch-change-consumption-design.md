# Per-watch 變化消費(設計)

**日期:** 2026-07-04
**狀態:** 設計已拍板(elek approved;經 spec-review 一輪修訂:保留 digest 為顯式模式,不砍統一流)
**定位:** 公開前必修(Show HN / outreach 之前)——修的是「demo 不能是壞的」的核心 primitive,不是變現/擴充後端。與 get_state 那條圍籬同性質。

---

## 問題(已對 code 驗證)

`list_changes` 目前是「每把 key 一條統一事件流 + 單一全域游標」,無法分 watch 獨立消費。

- `list_changes(since_cursor)` → `store.events_since(since_cursor)`(`store.py:389`)= `SELECT * FROM change_events WHERE id > ? ORDER BY id`,回**全部** watch 的事件,才在 service 層過濾成呼叫者擁有的(`server.py:162-164`)。
- 回傳的 cursor 是**全域高水位**(`server.py:158` 註解),不是 per-watch。
- `list_changes` 簽章(`server.py:288`)只有 `since_cursor`,**沒有 `watch_id`**。

**後果(dogfood 實撞,2026-07-04):** 訂兩個來源(HN + 某 YouTube 頻道)。呼叫者傳了 `watch_id` 想做 per-watch 過濾 → FastMCP **靜默吞掉未知參數** → 拿回兩個 watch 混在一起的 244 筆,cursor 跳到全域 max。以為在分來源拉取,其實沒有;而且共享 cursor 推進代表另一個 watch 的事件也被「讀掉」,下次增量拉取會漏。

> 咬人的不是「拿到 digest」,是「我以為在分 watch、其實沒有,而且它不吭聲」。核心病灶是**無聲的混流預設**。

---

## 決策:統一流當底層 + watch_id 過濾 + 沒有無聲 digest 預設

底層維持**一條統一事件流**(全域 `id` 序列、每筆事件帶 `watch_id`);`list_changes` / `/changes` 加 **`watch_id` 過濾**。關鍵:**digest(全 watch 抽乾)保留,但永遠是顯式的,不是省略時的靜默預設。**

### 兩種消費者,能力不同,各得其所

| 消費者 | 能力 | 模式 |
|---|---|---|
| **Agent**(MCP `list_changes`) | 能 `list_watches` 列出自己的 watch | **必帶 `watch_id`**;省略 → 報錯,不靜默給混流 |
| **Shell digest**(SessionStart hook 打 `/changes`) | **無法**列 watch_id(shell、只有 HTTP) | `/changes` **digest 為 documented 預設**;可另接 `?watch=` 做 per-watch |

digest 不是意外的 footgun——它對「無法列 watch 的 HTTP 消費者」是對的形狀(SessionStart hook 天生要「上次以來全部有什麼」一次抽乾)。錯的只是讓它當 **agent 面的靜默預設**。修法 = 移除無聲預設,逼呼叫者講清楚要單一 watch 還是整條 digest。

### 為什麼不是「per-watch 當唯一模型、砍掉 digest」

評估過(前一版 spec)。被 spec-review 抓到:`examples/sessionstart-hook.sh` 是 shipped 的 firehose 消費者(`curl /changes?since=` 單一 cursor、loop 印全部 watch),而它**無法改 per-watch**——shell hook 沒有列 watch_id 的途徑(`list_watches` 只在 MCP)。全砍 digest 會破壞這個真實消費者且無遷移路徑。故 digest 保留,但降為顯式模式。

### 為什麼 MCP 面不給 digest(YAGNI)

agent 能列 watch、要的是分來源消費。「firehose agent」(訂 N 源、不在乎哪個、一次抽乾)理論上存在,但按 YAGNI 現在不做;真在使用中冒出來,再以**顯式**參數(如 `all=true`)加回 MCP,永遠不會是靜默預設。

---

## 設計

### API 變更

**MCP `list_changes(watch_id: str, since_cursor: int | None = None)`** — `watch_id` **必填**。
- 省略 → `{error: "watch_id required", events: [], cursor: <normalized since_cursor>}`(見下方 cursor 正規化)。**不再靜默退回混流。**
- 回傳 `{events, cursor}`;`cursor` = **該 watch 在全域 id 空間的高水位**(本次回傳最後一筆事件的 id;無新事件則等於正規化後的傳入 cursor)。

**HTTP `GET /changes?since=<cursor>[&watch=<watch_id>]`**(`server.py:347`)。
- **`watch` 選填。** 帶 → per-watch(語意同上);省略 → **digest**(全擁有 watch,維持現行行為,服務 SessionStart hook)。
- digest 的 cursor = 全域高水位(現行行為不變)。

### Store 變更

`events_since` 加 optional watch 過濾(新方法或加參數):

```sql
-- watch_id 給定:
SELECT * FROM change_events WHERE watch_id = ? AND id > ? ORDER BY id
-- watch_id 省略(digest):維持現行
SELECT * FROM change_events WHERE id > ? ORDER BY id
```

- **不動 schema、不動 id 空間**:`change_events.id` 維持全域 `INTEGER PRIMARY KEY AUTOINCREMENT`(`store.py:109`)。
- 索引 `idx_change_events_watch`(`store.py:118`)**已存在**,per-watch 查詢直接受益。
- `new_cursor` = 過濾後最後一筆的 id;空集回傳入的 cursor(正規化後)。

### Cursor 正規化(修 bit-identity 洞)

**問題(spec-review 抓到):** 現行 `events_since(None)` 內部 `after = 0`、空集回 cursor **`0`**(`store.py:391,397`);而 ownership-reject / 缺 watch_id 路徑回 `{cursor: since_cursor}` = **`None`**。當 `since_cursor` 省略時,owned-但-空 的 watch 回 `cursor: 0`、非擁有/不存在回 `cursor: null` → **非位元級相同,首次呼叫即洩漏 watch 存在性**。

**要求:** 所有回傳路徑(正常空集、ownership-reject、缺 watch_id error)對 `since_cursor` 採**同一正規化**——`None` 一律正規化為 `0`(或三條路徑都原樣回傳 `since_cursor`,擇一但**必須一致**)。ownership-reject 回應須與「owned-但-空」回應**位元級相同**,含 `cursor` 欄位。

### Ownership(安全)

`watch_id` 給定時須屬於呼叫者。沿用 `replay_watch` / `delete_watch` 既有模式(`server.py:191-194`、`217-219`):
- `get_watch(watch_id)` 取 watch,比對 `watch.api_key_id == caller_key_id`。
- 不存在與非擁有者回**位元級相同**回應(見上方 cursor 正規化),**不洩漏 watch 是否存在**。

### 計費 gate(不變,反而更乾淨)

計費本就 per-watch(`meter_and_mark(wid, evs)`,`server.py:172`)。per-watch 消費後 `by_watch` 只有一個 entry,量到的正好是這個 watch 的量。digest 模式維持現行 per-watch 拆分計量。`unmetered`(self-host)路徑照舊全交付。

---

## Non-Goals(明確不做)

- **MCP 面的 digest / firehose**:YAGNI;若真有需求,日後以顯式非預設參數(`all=true`)回來。
- **Server-tracked per-watch cursor(stateful subscription)**:agent 連 cursor 都不用存的版本。刻意不碰——踩 killer risk #2(MCP 官方放出 stateful subscription → 核心變廢碼),且是 client-supplied → server-tracked 的大架構轉向。留待審慎評估。
- **`update_watch` + interval/cadence 自調**:Phase 1。interval 是「次佳」不是「壞掉」,固定 interval 能用。
- **`get_state`(任意時刻拿當前完整集)**:因誤診降級 Phase 1(rolling baseline 非靜默,現況早已當 `added` 送出)。仍有獨立價值,但非公開前必修。
- **一般性未知參數驗證**:加 `watch_id` 解掉這個 instance;「打錯參數被 FastMCP 無聲丟掉」的一般問題另議,不擋本設計。

---

## 破壞面 & 遷移

**Pre-public,無外部使用者**——破壞可接受。

**會壞(教 MCP `list_changes` 不帶 watch_id 的面):**
- `INSTRUCTIONS` 常數(`server.py:49`):教 agent `list_changes(since_cursor)`,watch_id 必填後這段教了會報錯的呼叫 → 改。
- `README.md`、quickstart(`docs/index.html` + `pt/ja/ko`)中**呈現 MCP `list_changes(since_cursor)` 呼叫**的範例 → 補 watch_id。實作時逐一核對哪些是 MCP-tool 形式(要改)、哪些是 HTTP `/changes?since=` 形式(不改)。
- 測試:`tests/test_metering.py`、`test_invariants_e2e.py`、`test_store.py`、`test_server.py`、`test_auth_service.py` 中呼叫 MCP `list_changes` / `events_since` 的點 → 補 watch_id。

**不會壞(digest 為預設的 HTTP 面):**
- `examples/sessionstart-hook.sh`:`curl /changes?since=`(不帶 watch)→ digest 預設 → **行為不變**。
- quickstart 的 `/changes?since=` HTTP 範例 → digest 預設 → 不改。

---

## 測試(關鍵不變式)

1. **隔離不變式(核心):** 拉 watch A 永不推進/消費 watch B——各自 cursor 獨立;A 拉到 max 後 B 從 0 拉仍拿到全部。
2. **Ownership + cursor bit-identity:** 非擁有者 / 不存在 watch_id → 與「owned-但-空」**位元級相同**的回應。**特別釘 `since_cursor=None` 這個洞**:owned-空 與 non-owned 在省略 cursor 時回傳的 `cursor` 欄位必須相同。
3. **缺 watch_id(MCP):** 明確 error,不退回 digest/混流。
4. **Digest 模式(HTTP `/changes` 省略 `watch`):** 回全擁有 watch、全域 cursor;`?watch=` 帶入則 per-watch。與 SessionStart hook 現行行為相容。
5. **Cursor 高水位:** 連續拉同一 watch,cursor 單調前進;無新事件時 cursor 不變、回空。
6. **計費 gate:** 免費額度用完的 watch 回 gated stub;per-watch 量測正確;`unmetered` 全交付。
