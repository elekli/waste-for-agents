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
from .sources.http_json import HttpJsonSource
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
        "run_seq": e.run_seq,
    }


def _gated_stub(e: ChangeEvent) -> dict[str, Any]:
    """C-stub:gated 事件不交付真實 detail,改回升級提示;原事件留 store(withheld)。"""
    return {
        "id": e.id,
        "watch_id": e.watch_id,
        "kind": e.kind,
        "row_key": e.row_key,
        "run_seq": e.run_seq,
        "gated": True,
        "message": (
            "此 watch 免費額度用完;付費後呼叫 replay_watch(watch_id) 補拿被保留的變化。"
        ),
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
        source_kind: str = "dataset",
        api_key_id: str | None = None,
    ) -> dict[str, Any]:
        watch = self.store.create_watch(
            source,
            query,
            key_columns,
            ignore_columns,
            interval_s,
            source_kind=source_kind,
            api_key_id=api_key_id,
        )
        return {"watch_id": watch.id}

    def list_changes(self, since_cursor: int | None) -> dict[str, Any]:
        """拉自游標以來的變化,套 per-watch 計費 gate(C-stub)。

        gated 輪的事件換成升級 stub,游標仍含其 id 照常前進(不變式 9:不卡其他
        watch)。計量靠持久水位 idempotent → `/changes` 鏡像共用此路徑亦不重計。
        """
        events, cursor = self.store.events_since(since_cursor)
        by_watch: dict[str, list[ChangeEvent]] = {}
        for e in events:
            by_watch.setdefault(e.watch_id, []).append(e)
        decisions: dict[str, dict[int, bool]] = {
            wid: self.store.meter_and_mark(wid, evs) for wid, evs in by_watch.items()
        }
        out = [
            _event_dict(e)
            if decisions.get(e.watch_id, {}).get(e.run_seq, True)
            else _gated_stub(e)
            for e in events
        ]
        return {"events": out, "cursor": cursor}

    def replay_watch(self, watch_id: str) -> dict[str, Any]:
        """付費後補拿 withheld 變化。非 paid 直接拒絕,絕不 claim(否則旗標被清、遺失)。"""
        watch = self.store.get_watch(watch_id)
        if watch is None:
            return {"events": [], "error": "watch not found"}
        tier = (
            self.store.get_api_key_tier(watch.api_key_id) if watch.api_key_id else None
        )
        if tier != "paid":
            return {"events": [], "error": "watch 未付費;replay 需 tier=paid"}
        events = self.store.claim_withheld(watch_id)
        return {"events": [_event_dict(e) for e in events]}

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
        source_kind: str = "dataset",
        api_key_id: str | None = None,
    ) -> dict[str, Any]:
        """訂閱一個結構化來源的 query。回 {watch_id}。(write)

        source_kind:'dataset'(完整資料集)或 'rolling_window'(RSS;added 對
        seen-set、不報 removed)。api_key_id:計費歸戶(free tier 觸發計費 gate)。
        """
        # ⚠ 濫用面(MVP 缺口,開放給可信任 tester 以外前必補,見 TODOS.md / README 安全段):
        #   query 原樣透傳給 TwinkleSource → Twinkle query_rows 接受 raw SQL where/group_by。
        #   create_watch 因此 = 借用維運者 token 的「持久排程 raw-SQL 執行 primitive」。
        #   目前無 query 驗證、無 rate-limit、無 interval 下限、無 source 白名單強制(Chunk 5 補)。
        return service.create_watch(
            source, query, key_columns, ignore_columns, interval_s,
            source_kind=source_kind, api_key_id=api_key_id,
        )

    @mcp.tool()
    def list_changes(since_cursor: int | None = None) -> dict[str, Any]:
        """拉自 since_cursor 以來的變化。回 {events, cursor};無變化回空。(read)

        免費額度用完的 watch,其變化回 gated stub(含升級提示);付費後用
        replay_watch 補拿。
        """
        return service.list_changes(since_cursor)

    @mcp.tool()
    def replay_watch(watch_id: str) -> dict[str, Any]:
        """付費後補拿某 watch 被保留(withheld)的變化。回 {events}(或 error)。(read)"""
        return service.replay_watch(watch_id)

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
    """起常駐 HTTP server。註冊 source adapters、建 Store、跑 uvicorn。"""
    import uvicorn

    base.register("twinkle", TwinkleSource())
    base.register("http_json", HttpJsonSource())
    store = Store.open(db_path)
    app = build_app(store, tick_s)
    uvicorn.run(app, host=host, port=port)
