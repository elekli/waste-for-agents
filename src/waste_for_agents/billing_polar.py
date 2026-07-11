"""Polar.sh webhook 接收:standard-webhooks 驗簽 + 訂閱生命週期 → 計費 tier 翻轉。

範圍(見 docs/superpowers/plans/2026-07-11-founding-tier-polar.md):
- 只記名單 + 翻已綁定 key 的 tier;**不自動發 key**(key 明文只回一次、store 只存
  hash——webhook 時點無人接收明文,自動發 = 得存明文,違反 auth 設計)。
- 綁定走 CLI `bind-subscription`(Phase 0 人工回信發 key 後綁)。

事件語意(釘死,防止未來誤改):
- trialing ∈ PAID_STATUSES:創始名單 = 卡號在檔即享全額交付,trial 期不是次級體驗。
- **canceled/revoked 分流看 event type,不看 data.status**——sandbox 實測
  (2026-07-11,fixture 在 tests/fixtures/polar/)`subscription.revoked` 的
  data.status 是 "canceled" 不是 "revoked";status 是 Polar 內部表示,
  event type 才是穩定語意。revoked 事件 → 權益終止 → free;canceled 事件 →
  本期末不續、期內權益照舊 → tier 不動。
- **tier 只升不自動降(降級唯一路徑 = revoked 事件)**:past_due/unpaid 等
  暫時性繳費狀態不動 tier——「卡號在檔」承諾下,一次扣款失敗不該立刻
  降級再回升震盪;Polar 催繳流程失敗的終點就是 revoked,由它收尾。
- 未知事件型別回「忽略」而非失敗:webhook 端點對上游新增事件必須寬容,
  否則 Polar 重試風暴;簽章錯誤則絕不寬容(由 verify_webhook 擋)。
- 亂序投遞 = 接受的風險:不做 timestamp 水位(Polar 重試屬罕見,終態事件
  revoked 冪等且單向,亂序最壞是短暫多交付,不會多收費)。
"""

from __future__ import annotations

import logging
from typing import Any

from standardwebhooks.webhooks import Webhook, WebhookVerificationError

logger = logging.getLogger(__name__)

__all__ = [
    "PAID_STATUSES",
    "SUBSCRIPTION_EVENTS",
    "WebhookVerificationError",
    "handle_event",
    "verify_webhook",
]

# 訂閱狀態 → 該享 paid tier 的集合(Polar SubscriptionStatus 子集)
PAID_STATUSES = frozenset({"trialing", "active"})

# 我們處理的事件型別;其餘一律忽略(寬容)
SUBSCRIPTION_EVENTS = frozenset(
    {
        "subscription.created",
        "subscription.updated",
        "subscription.active",
        "subscription.canceled",
        "subscription.uncanceled",
        "subscription.revoked",
    }
)


def verify_webhook(
    secret: str, headers: dict[str, str], raw_body: bytes
) -> dict[str, Any]:
    """驗 standard-webhooks 簽章,通過回 parse 後的 event dict。

    對 **raw bytes** 驗(先 parse 再 re-serialize 會炸簽)。失敗 raise
    WebhookVerificationError(呼叫端轉 401)。secret 收 Polar 後台原樣
    (lib 自行處理 `whsec_` 前綴與 base64)。
    """
    try:
        parsed: Any = Webhook(secret).verify(raw_body, headers)
    except WebhookVerificationError:
        raise
    except ValueError as exc:
        # lib 對壞 base64 簽章漏丟 binascii.Error(ValueError 子類)而非包裝後
        # 的驗證錯誤;收攏成同一個具名錯誤,呼叫端一律轉 401 而非 500。
        raise WebhookVerificationError(str(exc)) from exc
    if not isinstance(parsed, dict):
        raise WebhookVerificationError("payload is not a JSON object")
    return parsed


def handle_event(store: Any, event: dict[str, Any]) -> str:
    """處理已驗簽的事件,回一個動作字串(給 route 回應/log 用)。

    冪等:upsert 以 subscription_id 為 PK;tier 翻轉 set 同值無害;
    at-least-once 重送結果一致。store 型別鬆綁(Any)避免循環 import,
    實際依賴 Store 的 upsert_billing_subscription / get_billing_subscription /
    set_api_key_tier 三個方法。
    """
    event_type = event.get("type")
    if event_type not in SUBSCRIPTION_EVENTS:
        logger.info("polar webhook: ignored event type %s", event_type)
        return f"ignored:{event_type}"

    data = event.get("data")
    if not isinstance(data, dict):
        return "ignored:malformed-data"
    sub_id = data.get("id")
    status = data.get("status")
    if not isinstance(sub_id, str) or not isinstance(status, str):
        return "ignored:missing-id-or-status"

    customer = data.get("customer")
    customer_id: str | None = None
    customer_email: str | None = None
    if isinstance(customer, dict):
        cid = customer.get("id")
        customer_id = cid if isinstance(cid, str) else None
        mail = customer.get("email")
        customer_email = mail if isinstance(mail, str) else None

    # 名單記錄的 status:revoked 事件記我們自己的語意標記 "revoked"
    # (實測其 data.status 是 "canceled",直接記會讓名單看不出誰已終止)。
    effective_status = (
        "revoked" if event_type == "subscription.revoked" else status
    )
    store.upsert_billing_subscription(
        sub_id,
        customer_id=customer_id,
        customer_email=customer_email,
        status=effective_status,
    )

    # 回應的 action 字串只帶 subscription_id(呼叫者本來就有),不帶內部
    # api_key_id(不外洩內部識別)。
    sub = store.get_billing_subscription(sub_id)
    if sub is not None and sub.api_key_id is not None:
        if event_type == "subscription.revoked":
            store.set_api_key_tier(sub.api_key_id, "free")
            logger.info("polar webhook: %s revoked -> tier free", sub_id)
            return f"tier-set:{sub_id}:free"
        if event_type == "subscription.canceled":
            # 本期末不續:期內權益照舊,tier 不動
            return f"recorded:{sub_id}:{effective_status}"
        if status in PAID_STATUSES:
            store.set_api_key_tier(sub.api_key_id, "paid")
            logger.info("polar webhook: %s %s -> tier paid", sub_id, status)
            return f"tier-set:{sub_id}:paid"
        # 暫時性狀態(past_due 等):tier 凍結,只有 revoked 能降(見模組 docstring)
        return f"recorded:{sub_id}:{effective_status}"
    return f"recorded:{sub_id}:{effective_status}"
