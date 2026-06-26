# RSS 訂閱層 MVP Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 為 waste-for-agents 加上 agent-first 的 RSS 訂閱來源——滾動窗口 diff(seen-set)、per-watch 計費 gate(C-stub withhold/replay)、API key 認證、feed discovery、單一 data dir,並以 dogfood 監看 HN 驗證,全程守住 spec 的 10 條不變式。

**Architecture(建在 C-stub 上):** 保留**單一全域游標**。新增 `source_kind` watch-policy 軸:`dataset`(沿用現行「diff 對最後 snapshot、removed 為真」)/`rolling_window`(RSS:`added` 對累積 seen-set 判、`modified` 對 id 最後已知內容判、`removed` 抑制)。計費 gate 在 `list_changes` 把超額 watch 的計費事件**換成升級 stub**(游標照常前進)、原事件標 `withheld=1` 留 store,付費後經 `replay_watch` 補拿。認證走 API key middleware。

> **⚠ 可逆決策點(F3):** 本計畫採 **C-stub**(spec 傾向)。若改採 **C-perwatch**(游標改 per-watch、串流停在 gate),只需重寫 **Chunk 2** 的 gate/cursor 部分 + `/changes` 鏡像,其餘 chunk(seen-set 引擎、RSS adapter、discovery、auth、data-dir)不動。兩者比較見 spec gap-verification F3。

**Tech Stack:** Python 3.12、FastMCP + FastAPI、SQLite(上線遷 Neon/Postgres)、`feedparser`(pin)、`markdownify`(pin,HTML→MD)、`httpx`、pytest、mypy strict、ruff。

**設計 source of truth:** `docs/superpowers/specs/2026-06-25-agent-first-rss-design.md`(讀過再執行)。

---

## 不變式 → 測試對應(來自 spec gap-verification;每條都要有測試)

| # | 不變式 | 主要驗於 Chunk |
|---|--------|----------------|
| 1 | id 不在 seen-set 必產 added(含 baseline) | Chunk 1 |
| 2 | 滾出不報 removed、不污染他者 | Chunk 1 |
| 3 | 窗口滑動交互(進+出+重浮現同時正確) | Chunk 1 |
| 4 | id 跨輪穩定 | Chunk 3 |
| 5 | 重現不誤報(seen + 內容沒變 → 0 event) | Chunk 1 |
| 6 | 正規化 determinism(同 HTML → 位元級相同 MD) | Chunk 3 |
| 7 | gate 只延遲不遺失(withhold 付費後可補拿) | Chunk 2 |
| 8 | 輪次計量正確(只 added 輪計、計恰一次) | Chunk 2 |
| 9 | 交付恰一次 + 游標單調 | Chunk 2 |
| 10 | 跨 session 狀態持久(落 SQLite) | Chunk 2 |

---

## File Structure

**新增:**
- `src/waste_for_agents/sources/rss.py` — RSS adapter(feedparser、穩定 id、HTML→MD、宣告 `default_source_kind="rolling_window"`)。
- `src/waste_for_agents/normalize.py` — HTML→Markdown,pin 版本 + 版本戳計算(供 F5)。
- `src/waste_for_agents/discovery.py` — feed discovery(HTML `<link rel=alternate>`)。
- `src/waste_for_agents/auth.py` — API key 雜湊、驗證、rate limit、tier。
- `src/waste_for_agents/paths.py` — 單一 data dir 解析 + teardown。
- `tests/test_rolling_diff.py` / `test_metering.py` / `test_rss.py` / `test_discovery.py` / `test_auth.py` / `test_paths.py` / `test_invariants_e2e.py`。

**修改:**
- `src/waste_for_agents/diff.py` — 加 `diff_rolling(seen_state, new_rows, key, ignore, suppress_content_modified)`。
- `src/waste_for_agents/store.py` — schema 加欄位(`source_kind`/`free_rounds`/`delivered_rounds`/`last_metered_run_seq`/`api_key_id`、`change_events.run_seq`/`withheld`、`api_keys` 表、snapshot 版本戳);**snapshot 一律存 list,`get_snapshot` 維持回 `list[Row]`**(不合併、不存 dict);新增 `meter_and_mark`、`withheld_events`、`claim_withheld`。
- `src/waste_for_agents/scheduler.py` — 依 `source_kind` 選 diff 路徑、算 `run_seq`。
- `src/waste_for_agents/server.py` — create_watch 帶 `source_kind`/`api_key`、list_changes 接 gate、新 tool `replay_watch`、auth middleware、`source` 白名單。
- `pyproject.toml` / `uv.lock` — 加 `feedparser`、`markdownify`(pin)。

---

## Phase 0: 準備

### Task 0.1: Linear issues(已存在,本計畫沿用)

