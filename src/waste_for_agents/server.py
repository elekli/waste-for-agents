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

from fastapi import Request
from fastapi.responses import JSONResponse
from mcp.server.fastmcp import Context

from .discovery import FeedDiscoveryError, discover_feed
from .scheduler import scheduler_loop
from .sources import base
from .sources.http_json import HttpJsonSource
from .sources.rss import RssSource
from .sources.twinkle import TwinkleSource
from .store import ChangeEvent, Store, Watch


# MCP tool 注入用的 Context(三泛型補滿以過 mypy strict;FastMCP 仍以 origin 偵測注入)。
_Ctx = Context[Any, Any, Any]


def register_default_sources() -> None:
    """註冊內建 source adapters(twinkle / http_json / rss)。"""
    base.register("twinkle", TwinkleSource())
    base.register("http_json", HttpJsonSource())
    base.register("rss", RssSource())


def _source_default_kind(source: str) -> str:
    """取 source 宣告的 default_source_kind(未註冊或未宣告 → 'dataset')。"""
    try:
        return str(getattr(base.get_source(source), "default_source_kind", "dataset"))
    except base.UnknownSourceError:
        return "dataset"

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
    """工具邏輯;與傳輸層解耦,回傳一律 JSON-able dict。

    auth scope:帶 caller_key_id 的方法只作用於該呼叫者歸戶的 watch
    (caller_key_id=None = 匿名/本地,只見無歸戶 watch),堵 identity↔watch 洩漏。

    unmetered:self-host(模型 L)用——關掉計費 gate,list_changes 全交付、不 stub。
    計費 gate 是 SaaS 模型的產物;跑自己機器的人不該被自己的免費額度擋住長跑 workflow。
    """

    def __init__(self, store: Store, unmetered: bool = False) -> None:
        self.store = store
        self.unmetered = unmetered

    def issue_key(self) -> dict[str, Any]:
        """自助發放一把 free-tier API key。回明文 key(只此一次)+ id;store 只存 hash。"""
        from .auth import DEFAULT_FREE_RATE_LIMIT, generate_key, hash_key

        key = generate_key()
        kid = self.store.create_api_key(
            hash_key(key), tier="free", rate_limit=DEFAULT_FREE_RATE_LIMIT
        )
        return {"api_key": key, "api_key_id": kid}

    def create_watch(
        self,
        source: str,
        query: dict[str, Any],
        key_columns: list[str],
        ignore_columns: list[str],
        interval_s: int,
        source_kind: str | None = None,
        api_key_id: str | None = None,
    ) -> dict[str, Any]:
        # rss:把首頁/feed url 解析成確定的 feed url(agent 只知「訂 X 的 blog」)。
        if source == "rss":
            url = query.get("url")
            if not isinstance(url, str) or not url:
                raise FeedDiscoveryError("rss watch 的 query.url 必填")
            query = {**query, "url": discover_feed(url, query.get("headers"))}
        # source_kind 未指定 → 取 source 宣告的 default(rss=rolling_window;agent 免傳)。
        if source_kind is None:
            source_kind = _source_default_kind(source)
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

    def _owned_watch_ids(self, caller_key_id: str | None) -> set[str]:
        """呼叫者歸戶的 watch id 集合(None caller = 無歸戶 watch)。store 層 WHERE 過濾。"""
        return self.store.list_watch_ids_by_api_key(caller_key_id)

    def list_changes(
        self, since_cursor: int | None, caller_key_id: str | None = None
    ) -> dict[str, Any]:
        """拉自游標以來的變化(只回呼叫者歸戶的 watch),套 per-watch 計費 gate(C-stub)。

        privacy scope:事件先過濾成「呼叫者擁有的 watch」——不洩漏他人訂了什麼。
        游標仍推進到「全域」高水位(events_since 給的 cursor),呼叫者下次不重掃他人
        事件;他人事件不誤卡呼叫者。gated 輪事件換升級 stub。計量只動呼叫者自己的
        watch(持久水位 idempotent → `/changes` 鏡像共用此路徑不重計)。
        """
        events, cursor = self.store.events_since(since_cursor)
        owned = self._owned_watch_ids(caller_key_id)
        events = [e for e in events if e.watch_id in owned]
        if self.unmetered:
            # self-host:跳過計費 gate,全交付(游標照常推進)。
            return {"events": [_event_dict(e) for e in events], "cursor": cursor}
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

    def replay_watch(
        self, watch_id: str, caller_key_id: str | None = None
    ) -> dict[str, Any]:
        """付費後補拿 withheld 變化。先驗 ownership 再驗 tier=paid。

        ownership(M1 multi-review Critical 2):呼叫者身份須 == watch.api_key_id,否則
        知道 watch_id 即可竊取他人 withheld 變化。非擁有者**在觸及 claim 前**即拒,旗標
        不被清(否則別人一呼叫就把 withheld 翻 0、事件永久遺失)。非 paid 同理只拒不 claim。
        """
        watch = self.store.get_watch(watch_id)
        # 不存在與非擁有者回「位元級相同」回應(不洩漏 watch 是否存在;review Critical)
        if watch is None or watch.api_key_id != caller_key_id:
            return {"events": [], "error": "not found or unauthorized"}
        tier = (
            self.store.get_api_key_tier(watch.api_key_id) if watch.api_key_id else None
        )
        if tier != "paid":
            # 已確認呼叫者就是擁有者 → 可安全給「需付費」提示
            return {"events": [], "error": "watch 未付費;replay 需 tier=paid"}
        events = self.store.claim_withheld(watch_id)
        return {"events": [_event_dict(e) for e in events]}

    def list_watches(self, caller_key_id: str | None = None) -> dict[str, Any]:
        """只列呼叫者歸戶的 watch(privacy:不洩漏他人訂閱)。store 層 WHERE 過濾。"""
        return {
            "watches": [
                _watch_dict(w)
                for w in self.store.list_watches_by_api_key(caller_key_id)
            ]
        }

    def delete_watch(
        self, watch_id: str, caller_key_id: str | None = None
    ) -> dict[str, Any]:
        """刪 watch;須為呼叫者歸戶。不存在與非擁有者回相同 {deleted: False}(不洩漏存在性)。"""
        watch = self.store.get_watch(watch_id)
        if watch is None or watch.api_key_id != caller_key_id:
            return {"deleted": False}
        return {"deleted": self.store.delete_watch(watch_id)}


