# 實作計畫:Per-watch 變化消費

**日期:** 2026-07-05
**Spec:** `docs/superpowers/specs/2026-07-04-per-watch-change-consumption-design.md`(兩輪 spec-review approved)
**Branch:** `feat/per-watch-changes`(worktree `~/waste-for-agents-per-watch`,基底 origin/main `d1d6094`)
**定位:** 公開前必修——修 dogfood 撞出的混流 + 共享 cursor。

## 一句話目標

統一流當底層 + `watch_id` 過濾 + 沒有無聲 digest 預設:MCP `list_changes` 必帶 `watch_id`(省略報錯),HTTP `/changes` digest 為顯式預設(服務 shell hook,不破壞),per-watch cursor 各自獨立,修 cursor=None 的 bit-identity 洩漏洞。

## 驗證指令(本專案)

```bash
uv run pytest -q            # 測試(baseline 177 passed, 7 skipped)
uv run mypy .               # 型別(strict)
uv run ruff check .         # lint
```

## 架構切面(改動點,附現況 file:line)

| 層 | 檔 | 現況 | 改成 |
|---|---|---|---|
| Store | `store.py:389` `events_since(cursor)` | `WHERE id > ? ORDER BY id`,全 watch | 加 optional `watch_id`:給定則 `AND watch_id = ?`(索引 `store.py:118` 已在) |
| Service | `server.py:152` `list_changes(since_cursor, caller)` | 全撈→過濾 owned;cursor 全域高水位 | 加 `watch_id`;None→error dict;ownership 驗單一 watch(bit-identical reject);正規化 helper |
| MCP tool | `server.py:288` `list_changes(since_cursor, *, ctx)` | 無 watch_id | `watch_id: str \| None = None` + 手動檢查(見 spec 實作要點) |
| HTTP | `server.py:347` `changes(request, since)` | digest,無 watch | 加 `watch: str \| None = None`;省略=digest、帶入=per-watch |
| Onboarding | `server.py:49` `INSTRUCTIONS` | 教 `list_changes(since_cursor)` | 教 `list_changes(watch_id, since_cursor)` |
| Docs | `README.md`、`docs/index.html`+`pt/ja/ko` | 混 MCP/HTTP 範例 | MCP 範例補 watch_id;HTTP `/changes?since=` 不動 |

---

## Phase 0: 準備

### Task 0.1: Linear Issue(選用,本 batch 略)

本專案用 Linear team `THE`,但目前處 demand-validation 階段、此為公開前修補。**不強制建 issue**;若 elek 要追蹤再補(`linear-issue` skill)。

### Task 0.2: 建立 Git Worktree（強制）— ✅ 已完成

- [x] worktree `~/waste-for-agents-per-watch`,branch `feat/per-watch-changes`,基底 `d1d6094`
- [x] `uv sync` 完成
- [x] baseline 通過:**177 passed, 7 skipped**

---

## Chunk 1: Store 層 — per-watch 事件查詢

### Task 1.1: 加 per-watch 過濾的失敗測試

- [ ] `tests/test_store.py` 加測試:
  - `events_since(cursor, watch_id="A")` 只回 watch A、`id > cursor` 的事件,順序 by id
  - 回傳 cursor = 過濾後最後一筆 id(per-watch 高水位)
  - 隔離不變式:兩 watch 交錯寫入後,拉 A 到高水位,拉 B(from 0)仍拿到 B 全部
  - 空集(watch 無新事件):回傳傳入的 cursor(正規化後,見 Chunk 2 對齊)
  - `watch_id=None`(digest)維持現行:回全部
- [ ] `uv run pytest tests/test_store.py -q` 確認**失敗**

### Task 1.2: 實作 `events_since` 加 optional `watch_id`

- [ ] `store.py:389`:簽章改 `events_since(self, cursor, watch_id: str | None = None)`
- [ ] `watch_id` 給定 → `SELECT * FROM change_events WHERE watch_id = ? AND id > ? ORDER BY id`;None → 維持現行
- [ ] `new_cursor` 邏輯不變(過濾後最後一筆 id,空集回 `after`)
- [ ] `uv run pytest tests/test_store.py -q` 通過
- [ ] commit:`feat(store): events_since 支援 per-watch 過濾`