- [ ] 本 MVP 的 issue 已建於 Linear team `THE`、Project「RSS MVP — agent-first 訂閱層」:
  - **THE-6**(M1)= Chunk 1 · **THE-7**(M1)= Chunk 2 · **THE-8**(M2)= Chunk 3 · **THE-9**(M2)= Chunk 4 · **THE-10**(M2)= Chunk 5 · **THE-11**(M2)= Chunk 6 · **THE-12**(M3)= Chunk 7。
- [ ] 開始每個 chunk 時,把對應 issue 設為 **In Progress**;chunk 完成設 **In Review**(連 PR)。

### Task 0.2: Git Worktree(已就緒,確認即可)

**REQUIRED SUB-SKILL:** using-git-worktrees

- [ ] **本計畫已在 worktree 內執行**:`~/waste-for-agents-mvp`,branch `feat/rss-subscription`(從 merged `origin/main` 開,已含 Batch 2 的 http_json adapter + spec 兩個 commit)。不另建巢狀 worktree。
- [ ] 確認 baseline 綠後再動工:
  ```bash
  cd ~/waste-for-agents-mvp && uv run pytest -q && uv run mypy src && uv run ruff check
  ```
  Expected:48 passed + 4 skipped、mypy/ruff 乾淨。

---

## Chunk 1: rolling_window seen-set 引擎(THE-6 / F2)

**目標:** 讓引擎認得 `source_kind`。`rolling_window` 的 `added` 對「累積 seen-set」判而非最後窗口,根治重浮現假 added。baseline 不再特例。**全程不碰 RSS——這是純引擎層,用合成 rows 測。**

**設計重點:** rolling_window 的「snapshot」語意 = **累積 seen-set**(每個曾見 key → 最後已知 row),非最後窗口。**合併只在 `diff_rolling`**:它回已合併好的 `new_seen`,scheduler 轉成 list 交 `record_run` 原子持久化——**store 不做合併**(也拿不到 old_seen)。`removed` 不產。`modified` 對 seen-set 內的舊 row 比。

### Task 1.1: `diff_rolling` 純函式

**Files:**
- Modify: `src/waste_for_agents/diff.py`
- Test: `tests/test_rolling_diff.py`(create)

- [ ] **Step 1: 失敗測試**(涵蓋不變式 1/2/3/5)

```python
# tests/test_rolling_diff.py
from waste_for_agents.diff import diff_rolling

KEY = ["id"]

def _rows(*items):  # items: (id, title)
    return [{"id": i, "title": t} for i, t in items]

def test_baseline_all_added():
    # seen 空 → 全 added(不變式 1:baseline 非特例)
    res, seen = diff_rolling({}, _rows(("a", "A"), ("b", "B")), KEY, [], False)
    assert {r["id"] for r in res.added} == {"a", "b"}
    assert res.removed == [] and res.modified == []
    assert set(seen) == {'["a"]', '["b"]'}

def test_rollout_not_reported_not_polluting():
    # baseline {a,b,c} → 窗口 {b,c,d}:d added、a 滾出不報 removed、b/c 不動(不變式 2)
    _, seen = diff_rolling({}, _rows(("a","A"),("b","B"),("c","C")), KEY, [], False)
    res, seen = diff_rolling(seen, _rows(("b","B"),("c","C"),("d","D")), KEY, [], False)
    assert {r["id"] for r in res.added} == {"d"}
    assert res.removed == [] and res.modified == []

def test_reappearance_not_false_added():
    # a 滾出後重浮現、內容沒變 → 0 event(不變式 3/5,F2 的核心 bug)
    _, seen = diff_rolling({}, _rows(("a","A"),("b","B")), KEY, [], False)
    _, seen = diff_rolling(seen, _rows(("b","B"),("c","C")), KEY, [], False)  # a 滾出
    res, seen = diff_rolling(seen, _rows(("c","C"),("a","A")), KEY, [], False)  # a 回來
    assert res.added == [] and res.modified == [] and res.removed == []

def test_three_way_slide_single_round():
    # 不變式 3:同一輪「進 d + 出 b + 重浮現 a」三向同時正確
    _, seen = diff_rolling({}, _rows(("a","A"),("b","B"),("c","C")), KEY, [], False)
    _, seen = diff_rolling(seen, _rows(("c","C"),("d","D")), KEY, [], False)  # a,b 滾出,d 進
    res, _ = diff_rolling(seen, _rows(("c","C"),("d","D"),("a","A")), KEY, [], False)
    assert {r["id"] for r in res.added} == set()          # a 重浮現不假 added、無新 id
    assert res.removed == [] and res.modified == []
    # 再加真新文 e 同輪確認 added 仍會報
    res2, _ = diff_rolling(seen, _rows(("c","C"),("a","A"),("e","E")), KEY, [], False)
    assert {r["id"] for r in res2.added} == {"e"}

def test_reappearance_with_edit_is_modified():
    _, seen = diff_rolling({}, _rows(("a","A")), KEY, [], False)
    res, seen = diff_rolling(seen, _rows(("a","A2")), KEY, [], False)
    assert res.added == [] and len(res.modified) == 1
    assert res.modified[0].changes["title"] == ["A", "A2"]

def test_suppress_content_modified_rebaselines_silently():
    # F5:版本戳不符 → 內容變不報 modified,但 seen-set 仍更新成新內容
    _, seen = diff_rolling({}, _rows(("a","A")), KEY, [], False)
    res, seen = diff_rolling(seen, _rows(("a","A_reMD")), KEY, [], True)
    assert res.modified == []
    assert seen['["a"]']["title"] == "A_reMD"  # 已 re-baseline
```

