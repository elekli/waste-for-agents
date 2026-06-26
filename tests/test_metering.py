"""計費 gate(C-stub)的 store 層:meter_and_mark 持久水位 + withheld claim。

計費輪 = 產 ≥1 added 的 run_seq。前 free_rounds 輪 deliver、超額 withhold。
計量靠持久 last_metered_run_seq,對「同批重呼叫 / 游標重放」idempotent(不變式 8)。
gate 只對「有 free-tier api_key」的 watch 生效;無 key/paid = 不計量。
"""

from waste_for_agents.server import Service
from waste_for_agents.store import Store


def _free_key_watch(s, free_rounds=2):
    kid = s.create_api_key(key_hash="h", tier="free", rate_limit=100)
    w = s.create_watch(
        "rss", {}, ["id"], [], 3600, source_kind="rolling_window", api_key_id=kid
    )
    # free_rounds 預設 2,如需其他值另設(此處用預設)
    return kid, w


def _added_round(s, w, run_seq, ids):
    events = [("added", f'["{i}"]', {"row": {"id": i}}) for i in ids]
    rows = [{"id": i} for i in ids]
    s.record_run(w.id, rows, events, None, run_seq=run_seq)


def _modified_round(s, w, run_seq, key):
    s.record_run(
        w.id, [], [("modified", key, {"changes": {"c": ["A", "B"]}})], None,
        run_seq=run_seq,
    )


def _batch(s, w):
    evs, _ = s.events_since(None)
    return [e for e in evs if e.watch_id == w.id]


def test_free_rounds_then_gate(tmp_path):
    s = Store.open(tmp_path / "m.db")
    _, w = _free_key_watch(s)  # free_rounds=2
    _added_round(s, w, 1, ["a"])
    _added_round(s, w, 2, ["b"])
    _added_round(s, w, 3, ["c"])
    decisions = s.meter_and_mark(w.id, _batch(s, w))
    assert decisions == {1: True, 2: True, 3: False}  # 輪 3 超額被 gate
    # 輪 3 的 added 事件被標 withheld
    wh = s.withheld_events(w.id)
    assert {e.row_key for e in wh} == {'["c"]'}
    # delivered_rounds 推進到 2
    assert s.get_watch(w.id).delivered_rounds == 2


def test_metering_idempotent_on_recall(tmp_path):
    # 不變式 8:同批重呼叫(游標重放)→ delivered_rounds 不變、決策相同
    s = Store.open(tmp_path / "m.db")
    _, w = _free_key_watch(s)
    _added_round(s, w, 1, ["a"])
    _added_round(s, w, 2, ["b"])
    _added_round(s, w, 3, ["c"])
    batch = _batch(s, w)
    first = s.meter_and_mark(w.id, batch)
    dr1 = s.get_watch(w.id).delivered_rounds
    second = s.meter_and_mark(w.id, batch)  # 重放同批
    assert second == first  # 決策一致
    assert s.get_watch(w.id).delivered_rounds == dr1  # 不重複計
    assert {e.row_key for e in s.withheld_events(w.id)} == {'["c"]'}  # 仍只 c withheld


def test_modified_only_round_free_no_consume(tmp_path):
    s = Store.open(tmp_path / "m.db")
    _, w = _free_key_watch(s)
    _added_round(s, w, 1, ["a"])
    _modified_round(s, w, 2, '["a"]')  # 只 modified,無 added → 不計輪
    _added_round(s, w, 3, ["b"])
    decisions = s.meter_and_mark(w.id, _batch(s, w))
    assert decisions == {1: True, 2: True, 3: True}  # 輪 3 仍在免費額度內(輪 2 沒佔)
    assert s.get_watch(w.id).delivered_rounds == 2  # 只算 added 輪(1,3)


def test_unmetered_watch_all_deliver(tmp_path):
    # 無 api_key → 不計量,全交付
    s = Store.open(tmp_path / "m.db")
    w = s.create_watch("rss", {}, ["id"], [], 3600, source_kind="rolling_window")
    _added_round(s, w, 1, ["a"])
    _added_round(s, w, 2, ["b"])
    _added_round(s, w, 3, ["c"])
    decisions = s.meter_and_mark(w.id, _batch(s, w))
    assert all(decisions.values())
    assert s.get_watch(w.id).delivered_rounds == 0  # 不動 counter
    assert s.withheld_events(w.id) == []


def test_paid_tier_all_deliver(tmp_path):
    s = Store.open(tmp_path / "m.db")
    kid = s.create_api_key(key_hash="h", tier="paid", rate_limit=100)
    w = s.create_watch(
        "rss", {}, ["id"], [], 3600, source_kind="rolling_window", api_key_id=kid
    )
    for i, rs in enumerate(["a", "b", "c", "d"], start=1):
        _added_round(s, w, i, [rs])
    decisions = s.meter_and_mark(w.id, _batch(s, w))
    assert all(decisions.values())
    assert s.withheld_events(w.id) == []


