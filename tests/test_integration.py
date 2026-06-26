"""Token-gated end-to-end:真實 Twinkle 走完整 pipeline。

只在有 TWINKLE_TOKEN 時跑。證明 TwinkleSource -> scheduler -> diff -> store
這條路徑對真實政府資料成立,且**重抓不誤報**(moat 的現場驗證)。
"""

import os

import pytest

from waste_for_agents.scheduler import run_watch
from waste_for_agents.sources.twinkle import TwinkleSource
from waste_for_agents.store import Store

pytestmark = pytest.mark.skipif(
    not os.environ.get("TWINKLE_TOKEN"), reason="需要 TWINKLE_TOKEN"
)


async def test_twinkle_pipeline_no_false_positive(tmp_path) -> None:
    store = Store.open(tmp_path / "w.db")
    src = TwinkleSource()
    watch = store.create_watch(
        source="twinkle",
        query={
            "dataset_id": "ly-bills",
            "columns": ["議案編號", "議案狀態"],
            "where": "\"屆\"='11'",
            "limit": 20,
        },
        key_columns=["議案編號"],
        ignore_columns=[],
        interval_s=60,
    )

    # 首輪:建 baseline,抓到真實 rows,不產 event
    count1 = await run_watch(store, watch, lambda _: src)
    assert count1 == 0
    snapshot = store.get_snapshot(watch.id)
    assert snapshot is not None and len(snapshot) > 0
    assert "議案編號" in snapshot[0]
    assert store.get_watch(watch.id).last_error is None  # type: ignore[union-attr]

    # 次輪:同 query 重抓,資料未變 → 0 event(不誤報)
    count2 = await run_watch(store, watch, lambda _: src)
    assert count2 == 0
    events, _ = store.events_since(None)
    assert events == []
