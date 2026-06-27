"""排程器(asyncio 常駐 loop)。

對每個到期 watch:fetch -> diff(對上次 snapshot)-> 有變化才寫 change_event +
更新 snapshot。首輪只建 baseline(不產 event)。

失敗具名寫入 watch.last_error(經 list_watches 可見),不靜默吞、不影響其他 watch;
下輪成功會清掉 last_error。fetch 失敗時「不」覆蓋 snapshot,以免把錯誤狀態當基準。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timezone

from .diff import diff_rolling, diff_rows, row_key
from .normalize import norm_version
from .sources.base import Source, get_source
from .store import EventTuple, Row, Store, Watch

Resolve = Callable[[str], Source]

_MAX_ERROR_LEN = 2000  # last_error 截長:避免上游回應撐爆 DB / 經 list_watches 外洩過量


def _bound(text: str) -> str:
    return text if len(text) <= _MAX_ERROR_LEN else text[:_MAX_ERROR_LEN] + "…(truncated)"


async def run_watch(
    store: Store,
    watch: Watch,
    resolve: Resolve = get_source,
    norm_version: str | None = None,
) -> int:
    """跑單一 watch,回新增 change_event 數。

    norm_version:當前正規化版本戳(rolling 路徑用,偵測轉換器升級;見 F5)。
    dataset 路徑忽略它。
    """
    try:
        source = resolve(watch.source)
        rows = await source.fetch(watch.query)
    except Exception as exc:  # 具名記錄、不 re-raise(隔離其他 watch);不覆蓋 snapshot
        store.mark_run(watch.id, last_error=_bound(f"{type(exc).__name__}: {exc}"))
        return 0

    if watch.source_kind == "rolling_window":
        return _run_rolling(store, watch, rows, norm_version)

    old = store.get_snapshot(watch.id)
    if old is None:
        # 首輪:建 baseline,不產 event(snapshot 與 mark_run 在同一交易)
        store.record_run(watch.id, rows, [], None)
        return 0

    result = diff_rows(old, rows, watch.key_columns, watch.ignore_columns)
    events: list[EventTuple] = []
    for row in result.added:
        events.append(("added", row_key(row, watch.key_columns), {"row": row}))
    for row in result.removed:
        events.append(("removed", row_key(row, watch.key_columns), {"row": row}))
    for mod in result.modified:
        events.append(("modified", mod.key, {"changes": mod.changes}))

    # 原子:events + snapshot 前進 + mark_run 單一交易,中途失敗整批 rollback(不漏報)
    store.record_run(watch.id, rows, events, None)
    return len(events)


def _run_rolling(
    store: Store, watch: Watch, rows: list[Row], norm_version: str | None
) -> int:
    """rolling_window 路徑:added 對累積 seen-set 判、不產 removed、三態持久化。

    baseline 非靜默(seen 空 → 全 added、計費輪 1)。三態:
    ① 有 event → record_run 寫 events + snapshot,進 run_seq(計費輪)。
    ② 0 event 但 seen 變或版本戳變(F5 re-baseline)→ record_run 更新 snapshot +
       版本戳,不進 run_seq(否則新內容/版本戳永不落地、卡死)。
    ③ 真 0 變化 → 只 mark_run 推進 last_run_at,不重寫 snapshot。
    """
    # 並行安全(multi-review Critical 1/3):snapshot / last_run_seq 的唯一 writer 是
    # scheduler 這條 asyncio loop,且 _run_rolling 為同步函式(無 await)→ read-diff-write
    # 對 event loop 原子,FastAPI threadpool 的 list_changes 不碰這些欄位。故無 race。
    # ⚠ 遷 Postgres / 多實例時失效:屆時 run_seq 須在 record_run 交易內原子產生
    #   (見計畫未決清單「gate 原子性」)。
    old_list = store.get_snapshot(watch.id)
    seen: dict[str, Row] = (
        {}
        if old_list is None
        else {row_key(r, watch.key_columns): r for r in old_list}
    )
    old_nv = store.get_snapshot_norm_version(watch.id)
    suppress = old_nv is not None and norm_version is not None and old_nv != norm_version

    result, new_seen = diff_rolling(
        seen, rows, watch.key_columns, watch.ignore_columns, suppress
    )
    events: list[EventTuple] = []
    for row in result.added:
        events.append(("added", row_key(row, watch.key_columns), {"row": row}))
    for mod in result.modified:
        events.append(("modified", mod.key, {"changes": mod.changes}))
    # rolling 抑制 removed:舊文滾出 ≠ 刪除

    snapshot_list = list(new_seen.values())
    if events:
        store.record_run(
            watch.id, snapshot_list, events, None,
            run_seq=watch.last_run_seq + 1, norm_version=norm_version,
        )
    elif new_seen != seen or old_nv != norm_version:
        # ② F5 re-baseline:0 event 但內容/版本戳變 → 持久化,不計輪
        store.record_run(
            watch.id, snapshot_list, [], None, run_seq=0, norm_version=norm_version
        )
    else:
        # ③ 真 0 變化:只推進 last_run_at
        store.mark_run(watch.id, None)
    return len(events)


def _is_due(watch: Watch, now_epoch: float) -> bool:
    if watch.last_run_at is None:
        return True
    last = datetime.fromisoformat(watch.last_run_at).timestamp()
    return now_epoch - last >= watch.interval_s


async def run_due_watches(
    store: Store, now_epoch: float, resolve: Resolve = get_source
) -> int:
    """跑所有到期 watch,回本輪新增 event 總數。"""
    nv = norm_version()  # 當前正規化版本戳(全域),餵給 rolling watch 偵測升級(F5)
    total = 0
    for watch in store.list_watches():
        if _is_due(watch, now_epoch):
            total += await run_watch(store, watch, resolve, norm_version=nv)
    return total


async def scheduler_loop(
    store: Store, tick_s: float = 5.0, resolve: Resolve = get_source
) -> None:
    """常駐 loop:每 tick_s 秒跑一次到期 watch。被 cancel 時乾淨結束。"""
    while True:
        now = datetime.now(timezone.utc).timestamp()
        await run_due_watches(store, now, resolve)
        await asyncio.sleep(tick_s)
