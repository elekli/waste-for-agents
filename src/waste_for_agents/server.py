"""MCP server(FastMCP 掛在 FastAPI,streamable-http)。

Chunk 5 實作:create_watch / list_changes / list_watches / delete_watch 四個 tool;
FastAPI lifespan 啟動排程器背景 task(解「誰來常駐 fetch」)。
"""
