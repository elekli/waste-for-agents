"""scheduler 的 rolling_window 路徑:依 source_kind 選 diff、算 run_seq、三態持久化。

用注入的 resolve(fake source)驗,不碰網路。norm_version 參數驗 F5:版本戳變的
輪即使 0 event 也要把新內容 + 版本戳落地(否則卡死),但不進 run_seq(不計輪)。
"""

import asyncio

from waste_for_agents.scheduler import run_watch
from waste_for_agents.store import Store


class FakeSource:
    """可控 rows 的 fake source。"""

    def __init__(self, rows):
        self.rows = rows

    async def fetch(self, query):
        return self.rows


def _resolve(src):
    return lambda name: src


def _mk_rolling_watch(store):
    return store.create_watch(
        "rss", {"url": "x"}, ["id"], [], 3600, source_kind="rolling_window"
    )


def test_rolling_baseline_all_added(tmp_path):
    s = Store.open(tmp_path / "w.db")
    w = _mk_rolling_watch(s)
    src = FakeSource([{"id": "a", "c": "A"}, {"id": "b", "c": "B"}])
    n = asyncio.run(run_watch(s, w, _resolve(src), norm_version="v1"))
    assert n == 2  # baseline 全 added(rolling 非靜默)
    events, _ = s.events_since(None)
    assert {e.kind for e in events} == {"added"}
    assert all(e.run_seq == 1 for e in events)  # 計費輪 1
    assert {r["id"] for r in s.get_snapshot(w.id)} == {"a", "b"}


def test_rolling_rollout_and_reappearance(tmp_path):
    s = Store.open(tmp_path / "w.db")
    w = _mk_rolling_watch(s)
    # 輪1 {a,b}
    asyncio.run(run_watch(s, _reload(s, w), _resolve(FakeSource(
        [{"id": "a", "c": "A"}, {"id": "b", "c": "B"}])), norm_version="v1"))
    # 輪2 {b,c}:a 滾出(不報 removed),c added
    n2 = asyncio.run(run_watch(s, _reload(s, w), _resolve(FakeSource(
        [{"id": "b", "c": "B"}, {"id": "c", "c": "C"}])), norm_version="v1"))
    assert n2 == 1
    # 輪3 {c,a}:a 重浮現、內容沒變 → 0 event(F2 核心)
    n3 = asyncio.run(run_watch(s, _reload(s, w), _resolve(FakeSource(
        [{"id": "c", "c": "C"}, {"id": "a", "c": "A"}])), norm_version="v1"))
    assert n3 == 0
    removed = [e for e in s.events_since(None)[0] if e.kind == "removed"]
    assert removed == []  # 全程不產 removed


def test_run_seq_increments_only_on_event_rounds(tmp_path):
    s = Store.open(tmp_path / "w.db")
    w = _mk_rolling_watch(s)
    asyncio.run(run_watch(s, _reload(s, w), _resolve(FakeSource(
        [{"id": "a", "c": "A"}])), norm_version="v1"))  # 輪 → run_seq 1
    asyncio.run(run_watch(s, _reload(s, w), _resolve(FakeSource(
        [{"id": "a", "c": "A"}, {"id": "b", "c": "B"}])), norm_version="v1"))  # +b → run_seq 2
    assert _reload(s, w).last_run_seq == 2
    # 0 變化輪:run_seq 不進
    asyncio.run(run_watch(s, _reload(s, w), _resolve(FakeSource(
        [{"id": "a", "c": "A"}, {"id": "b", "c": "B"}])), norm_version="v1"))
    assert _reload(s, w).last_run_seq == 2


def test_suppress_rebaseline_persists_without_round(tmp_path):
    # F5:norm_version 變 → 內容變不報 modified,但新內容 + 新版本戳要落地、run_seq 不進
    s = Store.open(tmp_path / "w.db")
    w = _mk_rolling_watch(s)
    asyncio.run(run_watch(s, _reload(s, w), _resolve(FakeSource(
        [{"id": "a", "c": "A"}])), norm_version="v1"))
    seq_before = _reload(s, w).last_run_seq
    # 版本戳 bump,同一篇重算出不同內容
    n = asyncio.run(run_watch(s, _reload(s, w), _resolve(FakeSource(
        [{"id": "a", "c": "A_reMD"}])), norm_version="v2"))
    assert n == 0  # 0 event(modified 被抑制)
    assert _reload(s, w).last_run_seq == seq_before  # 不計輪
    assert s.get_snapshot(w.id) == [{"id": "a", "c": "A_reMD"}]  # 內容已 re-baseline
    assert s.get_snapshot_norm_version(w.id) == "v2"  # 版本戳前進(否則下輪再抑制、卡死)


def _reload(store, watch):
    w = store.get_watch(watch.id)
    assert w is not None
    return w
