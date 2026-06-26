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

from .diff import diff_rows, row_key
from .sources.base import Source, get_source
from .store import EventTuple, Store, Watch

Resolve = Callable[[str], Source]

_MAX_ERROR_LEN = 2000  # last_error 截長:避免上游回應撐爆 DB / 經 list_watches 外洩過量


def _bound(text: str) -> str:
    return text if len(text) <= _MAX_ERROR_LEN else text[:_MAX_ERROR_LEN] + "…(truncated)"


async def run_watch(store: Store, watch: Watch, resolve: Resolve = get_source) -> int:
    """跑單一 watch,回新增 change_event 數。"""
    try:
        source = resolve(watch.source)
        rows = await source.fetch(watch.query)
    except Exception as exc:  # 具名記錄、不 re-raise(隔離其他 watch);不覆蓋 snapshot
        store.mark_run(watch.id, last_error=_bound(f"{type(exc).__name__}: {exc}"))
        return 0

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


def _is_due(watch: Watch, now_epoch: float) -> bool:
    if watch.last_run_at is None:
        return True
    last = datetime.fromisoformat(watch.last_run_at).timestamp()
    return now_epoch - last >= watch.interval_s


async def run_due_watches(
    store: Store, now_epoch: float, resolve: Resolve = get_source
) -> int:
    """跑所有到期 watch,回本輪新增 event 總數。"""
    total = 0
    for watch in store.list_watches():
        if _is_due(watch, now_epoch):
            total += await run_watch(store, watch, resolve)
    return total


async def scheduler_loop(
    store: Store, tick_s: float = 5.0, resolve: Resolve = get_source
) -> None:
    """常駐 loop:每 tick_s 秒跑一次到期 watch。被 cancel 時乾淨結束。"""
    while True:
        now = datetime.now(timezone.utc).timestamp()
        await run_due_watches(store, now, resolve)
        await asyncio.sleep(tick_s)
