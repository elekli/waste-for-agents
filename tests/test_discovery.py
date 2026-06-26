"""Feed discovery:url 是 feed 直接用,否則從 HTML 找 <link rel=alternate>。"""

import asyncio

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
    async def fake_get(url, headers):
        return RSS

    monkeypatch.setattr(disco, "_get", fake_get)
    out = asyncio.run(discover_feed("https://blog.com/feed.xml"))
    assert out == "https://blog.com/feed.xml"  # 本身即 feed


def test_discover_feed_from_homepage(monkeypatch):
    html = b'<html><head><link rel="alternate" type="application/rss+xml" href="/rss"></head></html>'

    async def fake_get(url, headers):
        return html

    monkeypatch.setattr(disco, "_get", fake_get)
    out = asyncio.run(discover_feed("https://blog.com/"))
    assert out == "https://blog.com/rss"


def test_discover_feed_none_raises(monkeypatch):
    async def fake_get(url, headers):
        return b"<html><body>no feed</body></html>"

    monkeypatch.setattr(disco, "_get", fake_get)
    with pytest.raises(FeedDiscoveryError):
        asyncio.run(discover_feed("https://blog.com/"))
