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


def test_store_scoped_watch_queries_handle_null(tmp_path):
    # store 層 WHERE api_key_id(None 須用 IS NULL,否則 = NULL 永不命中)
    s = Store.open(tmp_path / "a.db")
    k1, _ = _key(s)
    w1 = _watch(s, api_key_id=k1)
    wfree = _watch(s, api_key_id=None)
    assert s.list_watch_ids_by_api_key(k1) == {w1.id}
    assert s.list_watch_ids_by_api_key(None) == {wfree.id}  # NULL 分區
    assert {w.id for w in s.list_watches_by_api_key(k1)} == {w1.id}
    assert {w.id for w in s.list_watches_by_api_key(None)} == {wfree.id}


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
    # 與「不存在」回應相同(不洩漏 watch 是否存在)
    assert rej == {"deleted": False}
    assert s.get_watch(w.id) is not None  # 仍在
    ok = svc.delete_watch(w.id, caller_key_id=k1)  # 自己可刪
    assert ok["deleted"] is True


def test_replay_delete_dont_leak_existence(tmp_path):
    # Critical(review):非擁有者與不存在的 watch_id 必須得到「位元級相同」回應
    s = Store.open(tmp_path / "a.db")
    svc = Service(s)
    k1, _ = _key(s)
    k2, _ = _key(s)
    w = _watch(s, api_key_id=k1)  # 屬於 k1
    # replay:不存在 vs 別人的 → 同回應
    assert svc.replay_watch("nonexistent", caller_key_id=k2) == svc.replay_watch(
        w.id, caller_key_id=k2
    )
    # delete:不存在 vs 別人的 → 同回應
    assert svc.delete_watch("nonexistent", caller_key_id=k2) == svc.delete_watch(
        w.id, caller_key_id=k2
    )


def test_list_changes_anonymous_sees_only_unowned(tmp_path):
    # Important(review):caller_key_id=None 只見無歸戶 watch,不洩漏任何 owned 事件
    s = Store.open(tmp_path / "a.db")
    svc = Service(s)
    k1, _ = _key(s)
    wowned = _watch(s, api_key_id=k1)
    wfree = _watch(s, api_key_id=None)  # 無歸戶
    _added_round(s, wowned, 1, ["o1"])
    _added_round(s, wfree, 1, ["f1"])
    res = svc.list_changes(None, caller_key_id=None, allow_digest=True)
    assert {e["watch_id"] for e in res["events"]} == {wfree.id}  # 只見無歸戶
    assert all(e["watch_id"] != wowned.id for e in res["events"])  # 零 owned 洩漏


def test_replay_ownership_rejects_nonowner_preserves_withheld(tmp_path):
    s = Store.open(tmp_path / "a.db")
    svc = Service(s)
    k1, _ = _key(s)
    k2, _ = _key(s)
    wa = _watch(s, api_key_id=k1)  # free_rounds=2
    for i, x in enumerate(["a1", "a2", "a3"], start=1):
        _added_round(s, wa, i, [x])
    svc.list_changes(None, caller_key_id=k1, allow_digest=True)  # 輪 3 gated
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
    res = svc.list_changes(None, caller_key_id=k1, allow_digest=True)
    assert {e["watch_id"] for e in res["events"]} == {wa.id}  # 只見自己的
    # 游標推進到全域高水位(不重掃他人事件)
    all_evs, gmax = s.events_since(None)
    assert res["cursor"] == gmax


def test_list_changes_requires_watch_id_on_mcp_face(tmp_path):
    # MCP 面(allow_digest 預設 False):省略 watch_id → error,不靜默給混流
    s = Store.open(tmp_path / "a.db")
    svc = Service(s)
    k1, _ = _key(s)
    _watch(s, api_key_id=k1)
    res = svc.list_changes(None, caller_key_id=k1)
    assert res["error"] == "watch_id required"
    assert res["events"] == []
    assert res["cursor"] == 0  # _norm_cursor(None)


