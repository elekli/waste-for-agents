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

> **只接此刻存在的兩條路徑。** ownership-reject / 缺 watch_id 這兩條到 Chunk 3 才生出來,四路徑一致的完整斷言在 Chunk 3 才測(plan-review 修正:別在路徑還沒建時就測它)。

### Task 2.1: 正規化 helper 的失敗測試(兩條現存路徑)

- [ ] `tests/test_server.py`(或新 `tests/test_cursor_norm.py`)加測試:
  - 定義預期:`None` → `0`(與 `events_since(None)` 內部 `after=0` 對齊)
  - **現存的兩條路徑**(正常空集 / auth-error `server.py:297`)回傳的 `cursor` 對同一 `since_cursor` 輸入必**相同**
- [ ] 確認失敗

### Task 2.2: 實作正規化 helper 並接兩條現存路徑

- [ ] `server.py` 加 `_norm_cursor(since_cursor: int | None) -> int`(`None → 0`)
- [ ] 正常空集回傳 + auth-error(`server.py:297`)改回 `_norm_cursor(since_cursor)`
- [ ] 確認測試通過
- [ ] commit:`fix(server): 加 cursor 正規化 helper(接現存兩路徑)`

> Chunk 3 生出 ownership-reject / 缺 watch_id 兩條路徑時,一併接上 `_norm_cursor` 並補齊四路徑一致斷言。

---

## Chunk 3: Service per-watch + allow_digest + 兩個 caller(原子綠落地)

> **plan-review 修正:merge 原 Chunk 3+4。** service 行為變更(`watch_id is None + allow_digest=False → error`)一旦落地,若 caller(MCP tool / HTTP `/changes`)沒同 commit 改好,中間狀態會:HTTP digest 壞、MCP 全報錯、`test_invariants_e2e` 7 處紅。故 service 簽章 + 兩個 caller 改寫 + **全部破壞測試遷移**必須同一 commit 綠落地。

### Task 3.1: 失敗測試(service + 兩 caller + 四路徑一致)

- [ ] `tests/test_server.py` / `test_metering.py` / `test_auth_service.py` 加:
  - **MCP 面(`allow_digest=False`)缺 watch_id** → `{error: "watch_id required", events: [], cursor: <_norm_cursor>}`(非硬參數 validation error;形狀含 `cursor`)
  - **MCP `list_changes(watch_id=X)`** → per-watch,認證 + ownership 正確
  - **ownership-reject:** 非擁有者 / 不存在 watch_id → 與「owned-但-空」**位元級相同**(含 `cursor`、皆無 `error` 欄位),不洩漏存在性
  - **特釘 `since_cursor=None` 洞:** owned-空 path 的 `cursor` == `_norm_cursor(since_cursor)`,且 == non-owned reject 的 `cursor`
  - **四路徑一致(補齊 Chunk 2):** 正常空集 / auth-error / ownership-reject / 缺 watch_id,四者 `cursor` 對同一輸入相同
  - **HTTP `/changes?since=`(無 watch,`allow_digest=True`)** → digest(全擁有 watch),與現行相容
  - **HTTP `/changes?since=&watch=X`** → per-watch
  - **per-watch 計費 gate:** 免費額度用完 → gated stub;`unmetered` 全交付
- [ ] 確認失敗

### Task 3.2: 實作 service + 兩個 caller(同一 commit)

- [ ] **Service**(`server.py:152`):簽章 `list_changes(since_cursor, caller_key_id=None, watch_id=None, *, allow_digest=False)`
  - `watch_id is None and not allow_digest` → error dict(`_norm_cursor`)
  - `watch_id is None and allow_digest` → digest(現行行為:全撈 → 過濾 owned)
  - `watch_id` 給定 → ownership(`get_watch`,`watch is None or api_key_id != caller` → 與 owned-空 **位元級相同** reject,沿用 replay/delete);通過則 `store.events_since(since_cursor, watch_id=watch_id)`
  - 全路徑 cursor 走 `_norm_cursor`(接上 Chunk 2 的 helper,補齊四路徑)
  - per-watch 計費 gate(`meter_and_mark`)不變
- [ ] **MCP tool**(`server.py:288`):`watch_id: str | None = None`;呼叫 service **`allow_digest=False`**(手動檢查落在 service)
- [ ] **HTTP**(`server.py:347`):`changes(request, since=None, watch=None)`;呼叫 service `watch_id=watch, allow_digest=True`(watch=None → digest)
- [ ] **遷移所有破壞測試(同 commit):** `tests/test_invariants_e2e.py`(7 處 `list_changes(None, caller_key_id=kid)`,行 172/195/198/212/216/221/228)——digest 型斷言改帶 `allow_digest=True`、per-watch 型改帶 `watch_id`;`test_metering.py` / `test_server.py` / `test_auth_service.py` 同步
- [ ] `uv run pytest -q` 全綠
- [ ] commit:`feat(server): list_changes per-watch(watch_id 必帶)、/changes digest 顯式`

> **allow_digest 是關鍵語意 seam**:MCP 省略 watch_id = 使用者錯誤(報錯);HTTP 省略 watch = 合法 digest。policy 在**呼叫端**(caller 知道自己能力:HTTP=True、MCP=False),別靠 watch_id 是否 None 猜意圖。

---

## Chunk 4: Onboarding 文案 + 破壞面 docs

### Task 4.1: INSTRUCTIONS + 測試

- [ ] 若有測試斷言 `INSTRUCTIONS` 內容則同步;`server.py:49` 改教 `list_changes(watch_id, since_cursor)`
- [ ] commit:`docs(server): INSTRUCTIONS 教 per-watch list_changes`

### Task 4.2: README + quickstart(逐一核對 MCP vs HTTP 形式)

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

- **digest vs 缺 watch_id 的語意混淆**(Chunk 3 的 `allow_digest` seam)= 本計畫最容易做錯的點。policy 在呼叫端(HTTP=True、MCP=False),別靠 watch_id 是否 None 猜。service + 兩 caller + 破壞測試遷移**同一 commit** 落地,否則中間 commit 紅。
- **bit-identity**:reject 回應與 owned-空必須連 `cursor` 欄位都相同、且都無 `error` 欄位(否則 `error` 之有無即洩漏存在性)。sub-skill:對照 `replay_watch`/`delete_watch` 現有模式。
- **SSH push**:key 不在 agent(治理:restricted 帳號 key 不進 Keychain,但此 repo 是 personal/standard——仍撞 publickey denied)。push 前 elek `ssh-add ~/.ssh/id_personal` 或由 elek push。**永不直推 main。**
- **mypy strict**:新參數與 helper 都要帶型別;`watch_id: str | None`、`_norm_cursor` 回 `int`。
