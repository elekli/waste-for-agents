"""billing_polar:Polar webhook 驗簽 + 訂閱生命週期 → 計費 tier 翻轉。

簽章走 standard-webhooks 規格(Polar 即此規格),測試用官方 lib 自簽 payload。
payload 形狀依 Polar 文件手寫;sandbox E2E 時以真 payload fixture 校正(見 plan)。
"""

import base64
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from standardwebhooks.webhooks import Webhook

from waste_for_agents.billing_polar import PAID_STATUSES, handle_event
from waste_for_agents.server import build_app
from waste_for_agents.store import Store

SECRET = "whsec_" + base64.b64encode(b"test-secret-32-bytes-long-000000").decode()


def _store(tmp_path: Path) -> Store:
    return Store.open(tmp_path / "billing.db")


def _signed_headers(
    payload: str,
    *,
    msg_id: str = "msg_1",
    ts: datetime | None = None,
    secret: str = SECRET,
) -> dict[str, str]:
    ts = ts or datetime.now(timezone.utc)
    sig = Webhook(secret).sign(msg_id, ts, payload)
    return {
        "webhook-id": msg_id,
        "webhook-timestamp": str(int(ts.timestamp())),
        "webhook-signature": sig,
        "content-type": "application/json",
    }


def _sub_event(
    event_type: str = "subscription.created",
    *,
    sub_id: str = "sub_1",
    status: str = "trialing",
    email: str | None = "founder@example.com",
    customer_id: str = "cus_1",
) -> str:
    customer: dict[str, Any] = {"id": customer_id}
    if email is not None:
        customer["email"] = email
    return json.dumps(
        {
            "type": event_type,
            "data": {"id": sub_id, "status": status, "customer": customer},
        }
    )


# --- route 掛載(env-gated)---


def test_no_secret_route_absent(tmp_path: Path) -> None:
    """未設 secret → 路由不存在(404),零攻擊面。"""
    app = build_app(_store(tmp_path))
    with TestClient(app) as client:
        r = client.post("/billing/polar/webhook", content=b"{}")
    assert r.status_code == 404


def test_bad_signature_401(tmp_path: Path) -> None:
    app = build_app(_store(tmp_path), polar_webhook_secret=SECRET)
    payload = _sub_event()
    headers = _signed_headers(payload)
    headers["webhook-signature"] = "v1,invalidinvalidinvalid"
    with TestClient(app) as client:
        r = client.post(
            "/billing/polar/webhook", content=payload, headers=headers
        )
    assert r.status_code == 401


def test_missing_headers_401(tmp_path: Path) -> None:
    app = build_app(_store(tmp_path), polar_webhook_secret=SECRET)
    with TestClient(app) as client:
        r = client.post("/billing/polar/webhook", content=_sub_event())
    assert r.status_code == 401


def test_stale_timestamp_401(tmp_path: Path) -> None:
    """standard-webhooks 預設 5 分鐘容忍;1 小時前的簽章要拒。"""
    app = build_app(_store(tmp_path), polar_webhook_secret=SECRET)
    payload = _sub_event()
    old = datetime.now(timezone.utc) - timedelta(hours=1)
    with TestClient(app) as client:
        r = client.post(
            "/billing/polar/webhook",
            content=payload,
            headers=_signed_headers(payload, ts=old),
        )
    assert r.status_code == 401


def test_valid_event_202_and_recorded(tmp_path: Path) -> None:
    store = _store(tmp_path)
    app = build_app(store, polar_webhook_secret=SECRET)
    payload = _sub_event("subscription.created", status="trialing")
    with TestClient(app) as client:
        r = client.post(
            "/billing/polar/webhook",
            content=payload,
            headers=_signed_headers(payload),
        )
    assert r.status_code == 202
    sub = store.get_billing_subscription("sub_1")
    assert sub is not None
    assert sub.status == "trialing"
    assert sub.customer_email == "founder@example.com"
    assert sub.api_key_id is None


def test_unknown_event_type_202(tmp_path: Path) -> None:
    """不認識的事件寬容回 202,不觸發 Polar 重試風暴。"""
    store = _store(tmp_path)
    app = build_app(store, polar_webhook_secret=SECRET)
    payload = json.dumps({"type": "order.paid", "data": {"id": "ord_1"}})
    with TestClient(app) as client:
        r = client.post(
            "/billing/polar/webhook",
            content=payload,
            headers=_signed_headers(payload),
        )
    assert r.status_code == 202
    assert store.get_billing_subscription("ord_1") is None


# --- 事件流 → tier 翻轉(handle_event 純函式層)---


def test_paid_statuses_shape() -> None:
    """trialing 算 paid:創始名單 = 卡號在檔即享全額交付,trial 期不是次級體驗。"""
    assert "trialing" in PAID_STATUSES
    assert "active" in PAID_STATUSES
    assert "revoked" not in PAID_STATUSES