def build_app(store: Store, tick_s: float = 5.0, unmetered: bool = False) -> Any:
    """組 FastAPI app:綁 Service 的 MCP tool + lifespan 啟動排程器。

    auth:MCP tool 從 Bearer header(Context 的底層 HTTP request)取 key、驗證 + rate
    limit;失敗回 {error}。issue_key 不需 auth(自助發放)。/changes 鏡像 header 選填
    (有則 scope 到該 key、無則只見無歸戶 watch)。

    unmetered:self-host 關閉計費 gate(見 Service)。
    """
    from mcp.server.fastmcp import FastMCP

    from .auth import AuthError, RateLimiter, authenticate, bearer_key

    service = Service(store, unmetered=unmetered)
    rate_limiter = RateLimiter()

    def _caller_from_ctx(ctx: _Ctx) -> str:
        """從 MCP 請求的底層 HTTP request 取 Bearer key、驗證,回 api_key_id;失敗拋 AuthError。"""
        req = getattr(ctx.request_context, "request", None)
        headers = getattr(req, "headers", {}) if req is not None else {}
        return authenticate(store, rate_limiter, bearer_key(headers)).id
    # streamable_http_path="/":streamable app 內部路由設為 /,mount 在 /mcp 後
    # 對外端點即 /mcp/(否則預設 /mcp mount 在 /mcp 會疊成 /mcp/mcp)。
    mcp = FastMCP(
        name="waste-for-agents", instructions=INSTRUCTIONS, streamable_http_path="/"
    )

    @mcp.tool()
    def issue_key() -> dict[str, Any]:
        """自助發放一把 free-tier API key。回 {api_key, api_key_id}。(write,免認證)

        api_key 只回這一次——存好它,之後所有呼叫用 `Authorization: Bearer <api_key>`
        帶上。server 只存雜湊,遺失無法找回。
        """
        return service.issue_key()

    @mcp.tool()
    def create_watch(
        source: str,
        query: dict[str, Any],
        key_columns: list[str],
        ignore_columns: list[str],
        interval_s: int = 300,
        source_kind: str | None = None,
        *,
        ctx: _Ctx,
    ) -> dict[str, Any]:
        """訂閱一個結構化來源的 query。回 {watch_id}。需 Bearer key。(write)

        watch 自動歸戶到呼叫者的 api_key(計費 + privacy:只有你看得到自己的 watch)。
        source_kind:省略則取 source 預設(rss=rolling_window、其餘 dataset);亦可顯式
        傳 'dataset' / 'rolling_window'。source='rss' 時 query.url 可給首頁,自動 discover feed。
        """
        # ⚠ 濫用面殘留(Task 5.3 後仍部分):query 原樣透傳給 TwinkleSource → raw SQL。
        #   auth + rate-limit 已擋匿名濫用;query 內容驗證仍後置(見 TODOS.md)。
        try:
            caller = _caller_from_ctx(ctx)
        except AuthError:
            return {"error": "unauthorized"}
        return service.create_watch(
            source, query, key_columns, ignore_columns, interval_s,
            source_kind=source_kind, api_key_id=caller,
        )

    @mcp.tool()
    def list_changes(since_cursor: int | None = None, *, ctx: _Ctx) -> dict[str, Any]:
        """拉自 since_cursor 以來、你自己 watch 的變化。回 {events, cursor}。需 Bearer key。(read)

        只回呼叫者歸戶的 watch(privacy)。免費額度用完的 watch 回 gated stub;
        付費後用 replay_watch 補拿。
        """
        try:
            caller = _caller_from_ctx(ctx)
        except AuthError:
            return {"error": "unauthorized", "events": [], "cursor": since_cursor}
        return service.list_changes(since_cursor, caller_key_id=caller)

    @mcp.tool()
    def replay_watch(watch_id: str, *, ctx: _Ctx) -> dict[str, Any]:
        """付費後補拿你自己某 watch 被保留(withheld)的變化。回 {events}(或 error)。需 Bearer key。(read)"""
        try:
            caller = _caller_from_ctx(ctx)
        except AuthError:
            return {"error": "unauthorized", "events": []}
        return service.replay_watch(watch_id, caller_key_id=caller)

    @mcp.tool()
    def list_watches(*, ctx: _Ctx) -> dict[str, Any]:
        """列出你自己的監看 + 各自 status(含 last_error)。需 Bearer key。(read)"""
        try:
            caller = _caller_from_ctx(ctx)
        except AuthError:
            return {"error": "unauthorized", "watches": []}
        return service.list_watches(caller_key_id=caller)

    @mcp.tool()
    def delete_watch(watch_id: str, *, ctx: _Ctx) -> dict[str, Any]:
        """刪除你自己的一個監看。回 {deleted}。需 Bearer key。(write)"""
        try:
            caller = _caller_from_ctx(ctx)
        except AuthError:
            return {"error": "unauthorized", "deleted": False}
        return service.delete_watch(watch_id, caller_key_id=caller)

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
    def changes(request: Request, since: int | None = None) -> Any:
        """list_changes 的唯讀 HTTP 鏡像,給 shell 端 SessionStart hook 用。

        Authorization: Bearer <key> 選填——有則 scope 到該 key 的 watch(計量共用持久
        水位、不重計);無則只見無歸戶(本地/dogfood)watch。給了但無效 → 401。
        """
        key = bearer_key(request.headers)
        caller: str | None = None
        if key is not None:
            try:
                caller = authenticate(store, rate_limiter, key).id
            except AuthError:
                return JSONResponse({"error": "unauthorized"}, status_code=401)
        return service.list_changes(since, caller_key_id=caller)

    app.mount("/mcp", mcp.streamable_http_app())
    return app


def serve(
    db_path: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8848,
    tick_s: float = 5.0,
    unmetered: bool = False,
) -> None:
    """起常駐 HTTP server。註冊 source adapters、建 Store、跑 uvicorn。

    db_path=None(預設)→ 落地物進單一 data dir(`paths.db_path()`,見 paths.py);
    顯式給路徑則用之(測試 / 自訂位置)。
    unmetered=True → 關閉計費 gate(self-host / workflow 用)。
    """
    import uvicorn

    from .paths import db_path as default_db_path
    from .paths import ensure_data_dir

    if db_path is None:
        ensure_data_dir()
        db_path = str(default_db_path())
    register_default_sources()
    store = Store.open(db_path)
    app = build_app(store, tick_s, unmetered=unmetered)
    uvicorn.run(app, host=host, port=port)
