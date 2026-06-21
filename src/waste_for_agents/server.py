"""MCP server(FastMCP 掛在 FastAPI,streamable-http)。

四個 tool:create_watch / list_changes / list_watches / delete_watch。
工具邏輯抽進 Service(吃 Store、回 JSON-able dict),可不架 HTTP 單測。
FastAPI lifespan 同時:① 跑 mcp.session_manager ② 啟動排程器背景 task
(解「誰來常駐 fetch」——常駐服務自己)。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from typing import Any

from .scheduler import scheduler_loop
from .sources import base
from .sources.twinkle import TwinkleSource
from .store import ChangeEvent, Store, Watch

INSTRUCTIONS = (
    "waste-for-agents:給 AI agent 的結構化監看訂閱層(pull-first)。\n"
    "用 create_watch 訂閱某結構化來源的一個 query;之後在每次醒來時呼叫 "
    "list_changes(since_cursor) 拉出自上次游標以來的變化——沒有變化就秒回空(沉默的號角)。"
)


def _event_dict(e: ChangeEvent) -> dict[str, Any]:
    return {
        "id": e.id,
        "watch_id": e.watch_id,
        "kind": e.kind,
        "row_key": e.row_key,
        "detail": e.detail,
        "created_at": e.created_at,
    }


def _watch_dict(w: Watch) -> dict[str, Any]:
    return {
        "id": w.id,
        "source": w.source,
        "query": w.query,
        "key_columns": w.key_columns,
        "ignore_columns": w.ignore_columns,
        "interval_s": w.interval_s,
        "created_at": w.created_at,
        "last_run_at": w.last_run_at,
        "last_error": w.last_error,
    }


class Service:
    """工具邏輯;與傳輸層解耦,回傳一律 JSON-able dict。"""

    def __init__(self, store: Store) -> None:
        self.store = store

    def create_watch(
        self,
        source: str,
        query: dict[str, Any],
        key_columns: list[str],
        ignore_columns: list[str],
        interval_s: int,
    ) -> dict[str, Any]:
        watch = self.store.create_watch(
            source, query, key_columns, ignore_columns, interval_s
        )
        return {"watch_id": watch.id}

    def list_changes(self, since_cursor: int | None) -> dict[str, Any]:
        events, cursor = self.store.events_since(since_cursor)
        return {"events": [_event_dict(e) for e in events], "cursor": cursor}

    def list_watches(self) -> dict[str, Any]:
        return {"watches": [_watch_dict(w) for w in self.store.list_watches()]}

    def delete_watch(self, watch_id: str) -> dict[str, Any]:
        return {"deleted": self.store.delete_watch(watch_id)}


def build_app(store: Store, tick_s: float = 5.0) -> Any:
    """組 FastAPI app:綁 Service 的四個 MCP tool + lifespan 啟動排程器。"""
    from mcp.server.fastmcp import FastMCP

    service = Service(store)
    # streamable_http_path="/":streamable app 內部路由設為 /,mount 在 /mcp 後
    # 對外端點即 /mcp/(否則預設 /mcp mount 在 /mcp 會疊成 /mcp/mcp)。
    mcp = FastMCP(
        name="waste-for-agents", instructions=INSTRUCTIONS, streamable_http_path="/"
    )

    @mcp.tool()
    def create_watch(
        source: str,
        query: dict[str, Any],
        key_columns: list[str],
        ignore_columns: list[str],
        interval_s: int = 300,
    ) -> dict[str, Any]:
        """訂閱一個結構化來源的 query。回 {watch_id}。(write)"""
        return service.create_watch(source, query, key_columns, ignore_columns, interval_s)

    @mcp.tool()
    def list_changes(since_cursor: int | None = None) -> dict[str, Any]:
        """拉自 since_cursor 以來的變化。回 {events, cursor};無變化回空。(read)"""
        return service.list_changes(since_cursor)

    @mcp.tool()
    def list_watches() -> dict[str, Any]:
        """列出所有監看 + 各自 status(含 last_error)。(read)"""
        return service.list_watches()

    @mcp.tool()
    def delete_watch(watch_id: str) -> dict[str, Any]:
        """刪除一個監看。回 {deleted}。(write)"""
        return service.delete_watch(watch_id)

    from fastapi import FastAPI

    @asynccontextmanager
    async def lifespan(app: Any) -> Any:
        # 把 ASGI lifespan 轉進 MCP session manager,並起排程器背景 task
        async with mcp.session_manager.run():
            task = asyncio.create_task(scheduler_loop(store, tick_s))
            try:
                yield
            finally:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(title="waste-for-agents", lifespan=lifespan)

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "watches": len(store.list_watches())}

    @app.get("/changes")
    def changes(since: int | None = None) -> dict[str, Any]:
        """list_changes 的唯讀 HTTP 鏡像,給 shell 端 SessionStart hook 用(read-free)。"""
        return service.list_changes(since)

    app.mount("/mcp", mcp.streamable_http_app())
    return app


def serve(
    db_path: str = "waste.db",
    host: str = "127.0.0.1",
    port: int = 8848,
    tick_s: float = 5.0,
) -> None:
    """起常駐 HTTP server。註冊 TwinkleSource、建 Store、跑 uvicorn。"""
    import uvicorn

    base.register("twinkle", TwinkleSource())
    store = Store.open(db_path)
    app = build_app(store, tick_s)
    uvicorn.run(app, host=host, port=port)