def test_bind_then_active_flips_paid(tmp_path: Path) -> None:
    store = _store(tmp_path)
    kid = store.create_api_key("hash1", tier="free")
    handle_event(store, json.loads(_sub_event("subscription.created")))
    assert store.bind_subscription_key("sub_1", kid) is True
    # 綁定當下即按現況(trialing ∈ paid statuses)翻 tier
    assert store.get_api_key_tier(kid) == "paid"
    handle_event(
        store, json.loads(_sub_event("subscription.active", status="active"))
    )
    assert store.get_api_key_tier(kid) == "paid"
    sub = store.get_billing_subscription("sub_1")
    assert sub is not None and sub.status == "active"


def test_revoked_flips_free(tmp_path: Path) -> None:
    """sandbox 實測:revoked 事件的 data.status 是 'canceled' 不是 'revoked'——
    分流必須看 event type;若誤看 status 會走進 canceled 不降級分支,永不撤銷。"""
    store = _store(tmp_path)
    kid = store.create_api_key("hash1", tier="free")
    handle_event(store, json.loads(_sub_event("subscription.created")))
    store.bind_subscription_key("sub_1", kid)
    handle_event(
        store, json.loads(_sub_event("subscription.revoked", status="canceled"))
    )
    assert store.get_api_key_tier(kid) == "free"
    sub = store.get_billing_subscription("sub_1")
    assert sub is not None and sub.status == "revoked"


def test_canceled_keeps_tier(tmp_path: Path) -> None:
    """canceled = 本期末不續,期內權益照舊;revoked 才降級。"""
    store = _store(tmp_path)
    kid = store.create_api_key("hash1", tier="free")
    handle_event(store, json.loads(_sub_event("subscription.created")))
    store.bind_subscription_key("sub_1", kid)
    handle_event(
        store, json.loads(_sub_event("subscription.canceled", status="canceled"))
    )
    assert store.get_api_key_tier(kid) == "paid"
    sub = store.get_billing_subscription("sub_1")
    assert sub is not None and sub.status == "canceled"


def test_uncanceled_restores(tmp_path: Path) -> None:
    store = _store(tmp_path)
    kid = store.create_api_key("hash1", tier="free")
    handle_event(store, json.loads(_sub_event("subscription.created")))
    store.bind_subscription_key("sub_1", kid)
    handle_event(
        store, json.loads(_sub_event("subscription.canceled", status="canceled"))
    )
    handle_event(
        store, json.loads(_sub_event("subscription.uncanceled", status="active"))
    )
    assert store.get_api_key_tier(kid) == "paid"


def test_replay_idempotent(tmp_path: Path) -> None:
    """webhook at-least-once:同事件重放結果一致。"""
    store = _store(tmp_path)
    kid = store.create_api_key("hash1", tier="free")
    ev = json.loads(_sub_event("subscription.created"))
    handle_event(store, ev)
    handle_event(store, ev)
    store.bind_subscription_key("sub_1", kid)
    rv = json.loads(_sub_event("subscription.revoked", status="canceled"))
    handle_event(store, rv)
    handle_event(store, rv)
    assert store.get_api_key_tier(kid) == "free"
    subs = store.list_billing_subscriptions()
    assert len(subs) == 1


def test_bind_missing_subscription_false(tmp_path: Path) -> None:
    store = _store(tmp_path)
    kid = store.create_api_key("hash1", tier="free")
    assert store.bind_subscription_key("sub_nope", kid) is False
    assert store.get_api_key_tier(kid) == "free"


def test_bind_missing_key_false(tmp_path: Path) -> None:
    """綁不存在的 api_key_id 要拒絕,不寫壞名單。"""
    store = _store(tmp_path)
    handle_event(store, json.loads(_sub_event("subscription.created")))
    assert store.bind_subscription_key("sub_1", "kid_nope") is False
    sub = store.get_billing_subscription("sub_1")
    assert sub is not None and sub.api_key_id is None


def test_rebind_different_key_refused(tmp_path: Path) -> None:
    """已綁訂閱拒絕改綁另一把 key(危險操作走人工);同 key 重綁冪等。"""
    store = _store(tmp_path)
    kid1 = store.create_api_key("hash1", tier="free")
    kid2 = store.create_api_key("hash2", tier="free")
    handle_event(store, json.loads(_sub_event("subscription.created")))
    assert store.bind_subscription_key("sub_1", kid1) is True
    assert store.bind_subscription_key("sub_1", kid2) is False
    assert store.bind_subscription_key("sub_1", kid1) is True  # 冪等
    sub = store.get_billing_subscription("sub_1")
    assert sub is not None and sub.api_key_id == kid1
    assert store.get_api_key_tier(kid2) == "free"


