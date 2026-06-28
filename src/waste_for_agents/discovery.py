"""Feed discovery:create_watch 收到的 url 可能是 feed,也可能是網站首頁。

agent 常只知道「訂 X 的 blog」→ 給首頁 url。此模組:
1. 若 url 本身解析得出 feed → 直接用。
2. 否則抓 HTML 找 `<link rel="alternate" type="application/rss+xml|atom+xml">`。
3. 仍無 → 試常見 feed 路徑後綴(`/feed`、`/rss`…;多數 WordPress/靜態站適用)。
4. 皆無 → 具名 FeedDiscoveryError,訊息帶可行動提示(不靜默失敗)。

直接服務「agent 自主訂閱」目標(agent-first):agent 給首頁就該能訂到。
"""

from __future__ import annotations

from urllib.parse import urljoin

import feedparser
import httpx
from bs4 import BeautifulSoup

from .netguard import guarded_get_sync

_DEFAULT_TIMEOUT = 10.0
_FEED_TYPES = {"application/rss+xml", "application/atom+xml"}

# 找不到 <link> 時依序試的常見 feed 路徑(WordPress / Hugo / Jekyll / Atom 慣例)。
_COMMON_FEED_PATHS = (
    "/feed",
    "/feed/",
    "/rss",
    "/rss.xml",
    "/feed.xml",
    "/index.xml",
    "/atom.xml",
)


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


def _get(url: str, headers: dict[str, str]) -> bytes:
    # sync:discovery 是 create_watch 的一次性呼叫(非 scheduler 熱路徑);
    # MCP sync tool 在 threadpool 執行,不擋 async scheduler loop。
    # follow_redirects=False:guarded_get_sync 逐跳重驗 + header allowlist(SSRF)。
    with httpx.Client(timeout=_DEFAULT_TIMEOUT, follow_redirects=False) as client:
        resp = guarded_get_sync(client, url, headers)
        resp.raise_for_status()
        return resp.content


def _forbidden_hint(exc: Exception, headers: dict[str, str]) -> str:
    """HTTP 401/403 且未帶 User-Agent → 提示加 UA(站台常擋無 UA 的請求)。"""
    if (
        isinstance(exc, httpx.HTTPStatusError)
        and exc.response.status_code in (401, 403)
        and not any(k.lower() == "user-agent" for k in headers)
    ):
        return (
            " — 站台拒絕了請求;多數情況是缺 User-Agent,"
            "在 query.headers 加一個瀏覽器 User-Agent 再試。"
        )
    return ""


def _probe_common_paths(base_url: str, headers: dict[str, str]) -> str | None:
    """依序試常見 feed 路徑後綴,回第一個解析得出 feed 的絕對 url;皆無回 None。

    每個候選的連線/狀態錯誤視為「此路徑無 feed」吞掉、續試下一個(404 等屬正常)。
    """
    seen: set[str] = set()
    for path in _COMMON_FEED_PATHS:
        candidate = urljoin(base_url, path)
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            content = _get(candidate, headers)
        except Exception:
            continue
        if is_feed(content):
            return candidate
    return None


def discover_feed(url: str, headers: dict[str, str] | None = None) -> str:
    """回可用的 feed url。

    解析順序:url 即 feed → 原樣回;首頁 `<link rel=alternate>` → 該 link;
    常見路徑後綴探測 → 命中者;皆無 → 拋 FeedDiscoveryError(帶可行動提示)。
    """
    hdrs = headers or {}
    try:
        content = _get(url, hdrs)
    except Exception as exc:
        # HTTP/連線層失敗:具名 + 視情況提示加 UA(與「找不到 feed」分層,不混淆)。
        raise FeedDiscoveryError(
            f"GET {url} 失敗:{type(exc).__name__}: {exc}{_forbidden_hint(exc, hdrs)}"
        ) from exc
    if is_feed(content):
        return url
    link = find_feed_link(content.decode("utf-8", errors="replace"), url)
    if link:
        return link
    probed = _probe_common_paths(url, hdrs)
    if probed:
        return probed
    raise FeedDiscoveryError(
        f"{url} 找不到 feed:首頁無 <link rel=alternate>,常見路徑"
        f"({', '.join(_COMMON_FEED_PATHS)})也都不是 feed。"
        f"請直接提供 feed 的 URL(例如 {urljoin(url, '/feed')})。"
    )
