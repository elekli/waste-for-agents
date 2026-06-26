"""RssSource — RSS/Atom adapter:GET feed → feedparser 解析 → 穩定 id → 固定 schema rows。

設計決策(見 spec):
- 穩定 id(decision 2):guid/atom:id → link → hash(title+published)。識別髒活關在
  adapter 內,對外暴露乾淨穩定 id(開箱即用,符合 agent-first)。
- feedparser 當解析底層(decision 3):externalize RSS/Atom 格式地獄。
- content 轉 pinned Markdown(decision 6):保留連結/結構、去 HTML 噪音、LLM 友善。
- 固定 schema 不含重生成時間戳(decision 4):只取 published(穩定),不取 updated,
  從源頭避免易變欄位造成的誤報——故 watch 不需特別設 ignore_columns。
- default_source_kind="rolling_window":agent 不需知道要傳 rolling(agent-first)。

id fallback 的固有限制:落到 hash(title+published) 的 feed,標題被編輯 → hash 變 →
那篇被當「滾出 + 新出現」。有 guid 的 feed(多數)不受影響。
"""

from __future__ import annotations

import hashlib
from typing import Any

import feedparser
import httpx

from ..normalize import html_to_markdown
from .base import Row

_MAX_ERROR_LEN = 1000
_DEFAULT_TIMEOUT = 10.0

_SCHEMA_KEYS = ("id", "title", "link", "published", "author", "summary", "content")


class RssFetchError(RuntimeError):
    """連線、HTTP 狀態碼、或 feed 解析/形狀失敗。一律具名,不靜默吞。"""


def _truncate(text: str) -> str:
    if len(text) > _MAX_ERROR_LEN:
        return text[:_MAX_ERROR_LEN] + "…(truncated)"
    return text


def _stable_id(entry: Any) -> str:
    """guid/atom:id → link → hash(title+published)。保證非空。"""
    if entry.get("id"):
        return str(entry["id"])
    if entry.get("link"):
        return str(entry["link"])
    basis = f"{entry.get('title', '')}\x00{entry.get('published', '')}"
    return "hash:" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def _content_html(entry: Any) -> str:
    """取 content:encoded,無則退到 description/summary。"""
    content = entry.get("content")
    if content:
        value = content[0].get("value", "")
        return str(value)
    return str(entry.get("summary", ""))


def _entry_to_row(entry: Any) -> Row:
    summary_html = str(entry.get("summary", ""))
    return {
        "id": _stable_id(entry),
        "title": str(entry.get("title", "")),
        "link": str(entry.get("link", "")),
        "published": str(entry.get("published", "")),
        "author": str(entry.get("author", "")),
        "summary": html_to_markdown(summary_html) if summary_html else "",
        "content": html_to_markdown(_content_html(entry)),
    }


def parse_feed(content: bytes) -> list[Row]:
    """bytes → list[Row](固定 schema)。無法解析為 feed 且無條目 → RssFetchError。"""
    parsed = feedparser.parse(content)
    if parsed.bozo and not parsed.entries:
        raise RssFetchError(
            _truncate(f"feed 解析失敗:{type(parsed.bozo_exception).__name__}: "
                      f"{parsed.bozo_exception}")
        )
    return [_entry_to_row(e) for e in parsed.entries]


class RssSource:
    """GET 一個 RSS/Atom feed url,解析成固定 schema rows。query={url, headers?}。"""

    default_source_kind = "rolling_window"

    def __init__(self, timeout: float = _DEFAULT_TIMEOUT) -> None:
        self._timeout = timeout

    async def fetch(self, query: Row) -> list[Row]:
        url = query.get("url")
        if not isinstance(url, str) or not url:
            raise RssFetchError("query.url 必填且須為非空字串")
        headers = query.get("headers") or {}
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, follow_redirects=True
            ) as client:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                content = resp.content
        except RssFetchError:
            raise
        except Exception as exc:  # 連線/狀態層失敗 → 具名 + 截長
            raise RssFetchError(
                _truncate(f"GET {url} 失敗:{type(exc).__name__}: {exc}")
            ) from exc
        return parse_feed(content)
