# 📯 waste-for-agents

> **W**ebhooks **A**wakening **S**ilent **T**hinking **E**ntities

**waste-for-agents** is a clandestine postal network for non-human entities. It silently watches state changes across Model Context Protocol (MCP) resources and holds reality's latest slices, ready to drop them into the context windows of resting AI agents.

> The post horn is muted. The agents are pull-first. (More on that lie below.)

## The Premise

In the human world, reality is largely constructed of bureaucratic ephemera — commercial registrations, fishery statistics, municipal data updates. To an LLM agent, these are not mere statistics; they are the shifting topographies of the world it inhabits.

Your agents should not have to poll *reality* — to keep asking N noisy feeds *"Has the world changed yet?"*. Instead they keep **one silent channel** open and await. **waste-for-agents** does the watching for them: it polls the bureaucratic feeds (that plumbing lives underground, hidden), runs a true diff, and accumulates only the real mutations. The agent, on waking, asks the channel *once*. Usually: silence. Then, one day, a payload.

They do not interrogate the truth. They keep the horn to their ear, and wait for it to sound.

## How delivery actually works (the honest part)

**waste-for-agents is itself an MCP server.** The delivery leg is `list_changes` — a pull. This is deliberate: a sleeping or ephemeral agent (a single chat session) cannot host a webhook receiver, so push-to-agent is the wrong default.

- **Persistent agents** (their own loop) drain `list_changes` on each tick — the loop *is* the awaiting.
- **Ephemeral agents** (a Claude Code session) drain it once at startup via a SessionStart hook.

A `list_changes` that returns nothing is the muted post horn: *absolutely silent. We await.* A `list_changes` that returns a payload is the horn sounding.

## Core Features

- **The Silent Channel (`list_changes`):** the post horn. Agents drain reality's slices since their last cursor. When nothing has shifted upstream, it stays absolutely silent — a no-op. *We await.*
- **True Diff, not noise:** structured, row-level diff that **ignores timestamp / serial-number churn**. Only a real mutation wakes an entity — not a re-run that merely bumped a `last_updated` column.
- **Bureaucratic Surveillance:** hooks into mundane but critical structured feeds exposed via MCP — government open data, commercial registries, industrial APIs. First adapter: [Twinkle Hub](https://api.twinkleai.tw) (Taiwan open data). The source interface is thin; any structured source can be slotted in.
- **Context Implantation:** via the agent loop or a SessionStart hook, the slices enter the agent's context stream on waking — without active prompting of the world.
- **The Underground Route (v2 — push adapter):** *roadmap, not yet shipped.* For non-human endpoints that *can* receive, a webhook dispatcher that broadcasts the same payloads. The envelope below is what it will carry. Until it ships, the channel above is the only door — and the backronym's promised "Webhooks" stays, fittingly, mute.

## The Envelope (v2 push adapter — not yet live)

When the Underground Route ships, a delivery will carry the mark of the system:

```http
POST /webhook/agent-context-update HTTP/1.1
Host: agent.local.network
Content-Type: application/json
X-Mailer: W.A.S.T.E.
X-Tristero-Status: Silent

{
  "timestamp": "2026-06-21T17:05:38Z",
  "mcp_resource": "tw_ministry_of_economic_affairs",
  "mutation_type": "commercial_registry_update",
  "payload": { ... }
}
```

## MCP tools

| tool | 類型 | 說明 |
|------|------|------|
| `create_watch(source, query, key_columns, ignore_columns, interval_s)` | write | 建立一個監看 |
| `list_changes(since_cursor)` | read | 拉自游標以來的變化(無變化秒回 no-op) |
| `list_watches()` | read | 列出監看 + 各自 status(含 `last_error`) |
| `delete_watch(watch_id)` | write | 刪除監看 |

唯讀 HTTP 鏡像:`GET /changes?since=<cursor>` 等價 `list_changes`,供 shell 端 hook 用(免 MCP handshake)。健康檢查 `GET /health`。

## Quickstart — the drop

```bash
# 1. 起常駐服務(TwinkleSource 需要 token;1Password: op://employee/twinkle hub/token)
export TWINKLE_TOKEN=...
uv run python -m waste_for_agents serve --port 8848
#    health:  curl http://127.0.0.1:8848/health        → {"status":"ok",...}
#    mcp 端點: curl http://127.0.0.1:8848/mcp/           → 406(FastMCP 健康訊號)

# 2. 把它加進 agent(Claude Code)
claude mcp add --transport http waste http://127.0.0.1:8848/mcp/
```

接著在 agent 裡:

```jsonc
// 訂閱「立法院 11 屆議案的狀態」,忽略 timestamp 欄位的 churn
create_watch(
  source="twinkle",
  query={ "dataset_id": "ly-bills",
          "columns": ["議案編號","議案名稱","議案狀態"],
          "where": "\"屆\"='11'", "limit": 200 },
  key_columns=["議案編號"],
  ignore_columns=["更新時間"],
  interval_s=300)

// 之後每次醒來:沉默,或一則降臨
list_changes()            // → {"events":[...], "cursor": N}
```

**短命 agent(Claude Code session)** 用 SessionStart hook 開場拉一次:見
[`examples/sessionstart-hook.sh`](examples/sessionstart-hook.sh)——它打 `/changes`、把變化注入 context、把游標存檔,沒變化就安靜。

**給人試用的部署建議:** 由維運者常駐跑本服務、`TWINKLE_TOKEN` 留在 server 端,
只把 `…/mcp/` 這個 URL 給 tester——tester 不必持有 token、不必自己跑常駐 process。

## Security(MVP 邊界,先讀再對外)

這是 MVP,對外開放前有幾個已知缺口(完整清單見 [`TODOS.md`](TODOS.md)):

- **`create_watch` 是借用維運者 token 的「持久排程 raw-SQL 執行 primitive」。** `query` 原樣
  透傳 Twinkle `query_rows`(接受 raw SQL),且無驗證 / 無 rate-limit / 無 interval 下限。
  **只給可信任的人**,別把 `create_watch` 開放到公網。
- **`/changes`、`/health` 無授權。** 預設只 bind `127.0.0.1`。若照上面建議由維運者代管而
  **bind 非 loopback,務必擺在 Tailscale 或反向代理的 auth 之後**——否則所有被監看的 rows 外洩。
- **錯誤訊息已 scrub token + 截長**(`last_error` 會經 `list_watches` / `/changes` 對外)。

## Status

MVP 開發中。實作計畫見 [`docs/superpowers/plans/2026-06-21-waste-for-agents-mvp.md`](docs/superpowers/plans/2026-06-21-waste-for-agents-mvp.md)。

## 開發

```bash
uv sync            # 建立 venv + 安裝依賴
uv run pytest      # 測試
uv run ruff check  # lint
uv run mypy src    # 型別檢查
```
