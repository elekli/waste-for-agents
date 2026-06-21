# custos MVP — Agent 持久監看訂閱層(實作計畫)

**日期:** 2026-06-21
**狀態:** 待執行
**設計文件(source of truth):** `~/.claude/plans/https-blogtrottr-com-expressive-reef.md`
**上層 HANDOFF:** `~/.claude/plans/agent-watch-service-HANDOFF.md`

---

## Context — 為什麼做這個

elek 在 office-hours 收斂出一個窄 wedge:給 AI agent 用的「持久監看訂閱層」,專盯**結構化來源**(另一個 MCP 的 tool 輸出 / API),做**真 diff**,有變化就讓 agent 取得。昨天參加 Claude workshop,要先寫個能動的東西丟過去給人試用,順便驗證需求(Batch 1 = 找到 1 個 elek 以外、今天就想要這個的人)。

**交付腿的關鍵決定(本次對話拍板):** 對 agent 來說 webhook 是錯的預設——短命 agent 建不起 receiver。改用 **pull-first**:整個監看服務**本身就是一個 MCP server**,agent 透過 `list_changes` tool 拉變化。常駐 agent(OpenClaw/Hermes 類)用自己的 loop poll;短命 agent(Claude Code session)用 SessionStart hook 開場 poll。webhook **不進 MVP**,降級為日後的 optional output adapter。

**這個 MVP 要證明兩件事:**
1. **需求**(go/no-go 真正關卡):拿給 workshop 的人,有沒有人今天就想讓 agent 訂閱某結構化源。
2. **技術**:結構化 diff 這條路順、不誤報(尤其 timestamp/流水號欄位)、webhook-free 的 pull 交付體驗成立。

**Non-goals(MVP 不做):** webhook 推送、RSS/網頁 adapter、SSRF 防護的完整實作(MVP 只白名單 Twinkle)、定價/billing、多租戶 auth、server→client push(等 Anthropic Triggers & Events WG)。

---

## 架構

```
                       custos(單一常駐 process,FastAPI + uvicorn)
                ┌──────────────────────────────────────────────────────┐
   agent ──MCP──┤  FastMCP tools (掛在 /mcp/, streamable-http)          │
   (HTTP)       │    create_watch  list_changes  list_watches  delete   │
                │         │                ▲                            │
                │         ▼                │ 讀 change_events(游標)      │
                │   ┌──────────────  SQLite  ──────────────┐            │
                │   │ watches │ snapshots │ change_events   │            │
                │   └─────────────────────────────────────-┘            │
                │         ▲                                             │
                │         │ 寫 events + 更新 snapshot                    │
                │   排程器(asyncio task,FastAPI lifespan 啟動)         │
                │     每個 due watch:                                   │
                │       Source.fetch(query) → diff(忽略欄位) → 有變化才寫 │
                │         │                                             │
                └─────────┼─────────────────────────────────────────────┘
                          │ MCP client (streamablehttp_client + Bearer)
                          ▼
                  Twinkle Hub MCP  (https://api.twinkleai.tw/mcp/, query_rows)
```

**dogfood:** 一個 MCP(custos)poll 另一個 MCP(Twinkle)。給 workshop 的 demo:加 custos MCP → `create_watch` 盯某 Twinkle query → agent 下次 `list_changes` 看到 diff,全程不碰 webhook。

**技術棧(已查證,對齊 polhub):**
- `mcp>=1.2`(`mcp.server.fastmcp.FastMCP` + `mcp.client.streamable_http.streamablehttp_client`)
- `fastapi` + `uvicorn`(掛載 + 常駐)
- `sqlite3`(stdlib,本機儲存)
- `pytest` / `ruff` / `mypy`(uv 管理)
- 範例參考:`~/npp-polhub/dd/serve/{mcp_tools.py,api.py,__main__.py}`、`~/npp-polhub/retrieval/server.py`(lifespan + transport 選型)

---

## Phase 0:準備

### Task 0.1:建立 repo + Python 專案骨架

