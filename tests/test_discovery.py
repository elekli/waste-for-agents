"""Feed discovery:url 是 feed 直接用,否則從 HTML 找 <link rel=alternate>。"""

import pytest

import waste_for_agents.discovery as disco
from waste_for_agents.discovery import (
    FeedDiscoveryError,
    discover_feed,
    find_feed_link,
    is_feed,
)

RSS = b'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>\
<item><title>a</title></item></channel></rss>'


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
    html = b'<html><head><link rel="alternate" type="application/rss+xml" href="/rss"></head></html>'
    monkeypatch.setattr(disco, "_get", lambda url, headers: html)
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
    monkeypatch.setattr(disco, "_get", lambda url, headers: html)
    svc = Service(Store.open(tmp_path / "w.db"))
    out = svc.create_watch("rss", {"url": "https://blog.com/"}, ["id"], [], 3600)
    w = svc.store.get_watch(out["watch_id"])
    assert w.query["url"] == "https://blog.com/rss"  # 已 discover
    assert w.source_kind == "rolling_window"  # 來自 source 的 default(agent 不需傳)