- [ ] **Step 2: 跑,確認失敗**

Run: `uv run pytest tests/test_rolling_diff.py -q`
Expected:FAIL(`ImportError: cannot import name 'diff_rolling'`)。

- [ ] **Step 3: 實作**

```python
# 加入 src/waste_for_agents/diff.py
def diff_rolling(
    seen_state: dict[str, Row],
    new_rows: list[Row],
    key_columns: list[str],
    ignore_columns: list[str],
    suppress_content_modified: bool,
) -> tuple[DiffResult, dict[str, Row]]:
    """滾動窗口 diff:added 對累積 seen-state 判,不產 removed。

    seen_state: {row_key: 最後已知 row}。回 (DiffResult, 更新後 seen_state)。
    suppress_content_modified=True 時(F5 版本戳不符)不產 modified,但仍把
    新內容併進 seen_state(silently re-baseline),避免轉換器升級偽報整片。
    """
    ignore = set(ignore_columns)
    result = DiffResult()
    new_seen = dict(seen_state)  # copy-on-write,不就地改入參
    for row in new_rows:
        k = row_key(row, key_columns)
        if k not in seen_state:
            result.added.append(row)
        elif not suppress_content_modified:
            changes = _compare(seen_state[k], row, ignore)
            if changes:
                result.modified.append(Modification(key=k, changes=changes))
        new_seen[k] = row  # 一律更新最後已知內容(含 re-baseline 情形)
    return result, new_seen
```

- [ ] **Step 4: 跑,確認通過**

Run: `uv run pytest tests/test_rolling_diff.py -q` → Expected:PASS(5 passed)。

- [ ] **Step 5: Commit**

```bash
git add src/waste_for_agents/diff.py tests/test_rolling_diff.py
git commit -m "feat(diff): rolling_window seen-set diff (F2 重浮現修正)"
```

### Task 1.2: store 支援 source_kind + rolling snapshot 持久化

**Files:**
- Modify: `src/waste_for_agents/store.py`(schema、`Watch` dataclass、`create_watch`、`record_run`)
- Test: `tests/test_store_rolling.py`(create)

- [ ] **Step 1: 失敗測試**

```python
# tests/test_store_rolling.py
from waste_for_agents.store import Store

def test_create_watch_source_kind_default(tmp_path):
    s = Store.open(tmp_path / "w.db")
    w = s.create_watch("twinkle", {"q": 1}, ["id"], [], 300)
    assert w.source_kind == "dataset"  # 不破壞既有

def test_rolling_snapshot_accumulates(tmp_path):
    s = Store.open(tmp_path / "w.db")
    w = s.create_watch("rss", {"url": "x"}, ["id"], [], 3600, source_kind="rolling_window")
    # 第一輪寫 {a,b},snapshot 應含 a,b
    s.record_run(w.id, [{"id": "a"}, {"id": "b"}], [], None, run_seq=1)
    snap = {r["id"] for r in (s.get_snapshot(w.id) or [])}
    assert snap == {"a", "b"}
    # 注意:rolling 的合併由 scheduler 算好 merged snapshot 後傳入(見 Task 1.3),
    # store 只負責原子持久化。這裡驗 store 忠實存下傳入的 rows。
```

- [ ] **Step 2: 跑,確認失敗**(`source_kind` 不存在 / `record_run` 無 `run_seq` 參數)。

