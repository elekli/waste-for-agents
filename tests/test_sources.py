"""Chunk 3 Source 介面 + TwinkleSource 測試。

網路層(真連 Twinkle)只在有 TWINKLE_TOKEN 時跑;沒有則 skip。
易錯的解析層 _extract_rows 當純函式測,不需網路。
"""

import os

import pytest

from waste_for_agents.sources import base
from waste_for_agents.sources.base import Row, Source, UnknownSourceError, get_source, register
from waste_for_agents.sources.twinkle import TwinkleFetchError, TwinkleSource, _extract_rows


class FakeSource:
    def __init__(self, rows: list[Row]) -> None:
        self._rows = rows
        self.calls: list[Row] = []

    async def fetch(self, query: Row) -> list[Row]:
        self.calls.append(query)
        return self._rows


def test_fake_source_satisfies_protocol() -> None:
    assert isinstance(FakeSource([]), Source)


async def test_fake_source_fetch() -> None:
    src = FakeSource([{"id": "1"}])
    assert await src.fetch({"q": 1}) == [{"id": "1"}]
    assert src.calls == [{"q": 1}]


def test_registry_register_and_get() -> None:
    base._REGISTRY.clear()
    src = FakeSource([])
    register("fake", src)
    assert get_source("fake") is src


def test_registry_unknown_raises() -> None:
    base._REGISTRY.clear()
    with pytest.raises(UnknownSourceError):
        get_source("nope")


# --- _extract_rows (column-oriented → list[dict]) ---


def test_extract_rows_column_oriented() -> None:
    payload = {
        "columns": ["議案編號", "議案狀態"],
        "rows": [["301", "交付審查"], ["302", "三讀"]],
        "row_count_returned": 2,
    }
    assert _extract_rows(payload) == [
        {"議案編號": "301", "議案狀態": "交付審查"},
        {"議案編號": "302", "議案狀態": "三讀"},
    ]


def test_extract_rows_empty() -> None:
    assert _extract_rows({"columns": ["a"], "rows": []}) == []


def test_extract_rows_bad_shape_raises() -> None:
    with pytest.raises(TwinkleFetchError):
        _extract_rows(["not", "a", "dict"])
    with pytest.raises(TwinkleFetchError):
        _extract_rows({"rows": []})  # 缺 columns


@pytest.mark.skipif(not os.environ.get("TWINKLE_TOKEN"), reason="需要 TWINKLE_TOKEN")
async def test_twinkle_live_fetch() -> None:
    src = TwinkleSource()
    rows = await src.fetch(
        {
            "dataset_id": "ly-bills",
            "columns": ["議案編號", "議案狀態"],
            "where": "\"屆\"='11'",
            "limit": 2,
        }
    )
    assert len(rows) == 2
    assert "議案編號" in rows[0]