def test_claim_withheld_idempotent(tmp_path):
    s = Store.open(tmp_path / "m.db")
    _, w = _free_key_watch(s)
    for i, x in enumerate(["a", "b", "c"], start=1):
        _added_round(s, w, i, [x])
    s.meter_and_mark(w.id, _batch(s, w))  # c withheld
    claimed = s.claim_withheld(w.id)
    assert {e.row_key for e in claimed} == {'["c"]'}
    assert s.claim_withheld(w.id) == []  # 第二次補拿回空(已 claim)
    assert s.withheld_events(w.id) == []  # 旗標已清


# --- Service 層(C-stub gate + replay)---


def test_list_changes_stubs_gated_delivers_others(tmp_path):
    # 不變式 9:單一 watch 被 gate 不誤卡/誤丟其他 watch
    s = Store.open(tmp_path / "m.db")
    _, wa = _free_key_watch(s)  # A:free,輪 3 超額
    wb = s.create_watch("rss", {}, ["id"], [], 3600, source_kind="rolling_window")  # B:無 key
    _added_round(s, wa, 1, ["a1"])
    _added_round(s, wa, 2, ["a2"])
    _added_round(s, wa, 3, ["a3"])
    _added_round(s, wb, 1, ["b1"])
    svc = Service(s)
    res = svc.list_changes(None)
    # B 的事件原樣交付(不受 A 的 gate 影響)
    b_evs = [e for e in res["events"] if e["watch_id"] == wb.id]
    assert len(b_evs) == 1 and not b_evs[0].get("gated")
    # A 輪 3 被 stub 化
    a_gated = [e for e in res["events"] if e["watch_id"] == wa.id and e.get("gated")]
    assert len(a_gated) == 1 and a_gated[0]["row_key"] == '["a3"]'
    # A 輪 1、2 正常交付
    a_ok = [
        e for e in res["events"] if e["watch_id"] == wa.id and not e.get("gated")
    ]
    assert {e["row_key"] for e in a_ok} == {'["a1"]', '["a2"]'}
    # 游標前進到含 stub 在內的最大 id(不卡 B)
    assert res["cursor"] == max(e["id"] for e in res["events"])


def test_replay_watch_rejects_unpaid_preserves_withheld(tmp_path):
    # 不變式 7 邊界:未付費 replay 不得清掉 withheld(否則永久遺失)
    s = Store.open(tmp_path / "m.db")
    _, wa = _free_key_watch(s)
    for i, x in enumerate(["a1", "a2", "a3"], start=1):
        _added_round(s, wa, i, [x])
    svc = Service(s)
    svc.list_changes(None)  # 輪 3 gated
    rej = svc.replay_watch(wa.id)
    assert rej["events"] == [] and rej.get("error")  # 拒絕
    assert s.withheld_events(wa.id)  # 旗標未清(關鍵)


def test_replay_watch_paid_then_idempotent(tmp_path):
    s = Store.open(tmp_path / "m.db")
    kid, wa = _free_key_watch(s)
    for i, x in enumerate(["a1", "a2", "a3"], start=1):
        _added_round(s, wa, i, [x])
    svc = Service(s)
    svc.list_changes(None)  # 輪 3 gated
    s.set_api_key_tier(kid, "paid")  # 付費
    paid = svc.replay_watch(wa.id)
    assert {e["row_key"] for e in paid["events"]} == {'["a3"]'}  # 補拿真實事件
    assert svc.replay_watch(wa.id)["events"] == []  # 再呼叫回空(已 claim)


def test_service_create_watch_persists_metering_params(tmp_path, monkeypatch):
    import waste_for_agents.discovery as disco

    s = Store.open(tmp_path / "m.db")
    kid = s.create_api_key(key_hash="h", tier="free")
    svc = Service(s)
    # rss create 會走 discovery;mock 成「url 即 feed」避免真連網
    monkeypatch.setattr(
        disco, "_get", lambda url, headers: b'<rss version="2.0"><channel><title>t</title></channel></rss>'
    )
    out = svc.create_watch(
        "rss", {"url": "https://x.com/feed"}, ["id"], [], 3600,
        source_kind="rolling_window", api_key_id=kid,
    )
    w = s.get_watch(out["watch_id"])
    assert w.source_kind == "rolling_window" and w.api_key_id == kid


def test_changes_http_mirror_shares_gate(tmp_path):
    # dual-entry:/changes 鏡像與 MCP list_changes 共用 gate(且持久水位不重計)
    from fastapi.testclient import TestClient

    from waste_for_agents.server import build_app

    s = Store.open(tmp_path / "m.db")
    _, wa = _free_key_watch(s)
    for i, x in enumerate(["a1", "a2", "a3"], start=1):
        _added_round(s, wa, i, [x])
    app = build_app(s, tick_s=3600.0)
    with TestClient(app) as client:
        res = client.get("/changes").json()
    gated = [e for e in res["events"] if e.get("gated")]
    assert len(gated) == 1 and gated[0]["row_key"] == '["a3"]'
