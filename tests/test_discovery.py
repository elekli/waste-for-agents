"""Feed discovery:url 是 feed 直接用,否則從 HTML 找 <link rel=alternate>。"""

import httpx
import pytest

import waste_for_agents.discovery as disco
from waste_for_agents.discovery import (
    FeedDiscoveryError,
    discover_feed,
    find_alternate_feeds,
    find_feed_link,
    is_feed,
)

RSS = b'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>\
<item><title>a</title></item></channel></rss>'
# valid rss20,但 0 篇 <item> — WordPress 首頁留言 feed 的典型形狀(空內容)。
COMMENTS_RSS = b'<?xml version="1.0"?><rss version="2.0"><channel>\
<title>Comments on: Home</title></channel></rss>'


def test_find_feed_link_rss_relative():
    html = '<html><head><link rel="alternate" type="application/rss+xml" href="/feed.xml"></head></html>'
    assert find_feed_link(html, "https://blog.com/posts") == "https://blog.com/feed.xml"


def test_find_feed_link_atom_absolute():
    html = '<link rel="alternate" type="application/atom+xml" href="https://a.com/atom">'
    assert find_feed_link(html, "https://blog.com/") == "https://a.com/atom"


def test_find_feed_link_none():
    assert find_feed_link("<html><body>no feed here</body></html>", "https://x.com") is None


def test_is_feed():
    assert is_feed(RSS) is True
    assert is_feed(b"<html><body>just a page</body></html>") is False


def test_discover_feed_direct_feed(monkeypatch):
    monkeypatch.setattr(disco, "_get", lambda url, headers: RSS)
    assert discover_feed("https://blog.com/feed.xml") == "https://blog.com/feed.xml"


def test_discover_feed_from_homepage(monkeypatch):
    # discover 現在會抓回候選驗「有內容」→ /rss 須回真 feed,其餘回首頁 html。
    html = b'<html><head><link rel="alternate" type="application/rss+xml" href="/rss"></head></html>'

    def fake_get(url, headers):
        return RSS if url == "https://blog.com/rss" else html

    monkeypatch.setattr(disco, "_get", fake_get)
    assert discover_feed("https://blog.com/") == "https://blog.com/rss"


def test_discover_feed_none_raises(monkeypatch):
    monkeypatch.setattr(disco, "_get", lambda url, headers: b"<html>no feed</html>")
    with pytest.raises(FeedDiscoveryError):
        discover_feed("https://blog.com/")


def test_create_watch_rss_discovers_and_defaults_rolling(tmp_path, monkeypatch):
    # create_watch 對 rss:首頁 url → discover feed,且套 source 的 default_source_kind
    from waste_for_agents.server import Service, register_default_sources
    from waste_for_agents.store import Store

    register_default_sources()
    html = b'<html><head><link rel="alternate" type="application/rss+xml" href="/rss"></head></html>'

    def fake_get(url, headers):
        return RSS if url == "https://blog.com/rss" else html

    monkeypatch.setattr(disco, "_get", fake_get)
    svc = Service(Store.open(tmp_path / "w.db"))
    out = svc.create_watch("rss", {"url": "https://blog.com/"}, ["id"], [], 3600)
    w = svc.store.get_watch(out["watch_id"])
    assert w.query["url"] == "https://blog.com/rss"  # 已 discover
    assert w.source_kind == "rolling_window"  # 來自 source 的 default(agent 不需傳)


# --- A:常見路徑 fallback / C:可行動錯誤訊息 ---


def test_discover_feed_fallback_common_path(monkeypatch):
    # 首頁無 <link>,但 /feed 解析得出 feed → 探測命中、回 /feed
    def fake_get(url, headers):
        if url.endswith("/feed"):
            return RSS
        return b"<html><body>no link here</body></html>"

    monkeypatch.setattr(disco, "_get", fake_get)
    assert discover_feed("https://blog.com/") == "https://blog.com/feed"


def test_discover_feed_403_hints_user_agent(monkeypatch):
    # HTTP 403 且未帶 UA → 錯誤訊息提示加 User-Agent(與「找不到 feed」分層)
    def fake_get(url, headers):
        req = httpx.Request("GET", url)
        resp = httpx.Response(403, request=req)
        raise httpx.HTTPStatusError("forbidden", request=req, response=resp)

    monkeypatch.setattr(disco, "_get", fake_get)
    with pytest.raises(FeedDiscoveryError) as ei:
        discover_feed("https://blog.com/")
    assert "User-Agent" in str(ei.value)


