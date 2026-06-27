"""出站 URL SSRF 閘:scheme + 內網/metadata 位址阻擋 + redirect 逐跳重驗 + header allowlist。"""

import asyncio
import socket

import pytest

from waste_for_agents.netguard import (
    UnsafeUrlError,
    check_outbound_url,
    guarded_get,
    guarded_get_sync,
    safe_headers,
)


def _mock_resolve(monkeypatch, ip):
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", (ip, 0))]
    )


def test_allows_public(monkeypatch):
    _mock_resolve(monkeypatch, "93.184.216.34")
    check_outbound_url("https://example.com/feed.xml")  # 不拋


def test_rejects_non_http_scheme():
    with pytest.raises(UnsafeUrlError):
        check_outbound_url("file:///etc/passwd")


def test_rejects_loopback(monkeypatch):
    _mock_resolve(monkeypatch, "127.0.0.1")
    with pytest.raises(UnsafeUrlError):
        check_outbound_url("http://localhost/x")


def test_rejects_metadata(monkeypatch):
    _mock_resolve(monkeypatch, "169.254.169.254")
    with pytest.raises(UnsafeUrlError):
        check_outbound_url("http://169.254.169.254/latest/meta-data/")


def test_rejects_private_rfc1918(monkeypatch):
    _mock_resolve(monkeypatch, "10.0.0.5")
    with pytest.raises(UnsafeUrlError):
        check_outbound_url("http://internal.corp/admin")


def test_rejects_no_host():
    with pytest.raises(UnsafeUrlError):
        check_outbound_url("https:///nohost")


# --- 出站 header allowlist ---


def test_safe_headers_drops_dangerous_keeps_allowed():
    out = safe_headers(
        {
            "User-Agent": "wfa/1",
            "If-None-Match": "etag1",
            "Accept": "application/rss+xml",
            "Authorization": "Bearer secret",  # 不可外送
            "Host": "evil.example",  # 不可外送
            "Cookie": "sid=1",  # 不可外送
            "Proxy-Authorization": "x",  # 不可外送
        }
    )
    assert out == {
        "User-Agent": "wfa/1",
        "If-None-Match": "etag1",
        "Accept": "application/rss+xml",
    }


def test_safe_headers_none():
    assert safe_headers(None) == {}


# --- redirect 逐跳重驗 ---


def _resolve(monkeypatch, mapping):
    """host → ip 對照(未列者預設公開 IP)。"""
    def fake(host, *a, **k):
        return [(2, 1, 6, "", (mapping.get(host, "93.184.216.34"), 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake)


class _Resp:
    def __init__(self, status, location=None):
        self.status_code = status
        self.headers = {"location": location} if location else {}

    @property
    def is_redirect(self):
        return self.status_code in (301, 302, 303, 307, 308)


class _FakeAsyncClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.requested: list[str] = []
        self.sent_headers: list[dict] = []

    async def get(self, url, headers=None):
        self.requested.append(url)
        self.sent_headers.append(headers or {})
        return self._responses.pop(0)


class _FakeSyncClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.requested: list[str] = []

    def get(self, url, headers=None):
        self.requested.append(url)
        return self._responses.pop(0)


def test_guarded_get_blocks_redirect_to_internal(monkeypatch):
    # 公開 url → 302 導向內網 → 重驗該跳即擋(不真打內網)
    _resolve(monkeypatch, {"evil.internal": "10.0.0.1"})
    client = _FakeAsyncClient(
        [_Resp(302, location="http://evil.internal/x"), _Resp(200)]
    )
    with pytest.raises(UnsafeUrlError):
        asyncio.run(guarded_get(client, "http://good.com/feed", {}))
    assert client.requested == ["http://good.com/feed"]  # 內網那跳沒真送出


def test_guarded_get_follows_safe_redirect(monkeypatch):
    _resolve(monkeypatch, {})  # 全部公開
    client = _FakeAsyncClient(
        [_Resp(302, location="https://cdn.good.com/feed"), _Resp(200)]
    )
    resp = asyncio.run(guarded_get(client, "http://good.com/feed", {"User-Agent": "x"}))
    assert resp.status_code == 200
    assert client.requested == ["http://good.com/feed", "https://cdn.good.com/feed"]
    # header allowlist:每跳只送安全 header
    assert all(h == {"User-Agent": "x"} for h in client.sent_headers)


def test_guarded_get_strips_dangerous_headers(monkeypatch):
    _resolve(monkeypatch, {})
    client = _FakeAsyncClient([_Resp(200)])
    asyncio.run(
        guarded_get(client, "http://good.com/feed", {"Authorization": "secret"})
    )
    assert client.sent_headers == [{}]  # Authorization 被丟棄


def test_guarded_get_sync_blocks_redirect_to_internal(monkeypatch):
    _resolve(monkeypatch, {"evil.internal": "169.254.169.254"})
    client = _FakeSyncClient(
        [_Resp(302, location="http://evil.internal/meta"), _Resp(200)]
    )
    with pytest.raises(UnsafeUrlError):
        guarded_get_sync(client, "http://good.com/", {})
    assert client.requested == ["http://good.com/"]
