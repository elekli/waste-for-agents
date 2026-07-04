# Per-watch 變化消費(設計)

**日期:** 2026-07-04
**狀態:** 設計已拍板(elek approved,per-watch 當唯一模型)
**定位:** 公開前必修(Show HN / outreach 之前)——修的是「demo 不能是壞的」的核心 primitive,不是變現/擴充後端。與 get_state 之前那條圍籬同性質。

---

## 問題(已對 code 驗證)

`list_changes` 目前是「每把 key 一條統一事件流 + 單一全域游標」,無法分 watch 獨立消費。

- `list_changes(since_cursor)` → `store.events_since(since_cursor)`(`store.py:389`)= `SELECT * FROM change_events WHERE id > ? ORDER BY id`,回**全部** watch 的事件,才在 service 層過濾成呼叫者擁有的(`server.py:162-164`)。
- 回傳的 cursor 是**全域高水位**(`server.py:158` 註解白紙黑字),不是 per-watch。
- `list_changes` 簽章(`server.py:288`)只有 `since_cursor`,**沒有 `watch_id`**。

**後果(dogfood 實撞,2026-07-04):** 訂兩個來源(HN + 某 YouTube 頻道)。呼叫者傳了 `watch_id` 想做 per-watch 過濾 → FastMCP **靜默吞掉未知參數** → 拿回兩個 watch 混在一起的 244 筆,cursor 跳到全域 max。以為在分來源拉取,其實從頭到尾沒有;而且 cursor 推進代表另一個 watch 的事件也被「讀掉」,下次增量拉取會漏。

> 使用者當然會分來源問更新(「HN 有什麼新的?」vs「這個頻道有沒有出片?」)。統一流 + 共享 cursor 讓這件基本事做不到,還是個靜默陷阱。

---

## 決策:per-watch 當唯一消費模型

`list_changes` **必帶 `watch_id`**;統一流移除。

### 為什麼砍掉統一流

評估過保留統一流(當預設或當顯式 opt-in)。唯一站得住的情境是「firehose / digest」agent——訂 N 個來源、不在乎是哪個、每 tick 抽乾全部。但它三點都不夠硬:

1. **是便利不是能力。** per-watch + 一個 loop 就能服務同一需求;沒有統一流 agent 不會少做任何事。
2. **跨來源排序救得回來。** 事件 id 全域自增,per-watch 每筆帶 id,agent 按 id sort 即重建全域時序。統一流「獨有」的東西不獨有。
3. **它就是那個陷阱。** 統一流當預設 = naïve 呼叫 `list_changes()` 拿到混流 + 共享 cursor;只要 agent 有任何分來源邏輯就爆。便利小、footgun 大,且已見血。

再者統一流**大概不是為某情境設計的**,是 MVP 圖「一條 log、一個 cursor」好寫的產物;design 文件只記它「交付腿 + `/changes` 鏡像」,沒有 use case 逼出它。因為好寫而存在 ≠ 因為有人要而存在。

按 YAGNI:現在不做 firehose。真在使用中冒出「就是要一次抽乾全部」的需求,再加回來——而且是**顯式、非預設**的另一個呼叫(如 `list_all_changes`),永遠不會再是不小心掉進去的預設。

---

## 設計

### API 變更

**`list_changes(watch_id: str, since_cursor: int | None = None)`** — `watch_id` 必填。
- 省略 `watch_id` → 明確回 `{error: "watch_id required"}`(**不再靜默退回統一流**)。這一步本身就把 footgun 關掉。
- 回傳不變:`{events, cursor}`。`cursor` 語意改為**該 watch 在全域 id 空間的高水位**(= 本次回傳最後一筆事件的 id;無新事件則等於傳入的 cursor)。

**`GET /changes?watch=<watch_id>&since=<cursor>`**(HTTP 鏡像,`server.py:347`)— `watch` 必填,語意同上。

### Store 變更

`events_since` 加 watch 過濾(新方法或加參數):

```sql
SELECT * FROM change_events WHERE watch_id = ? AND id > ? ORDER BY id
```