- [ ] `~/custos` 建專案:`uv init`,`pyproject.toml` 加依賴 `mcp>=1.2`、`fastapi`、`uvicorn`,dev 依賴 `pytest`、`ruff`、`mypy`
- [ ] 套件結構:`src/custos/{__init__.py,store.py,diff.py,sources/__init__.py,sources/base.py,sources/twinkle.py,scheduler.py,server.py}`、`tests/`
- [ ] `git init`,**personal 身份**(`git config user.name elekli` / `user.email elek.li@gmail.com`;remote 用 `github.com-personal` alias,push 前 `git remote -v` 驗證)
- [ ] `.gitignore`(`.venv`、`__pycache__`、`*.db`、`.env`)、`README.md` 占位、`.env.example`(`TWINKLE_TOKEN=`)
- [ ] **secret 不入庫**:`TWINKLE_TOKEN` 走環境變數,程式從 `os.environ` 讀;絕不 hardcode token
- [ ] 初始 commit 到 `main`(**greenfield 首推空 remote 例外**,ENGINEERING.md 明列;之後的變更回到 branch+PR)

### Task 0.2:baseline 綠燈

- [ ] `uv run pytest`(空測試)綠
- [ ] `uv run python -c "import mcp, fastapi; from mcp.server.fastmcp import FastMCP"` 無誤
- [ ] commit

---

## Chunk 1:儲存層(SQLite)

`src/custos/store.py`。TDD。

### Task 1.1:schema + 連線

- [ ] 寫測試:`init_db(path)` 建表後,三張表存在
- [ ] 實作 schema:
  - `watches(id TEXT PK, source TEXT, query_json TEXT, key_columns_json TEXT, ignore_columns_json TEXT, interval_s INT, created_at TEXT, last_run_at TEXT, last_error TEXT)`
  - `snapshots(watch_id TEXT PK, rows_json TEXT, updated_at TEXT)`
  - `change_events(id INTEGER PK AUTOINCREMENT, watch_id TEXT, kind TEXT, row_key TEXT, detail_json TEXT, created_at TEXT)`
- [ ] 跑測試綠 → commit

### Task 1.2:CRUD

- [ ] 測試:`create_watch` / `get_watch` / `list_watches` / `delete_watch` round-trip;`append_event` 後 `events_since(cursor)` 回正確子集 + 新游標(`max(id)`);沒新事件回空 + 同游標(秒回 no-op)
- [ ] 實作之 → 測試綠 → commit

---

## Chunk 2:結構化 diff 引擎(moat,重 TDD)

`src/custos/diff.py`。這是護城河,測試要厚。

### Task 2.1:row-level diff

- [ ] 測試先行,涵蓋:
  - 新增 row → `added`
  - 刪除 row → `removed`
  - 某欄變動 → `modified`(detail 含 changed columns 的 old/new)
  - **忽略欄位誤判**:只有 `ignore_columns`(timestamp/流水號)變 → **無變化**(關鍵反誤報測試)
  - 欄位順序/型別正規化(數字 vs 字串)不造成假 diff
  - 空 → 有、有 → 空 的邊界
- [ ] 實作 `diff_rows(old_rows, new_rows, key_columns, ignore_columns) -> DiffResult(added, removed, modified)`:依 `key_columns` 組 row key,逐 key 比對,比較時排除 `ignore_columns`
- [ ] 測試綠 → commit

---

## Chunk 3:Source 介面 + TwinkleSource

`src/custos/sources/`。介面留薄——elek 明示「行得通一定會抽象」。

### Task 3.1:Source protocol

- [ ] `sources/base.py`:`class Source(Protocol)` 含 `async def fetch(self, query: dict) -> list[dict]`;`registry: dict[str, Source]`(以 `source` 名查 adapter)
- [ ] 測試:`FakeSource` 實作 protocol、可註冊、`fetch` 回固定 rows → commit

### Task 3.2:TwinkleSource(MCP client)

