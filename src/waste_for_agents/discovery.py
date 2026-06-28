"""Feed discovery:create_watch 收到的 url 可能是 feed,也可能是網站首頁。

agent 常只知道「訂 X 的 blog」→ 給首頁 url。此模組:
1. 若 url 本身解析得出 feed → 直接用。
2. 否則抓 HTML 找 `<link rel="alternate" type="application/rss+xml|atom+xml">`,
   抓回來驗「有條目」才採用(擋掉 WordPress 首頁的留言 feed 這類 0 篇空 feed)。
3. 仍無 → 試常見 feed 路徑後綴(`/feed`、`/rss`…;同樣驗有條目)。
4. 皆無 → 具名 FeedDiscoveryError,訊息帶可行動提示、點名見過的留言/空 feed(不靜默失敗)。

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


def _is_content_feed(content: bytes) -> bool:
    """是 feed **且有條目**——「內容 feed」。

    用於擋掉「valid 但 0 篇」的 feed(典型:WordPress 首頁的留言 feed),避免靜默
    訂到一個永遠不更新的空 feed(zero-silent-failure)。
    """
    parsed = feedparser.parse(content)
    return bool(parsed.version) and len(parsed.entries) > 0


def find_alternate_feeds(html: str, base_url: str) -> tuple[list[str], list[str]]:
    """從 HTML 收集所有 RSS/Atom alternate link,依 title 分成 (內容, 留言) 兩串。

    WordPress 等常同時宣告內容 feed 與留言 feed;`<link>` 的 title 含 "comment"
    (不分大小寫)→ 視為留言 feed。內容 feed 才會被自動採用;留言 feed 只用來
    在找不到內容 feed 時豐富錯誤訊息(不把留言當內容餵給 agent)。順序保留。
    回絕對 url(相對 href 用 base 補)。
    """
    soup = BeautifulSoup(html, "html.parser")
    content_feeds: list[str] = []
    comment_feeds: list[str] = []
    for link in soup.find_all("link", rel="alternate"):
        href = link.get("href")
        if link.get("type") not in _FEED_TYPES or not href:
            continue
        abs_url = urljoin(base_url, str(href))
        title = str(link.get("title") or "").lower()
        (comment_feeds if "comment" in title else content_feeds).append(abs_url)
    return content_feeds, comment_feeds


def find_feed_link(html: str, base_url: str) -> str | None:
    """從 HTML 找第一個**內容** RSS/Atom alternate link;無則 None(向後相容薄包裝)。"""
    content_feeds, _ = find_alternate_feeds(html, base_url)
    return content_feeds[0] if content_feeds else None


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


def _try_get(url: str, headers: dict[str, str]) -> bytes | None:
    """_get 的吞錯版:連線/狀態錯誤回 None(供候選探測逐一試、不中斷)。"""
    try:
        return _get(url, headers)
    except Exception:
        return None


def _probe_common_paths(base_url: str, headers: dict[str, str]) -> str | None:
    """依序試常見 feed 路徑後綴,回第一個**有內容**的 feed 絕對 url;皆無回 None。

    每個候選的連線/狀態錯誤視為「此路徑無 feed」吞掉、續試下一個(404 等屬正常)。
    用 _is_content_feed:空 feed(如留言 feed)不該被當常見路徑命中而靜默接受。
    """
    seen: set[str] = set()
    for path in _COMMON_FEED_PATHS:
        candidate = urljoin(base_url, path)
        if candidate in seen:
            continue
        seen.add(candidate)
        content = _try_get(candidate, headers)
        if content is not None and _is_content_feed(content):
            return candidate
    return None


def _no_feed_message(
    url: str, empty: list[str], comment_feeds: list[str]
) -> str:
    """找不到內容 feed 的具名訊息;若見過空/留言 feed 則點名(讓陷阱可見)。"""
    msg = (
        f"{url} 找不到內容 feed:首頁無內容 <link rel=alternate>,常見路徑"
        f"({', '.join(_COMMON_FEED_PATHS)})也都不是有內容的 feed。"
        f"請直接提供 feed 的 URL(例如 {urljoin(url, '/feed')})。"
    )
    suspect = comment_feeds + empty
    if suspect:
        msg += (
            f" 註:在 {', '.join(suspect)} 找到的是留言/空 feed,不是內容 feed"
            "(0 篇),已略過——若那其實是你要的,請直接給它的 URL。"
        )
    return msg


def discover_feed(url: str, headers: dict[str, str] | None = None) -> str:
    """回可用的**內容** feed url。

    解析順序:url 即 feed → 原樣回(信任明示);首頁內容 `<link rel=alternate>` →
    抓回來驗有內容才回;常見路徑後綴探測(同樣驗有內容)→ 命中者;皆無 → 拋
    FeedDiscoveryError(帶可行動提示;見過的留言/空 feed 會被點名,不靜默訂閱)。
    """
    hdrs = headers or {}
    try:
        content = _get(url, hdrs)
    except Exception as exc:
        # HTTP/連線層失敗:具名 + 視情況提示加 UA(與「找不到 feed」分層,不混淆)。
        raise FeedDiscoveryError(
            f"GET {url} 失敗:{type(exc).__name__}: {exc}{_forbidden_hint(exc, hdrs)}"
        ) from exc
    # url 本身就是 feed → 信任(使用者明示給了 feed url,即使空也照用)。
    if is_feed(content):
        return url
    content_feeds, comment_feeds = find_alternate_feeds(
        content.decode("utf-8", errors="replace"), url
    )
    empty: list[str] = []
    for cand in content_feeds:
        cand_content = _try_get(cand, hdrs)
        if cand_content is None:
            continue
        if _is_content_feed(cand_content):
            return cand
        if is_feed(cand_content):
            # 是 feed 但 0 篇(如沒帶 comment title 的留言 feed)→ 記下,不靜默回傳。
            empty.append(cand)
    probed = _probe_common_paths(url, hdrs)
    if probed:
        return probed
    raise FeedDiscoveryError(_no_feed_message(url, empty, comment_feeds))
