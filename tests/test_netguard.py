"""出站 URL SSRF 閘:scheme + 內網/metadata 位址阻擋。"""

import socket

import pytest

from waste_for_agents.netguard import UnsafeUrlError, check_outbound_url


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