- [ ] **Step 3: 實作**
  - `_SCHEMA`:`watches` 加 `source_kind TEXT NOT NULL DEFAULT 'dataset'`、`free_rounds INTEGER NOT NULL DEFAULT 2`、`delivered_rounds INTEGER NOT NULL DEFAULT 0`、`api_key_id TEXT`;`change_events` 加 `run_seq INTEGER NOT NULL DEFAULT 0`、`withheld INTEGER NOT NULL DEFAULT 0`;`snapshots` 加 `norm_version TEXT`。**用 `_migrate()` 對既有 db 做 `ALTER TABLE ... ADD COLUMN`(讀 `PRAGMA table_info` 判斷欄位是否已存在,no-op 安全)。**
  - `Watch` dataclass + `_row_to_watch` 加 `source_kind` / `free_rounds` / `delivered_rounds` / `api_key_id`。
  - `create_watch` 加參數 `source_kind: str = "dataset"`、`api_key_id: str | None = None`,寫入。
  - `record_run` 加參數 **`run_seq: int = 0` 與 `norm_version: str | None = None`(皆有預設)**——dataset 路徑沿用預設,既有 `scheduler.py:41/54` 與 `test_store.py:130` 的舊 positional caller 不破(Task 1.3 才改 scheduler 傳實際值);`run_seq` 寫進每個 event 的 `run_seq` 欄,`norm_version` 寫進 `snapshots.norm_version`(同一交易)。

- [ ] **Step 4: 跑,確認通過。** 另跑全套 `uv run pytest -q` 確認既有測試不破(migration 對舊 db 安全)。

- [ ] **Step 5: Commit**

```bash
git add src/waste_for_agents/store.py tests/test_store_rolling.py
git commit -m "feat(store): source_kind 欄位 + run_seq + rolling snapshot 持久化 + 安全 migration"
```

### Task 1.3: scheduler 依 source_kind 選 diff + 算 run_seq

**Files:**
- Modify: `src/waste_for_agents/scheduler.py`
- Test: `tests/test_scheduler_rolling.py`(create,用 fake source 注入合成 rows)

- [ ] **Step 1: 失敗測試** — 註冊一個回傳可控 rows 的 fake rolling source,跑兩輪,斷言:第一輪全 added(run_seq=1)、第二輪滾出的舊 id 不產 removed、重浮現不假 added;且每輪 events 的 `run_seq` 遞增。**另加一條「無事件仍持久化」case**:第二輪 source 回相同 rows 但模擬內容變 + suppress(seen 變、0 event)→ 斷言 store 的 snapshot 內容**已更新**、`run_seq` **沒**遞增(0 事件不計輪,但內容落地)。

- [ ] **Step 2: 跑,確認失敗。**

- [ ] **Step 3: 實作** — 在 `scheduler` 的 per-watch tick:
  - 讀 `watch.source_kind`。
  - **`rolling_window`**:store 一律存 list,**dict↔list 轉換只在此邊界做**:
    - `snap_list = get_snapshot() or []`;`seen = {row_key(r, key_cols): r for r in snap_list}`(list→dict)。
    - 算當前 `nv = norm_version()`;`suppress = (snapshot 存的 norm_version != nv)`(F5)。
    - `result, new_seen = diff_rolling(seen, new_rows, key_cols, ignore_cols, suppress)`。
    - 回寫 store 的 snapshot 傳 **`list(new_seen.values())`**(dict→list)+ 新 `norm_version=nv`。
  - **`dataset`**:維持現行 `diff_rows`。
  - `run_seq = (watch.last_run_seq or 0) + 1`(watches 加 `last_run_seq` 計數欄,record_run 同交易 +1)。
  - **持久化條件(取代「只有非空 result 才 record_run」,修 reviewer 的 F5 漏洞):**
    - **有事件(result 非空)** → `record_run` 寫 events + snapshot,**進 run_seq**(計費輪候選)。
    - **無事件但 `new_seen != seen` 或 `nv` 變了**(典型:F5 版本戳輪——0 added、modified 全被 suppress)→ **仍 `record_run`(events 空)更新 snapshot 內容 + norm_version,但不進 run_seq、不計輪**。否則新 MD/版本戳永不落地、版本戳卡死、下輪再次全抑制。
    - **真 0 變化**(result 空且 seen 未變、nv 未變) → 不寫。
  - record_run 需能接「events 空但要更新 snapshot」——確認 Task 1.2 的 `record_run` 在 events=[] 時仍正常寫 snapshot(現有實作即如此)。

- [ ] **Step 4: 跑,確認通過 + 全套綠。**

- [ ] **Step 5: Commit**

```bash
git add src/waste_for_agents/scheduler.py src/waste_for_agents/store.py tests/test_scheduler_rolling.py
git commit -m "feat(scheduler): 依 source_kind 走 rolling diff + run_seq 計數"
```

> **Chunk 1 review loop:** dispatch plan-document-reviewer(附本 chunk + spec 路徑)。處理 ❌ 後再進 Chunk 2。

---

## Chunk 2: 計費 gate(C-stub)+ replay(THE-7 / F3/F4)

