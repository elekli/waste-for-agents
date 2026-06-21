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
| `list_watches()` | read | 列出監看 + 各自 status |
| `delete_watch(watch_id)` | write | 刪除監看 |

## Status

MVP 開發中。實作計畫見 [`docs/superpowers/plans/2026-06-21-custos-mvp.md`](docs/superpowers/plans/2026-06-21-custos-mvp.md)。

## 開發

```bash
uv sync            # 建立 venv + 安裝依賴
uv run pytest      # 測試
uv run ruff check  # lint
uv run mypy src    # 型別檢查
```
