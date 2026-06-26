"""Chunk 4 排程器測試(用 FakeSource,不碰網路)。"""

import asyncio
import time

import pytest

from waste_for_agents.scheduler import run_due_watches, run_watch, scheduler_loop
from waste_for_agents.sources.base import Row
from waste_for_agents.store import Store


class FakeSource:
    """可控制每次 fetch 回傳;可設定 raise 來模擬失敗。"""

    def __init__(self, rows: list[Row]) -> None:
        self.rows = rows
        self.error: Exception | None = None
        self.calls = 0

    async def fetch(self, query: Row) -> list[Row]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.rows


def _store(tmp_path) -> Store:
    return Store.open(tmp_path / "w.db")


async def test_first_run_builds_baseline_no_events(tmp_path) -> None:
    store = _store(tmp_path)
    w = store.create_watch("fake", {}, ["id"], [], 60)
    src = FakeSource([{"id": "1", "name": "a"}])

    count = await run_watch(store, w, lambda _: src)
    assert count == 0
    assert store.get_snapshot(w.id) == [{"id": "1", "name": "a"}]
    events, _ = store.events_since(None)
    assert events == []


async def test_second_run_emits_diff_events(tmp_path) -> None:
    store = _store(tmp_path)
    w = store.create_watch("fake", {}, ["id"], [], 60)
    src = FakeSource([{"id": "1", "name": "a"}])

    await run_watch(store, w, lambda _: src)  # baseline
    src.rows = [{"id": "1", "name": "b"}, {"id": "2", "name": "c"}]  # 改:modified + added
    count = await run_watch(store, w, lambda _: src)

    assert count == 2
    events, _ = store.events_since(None)
    kinds = sorted(e.kind for e in events)
    assert kinds == ["added", "modified"]


async def test_ignore_columns_no_events(tmp_path) -> None:
    store = _store(tmp_path)
    w = store.create_watch("fake", {}, ["id"], ["updated_at"], 60)
    src = FakeSource([{"id": "1", "name": "a", "updated_at": "T1"}])

    await run_watch(store, w, lambda _: src)
    src.rows = [{"id": "1", "name": "a", "updated_at": "T2"}]  # 只動忽略欄位
    count = await run_watch(store, w, lambda _: src)

    assert count == 0
    assert store.events_since(None)[0] == []


async def test_fetch_error_named_and_isolated(tmp_path) -> None:
    store = _store(tmp_path)
    w = store.create_watch("fake", {}, ["id"], [], 60)
    src = FakeSource([{"id": "1"}])
    await run_watch(store, w, lambda _: src)  # baseline ok

    src.error = RuntimeError("upstream down")
    count = await run_watch(store, w, lambda _: src)
    assert count == 0
    got = store.get_watch(w.id)
    assert got is not None
    assert got.last_error is not None
    assert "upstream down" in got.last_error
    # snapshot 未被覆蓋
    assert store.get_snapshot(w.id) == [{"id": "1"}]

    # 下輪恢復 → last_error 清掉
    src.error = None
    await run_watch(store, w, lambda _: src)
    got = store.get_watch(w.id)
    assert got is not None
    assert got.last_error is None


async def test_run_due_skips_not_due(tmp_path) -> None:
    store = _store(tmp_path)
    store.create_watch("fake", {}, ["id"], [], 60)
    src = FakeSource([{"id": "1"}])

    # mark_run 用真實時鐘戳 last_run_at,故 now 也用真實時鐘為基準
    now = time.time()
    n = await run_due_watches(store, now, lambda _: src)  # last_run_at=None → due
    assert src.calls == 1
    assert n == 0  # 首次無 event
    # interval 60 內 → 不 due
    await run_due_watches(store, now + 30, lambda _: src)
    assert src.calls == 1
    # 過了 interval → 再跑
    await run_due_watches(store, now + 120, lambda _: src)
    assert src.calls == 2


async def test_scheduler_loop_runs_and_cancels(tmp_path) -> None:
    store = _store(tmp_path)
    w = store.create_watch("fake", {}, ["id"], [], 0)
    src = FakeSource([{"id": "1"}])

    task = asyncio.create_task(scheduler_loop(store, tick_s=0.01, resolve=lambda _: src))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert store.get_snapshot(w.id) == [{"id": "1"}]
    assert src.calls >= 1