**目標:** per-watch 計費 gate。計費輪 = 產 ≥1 added 的 run_seq。免費額度內交付、超額換 stub + 標 withheld;`replay_watch` 付費後補拿。**gate 只對「有 free-tier api_key 的 watch」生效**;無 api_key(本地/dogfood 信任)= 不計量,避免回頭計量既有 twinkle watch。

### Task 2.1: api_keys 表 + tier 查詢(store)

**Files:** Modify `store.py`;Test `tests/test_metering.py`(create)。

> **⚠ 計量必須對「游標重放 / 重複呼叫 / 雙 read 入口」idempotent(reviewer Critical):** 游標是 client 給的參數(`events_since` 是 `WHERE id > ?`),agent 用同一 `since_cursor` 重打、或 `/changes` 鏡像(`server.py:144`)也是 read 入口——**不能靠「批內出現」當計量依據**。改用 **per-watch 持久水位 `last_metered_run_seq`**:只對 `run_seq > last_metered_run_seq` 的輪計費並推進水位。如此同一輪不論被讀幾次、由哪個入口讀,都只計一次。**`/changes` 與 MCP `list_changes` 共用同一 `Service.list_changes`(同一水位邏輯)→ 兩入口計量一致、無免費繞過。**

- [ ] **Step 1: 失敗測試** — 建 free key、建掛該 key 的 watch:
  - `free_rounds=2`:第 1、2 個 added 輪 deliver=True;第 3 個 added 輪 deliver=False 且事件被標 `withheld=1`;modified-only 輪永遠 deliver=True 不佔額度。
  - **(idempotency,直驗不變式 8)** 對**同一批** events 連呼叫 `meter_and_mark` 兩次 → 第二次 `delivered_rounds` **不變**、不重複推進水位、回相同決策。
  - **(游標重放)** 模擬 agent 用同一 `since_cursor` 重取同批 → 計量不重複。

- [ ] **Step 2: 跑,確認失敗。**

- [ ] **Step 3: 實作**
  - `api_keys` 表:`id TEXT PK / key_hash TEXT / tier TEXT / rate_limit INTEGER / created_at TEXT`(migration ADD)。
  - `watches` 加 `last_metered_run_seq INTEGER NOT NULL DEFAULT 0`(計量水位,持久;呼應不變式 10)。
  - `Store.meter_and_mark(watch_id, events) -> dict[int, bool]`:**單一交易內**(現有全域 RLock 已序列化):
    1. 把傳入 events 按 `run_seq` 分組。
    2. 若 watch 無 `api_key_id` 或其 tier=`paid` → 全部 deliver,回 `{run_seq: True}`,不動 counter/水位。
    3. free tier:逐 run_seq **升序**,**跳過 `run_seq <= last_metered_run_seq` 的輪**(已計過,維持先前決策:已 withheld 的續 withheld、已 deliver 的續 deliver);對 `run_seq > last_metered_run_seq` 的新輪:有 added 才算「計費輪」,`delivered_rounds < free_rounds` → deliver + `delivered_rounds++`,否則 added 事件 `withheld=1` + deliver=False;只 modified/無 added → deliver=True 不計。處理完把 `last_metered_run_seq = max(本批 run_seq)`。
    4. 回每個 run_seq 的 deliver 決策。
  - `Store.withheld_events(watch_id)` / `claim_withheld(watch_id)`:回 withheld 事件 / 把它們 `withheld=0`(idempotent claim)。

- [ ] **Step 4: 跑,確認通過。**
- [ ] **Step 5: Commit** `feat(store): api_keys 表 + 持久計量水位 meter_and_mark + withheld claim`

### Task 2.2: Service.list_changes 接 gate(C-stub stub 化)

**Files:** Modify `server.py`(`Service.list_changes`、`_event_dict`);Test `tests/test_metering.py`(續)。

- [ ] **Step 1: 失敗測試**(涵蓋不變式 7/8/9)
  - 多 watch 並存:watch A(free,超額)、watch B(無 key,不計量)。一次 `list_changes` 應:B 的事件原樣交付;A 超額輪的事件 `detail` 被換成 `{"gated": true, "watch_id": ..., "message": ...}`;**游標前進到含 stub 在內的最大 event id**(不變式 9:游標單調、不卡 B)。
  - 付費後 `replay_watch(A)` 回 A 的真實 withheld 事件;再呼叫一次回空(不變式 7 補拿一次、不重複)。
  - **(未付費 replay,不變式 7 邊界)** free-tier `replay_watch(A)` 必須**拒絕/回空**,且事後 `withheld_events(A)` **仍非空**(旗標未被清)——防止 free 呼叫流到 `claim_withheld` 把 withheld 翻 0 卻沒交付 → 永久遺失。

- [ ] **Step 2: 跑,確認失敗。**

