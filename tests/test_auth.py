"""API key 認證:生成 / 雜湊(只存 hash)/ 驗證 / rate limit。

key 是高熵隨機 token(非密碼)→ 用 sha256 快雜湊即可,只存 hash 不可逆。
verify 以 hash 查 store 對應 api_key。RateLimiter 記憶體滑動窗,讀 per-key limit。
"""

import pytest

from waste_for_agents.auth import (
    AuthError,
    RateLimiter,
    authenticate,
    bearer_key,
    generate_key,
    hash_key,
    verify,
)
from waste_for_agents.store import Store


def test_generate_key_unique_and_prefixed():
    a, b = generate_key(), generate_key()
    assert a != b  # 高熵隨機
    assert a.startswith("wfa_") and b.startswith("wfa_")
    assert len(a) > 20  # 非空 + 足夠長


def test_hash_key_deterministic_irreversible():
    key = generate_key()
    h = hash_key(key)
    assert h == hash_key(key)  # 決定性
    assert h != key  # 不是原文
    assert key not in h  # 不含原文
    assert hash_key("other") != h  # 不同 key 不同 hash


def test_verify_roundtrip(tmp_path):
    s = Store.open(tmp_path / "a.db")
    key = generate_key()
    kid = s.create_api_key(key_hash=hash_key(key), tier="free", rate_limit=60)
    rec = verify(s, key)
    assert rec is not None
    assert rec.id == kid and rec.tier == "free" and rec.rate_limit == 60


def test_verify_rejects_unknown_and_empty(tmp_path):
    s = Store.open(tmp_path / "a.db")
    s.create_api_key(key_hash=hash_key(generate_key()), tier="free")
    assert verify(s, generate_key()) is None  # 未註冊的 key
    assert verify(s, "") is None  # 空字串


def test_rate_limiter_allows_under_blocks_over():
    t = [1000.0]
    rl = RateLimiter(window_s=60.0, now=lambda: t[0])
    # limit=3:前 3 次放行,第 4 次擋
    assert [rl.allow("k", 3) for _ in range(4)] == [True, True, True, False]


def test_rate_limiter_window_slides():
    t = [1000.0]
    rl = RateLimiter(window_s=60.0, now=lambda: t[0])
    assert all(rl.allow("k", 2) for _ in range(2))  # 用完額度
    assert rl.allow("k", 2) is False  # 窗內擋
    t[0] += 61.0  # 滑出窗
    assert rl.allow("k", 2) is True  # 重新放行


def test_rate_limiter_zero_limit_unlimited():
    rl = RateLimiter(window_s=60.0, now=lambda: 0.0)
    assert all(rl.allow("k", 0) for _ in range(100))  # 0 = 不限


def test_rate_limiter_isolates_keys():
    t = [0.0]
    rl = RateLimiter(window_s=60.0, now=lambda: t[0])
    assert rl.allow("k1", 1) is True
    assert rl.allow("k1", 1) is False  # k1 用完
    assert rl.allow("k2", 1) is True  # k2 不受影響


# --- Bearer header 解析 ---


def test_bearer_key_parses_authorization_header():
    assert bearer_key({"authorization": "Bearer abc123"}) == "abc123"
    assert bearer_key({"authorization": "bearer abc123"}) == "abc123"  # scheme 大小寫不敏感
    assert bearer_key({"authorization": "Bearer   sp aced "}) == "sp aced"  # 去前後空白、保留中間


def test_bearer_key_missing_or_malformed_returns_none():
    assert bearer_key({}) is None  # 無 header
    assert bearer_key({"authorization": ""}) is None
    assert bearer_key({"authorization": "abc123"}) is None  # 無 Bearer 前綴
    assert bearer_key({"authorization": "Basic xyz"}) is None  # 非 Bearer scheme
    assert bearer_key({"authorization": "Bearer"}) is None  # 只有 scheme 無 token


# --- authenticate(verify + rate limit,失敗拋 AuthError)---


def test_authenticate_valid_key(tmp_path):
    s = Store.open(tmp_path / "a.db")
    key = generate_key()
    kid = s.create_api_key(key_hash=hash_key(key), tier="free", rate_limit=60)
    rl = RateLimiter(now=lambda: 0.0)
    rec = authenticate(s, rl, key)
    assert rec.id == kid


def test_authenticate_invalid_key_raises(tmp_path):
    s = Store.open(tmp_path / "a.db")
    rl = RateLimiter(now=lambda: 0.0)
    with pytest.raises(AuthError):
        authenticate(s, rl, generate_key())  # 未註冊
    with pytest.raises(AuthError):
        authenticate(s, rl, None)  # 缺 key


def test_authenticate_rate_limited_raises(tmp_path):
    s = Store.open(tmp_path / "a.db")
    key = generate_key()
    s.create_api_key(key_hash=hash_key(key), tier="free", rate_limit=1)
    rl = RateLimiter(window_s=60.0, now=lambda: 0.0)
    authenticate(s, rl, key)  # 第 1 次 OK
    with pytest.raises(AuthError):
        authenticate(s, rl, key)  # 第 2 次超限