def test_list_changes_per_watch_returns_only_that_watch(tmp_path):
    s = Store.open(tmp_path / "a.db")
    svc = Service(s)
    k1, _ = _key(s)
    wa = _watch(s, api_key_id=k1)
    wb = _watch(s, api_key_id=k1)
    _added_round(s, wa, 1, ["a1"])
    _added_round(s, wb, 1, ["b1"])
    res = svc.list_changes(None, caller_key_id=k1, watch_id=wa.id)
    assert {e["watch_id"] for e in res["events"]} == {wa.id}  # 只 wa,不含 wb
    assert {e["row_key"] for e in res["events"]} == {'["a1"]'}


def test_list_changes_per_watch_ownership_reject_bit_identical(tmp_path):
    # 非擁有者 / 不存在 watch_id → 與「owned-但-空」位元級相同,無 error 欄位(不洩漏存在性)
    s = Store.open(tmp_path / "a.db")
    svc = Service(s)
    k1, _ = _key(s)
    k2, _ = _key(s)
    wa = _watch(s, api_key_id=k1)  # k1 擁有,無事件 → owned-空
    wb = _watch(s, api_key_id=k2)  # k2 擁有 → 對 k1 是非擁有者

    owned_empty = svc.list_changes(None, caller_key_id=k1, watch_id=wa.id)
    non_owner = svc.list_changes(None, caller_key_id=k1, watch_id=wb.id)
    nonexistent = svc.list_changes(None, caller_key_id=k1, watch_id="does-not-exist")

    # 位元級相同:三者完全一致
    assert owned_empty == non_owner == nonexistent
    # 且皆無 error 欄位(error 之有無本身即洩漏存在性)
    assert "error" not in owned_empty
    # 特釘 since_cursor=None 洞:cursor 皆正規化為 0
    assert owned_empty["cursor"] == non_owner["cursor"] == 0


def test_list_changes_cursor_norm_service_paths_consistent(tmp_path):
    # 三條 service 路徑(digest-空 / 缺 watch_id / ownership-reject)對 None 回同一 cursor
    s = Store.open(tmp_path / "a.db")
    svc = Service(s)
    k1, _ = _key(s)
    digest_empty = svc.list_changes(None, caller_key_id=k1, allow_digest=True)["cursor"]
    missing_wid = svc.list_changes(None, caller_key_id=k1)["cursor"]
    reject = svc.list_changes(None, caller_key_id=k1, watch_id="nope")["cursor"]
    assert digest_empty == missing_wid == reject == 0


def test_list_changes_per_watch_gate_triggers(tmp_path):
    # per-watch 路徑也套計費 gate(review Important):超 free_rounds 的 watch,帶
    # watch_id 拉回 gated stub(既有 metering 測試只走 digest 面 allow_digest=True)
    s = Store.open(tmp_path / "a.db")
    svc = Service(s)
    k1, _ = _key(s)
    wa = _watch(s, api_key_id=k1)  # free_rounds=2
    for i, x in enumerate(["a1", "a2", "a3"], start=1):
        _added_round(s, wa, i, [x])  # 輪 3 超額
    res = svc.list_changes(None, caller_key_id=k1, watch_id=wa.id)
    gated = [e for e in res["events"] if e.get("gated")]
    assert len(gated) == 1 and gated[0]["row_key"] == '["a3"]'  # 輪 3 stub


def test_list_changes_per_watch_unmetered_delivers_all(tmp_path):
    # self-host(unmetered):per-watch 也全交付、不 stub
    s = Store.open(tmp_path / "a.db")
    svc = Service(s, unmetered=True)
    k1, _ = _key(s)
    wa = _watch(s, api_key_id=k1)
    for i, x in enumerate(["a1", "a2", "a3"], start=1):
        _added_round(s, wa, i, [x])
    res = svc.list_changes(None, caller_key_id=k1, watch_id=wa.id)
    assert all(not e.get("gated") for e in res["events"])
    assert {e["row_key"] for e in res["events"]} == {'["a1"]', '["a2"]', '["a3"]'}


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
    res = svc.list_changes(None, caller_key_id=k1, allow_digest=True)
    gated = [e for e in res["events"] if e.get("gated")]
    assert len(gated) == 1 and gated[0]["row_key"] == '["a3"]'  # wa 超額 stub
    ok = [e for e in res["events"] if not e.get("gated")]
    assert '["b1"]' in {e["row_key"] for e in ok}  # wb 照常交付