- [ ] **Step 3: 實作**
  - `Service.list_changes(since_cursor)`:
    1. `events, cursor = store.events_since(since_cursor)`(維持全域游標)。
    2. 按 watch_id 分組,對每個 watch 呼叫 `store.meter_and_mark(watch_id, 該 watch 本批 events)` 得每 run_seq 的 deliver 決策。
    3. 組裝回傳:deliver=True 的事件原樣;deliver=False(gated)的事件 → 換成 stub dict(`gated/watch_id/kind/row_key/upgrade message`),**但游標仍含其 id**(stub 佔游標位置)。
    4. 回 `{events: [...], cursor}`。
  - 新 `Service.replay_watch(watch_id)`:**先**檢查該 watch 的 api_key tier=paid——**非 paid 直接回拒絕,絕不呼叫 `claim_withheld`**(否則旗標被清、事件遺失);paid 才回 `claim_withheld(watch_id)` 的真實事件(idempotent)。
  - **`/changes` HTTP 鏡像(`server.py:144`)維持呼叫同一 `Service.list_changes`** → 與 MCP 入口計量一致(持久水位保證重複讀不重計),無免費繞過。

- [ ] **Step 4: 跑,確認通過 + 全套綠。**
- [ ] **Step 5: Commit** `feat(server): list_changes C-stub 計費 gate + replay_watch 補拿`

### Task 2.3: 暴露 replay_watch MCP tool + create_watch 帶計量欄位

**Files:** Modify `server.py`(`create_watch` tool 加 `source_kind`/`api_key` 透傳、註冊 `replay_watch` tool);Test 續。

- [ ] **Step 1: 失敗測試** — 經 MCP tool 層(`build_app` 的 TestClient)走一次 create→list→replay,斷言 stub/replay 行為端到端成立。
- [ ] **Step 2-4: 實作 + 通過。** `create_watch` tool 加 `source_kind`(預設讀 source 的 `default_source_kind`,見 Chunk 3)、`api_key` 參數;新增 `@mcp.tool() replay_watch`。
- [ ] **Step 5: Commit** `feat(server): replay_watch tool + create_watch 計量參數`

> **Chunk 2 review loop:** plan-document-reviewer。重點查不變式 7/8/9 在 C-stub 下守得住、游標不因單一 watch gated 而卡其他 watch。

---

## Chunk 3: RSS adapter(THE-8 / decisions 2–4, 6 + F5)

**目標:** `sources/rss.py`:feedparser 解析 → 穩定 id → 固定 schema rows;`normalize.py`:HTML→MD(pin)+ 版本戳。宣告 `default_source_kind="rolling_window"`(agent-first:agent 不需知道要傳 rolling)。

### Task 3.0: pin 依賴

- [ ] `uv add feedparser==6.0.11 markdownify==0.13.1`(以實際最新穩定版為準,**pin 精確版**)。Commit `chore(deps): pin feedparser + markdownify`。

### Task 3.1: `normalize.py` HTML→MD + 版本戳(不變式 6)

**Files:** Create `src/waste_for_agents/normalize.py`;Test `tests/test_normalize.py`。

- [ ] **Step 1: 失敗測試** — 同一份 HTML 連跑兩次得**位元級相同** MD(determinism);`norm_version()` 回含 feedparser + markdownify 版本的穩定字串;HTML 連結保留為 Markdown link。
- [ ] **Step 2-4:** 實作 `html_to_markdown(html) -> str`(markdownify 固定參數)、`norm_version() -> str`(`f"md{markdownify.__version__}+fp{feedparser.__version__}"`)。
- [ ] **Step 5: Commit** `feat(normalize): 決定性 HTML→Markdown + 版本戳`

### Task 3.2: `sources/rss.py`(不變式 4)

**Files:** Create `src/waste_for_agents/sources/rss.py`;Test `tests/test_rss.py`(本地 fixture feed,**無網路**)。

- [ ] **Step 1: 失敗測試**(用 `tests/fixtures/sample.xml` 餵 bytes,monkeypatch httpx)
  - 穩定 id:有 guid 用 guid;無 guid 用 link;皆無用 `hash(title+published)`;每筆非空 id。
  - 同一篇兩次解析得相同 id(不變式 4)。
  - content HTML 經 normalize 成 MD;rows 為固定 schema(id/title/link/published/author/summary/content),值皆 str。
  - 解析失敗 → `RssFetchError`(具名,不靜默)。
- [ ] **Step 2-4:** 實作 `RssSource`(`Source` protocol;`async fetch(query)`),`default_source_kind = "rolling_window"`,`ignore_columns` 預設含重生成時間戳(`updated`)。
- [ ] **Step 5: Commit** `feat(sources): RSS adapter(穩定 id + MD content + rolling 預設)`

