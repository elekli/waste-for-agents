"""waste-for-agents — Agent 持久監看訂閱層。

盯結構化來源(MCP tool / API),做真 diff,有變化讓 agent 透過 list_changes 取得。
pull-first:整個服務本身是一個 MCP server,agent 拉變化,不依賴 webhook。
"""

__version__ = "0.1.0"
