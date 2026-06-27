"""API key 認證:生成 / 雜湊(只存 hash)/ 驗證 / rate limit。

威脅模型(THE-10):補裸端點濫用缺口。key 是高熵隨機 token(非使用者密碼),
故用 sha256 快雜湊即足夠——攻擊者要逆推等同猜 256-bit 秘密;不需慢 KDF。
只存 hash:db 外洩也拿不到原文 key。

privacy:key 走 Bearer header 傳遞(不進 MCP tool-call 參數記錄),避免把
「身份(key)」與「watch 內容」記在同一處 → 護住 identity↔watch 連結。
"""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable, Mapping

from .store import ApiKey, Store

_KEY_PREFIX = "wfa_"

# 自助發放的 free key 預設每分鐘上限(RateLimiter 預設窗 60s)。
DEFAULT_FREE_RATE_LIMIT = 60


class AuthError(Exception):
    """認證失敗:缺 key / key 無效 / rate limit 超限。"""


def generate_key() -> str:
    """產一把高熵隨機 key(帶 wfa_ 前綴,便於辨識與 log 過濾)。"""
    return _KEY_PREFIX + secrets.token_urlsafe(32)


def hash_key(key: str) -> str:
    """key → sha256 hex。決定性、不可逆;只存此 hash。"""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def verify(store: Store, presented_key: str) -> ApiKey | None:
    """驗呈現的 key,回對應 ApiKey(含 tier/rate_limit)或 None。空字串直接拒。"""
    if not presented_key:
        return None
    return store.get_api_key_by_hash(hash_key(presented_key))


def bearer_key(headers: Mapping[str, str]) -> str | None:
    """從 Authorization header 取 Bearer token。scheme 大小寫不敏感;格式不符回 None。"""
    raw = headers.get("authorization")
    if not raw:
        return None
    parts = raw.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


def authenticate(
    store: Store, rate_limiter: RateLimiter, presented_key: str | None
) -> ApiKey:
    """驗 key + 套 rate limit,回 ApiKey;任一關卡失敗拋 AuthError(不洩漏細節)。"""
    rec = verify(store, presented_key or "")
    if rec is None:
        raise AuthError("無效或缺少 API key")
    if not rate_limiter.allow(rec.id, rec.rate_limit):
        raise AuthError("rate limit 超限,請稍後再試")
    return rec


class RateLimiter:
    """記憶體滑動窗 rate limiter(單實例 MVP;多實例需移到 store/Redis)。

    per-key 保留窗內呼叫時戳;`allow` 計窗內次數,未達 limit 才放行並記一次。
    limit <= 0 視為不限(付費/受信任 key)。時鐘可注入以利測試。
    """

    def __init__(
        self, window_s: float = 60.0, now: Callable[[], float] = time.monotonic
    ) -> None:
        self._window_s = window_s
        self._now = now
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()  # tools/route 跨 threadpool 緒共用 → 序列化 deque 存取

    def allow(self, key_id: str, limit: int) -> bool:
        if limit <= 0:
            return True
        with self._lock:  # 修 deque 的 TOCTOU race(review Important)
            now = self._now()
            cutoff = now - self._window_s
            q = self._hits[key_id]
            while q and q[0] <= cutoff:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True
        # 註:閒置 key 的空 deque 不主動回收(_hits 隨 distinct key 成長)——
        # 與 issue_key 無限流同源,見 TODOS;MVP 預設 bind loopback,可接受。