### Task 3.3: 註冊 rss source + scheduler 串 norm_version(F5)

**Files:** Modify `server.py`(`base.register("rss", RssSource())`)、`scheduler.py`(rolling tick 比對 snapshot 的 `norm_version` 與當前 `norm_version()`,不符 → `diff_rolling(..., suppress_content_modified=True)` 並更新存的版本戳)。

- [ ] **Step 1: 失敗測試** — 模擬版本戳變更,斷言該輪不爆整片 modified、且 seen-set 已 re-baseline 成新內容、版本戳更新。
- [ ] **Step 2-5:** 實作 + 通過 + Commit `feat(scheduler): norm_version 版本戳防升級偽報(F5)`

> **Chunk 3 review loop:** plan-document-reviewer。

---

## Chunk 4: Feed discovery(THE-9)

**目標:** create_watch 的 url 可能是 feed 或網站首頁;自動發現 feed。

### Task 4.1: `discovery.py`

**Files:** Create `src/waste_for_agents/discovery.py`;Test `tests/test_discovery.py`(本地 HTML fixture,無網路)。

- [ ] **Step 1: 失敗測試** — 給含 `<link rel="alternate" type="application/rss+xml" href="...">` 的 HTML → 回該 href;給直接是 feed 的 bytes(feedparser 解析成功)→ 原 url;給無 feed 的 HTML → `FeedDiscoveryError`(具名)。
- [ ] **Step 2-4:** 實作 `async discover_feed(url) -> str`:先試解析為 feed;否則抓 HTML 找 alternate link(相對 href 用 `urljoin` 補絕對)。
- [ ] **Step 5: Commit** `feat(discovery): RSS/Atom feed 自動發現`

### Task 4.2: create_watch 串 discovery(rss source)

**Files:** Modify `server.py`(source=rss 且 query.url 非 feed → 先 discover)。
- [ ] 失敗測試(TestClient)→ 實作 → 通過 → Commit `feat(server): create_watch 對 rss 自動 feed discovery`。

> **Chunk 4 review loop:** plan-document-reviewer。

---

## Chunk 5: API key 認證 + rate limit(THE-10)

**目標:** 補裸端點濫用缺口;free key 自助 + rate limit;付費 = tier 調 paid。

### Task 5.1: `auth.py`(key 雜湊 + 驗證 + rate limit)

**Files:** Create `src/waste_for_agents/auth.py`;Test `tests/test_auth.py`。
- [ ] 失敗測試 — `hash_key` 只存雜湊不可逆;`verify(key)` 對應 api_key row;`check_rate(key_id)` 超限回 False(簡單滑動窗/計數,記憶體或 store)。
- [ ] 實作 → 通過 → Commit `feat(auth): API key 雜湊 + 驗證 + rate limit`。

### Task 5.2: server 掛 auth middleware + issue_key tool

**Files:** Modify `server.py`(MCP tool 前置驗 key;新 `issue_key`(free)tool;create/list/delete/replay 經 auth)。
- [ ] 失敗測試(無 key→401;free key→可 create;rate 超限→拒)→ 實作 → 通過 → Commit `feat(server): auth middleware + 自助 free key 發放`。
- [ ] **replay_watch ownership 檢查(M1 multi-review Critical 2):** auth 上線後,`replay_watch` 除了驗 tier=paid,**必須**驗呼叫者身份 == `watch.api_key_id`,否則知道 watch_id 即可竊取他人 withheld 變化。加失敗測試:別人的 key 呼叫 replay → 拒絕、withheld 不清。(server.py 的 replay_watch 已留 ⚠ 註解。)
- [ ] **更新 README 安全段 / TODOS**:移除「裸端點」缺口標註。

> **Chunk 5 review loop:** plan-document-reviewer(**重點:auth 正確性、key 不外洩、rate limit 邊界**)。

---

## Chunk 6: 單一 data dir + teardown(THE-11)

**目標:** 落地物集中單一 dir;提供 teardown。

### Task 6.1: `paths.py`

**Files:** Create `src/waste_for_agents/paths.py`;Test `tests/test_paths.py`。
- [ ] 失敗測試 — `data_dir()` 預設 `~/.waste-for-agents/`(可 env `WASTE_DATA_DIR` 覆寫);`db_path()` 在其下;`teardown()` 刪整個 dir 且只刪該 dir(安全檢查:拒刪非預期路徑)。
- [ ] 實作 → 通過 → Commit `feat(paths): 單一 data dir 解析 + 安全 teardown`。

### Task 6.2: server/serve 改用 paths + README 落地物清單