- **不動 schema、不動 id 空間**:`change_events.id` 維持全域 `INTEGER PRIMARY KEY AUTOINCREMENT`(`store.py:109`)。
- 索引 `idx_change_events_watch`(`store.py:118`)**已存在**,查詢直接受益。
- `new_cursor` = 過濾後最後一筆的 id;空集則回傳入的 cursor(與現行 `events_since` 行為一致)。

### Ownership(安全)

`watch_id` 須屬於呼叫者。沿用 `replay_watch` / `delete_watch` 既有模式(`server.py:191-194`、`213-219`):
- 用 `get_watch(watch_id)` 取 watch,比對 `watch.api_key_id == caller_key_id`。
- 不存在與非擁有者回**位元級相同**回應(`{events: [], cursor: since_cursor}`),**不洩漏 watch 是否存在**。
- 這順帶讓 service 層原本的「先全撈再過濾 owned」(`server.py:163-164`)收斂成「先驗單一 watch ownership 再撈」——更省、privacy 面更乾淨。

### 計費 gate(不變,反而更乾淨)

計費本來就 per-watch(`meter_and_mark(wid, evs)`,`server.py:172`)。per-watch 消費後,`by_watch` 只有一個 entry,量到的正好是這個 watch 拉的量,不需跨 watch 拆分。`unmetered`(self-host)路徑照舊全交付。

---

## 伴隨處理

- **未知參數靜默吞是一般性陷阱。** 加 `watch_id` 解掉這個 instance。一般性的「打錯參數被無聲丟掉」仍在——至少在 tool description 明列合法參數;要不要上 FastMCP 層嚴格驗證另議,**不擋本設計**。
- **`get_state` 降級 Phase 1。** 它的動機(訂閱看不到現況)是誤診:rolling baseline 非靜默,首輪已把現況當 `added` 送出。get_state 作為「任意時刻拿當前完整集」仍有獨立價值,但不是公開前必修,回 Phase 1 backlog。

---

## Non-Goals(明確不做)

- **統一流 / firehose**:YAGNI;若真有需求,日後以顯式非預設的 `list_all_changes` 回來。
- **Server-tracked per-watch cursor(stateful subscription)**:agent 連 cursor 都不用存的版本。刻意不碰——正踩 killer risk #2(MCP 官方放出 stateful subscription → 核心變廢碼),且是 client-supplied → server-tracked 的大架構轉向。留 Phase 1 之後,審慎評估。
- **`update_watch` + interval/cadence 自調**:Phase 1。interval 是「次佳」不是「壞掉」,固定 interval 能用。
- **`get_state`**:見上,Phase 1。

---

## 破壞面 & 遷移

**Pre-public,無外部使用者**——破壞可接受,只影響:
- **測試:** `tests/test_metering.py`、`test_invariants_e2e.py`、`test_store.py`、`test_server.py`、`test_auth_service.py` 中呼叫 `list_changes` / `events_since` 的點,全部補 `watch_id`。
- **dogfood 消費者:** `~/posthorn-dogfood/telegram_bridge.py`(repo 外)須改為 per-watch 拉取、每 watch 各存一個 cursor。這也正是把它從「一次吃光」修好的機會。

---

## 測試(關鍵不變式)

1. **隔離不變式(核心):** 拉 watch A 永不推進/消費 watch B——各自 cursor 獨立,A 拉到 max 後 B 從 0 拉仍拿到全部。
2. **Ownership:** 非擁有者 / 不存在的 watch_id → 位元級相同的空回應,不洩漏存在性。
3. **Cursor 高水位:** 連續拉同一 watch,cursor 單調前進;無新事件時 cursor 不變、回空。
4. **缺 watch_id:** 明確 error,不退回統一流。
5. **計費 gate:** 免費額度用完的 watch 回 gated stub;per-watch 量測正確;`unmetered` 全交付。
6. **`/changes` 鏡像:** 與 MCP tool 同語意(含缺 `watch` 的 error、ownership、cursor)。
