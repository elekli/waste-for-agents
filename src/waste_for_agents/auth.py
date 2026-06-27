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
import time
from collections import defaultdict, deque
from collections.abc import Callable

from .store import ApiKey, Store

_KEY_PREFIX = "wfa_"


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

    def allow(self, key_id: str, limit: int) -> bool:
        if limit <= 0:
            return True
        now = self._now()
        cutoff = now - self._window_s
        q = self._hits[key_id]
        while q and q[0] <= cutoff:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True
