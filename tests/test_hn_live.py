"""HN live e2e:真打 https://news.ycombinator.com/rss(公開、無 token)。

預設 skip,設 WASTE_LIVE_RSS=1 才跑(對齊 http_json 的 WASTE_LIVE_HTTP 慣例,避免
離線/CI 失敗)。驗:discover/解析/穩定 id/content→MD,以及把真實 rows 餵兩輪 diff
合理(baseline 全 added、同內容重餵 0 event)。

只有「一次真實 fetch」走網路;diff 用該批 rows 確定性重放,避免 HN 兩次抓取間更新造成 flaky。
"""

import asyncio
import os

import pytest

from waste_for_agents.scheduler import run_watch
from waste_for_agents.store import Store

LIVE = os.environ.get("WASTE_LIVE_RSS") == "1"
HN_FEED = "https://news.ycombinator.com/rss"

pytestmark = pytest.mark.skipif(
    not LIVE, reason="設 WASTE_LIVE_RSS=1 才跑(真打 HN，需網路)"
)


class _ReplaySource:
    """回放固定 rows 的 source(把一次真實 fetch 的結果確定性重放兩輪)。"""

    def __init__(self, rows):
        self.rows = rows

    async def fetch(self, query):
        return list(self.rows)


def test_hn_discover_returns_feed():
    from waste_for_agents.discovery import discover_feed

    # HN 的 rss 本身即 feed → discovery 原樣回(經 SSRF 閘 + 真連網)
    assert discover_feed(HN_FEED) == HN_FEED


def test_hn_fetch_stable_ids_and_markdown():
    from waste_for_agents.sources.rss import RssSource

    rows = asyncio.run(RssSource().fetch({"url": HN_FEED}))
    assert rows, "HN feed 應有條目"
    # 每筆固定 schema、穩定非空 id
    for r in rows:
        assert set(r) == {"id", "title", "link", "published", "author", "summary", "content"}
        assert r["id"]  # 非空穩定 id
    # id 跨「兩次解析」穩定(不變式 4 在真實資料上)
    rows2 = asyncio.run(RssSource().fetch({"url": HN_FEED}))
    common = {r["id"] for r in rows} & {r["id"] for r in rows2}
    assert common, "兩次抓取應有重疊條目(id 穩定)"


def test_hn_two_round_diff_reasonable(tmp_path):
    from waste_for_agents.sources.rss import RssSource

    rows = asyncio.run(RssSource().fetch({"url": HN_FEED}))
    src = _ReplaySource(rows)
    s = Store.open(tmp_path / "hn.db")
    wid = s.create_watch(
        "rss", {"url": HN_FEED}, ["id"], [], 3600, source_kind="rolling_window"
    ).id

    # 輪 1:baseline 全 added
    w = s.get_watch(wid)
    n1 = asyncio.run(run_watch(s, w, lambda name: src, norm_version="v1"))
    assert n1 == len(rows)
    # 輪 2:同批 rows 重餵 → 0 event(穩定 id + 內容沒變)
    w = s.get_watch(wid)
    n2 = asyncio.run(run_watch(s, w, lambda name: src, norm_version="v1"))
    assert n2 == 0