---

## Chunk 2: Cursor 正規化 helper

### Task 2.1: 正規化 helper 的失敗測試

- [ ] `tests/test_server.py`(或新 `tests/test_cursor_norm.py`)加測試:
  - 定義預期:`None` → `0`(與 `events_since(None)` 內部 `after=0` 對齊)
  - 四條路徑(正常空集 / ownership-reject / 缺 watch_id / auth-error)回傳的 `cursor` 對同一 `since_cursor` 輸入必**相同**
- [ ] 確認失敗

### Task 2.2: 實作正規化 helper 並接四路徑

- [ ] `server.py` 加 `_norm_cursor(since_cursor: int | None) -> int`(`None → 0`)
- [ ] `list_changes`(service + tool)、`/changes`、auth-error(`server.py:297`)全部回 `_norm_cursor(since_cursor)`
- [ ] 確認測試通過
- [ ] commit:`fix(server): cursor 正規化收斂四路徑(single source of truth)`

---

## Chunk 3: Service.list_changes — watch_id + ownership + bit-identity

### Task 3.1: 失敗測試

- [ ] `tests/test_server.py` / `test_metering.py` 加:
  - **缺 watch_id(None):** 回 `{error: "watch_id required", events: [], cursor: <norm>}`
  - **ownership-reject:** 非擁有者 / 不存在 watch_id → 回應與「owned-但-空」**位元級相同**(含 `cursor`,無 `error` 欄位),不洩漏存在性
  - **特釘 `since_cursor=None` 洞:** owned-空 vs non-owned,省略 cursor 時 `cursor` 欄位相同
  - **per-watch 計費 gate:** 免費額度用完 → gated stub;`unmetered` 全交付
- [ ] 確認失敗

### Task 3.2: 實作 Service.list_changes(watch_id)

- [ ] `server.py:152` 簽章加 `watch_id: str | None`
- [ ] `watch_id is None` → 回 error dict(`_norm_cursor`)
- [ ] ownership:`get_watch(watch_id)`,`watch is None or watch.api_key_id != caller` → 回與 owned-空 位元級相同的 dict(沿用 replay/delete 模式)
- [ ] 通過則 `store.events_since(since_cursor, watch_id=watch_id)`;套現有 per-watch 計費 gate(`meter_and_mark`)
- [ ] 確認測試通過
- [ ] commit:`feat(server): list_changes per-watch + ownership + bit-identity reject`

---

## Chunk 4: MCP tool + HTTP 鏡像 wiring

### Task 4.1: 失敗測試

- [ ] `tests/test_server.py` / `test_auth_service.py` 加:
  - **MCP `list_changes()` 省略 watch_id** → error dict(非硬參數 validation error;驗證形狀含 `cursor`)
  - **MCP `list_changes(watch_id=X)`** → per-watch,認證 + ownership 正確
  - **HTTP `/changes?since=`(無 watch)** → digest(全擁有 watch),與現行相容
  - **HTTP `/changes?since=&watch=X`** → per-watch
- [ ] 確認失敗

### Task 4.2: 實作 tool + mirror

- [ ] MCP tool(`server.py:288`):`watch_id: str | None = None` + 傳給 service(手動檢查在 service 層)
- [ ] HTTP(`server.py:347`):`changes(request, since=None, watch=None)`;`service.list_changes(since, caller, watch_id=watch)` — **watch=None 時仍要走 digest**,故 service 需區分「digest(HTTP 允許)」vs「缺 watch_id(MCP 報錯)」→ 見下方設計註記
- [ ] 確認測試通過
- [ ] commit:`feat(server): list_changes tool 必帶 watch_id、/changes 保留 digest`

> **設計註記(digest vs 缺 watch_id 的區分):** MCP 省略 watch_id = 使用者錯誤(報錯);HTTP 省略 watch = 合法 digest。兩者都呼叫 `service.list_changes`,但語意不同。做法:service 加一個明確參數區分意圖(如 `allow_digest: bool`,HTTP 傳 `True`、MCP 傳 `False`),而非靠 watch_id 是否為 None 猜。`allow_digest=False and watch_id is None` → error;`allow_digest=True and watch_id is None` → digest。**這是本 chunk 要釘死的關鍵語意,實作前先確認測試涵蓋兩條。**