def test_bind_revoked_subscription_keeps_tier(tmp_path: Path) -> None:
    """綁定終止態訂閱不動 tier(不升也不降)。"""
    store = _store(tmp_path)
    kid = store.create_api_key("hash1", tier="free")
    handle_event(store, json.loads(_sub_event("subscription.created")))
    handle_event(
        store, json.loads(_sub_event("subscription.revoked", status="canceled"))
    )
    assert store.bind_subscription_key("sub_1", kid) is True
    assert store.get_api_key_tier(kid) == "free"


def test_past_due_freezes_tier(tmp_path: Path) -> None:
    """暫時性繳費狀態(past_due)不降級——降級唯一路徑是 revoked 事件。"""
    store = _store(tmp_path)
    kid = store.create_api_key("hash1", tier="free")
    handle_event(store, json.loads(_sub_event("subscription.created")))
    store.bind_subscription_key("sub_1", kid)
    assert store.get_api_key_tier(kid) == "paid"
    handle_event(
        store, json.loads(_sub_event("subscription.updated", status="past_due"))
    )
    assert store.get_api_key_tier(kid) == "paid"


def test_malformed_payloads_tolerated(tmp_path: Path) -> None:
    """缺 data / 缺 id / 缺 status 一律「忽略」不炸(webhook 寬容原則)。"""
    store = _store(tmp_path)
    assert handle_event(store, {"type": "subscription.created"}).startswith(
        "ignored:"
    )
    assert handle_event(
        store, {"type": "subscription.created", "data": {"status": "trialing"}}
    ).startswith("ignored:")
    assert handle_event(
        store, {"type": "subscription.created", "data": {"id": "sub_x"}}
    ).startswith("ignored:")
    assert store.list_billing_subscriptions() == []


def test_action_string_no_internal_key_id(tmp_path: Path) -> None:
    """回應的 action 不外洩內部 api_key_id。"""
    store = _store(tmp_path)
    kid = store.create_api_key("hash1", tier="free")
    handle_event(store, json.loads(_sub_event("subscription.created")))
    store.bind_subscription_key("sub_1", kid)
    action = handle_event(
        store, json.loads(_sub_event("subscription.active", status="active"))
    )
    assert kid not in action
    assert action == "tier-set:sub_1:paid"


def test_upsert_preserves_binding(tmp_path: Path) -> None:
    """後續事件 upsert 不能沖掉已綁的 api_key_id。"""
    store = _store(tmp_path)
    kid = store.create_api_key("hash1", tier="free")
    handle_event(store, json.loads(_sub_event("subscription.created")))
    store.bind_subscription_key("sub_1", kid)
    handle_event(
        store, json.loads(_sub_event("subscription.updated", status="active"))
    )
    sub = store.get_billing_subscription("sub_1")
    assert sub is not None and sub.api_key_id == kid


def test_event_missing_email_tolerated(tmp_path: Path) -> None:
    """payload 形狀防禦:缺 email 不炸,記 None。"""
    store = _store(tmp_path)
    handle_event(store, json.loads(_sub_event(email=None)))
    sub = store.get_billing_subscription("sub_1")
    assert sub is not None and sub.customer_email is None


# --- 真實 payload fixture(Polar sandbox 2026-07-11 抓取,已匿名化)---

FIXTURES = Path(__file__).parent / "fixtures" / "polar"


def test_real_payload_lifecycle(tmp_path: Path) -> None:
    """用 Polar 真發出的 payload 走完整生命週期:created(trialing)→ bind →
    active → canceled(期內不降)→ revoked(降 free)。防手寫測試與現實脫節。"""
    store = _store(tmp_path)
    kid = store.create_api_key("hash1", tier="free")

    created = json.loads((FIXTURES / "subscription.created.json").read_text())
    sub_id = created["data"]["id"]
    handle_event(store, created)
    sub = store.get_billing_subscription(sub_id)
    assert sub is not None and sub.status == "trialing"
    assert sub.customer_email == "founder@example.com"

    assert store.bind_subscription_key(sub_id, kid) is True
    assert store.get_api_key_tier(kid) == "paid"

    handle_event(
        store, json.loads((FIXTURES / "subscription.active.json").read_text())
    )
    assert store.get_api_key_tier(kid) == "paid"

    handle_event(
        store, json.loads((FIXTURES / "subscription.canceled.json").read_text())
    )
    assert store.get_api_key_tier(kid) == "paid"  # 期內不降級

    handle_event(
        store, json.loads((FIXTURES / "subscription.revoked.json").read_text())
    )
    assert store.get_api_key_tier(kid) == "free"
    sub = store.get_billing_subscription(sub_id)
    assert sub is not None and sub.status == "revoked"
