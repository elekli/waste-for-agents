"""Source protocol + registry。

Source(Protocol) 含 async fetch(query) -> list[dict];registry 以 source 名查 adapter。
介面刻意留薄——「行得通一定會抽象」,先支撐單一 TwinkleSource。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

Row = dict[str, Any]


@runtime_checkable
class Source(Protocol):
    async def fetch(self, query: Row) -> list[Row]:
        """以 query(adapter 自定義語意)抓回一批結構化 rows。失敗應拋具名 exception。"""
        ...


class UnknownSourceError(KeyError):
    """registry 查不到指定的 source 名。"""


_REGISTRY: dict[str, Source] = {}


def register(name: str, source: Source) -> None:
    _REGISTRY[name] = source


def get_source(name: str) -> Source:
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        raise UnknownSourceError(name) from exc


def registry() -> dict[str, Source]:
    return dict(_REGISTRY)
