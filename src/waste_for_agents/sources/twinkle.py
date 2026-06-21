"""TwinkleSource — 當 MCP client 連 Twinkle Hub,呼叫 query_rows。

Chunk 3 實作:streamablehttp_client("https://api.twinkleai.tw/mcp/",
headers={"Authorization": "Bearer <TWINKLE_TOKEN from env>"}) -> 解析 rows。
失敗具名(TwinkleFetchError),不靜默吞。token 走環境變數,絕不 hardcode。
"""
