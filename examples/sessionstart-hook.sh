#!/usr/bin/env bash
# waste-for-agents — Claude Code SessionStart hook
#
# 開場拉自上次游標以來的結構化變化,注入 session context——pull-first 的「沉默號角」:
# 沒有變化就安靜退出(不污染 session),有變化才把它降臨到 context。
#
# 安裝:在 ~/.claude/settings.json 加(路徑換成本檔絕對路徑):
#   "hooks": {
#     "SessionStart": [
#       { "hooks": [ { "type": "command",
#                      "command": "/ABS/PATH/examples/sessionstart-hook.sh" } ] }
#     ]
#   }
#
# 設定(環境變數):
#   WASTE_URL          預設 http://127.0.0.1:8848(指向常駐的 waste-for-agents)
#   WASTE_CURSOR_FILE  預設 ~/.cache/waste-for-agents/cursor

set -euo pipefail

URL="${WASTE_URL:-http://127.0.0.1:8848}"
CURSOR_FILE="${WASTE_CURSOR_FILE:-$HOME/.cache/waste-for-agents/cursor}"
mkdir -p "$(dirname "$CURSOR_FILE")"
SINCE="$(cat "$CURSOR_FILE" 2>/dev/null || echo 0)"

# 用唯讀 HTTP 鏡像 /changes(免 MCP handshake)。伺服器沒起就安靜退出。
RESP="$(curl -fsS --max-time 5 "$URL/changes?since=$SINCE" 2>/dev/null || true)"
[ -z "$RESP" ] && exit 0

printf '%s' "$RESP" | python3 - "$CURSOR_FILE" <<'PY'
import json, sys

cursor_file = sys.argv[1]
data = json.loads(sys.stdin.read() or "{}")
events = data.get("events", [])
if not events:
    sys.exit(0)  # 沉默的號角

cursor = data.get("cursor")
if cursor is not None:
    with open(cursor_file, "w") as fh:
        fh.write(str(cursor))

print(f"\U0001F4EF waste-for-agents:自上次以來有 {len(events)} 筆結構化變化")
for e in events:
    detail = json.dumps(e.get("detail", {}), ensure_ascii=False)
    print(f"- [{e['kind']}] watch={e['watch_id']} key={e['row_key']} :: {detail}")
PY
