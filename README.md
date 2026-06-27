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

所有 tool(除 `issue_key`)需 `Authorization: Bearer <api_key>`;watch 自動歸戶呼叫者,`list_*` 只回你自己的(privacy)。

| tool | 類型 | 說明 |
|------|------|------|
| `issue_key()` | write(免認證) | 自助發一把 free-tier key;回明文一次(只存 hash) |
| `create_watch(source, query, key_columns, ignore_columns, interval_s)` | write | 建立一個監看(歸戶呼叫者) |
| `list_changes(since_cursor)` | read | 拉自游標以來、你自己 watch 的變化(無變化秒回 no-op) |
| `replay_watch(watch_id)` | read | 付費後補拿被 gate 保留(withheld)的變化 |
| `list_watches()` | read | 列出你自己的監看 + 各自 status(含 `last_error`) |
| `delete_watch(watch_id)` | write | 刪除你自己的監看 |

唯讀 HTTP 鏡像:`GET /changes?since=<cursor>`(`Authorization` 選填,有則 scope)等價 `list_changes`,供 shell 端 hook 用(免 MCP handshake)。健康檢查 `GET /health`。

## Quickstart — the drop

```bash
# 1. 起常駐服務(落地物進單一 data dir ~/.waste-for-agents/;TwinkleSource 才需 token)
uv run python -m waste_for_agents serve --port 8848
#    health:  curl http://127.0.0.1:8848/health        → {"status":"ok",...}
#    mcp 端點: curl http://127.0.0.1:8848/mcp/           → 406(FastMCP 健康訊號)

# 2. 領一把 free key(issue_key 免認證);之後所有呼叫帶 Authorization: Bearer <key>
#    可先不帶 key 連上、呼叫 issue_key 拿 key,再重設帶 header 的連線。

# 3. 把它加進 agent(Claude Code)——header 設一次,所有呼叫自動帶上
claude mcp add --transport http waste http://127.0.0.1:8848/mcp/ \
  --header "Authorization: Bearer wfa_<your-key>"
```

### dogfood:監看 Hacker News

最小可跑範例(RSS source,agent-first:給首頁也會自動 discover feed):

```jsonc
// 訂閱 HN 首頁變化(rolling_window 預設:新文 added、舊文滾出不報 removed)
create_watch(
  source="rss",
  query={ "url": "https://news.ycombinator.com/rss" },
  key_columns=["id"],
  ignore_columns=[])

// 之後每次醒來:沉默,或一批新貼文降臨(content 已轉乾淨 Markdown)
list_changes()            // → {"events":[{"kind":"added","detail":{"row":{...}}}, ...], "cursor": N}
```

實測:HN feed 一輪約 30 篇,穩定 id(guid/link)、`content` 轉 Markdown;同批重抓 0 event(`WASTE_LIVE_RSS=1 uv run pytest tests/test_hn_live.py`)。

### 進階:Twinkle(台灣開放資料)

```bash
export TWINKLE_TOKEN=...   # 1Password: op://employee/twinkle hub/token
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

**認證(THE-10):** create / list / delete / replay 等 MCP tool 需 API key,以
`Authorization: Bearer <key>` 帶上(MCP client 設一次即可;key 不進每次 tool-call 參數
記錄)。`issue_key` 免認證自助發 free-tier key(回明文一次,server 只存雜湊);每把 key
有 rate limit。watch 自動歸戶呼叫者,`list_watches` / `list_changes` **只回呼叫者自己的**
watch(privacy:不洩漏他人訂了什麼);`replay_watch` / `delete_watch` 驗 ownership。

**SSRF(THE-10):** 抓取 agent 提供的 URL(RSS / discovery / http_json)一律經 `netguard`:
scheme allowlist、阻擋內網/loopback/link-local/metadata(169.254.169.254)、**redirect 逐跳
重驗**(防 `公開→內網` 繞道)、**出站 header allowlist**(丟棄 Host/Authorization/Cookie/Proxy-*)。

對外開放前仍有的已知缺口(完整清單見 [`TODOS.md`](TODOS.md)):

- **`create_watch` 的 `query` 仍未驗證**——原樣透傳 Twinkle `query_rows`(接受 raw SQL)。
  auth + rate-limit 已擋匿名濫用,但持 key 者仍能下 raw SQL;結構化 query 驗證 + interval
  下限 + watch 數量上限尚未做。
- **`/health` 無授權**(只回 watch 數);`/changes` Bearer 選填(無 key 只見無歸戶 watch)。
  預設 bind `127.0.0.1`;bind 非 loopback 時 `/health` 仍應擺在反向代理 auth 之後。
- **DNS-rebinding** 未防(check 解析 IP 與實連 IP 可能不同)——記為 fast-follow,MVP 接受。
- **錯誤訊息已 scrub token + 截長**(`last_error` 會經 `list_watches` / `/changes` 對外)。

## 落地物 / 清理

所有狀態集中在**單一 data dir**:
- 預設 `~/.waste-for-agents/`,可由 env `WASTE_DATA_DIR` 覆寫。
- 內含 `waste.db`(SQLite:watches / snapshots / change_events / api_keys)。
- `serve` 省略 `--db` 即落在此(顯式 `--db <path>` 可覆寫)。

清空(刪整個 data dir,只刪該 dir;安全閘:必在 home 或暫存目錄下,且拒刪 home / cwd /
root 與其祖先如 `/Users`、以及 `/usr` `/etc` `/var` 等系統 dir):

```bash
waste-for-agents teardown            # 刪預設 ~/.waste-for-agents/
WASTE_DATA_DIR=/tmp/wfa waste-for-agents teardown   # 刪指定 dir
```

## Status

MVP 開發中。實作計畫見 [`docs/superpowers/plans/2026-06-21-waste-for-agents-mvp.md`](docs/superpowers/plans/2026-06-21-waste-for-agents-mvp.md)。

## 開發

```bash
uv sync            # 建立 venv + 安裝依賴
uv run pytest      # 測試
uv run ruff check  # lint
uv run mypy src    # 型別檢查
```
