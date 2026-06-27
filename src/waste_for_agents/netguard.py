"""出站 URL 安全閘(SSRF 防護)。

本服務會抓取 agent 提供的任意 URL(RSS feed / feed discovery)→ SSRF 面。
此閘現提供:
  ① scheme allowlist(僅 http/https,擋 file:// 等)。
  ② 解析 host → 擋 private / loopback / link-local / metadata(169.254.169.254)/
     reserved / multicast 位址(擋 cloud metadata + 內網探測)。

本閘現提供(③④ 為 THE-10 security PR 補完):
  ③ redirect 逐跳重驗(guarded_get / guarded_get_sync:follow_redirects=False,每跳
     Location 先過 check_outbound_url 才續打 → 擋 `公開→169.254.169.254` 繞道)。
  ④ 出站 header allowlist(safe_headers:只放行條件式 GET + UA/Accept,丟棄
     Host/Authorization/Cookie/Proxy-*,防 header 注入)。

⚠ 仍接受的殘餘風險(MVP,記 fast-follow):
  · DNS-rebinding:check 解析的 IP 與 httpx 實連 IP 可能不同(TOCTOU)。feed 規模小、
    polls 稀疏,風險低;上線前以「pin 解析 IP 後用該 IP 連線」收尾。
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Mapping
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

_ALLOWED_SCHEMES = {"http", "https"}

_MAX_REDIRECTS = 5

# 出站 header allowlist:只放行條件式 GET 與內容協商所需,其餘(含 Host/Authorization/
# Cookie/Proxy-*)一律丟棄,避免把維運者憑證/內部路由資訊注入到 agent 指定的 URL。
_ALLOWED_OUTBOUND_HEADERS = frozenset(
    {"user-agent", "accept", "accept-language", "if-modified-since", "if-none-match"}
)


class UnsafeUrlError(ValueError):
    """URL 未通過出站安全閘(scheme 不允許、指向內網/保留位址、或重導向次數過多)。"""


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local  # 含 169.254.0.0/16(cloud metadata)
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def check_outbound_url(url: str) -> None:
    """驗 URL 可安全出站,否則拋 UnsafeUrlError。見模組 docstring 的未完成項。"""
    parsed = urlparse(url)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise UnsafeUrlError(f"不允許的 URL scheme:{parsed.scheme!r}(僅 http/https)")
    host = parsed.hostname
    if not host:
        raise UnsafeUrlError(f"URL 無 host:{url!r}")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"無法解析 host:{host}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if _is_blocked_ip(ip):
            raise UnsafeUrlError(f"拒絕指向內網/保留位址的 URL:{host} → {ip}")


def safe_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    """過濾出站 header,只保留 allowlist 內的(case-insensitive 比對,保留原 key 大小寫)。"""
    return {
        k: v
        for k, v in (headers or {}).items()
        if k.lower() in _ALLOWED_OUTBOUND_HEADERS
    }


def _redirect_target(resp: Any, current: str) -> str | None:
    """若 resp 是重導向且有 Location,回絕對化的下一跳 url;否則 None。"""
    if not resp.is_redirect:
        return None
    loc = resp.headers.get("location")
    if not loc:
        return None
    return urljoin(current, str(loc))


async def guarded_get(
    client: httpx.AsyncClient, url: str, headers: Mapping[str, str] | None = None
) -> httpx.Response:
    """逐跳 SSRF-safe GET(async)。每跳先 check_outbound_url 再打,只送 safe_headers。

    client 須 follow_redirects=False(否則 httpx 會自動跟隨、跳過逐跳重驗)。
    """
    safe = safe_headers(headers)
    current = url
    for _ in range(_MAX_REDIRECTS + 1):
        check_outbound_url(current)
        resp = await client.get(current, headers=safe)
        nxt = _redirect_target(resp, current)
        if nxt is None:
            return resp
        current = nxt
    raise UnsafeUrlError(f"重導向次數過多(> {_MAX_REDIRECTS}):{url}")


def guarded_get_sync(
    client: httpx.Client, url: str, headers: Mapping[str, str] | None = None
) -> httpx.Response:
    """逐跳 SSRF-safe GET(sync)。語意同 guarded_get;client 須 follow_redirects=False。"""
    safe = safe_headers(headers)
    current = url
    for _ in range(_MAX_REDIRECTS + 1):
        check_outbound_url(current)
        resp = client.get(current, headers=safe)
        nxt = _redirect_target(resp, current)
        if nxt is None:
            return resp
        current = nxt
    raise UnsafeUrlError(f"重導向次數過多(> {_MAX_REDIRECTS}):{url}")
