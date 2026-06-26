"""RSS adapter:feedparser → 穩定 id → 固定 schema rows(decisions 2-4, 6)。"""

import asyncio
import socket

import pytest

from waste_for_agents.sources.rss import RssFetchError, RssSource, parse_feed

ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>A</title>
  <entry>
    <id>urn:uuid:abc-123</id>
    <title>Atom Post \xe4\xb8\xad\xe6\x96\x87</title>
    <link href="https://x.com/atom/1"/>
    <updated>2026-01-01T00:00:00Z</updated>
    <content type="html">&lt;p&gt;body&lt;/p&gt;</content>
  </entry>
</feed>"""

RSS = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>T</title>
<item>
  <guid>g1</guid><title>First</title><link>https://x.com/1</link>
  <pubDate>Mon, 01 Jan 2026 00:00:00 GMT</pubDate>
  <description>&lt;p&gt;Hello &lt;a href="https://x.com/a"&gt;link&lt;/a&gt;&lt;/p&gt;</description>
</item>
<item><title>No guid</title><link>https://x.com/2</link></item>
<item><title>Only title</title></item>
</channel></rss>"""


def test_parse_feed_fixed_schema_and_count():
    rows = parse_feed(RSS)
    assert len(rows) == 3
    keys = {"id", "title", "link", "published", "author", "summary", "content"}
    for r in rows:
        assert set(r) == keys
        assert all(isinstance(v, str) for v in r.values())  # 全字串


def test_stable_id_guid_then_link_then_hash():
    rows = parse_feed(RSS)
    assert rows[0]["id"] == "g1"  # guid 優先
    assert rows[1]["id"] == "https://x.com/2"  # 無 guid → link
    assert rows[2]["id"] and rows[2]["id"] not in ("", "g1")  # 皆無 → hash,非空


def test_id_stable_across_parses():
    # 不變式 4:同一篇兩次解析得相同 id
    a = parse_feed(RSS)
    b = parse_feed(RSS)
    assert [r["id"] for r in a] == [r["id"] for r in b]


def test_content_html_to_markdown():
    rows = parse_feed(RSS)
    # description 的 HTML 連結轉成 MD link(decision 6 + 不變式 6)
    assert "[link](https://x.com/a)" in rows[0]["content"]
    assert "<p>" not in rows[0]["content"]


def test_empty_feed_returns_empty():
    rows = parse_feed(b'<?xml version="1.0"?><rss version="2.0"><channel><title>e</title></channel></rss>')
    assert rows == []


def test_non_feed_raises_named():
    with pytest.raises(RssFetchError):
        parse_feed(b"this is definitely not xml or a feed {{{")


def test_default_source_kind_rolling():
    # agent-first:RSS watch 不需 agent 知道要傳 rolling_window
    assert RssSource().default_source_kind == "rolling_window"


def test_rss_source_registered():
    from waste_for_agents.server import register_default_sources
    from waste_for_agents.sources import base

    register_default_sources()
    assert isinstance(base.get_source("rss"), RssSource)


def test_parse_atom_feed():
    rows = parse_feed(ATOM)
    assert len(rows) == 1
    assert rows[0]["id"] == "urn:uuid:abc-123"  # atom:id(feedparser 統一到 .id)
    assert rows[0]["link"] == "https://x.com/atom/1"
    assert "中文" in rows[0]["title"]  # UTF-8 多位元組保留
    assert "body" in rows[0]["content"]


def test_fetch_rejects_internal_url(monkeypatch):
    # SSRF 閘:解析到 loopback → RssFetchError(不真連)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("127.0.0.1", 0))])
    with pytest.raises(RssFetchError):
        asyncio.run(RssSource().fetch({"url": "http://localhost/feed"}))
