"""HttpJsonSource 測試:純函式解析層 + token-free live e2e(YouBike 上游)。

unit 部分(_stringify / _extract_rows)無網路,CI 安全。
live 部分打台北市 YouBike 即時 JSON(公開、無 token、每分鐘變),
證明同一個 diff 引擎能跑在真正高頻的源上、且忽略 timestamp 欄位不誤報。
預設 skip,設 WASTE_LIVE_HTTP=1 才跑(避免離線/CI 失敗)。
"""

import os

import pytest

from waste_for_agents.scheduler import run_watch
from waste_for_agents.sources.http_json import (
    HttpJsonFetchError,
    HttpJsonSource,
    _extract_rows,
    _stringify,
)
from waste_for_agents.store import Store

YOUBIKE_URL = "https://tcgbusfs.blob.core.windows.net/dotapp/youbike/v2/youbike_immediate.json"
# 上游每筆都帶這幾個 timestamp 欄位,每分鐘跳動但不代表車輛數有變 → 忽略才不誤報
YOUBIKE_IGNORE = ["mday", "updateTime", "infoTime", "srcUpdateTime", "infoDate"]


# --- _stringify(值正規化) ---


def test_stringify_scalars() -> None:
    assert _stringify("28") == "28"
    assert _stringify(28) == "28"
    assert _stringify(25.026) == "25.026"
    assert _stringify(None) == ""
    assert _stringify(True) == "true"
    assert _stringify(False) == "false"


def test_stringify_nested_is_canonical() -> None:
    # key 順序不影響結果 → 巢狀值不會因序列化順序誤報
    assert _stringify({"b": 1, "a": 2}) == _stringify({"a": 2, "b": 1})
    assert _stringify([1, "2"]) == '[1, "2"]'


# --- _extract_rows(payload → list[dict]) ---


def test_extract_rows_top_level_list() -> None:
    payload = [{"sno": "1", "n": 5}, {"sno": "2", "n": 3}]
    assert _extract_rows(payload, None) == [
        {"sno": "1", "n": "5"},
        {"sno": "2", "n": "3"},
    ]


def test_extract_rows_records_path() -> None:
    payload = {"data": {"items": [{"id": 1}]}}
    assert _extract_rows(payload, "data.items") == [{"id": "1"}]


def test_extract_rows_bad_path_raises() -> None:
    with pytest.raises(HttpJsonFetchError):
        _extract_rows({"data": []}, "data.items")  # 'items' 段不存在


def test_extract_rows_not_a_list_raises() -> None:
    with pytest.raises(HttpJsonFetchError):
        _extract_rows({"foo": "bar"}, None)


def test_extract_rows_non_object_record_raises() -> None:
    with pytest.raises(HttpJsonFetchError):
        _extract_rows([{"ok": 1}, "not-an-object"], None)


async def test_fetch_rejects_missing_url() -> None:
    with pytest.raises(HttpJsonFetchError):
        await HttpJsonSource().fetch({"records_path": "x"})


# --- live e2e(token-free,gated) ---

live = pytest.mark.skipif(
    not os.environ.get("WASTE_LIVE_HTTP"), reason="設 WASTE_LIVE_HTTP=1 才跑 live"
)


@live
async def test_youbike_live_fetch() -> None:
    rows = await HttpJsonSource().fetch({"url": YOUBIKE_URL})
    assert len(rows) > 1000  # 台北 ~1700 站
    assert "sno" in rows[0]
    assert "available_rent_bikes" in rows[0]


@live
async def test_youbike_pipeline_baseline(tmp_path) -> None:
    """首輪建 baseline 不產 event;snapshot 抓到真實站點。"""
    store = Store.open(tmp_path / "w.db")
    src = HttpJsonSource()
    watch = store.create_watch(
        source="http_json",
        query={"url": YOUBIKE_URL},
        key_columns=["sno"],
        ignore_columns=YOUBIKE_IGNORE,
        interval_s=60,
    )
    count = await run_watch(store, watch, lambda _: src)
    assert count == 0
    snapshot = store.get_snapshot(watch.id)
    assert snapshot is not None and len(snapshot) > 1000
    assert "sno" in snapshot[0]
    assert store.get_watch(watch.id).last_error is None  # type: ignore[union-attr]