**Files:** Modify `server.py`(`serve` 預設 db 走 `paths.db_path()`);README 加「落地物清單 + teardown 指令」。
- [ ] 失敗測試 → 實作 → 通過 → Commit `feat(server): 落地物集中 data dir + 文件化 teardown`。

> **Chunk 6 review loop:** plan-document-reviewer。

---

## Chunk 7: HN dogfood + 不變式 e2e(THE-12)

**目標:** dogfood = 監看 HN;把 10 條不變式落為 property/integration 測試。

### Task 7.1: 不變式 property 測試(合成,CI 安全)

**Files:** Create `tests/test_invariants_e2e.py`。
- [ ] 用合成多輪 feed 序列(進/出/重浮現/編輯/版本戳變)端到端跑 scheduler+store+gate,逐條斷言不變式 1–10。**這是 spec 測試策略的落地,gate 後的最後防線。**
- [ ] 實作 → 通過 → Commit `test(invariants): 10 條不變式端到端 property 測試`。

### Task 7.2: HN live e2e(gated by env)

**Files:** Create `tests/test_hn_live.py`。
- [ ] 真打 `https://news.ycombinator.com/rss`,gated by `WASTE_LIVE_RSS=1`(對齊 http_json 的 `WASTE_LIVE_HTTP` 慣例);斷言能 discover/解析/穩定 id/兩輪 diff 合理(新文 added、重複 0 event)。
- [ ] 實作 → 通過(`WASTE_LIVE_RSS=1 uv run pytest tests/test_hn_live.py`)→ Commit `test(rss): HN live e2e(gated)`。

### Task 7.3: dogfood 跑起來 + 文件

**Files:** Modify README(quickstart 加「監看 HN」範例:issue free key → create_watch rss news.ycombinator.com → list_changes)。
- [ ] 本地起 server,實際建一個 HN watch,跑一輪確認 list_changes 回合理結果(手動驗證,記錄於 PR)。
- [ ] Commit `docs: HN dogfood quickstart`。

> **Chunk 7 review loop:** plan-document-reviewer。

> **需求驗證分支(非 code,但寫進 PR/HANDOFF):** HN dogfood 跑通且爽 → 拿去找「elek 以外、今天就想要 agent-first RSS 的人」驗需求;需求不綠 → STOP 重想(護城河薄,設計文件已認)。

---

## Phase N: 驗證、Code Review、PR

### Task N.1: 完整驗證

- [ ] 測試:`uv run pytest`(全綠;live e2e 另 `WASTE_LIVE_RSS=1 WASTE_LIVE_HTTP=1 uv run pytest -q`)。
- [ ] 型別:`uv run mypy src`(strict 乾淨)。
- [ ] Lint:`uv run ruff check`。

所有測試必須通過、無新增失敗,才能繼續。

### Task N.2: Multi-Model Code Review

- [ ] 跑 canonical multi-review(對齊 ENGINEERING.md,不用 Kabelog 專屬 op 路徑):
  ```bash
  ~/.claude/bin/multi-review.sh --mode code --base origin/main \
    "RSS 訂閱層 MVP:重點查 (1) rolling seen-set diff 正確性(不變式 1-6) (2) C-stub 計費 gate 在多 watch 下游標不誤卡、withhold 不遺失(不變式 7-9) (3) auth/key 不外洩、rate limit 邊界 (4) 跨 session 狀態持久(不變式 10)"
  ```
- [ ] 讀 findings,處理所有 **Critical / Important** 再繼續。

**不可跳過**:本 chunk 全是程式碼變更(僅 docs-only 的 commit 例外)。

### Task N.3: 開 PR 並更新 Issue

- [ ] `git push origin feat/rss-subscription`(standard tier,可自動推 feature branch;**永不直推 main**)。
- [ ] `gh pr create --base main` —— PR body 含:
  - Linear Project「RSS MVP」+ issues **THE-6~12** 連結。
  - 架構說明:source_kind 軸 / C-stub gate / seen-set(引用 spec gap-verification F1-F6)。
  - 測試說明:10 條不變式 → 測試對應表;live e2e gating。
  - 手動驗證:HN dogfood 一輪的實際輸出。
  - 🤖 Generated with [Claude Code](https://claude.com/claude-code)
- [ ] Linear:THE-6~12 狀態更新為 **In Review**。

---

## 未決(承 spec,實作前確認)

- **C-stub vs C-perwatch**(F3):本計畫採 C-stub。改 C-perwatch 只動 Chunk 2 + `/changes`。
- **seen-set bounding**(F2):MVP 先**不設上限**但**明確記錄**(非靜默);feed 規模小、polls 稀疏,風險低。fast-follow 加「留最近 K / T 天」淘汰。
- **conditional GET / 真金流(綠界)/ Postgres 遷移**:全部後置(spec 後置清單)。