def test_discover_feed_403_with_ua_no_hint(monkeypatch):
    # 已帶 UA 仍 403 → 不再提示加 UA(避免誤導:問題不在 UA)
    def fake_get(url, headers):
        req = httpx.Request("GET", url)
        resp = httpx.Response(403, request=req)
        raise httpx.HTTPStatusError("forbidden", request=req, response=resp)

    monkeypatch.setattr(disco, "_get", fake_get)
    with pytest.raises(FeedDiscoveryError) as ei:
        discover_feed("https://blog.com/", {"User-Agent": "x"})
    assert "User-Agent" not in str(ei.value)


def test_discover_feed_no_feed_suggests_direct_url(monkeypatch):
    # 首頁、<link>、常見路徑皆無 → 訊息建議直接給 feed URL
    monkeypatch.setattr(disco, "_get", lambda url, headers: b"<html>no feed</html>")
    with pytest.raises(FeedDiscoveryError) as ei:
        discover_feed("https://blog.com/")
    assert "/feed" in str(ei.value)


# --- Fix(1):留言/空 feed 不再靜默訂閱 ---


def test_find_alternate_feeds_splits_comments():
    # title 含 comment → 歸留言類;其餘歸內容類;順序保留。
    html = (
        '<link rel="alternate" type="application/rss+xml" title="Home Comments Feed" href="/comments/feed">'
        '<link rel="alternate" type="application/rss+xml" title="Feed" href="/feed">'
    )
    content, comments = find_alternate_feeds(html, "https://blog.com/")
    assert content == ["https://blog.com/feed"]
    assert comments == ["https://blog.com/comments/feed"]


def test_discover_feed_prefers_content_over_comments(monkeypatch):
    # 首頁同時宣告留言 feed(在前、0 篇)與內容 feed → 回內容 feed,不被留言 feed 騙。
    html = (
        b'<html><head>'
        b'<link rel="alternate" type="application/rss+xml" title="Comments Feed" href="/comments/feed">'
        b'<link rel="alternate" type="application/rss+xml" title="Feed" href="/feed">'
        b'</head></html>'
    )

    def fake_get(url, headers):
        if url == "https://blog.com/feed":
            return RSS
        if url == "https://blog.com/comments/feed":
            return COMMENTS_RSS
        return html

    monkeypatch.setattr(disco, "_get", fake_get)
    assert discover_feed("https://blog.com/") == "https://blog.com/feed"


def test_discover_feed_comments_only_raises(monkeypatch):
    # 只宣告留言 feed(0 篇)、常見路徑皆無 → 具名失敗、訊息點名該留言 feed(CWT 情境)。
    html = (
        b'<html><head>'
        b'<link rel="alternate" type="application/rss+xml" title="Home Comments Feed" href="/home/feed/">'
        b'</head></html>'
    )

    def fake_get(url, headers):
        # 首頁回 html;留言 feed 抓得到(但 0 篇);常見路徑全連不上。
        if url == "https://blog.com/":
            return html
        if url == "https://blog.com/home/feed/":
            return COMMENTS_RSS
        raise httpx.ConnectError("no feed here")

    monkeypatch.setattr(disco, "_get", fake_get)
    with pytest.raises(FeedDiscoveryError) as ei:
        discover_feed("https://blog.com/")
    # 留言 feed 不被當內容回傳,且在訊息中被點名(不靜默)
    assert "/home/feed/" in str(ei.value)


def test_discover_feed_empty_untitled_feed_not_returned(monkeypatch):
    # 沒帶 comment title 的 alternate,但抓回來是 0 篇 → 不回傳,記為空、具名失敗點名。
    html = (
        b'<html><head>'
        b'<link rel="alternate" type="application/rss+xml" href="/feed">'
        b'</head></html>'
    )

    def fake_get(url, headers):
        if url == "https://blog.com/":
            return html
        if url == "https://blog.com/feed":
            return COMMENTS_RSS  # valid feed 但 0 篇
        raise httpx.ConnectError("nope")

    monkeypatch.setattr(disco, "_get", fake_get)
    with pytest.raises(FeedDiscoveryError) as ei:
        discover_feed("https://blog.com/")
    assert "https://blog.com/feed" in str(ei.value)