- [ ] `sources/twinkle.py`:用 `mcp.client.streamable_http.streamablehttp_client("https://api.twinkleai.tw/mcp/", headers={"Authorization": f"Bearer {os.environ['TWINKLE_TOKEN']}"})` 開 session,呼叫 `query_rows`(dataset_id / where / limit / columns 由 `query` dict 帶),解析 result 成 `list[dict]`
- [ ] **失敗具名**:連線/auth/工具錯誤拋具體 exception(`TwinkleFetchError`),不靜默吞
- [ ] 整合測試:有 `TWINKLE_TOKEN` 才跑(`pytest.mark.skipif`),對真實 Twinkle 拉一個小 query 驗證回 rows;無 token 則 skip
- [ ] commit

---

## Chunk 4:排程器

`src/custos/scheduler.py`。TDD with `FakeSource`。

### Task 4.1:單輪 poll

- [ ] 測試(用 FakeSource 控制兩次 fetch 的 rows):第一輪建 snapshot 不產 event;第二輪 rows 變 → 產對應 change_events + 更新 snapshot;只動 ignore 欄位 → 不產 event
- [ ] 測試:fetch 拋錯 → `watches.last_error` 被寫入、該 watch 跳過、**不影響其他 watch、不靜默吞**;下輪恢復正常會清掉 last_error
- [ ] 實作 `run_due_watches(store, now)`:挑 `last_run_at + interval_s` 到期的 watch,逐個 fetch → diff → 寫 → 更新 `last_run_at`
- [ ] 測試綠 → commit

### Task 4.2:常駐 loop

- [ ] 實作 `async def scheduler_loop(store, interval=...)`:`while True: run_due_watches(); await asyncio.sleep(tick)`;可被 cancel(乾淨關閉)
- [ ] 測試:loop 跑 N tick 後產出預期 events,cancel 後停 → commit

---

## Chunk 5:MCP server + tools

`src/custos/server.py`。抄 `~/npp-polhub/dd/serve/api.py` 的 FastAPI 掛載 + lifespan 模式。

### Task 5.1:FastMCP tools

- [ ] `mcp = FastMCP(name="custos", instructions=...)`,定義:
  - `create_watch(source, query, key_columns, ignore_columns, interval_s)` → `watch_id`(**write**,日後付費)
  - `list_changes(since_cursor=None)` → `{events, cursor}`(**read**,免費、無變化秒回)
  - `list_watches()` → watches + 各自 `last_error` status
  - `delete_watch(watch_id)`
- [ ] 測試:in-process 呼叫 tool functions round-trip(create → 排程器產 event → list_changes 取到 → cursor 前進)→ commit

### Task 5.2:FastAPI 掛載 + lifespan 起排程器

- [ ] `app.mount("/mcp", mcp.streamable_http_app())`;lifespan 內 `async with mcp.session_manager.run():` 並 `asyncio.create_task(scheduler_loop(...))`,關閉時 cancel
- [ ] `__main__.py`:argparse 支援 `serve`(uvicorn HTTP)為主;預留 stdio 選項
- [ ] 手動驗證:`uv run python -m custos serve` 起得來,`curl -sS -o /dev/null -w "%{http_code}" http://localhost:PORT/mcp/` 回 **406**(FastMCP streamable-http 對普通 GET 的健康訊號)→ commit

---

## Chunk 6:交付體驗 + README

讓 workshop 的人零摩擦試用。

### Task 6.1:短命 agent 的 poll 觸發範例

- [ ] `examples/sessionstart-hook.sh`:Claude Code SessionStart hook 範例,開場呼叫 custos `list_changes` 並把變化注入 context(解掉「agent 怎麼知道要打」對短命 agent 的那一半)
- [ ] commit

### Task 6.2:README + demo walkthrough

