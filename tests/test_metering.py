"""計費 gate(C-stub)的 store 層:meter_and_mark 持久水位 + withheld claim。

計費輪 = 產 ≥1 added 的 run_seq。前 free_rounds 輪 deliver、超額 withhold。
計量靠持久 last_metered_run_seq,對「同批重呼叫 / 游標重放」idempotent(不變式 8)。
gate 只對「有 free-tier api_key」的 watch 生效;無 key/paid = 不計量。
"""

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
