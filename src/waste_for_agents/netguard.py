"""出站 URL 安全閘(SSRF 防護)。

本服務會抓取 agent 提供的任意 URL(RSS feed / feed discovery)→ SSRF 面。
此閘現提供:
  ① scheme allowlist(僅 http/https,擋 file:// 等)。
  ② 解析 host → 擋 private / loopback / link-local / metadata(169.254.169.254)/
     reserved / multicast 位址(擋 cloud metadata + 內網探測)。

⚠ 仍未完成(security PR / Chunk 5,在那之前只供受信任 client / MVP):
  · redirect 逐跳重驗(目前 fetch 用 follow_redirects,redirect 到內網可繞過此閘)。
  · DNS-rebinding(此處解析的 IP 與 httpx 實際連線的 IP 可能不同)。
  · 出站 header allowlist(query.headers 目前原樣透傳,可注入 Host/Authorization)。
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

_ALLOWED_SCHEMES = {"http", "https"}


class UnsafeUrlError(ValueError):
    """URL 未通過出站安全閘(scheme 不允許或指向內網/保留位址)。"""


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