- [ ] README 寫:一句話定位、`claude mcp add --transport http <custos-url>/mcp/` 加法、`create_watch` 盯一個 Twinkle query 的具體範例、`list_changes` 取變化、SessionStart hook 用法
- [ ] **hosting 建議(降低 demo 風險):** 建議由 elek 常駐跑 custos(`TWINKLE_TOKEN` 留在 server 端),只給 tester custos 的 MCP URL——tester 不必持有 Twinkle token、不必自己跑常駐 process
- [ ] commit

---

## Phase N:驗證、Review、交付

### Task N.1:完整驗證

- [ ] `uv run pytest`(全綠,含 diff 反誤報測試)
- [ ] `uv run mypy src/`、`uv run ruff check`
- [ ] **end-to-end demo 驗證(真實 Twinkle):** 起 custos → `create_watch` 盯一個會變的 Twinkle query → 等排程器跑一輪 → `list_changes` 確實取到結構化 diff、cursor 前進、無誤報。**這是 Batch 1 技術 spike 的綠燈判準**,結果寫回 HANDOFF

### Task N.2:Code Review

- [ ] 跑 `/code-review`(或 `~/.claude/bin/multi-review.sh --mode code`)對 MVP diff;處理 Critical/Important
- [ ] 重點維度:secret 處理(token 不入庫/不入 log)、失敗具名不靜默吞、diff 反誤報邏輯、SSRF 面雖白名單但註解標明日後缺口

### Task N.3:推上 GitHub + 收尾

- [ ] `gh repo create custos --private`(personal 身份),push `main`
- [ ] 回寫 `~/.claude/plans/agent-watch-service-HANDOFF.md`:需求驗證進度(誰要試?)+ spike 結果(diff 順不順)+ 兩者綠燈程度
- [ ] 把「持續接收更新」研究 thread 寫進設計文件 open questions(見下)

---

## Risks / 待釘住的研究

- **R1 — 「持續接收更新」研究 thread(elek 本次明確要求排研究):** 只要使用者抗拒關掉重開、agent 變準常駐,「持續接收」就從『有沒有 webhook』升級成『agent 的 inbox 模型該長怎樣』:loop poll 的頻率與成本、變化去重與已讀游標、跨 session 記憶接續。**不擋 MVP,但決定 v2 形狀。** 寫進設計文件 open questions,標「投入前要做的研究」。
- **R2 — 競品:OpenClaw/Hermes 已內建 RSS 訂閱。** 待驗證 premise:它們的內建版接不了「結構化源 + 可設定忽略欄位的真 diff」。MVP 拿給這類人問,是 Batch 1 需求驗證的一部分,不是既定事實。
- **R3 — secret 託管:** `TWINKLE_TOKEN` 是真 secret。MVP 走 env;handoff 建議 elek-hosted 讓 token 留 server 端。日後開放任意 MCP 源時,MCP client 憑證託管要正式設計。
- **R4 — SSRF/濫用面:** MVP 只白名單 Twinkle 一個源,先天關著。程式註解標明「開放任意 URL/MCP 源時必補」,別事後發現。
- **R5 — stdio vs HTTP 常駐:** 監看要常駐 fetch,所以 custos 必須是 HTTP 常駐 daemon(scheduler 在 lifespan 裡),不能只當 per-client spawn 的 stdio server。已採 FastAPI+uvicorn 解決。

---

## 驗證(end-to-end,怎麼確認真的動)

1. `export TWINKLE_TOKEN=...`(1Password `op://employee/twinkle hub/token`)
2. `uv run python -m custos serve` → `curl` `/mcp/` 回 406
3. `claude mcp add --transport http custos http://localhost:PORT/mcp/`
4. agent 呼叫 `create_watch(source="twinkle", query={...會變動的 dataset...}, ignore_columns=[時間戳欄位], interval_s=60)`
5. 等一輪 → `list_changes()` 應回結構化 diff;只動 ignore 欄位時應回 no-op
6. 全綠 = 技術 spike 通過,回設計文件細化里程碑 2/3 + 定價 + SSRF;任一不綠 → STOP,把學到的寫回 premises,別 patch-on-patch
