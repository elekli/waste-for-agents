"""Feed discovery:create_watch 收到的 url 可能是 feed,也可能是網站首頁。

agent 常只知道「訂 X 的 blog」→ 給首頁 url。此模組:
1. 若 url 本身解析得出 feed → 直接用。
2. 否則抓 HTML 找 `<link rel="alternate" type="application/rss+xml|atom+xml">`。
3. 找不到 → 具名 FeedDiscoveryError(不靜默失敗)。

直接服務「agent 自主訂閱」目標(agent-first)。
"""

from __future__ import annotations

from urllib.parse import urljoin

import feedparser
import httpx
from bs4 import BeautifulSoup

_DEFAULT_TIMEOUT = 10.0
_FEED_TYPES = {"application/rss+xml", "application/atom+xml"}


class FeedDiscoveryError(RuntimeError):
    """無法從 url 發現可用的 RSS/Atom feed。"""


def is_feed(content: bytes) -> bool:
    """content 是否為可解析的 feed(feedparser 對非 feed 回空 version)。"""
    parsed = feedparser.parse(content)
    return bool(parsed.version)


def find_feed_link(html: str, base_url: str) -> str | None:
    """從 HTML 找第一個 RSS/Atom alternate link,回絕對 url(相對 href 用 base 補)。"""
    soup = BeautifulSoup(html, "html.parser")
    for link in soup.find_all("link", rel="alternate"):
        href = link.get("href")
        if link.get("type") in _FEED_TYPES and href:
            return urljoin(base_url, str(href))
    return None


async def _get(url: str, headers: dict[str, str]) -> bytes:
    async with httpx.AsyncClient(
        timeout=_DEFAULT_TIMEOUT, follow_redirects=True
    ) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        return resp.content


async def discover_feed(url: str, headers: dict[str, str] | None = None) -> str:
    """回可用的 feed url。url 即 feed → 原樣回;首頁 → 找 alternate link;皆無 → 拋。"""
    try:
        content = await _get(url, headers or {})
    except Exception as exc:
        raise FeedDiscoveryError(
            f"GET {url} 失敗:{type(exc).__name__}: {exc}"
        ) from exc
    if is_feed(content):
        return url
    link = find_feed_link(content.decode("utf-8", errors="replace"), url)
    if link:
        return link
    raise FeedDiscoveryError(f"{url} 無可發現的 RSS/Atom feed")
