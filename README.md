# waste-for-agents

**Agent 持久監看訂閱層** — 盯結構化來源(另一個 MCP 的 tool 輸出 / API),做**真 diff**,有變化時讓 AI agent 取得。

對 agent 而言 webhook 是錯的預設(短命 agent 建不起 receiver)。本服務反過來:**它本身就是一個 MCP server**,agent 透過 `list_changes` tool **拉**變化(pull-first)。常駐 agent 用自己的 loop poll;短命 agent 用 SessionStart hook 開場 poll。

> 第一個 source adapter 盯 [Twinkle Hub](https://api.twinkleai.tw)(台灣政府開放資料);介面留薄,之後可換任意結構化來源。

## 狀態

MVP 開發中。實作計畫見 [`docs/superpowers/plans/2026-06-21-custos-mvp.md`](docs/superpowers/plans/2026-06-21-custos-mvp.md)。

## MCP tools(規劃)

| tool | 類型 | 說明 |
|------|------|------|
| `create_watch(source, query, key_columns, ignore_columns, interval_s)` | write | 建立一個監看 |
| `list_changes(since_cursor)` | read | 拉自游標以來的變化(無變化秒回 no-op) |
| `list_watches()` | read | 列出監看 + 各自 status |
| `delete_watch(watch_id)` | write | 刪除監看 |

## 開發

```bash
uv sync            # 建立 venv + 安裝依賴
uv run pytest      # 測試
uv run ruff check  # lint
uv run mypy src    # 型別檢查
```
