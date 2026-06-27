"""Service 層 auth 邏輯:issue_key 自助發放、ownership(delete/replay)、
按呼叫者 scope(list_watches / list_changes)。

privacy 核心:任何持 key 者只能看到/操作自己歸戶的 watch——堵住 identity↔watch
洩漏(別人知道 watch_id 也讀不到、刪不掉、replay 不到)。caller_key_id=None =
匿名/本地,只見「無歸戶(keyless)」watch。
"""

from waste_for_agents.auth import hash_key, verify
from waste_for_agents.server import Service
from waste_for_agents.store import Store


def _key(s, tier="free"):
    from waste_for_agents.auth import generate_key

    k = generate_key()
    return s.create_api_key(key_hash=hash_key(k), tier=tier), k


def _watch(s, api_key_id=None):
    return s.create_watch(
        "rss", {}, ["id"], [], 3600, source_kind="rolling_window", api_key_id=api_key_id
    )


def _added_round(s, w, run_seq, ids):
    events = [("added", f'["{i}"]', {"row": {"id": i}}) for i in ids]
    s.record_run(w.id, [{"id": i} for i in ids], events, None, run_seq=run_seq)


def test_issue_key_returns_plaintext_and_stores_hash(tmp_path):
    s = Store.open(tmp_path / "a.db")
    out = Service(s).issue_key()
    assert out["api_key"].startswith("wfa_")
    assert out["api_key_id"]
    rec = verify(s, out["api_key"])  # 明文可驗回同一 key
    assert rec is not None and rec.id == out["api_key_id"] and rec.tier == "free"


def test_list_watches_scoped_to_caller(tmp_path):
    s = Store.open(tmp_path / "a.db")
    svc = Service(s)
    k1, _ = _key(s)
    k2, _ = _key(s)
    w1 = _watch(s, api_key_id=k1)
    w2 = _watch(s, api_key_id=k2)
    w3 = _watch(s, api_key_id=None)  # 無歸戶
    assert {w["id"] for w in svc.list_watches(caller_key_id=k1)["watches"]} == {w1.id}
    assert {w["id"] for w in svc.list_watches(caller_key_id=k2)["watches"]} == {w2.id}
    assert {w["id"] for w in svc.list_watches(caller_key_id=None)["watches"]} == {w3.id}


def test_delete_watch_requires_ownership(tmp_path):
    s = Store.open(tmp_path / "a.db")
    svc = Service(s)
    k1, _ = _key(s)
    k2, _ = _key(s)
    w = _watch(s, api_key_id=k1)
    rej = svc.delete_watch(w.id, caller_key_id=k2)  # 別人刪不掉
    assert rej["deleted"] is False and rej.get("error")
    assert s.get_watch(w.id) is not None  # 仍在
    ok = svc.delete_watch(w.id, caller_key_id=k1)  # 自己可刪
    assert ok["deleted"] is True


def test_replay_ownership_rejects_nonowner_preserves_withheld(tmp_path):
    s = Store.open(tmp_path / "a.db")
    svc = Service(s)
    k1, _ = _key(s)
    k2, _ = _key(s)
    wa = _watch(s, api_key_id=k1)  # free_rounds=2
    for i, x in enumerate(["a1", "a2", "a3"], start=1):
        _added_round(s, wa, i, [x])
    svc.list_changes(None, caller_key_id=k1)  # 輪 3 gated
    s.set_api_key_tier(k1, "paid")
    rej = svc.replay_watch(wa.id, caller_key_id=k2)  # 非擁有者(即便已 paid)
    assert rej["events"] == [] and rej.get("error")
    assert s.withheld_events(wa.id)  # 旗標未清(關鍵:沒被別人觸發 claim)
    ok = svc.replay_watch(wa.id, caller_key_id=k1)  # 擁有者
    assert {e["row_key"] for e in ok["events"]} == {'["a3"]'}


def test_list_changes_scoped_to_caller(tmp_path):
    s = Store.open(tmp_path / "a.db")
    svc = Service(s)
    k1, _ = _key(s)
    k2, _ = _key(s)
    wa = _watch(s, api_key_id=k1)
    wb = _watch(s, api_key_id=k2)
    _added_round(s, wa, 1, ["a1"])
    _added_round(s, wb, 1, ["b1"])
    res = svc.list_changes(None, caller_key_id=k1)
    assert {e["watch_id"] for e in res["events"]} == {wa.id}  # 只見自己的
    # 游標推進到全域高水位(不重掃他人事件)
    all_evs, gmax = s.events_since(None)
    assert res["cursor"] == gmax


def test_list_changes_gating_within_caller_partition(tmp_path):
    # 不變式 9 在「同一呼叫者多 watch」下:一個 watch 超額被 gate 不卡同呼叫者另一 watch
    s = Store.open(tmp_path / "a.db")
    svc = Service(s)
    k1, _ = _key(s)
    wa = _watch(s, api_key_id=k1)  # 將超額
    wb = _watch(s, api_key_id=k1)  # 同呼叫者、新鮮
    for i, x in enumerate(["a1", "a2", "a3"], start=1):
        _added_round(s, wa, i, [x])  # wa 輪 3 超 free_rounds=2
    _added_round(s, wb, 1, ["b1"])
    res = svc.list_changes(None, caller_key_id=k1)
    gated = [e for e in res["events"] if e.get("gated")]
    assert len(gated) == 1 and gated[0]["row_key"] == '["a3"]'  # wa 超額 stub
    ok = [e for e in res["events"] if not e.get("gated")]
    assert '["b1"]' in {e["row_key"] for e in ok}  # wb 照常交付