---

## Chunk 5: Onboarding 文案 + 破壞面 docs

### Task 5.1: INSTRUCTIONS + 測試

- [ ] 若有測試斷言 `INSTRUCTIONS` 內容則同步;`server.py:49` 改教 `list_changes(watch_id, since_cursor)`
- [ ] commit:`docs(server): INSTRUCTIONS 教 per-watch list_changes`

### Task 5.2: README + quickstart(逐一核對 MCP vs HTTP 形式)

- [ ] `README.md`:MCP `list_changes` 範例補 watch_id;`/changes?since=` HTTP 不動
- [ ] `docs/index.html` + `pt/ja/ko`:同上逐頁核對(MCP 呼叫補 watch_id;HTTP 範例保留)
- [ ] chrome-devtools 或 grep 核對四頁一致
- [ ] commit:`docs(site): quickstart MCP list_changes 範例補 watch_id`

> **不改(digest 預設保護):** `examples/sessionstart-hook.sh`(`/changes?since=` 無 watch → digest)。實作後跑一次確認 hook 行為不變。

---

## Phase N: 驗證、Code Review、PR

### Task N.1: 完整驗證

- [ ] `uv run pytest -q` — 全過,無新增失敗(baseline 177 passed;新測試計入)
- [ ] `uv run mypy .` — strict 零錯
- [ ] `uv run ruff check .` — 乾淨
- [ ] **手動驗**:起 `uv run waste-for-agents serve --unmetered`(換 port 避開 dogfood 8848),建兩個 watch,per-watch 拉取確認隔離、`/changes` digest 確認不破壞、缺 watch_id 確認報錯

### Task N.2: Multi-Model Code Review

- [ ] 用本專案慣例:`~/.claude/bin/multi-review.sh --mode code --base d1d6094 "per-watch 變化消費:驗 ownership bit-identity 無存在性洩漏、cursor 正規化四路徑一致、digest vs 缺 watch_id 語意區分正確、per-watch 隔離不變式、計費 gate per-watch 正確"`
- [ ] 讀 findings,處理所有 **Critical / Important** 再繼續
- [ ] **不可跳過**:本變更涉及 auth/ownership/privacy 與計費,review 必跑

### Task N.3: 開 PR

- [ ] `git push origin feat/per-watch-changes`(⚠ SSH key 目前不在 agent;push 由 elek 執行或先 `ssh-add`)
- [ ] `gh pr create` — PR body 含:
  - 連結 spec(`docs/superpowers/specs/2026-07-04-...md`)
  - 架構:統一流 + watch_id 過濾 + 無聲 digest 預設移除;MCP 必帶 / HTTP digest 顯式預設
  - 測試:隔離不變式、ownership bit-identity(含 cursor=None 洞)、digest vs 缺 watch_id、計費 gate
  - 手動驗證清單(Task N.1)
- [ ] **repo 外後續(非本 PR):** `~/posthorn-dogfood/telegram_bridge.py` 改 per-watch 拉取、每 watch 各存 cursor(記進 dogfood 的 TODO,不進本 repo)

---

## 風險 & 注意

- **digest vs 缺 watch_id 的語意混淆**(Chunk 4 設計註記)= 本計畫最容易做錯的點。用顯式 `allow_digest` 區分,別靠 watch_id 是否 None 猜。
- **bit-identity**:reject 回應與 owned-空必須連 `cursor` 欄位都相同、且都無 `error` 欄位(否則 `error` 之有無即洩漏存在性)。sub-skill:對照 `replay_watch`/`delete_watch` 現有模式。
- **SSH push**:key 不在 agent(治理:restricted 帳號 key 不進 Keychain,但此 repo 是 personal/standard——仍撞 publickey denied)。push 前 elek `ssh-add ~/.ssh/id_personal` 或由 elek push。**永不直推 main。**
- **mypy strict**:新參數與 helper 都要帶型別;`watch_id: str | None`、`_norm_cursor` 回 `int`。
