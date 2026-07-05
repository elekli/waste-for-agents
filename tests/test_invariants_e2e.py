"""10 條不變式的端到端 property 測試(spec gap-verification 的落地、gate 後最後防線)。

合成多輪 feed 序列,透過**真** scheduler(run_watch)+ **真** Store(SQLite)+ **真**
計費 gate(Service.list_changes / replay)驅動,逐條斷言不變式 1–10。不碰網路。

不變式對照(spec):
 1 id 不在 seen-set 必產 added(含 baseline 非特例)
 2 滾出不報 removed、不污染他者
 3 窗口滑動交互(進+出+重浮現同時正確)
 4 id 跨輪穩定
 5 重現不誤報(seen + 內容沒變 → 0 event)
 6 正規化 determinism(同 HTML → 位元級相同 MD)
 7 gate 只延遲不遺失(withhold 付費後可補拿)
 8 輪次計量正確(只 added 輪計、計恰一次)
 9 交付恰一次 + 游標單調
10 跨 session 狀態持久(落 SQLite)
"""

import asyncio

from waste_for_agents.normalize import html_to_markdown, norm_version
from waste_for_agents.scheduler import run_watch
from waste_for_agents.server import Service
from waste_for_agents.store import Store


class _Src:
    """可變 rows 的 fake rolling source(每輪改 .rows)。"""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    async def fetch(self, query):
        return list(self.rows)


def _resolve(src):
    return lambda name: src


def _rolling_watch(store, api_key_id=None):
    return store.create_watch(
        "rss", {"url": "x"}, ["id"], [], 3600,
        source_kind="rolling_window", api_key_id=api_key_id,
    ).id


def _round(store, src, watch_id, rows, nv="v1"):
    """跑一輪:設 source rows → 以 reloaded watch 跑 run_watch,回新增 event 數。"""
    src.rows = rows
    w = store.get_watch(watch_id)
    return asyncio.run(run_watch(store, w, _resolve(src), norm_version=nv))


def _kinds(store, watch_id):
    evs, _ = store.events_since(None)
    return [(e.kind, e.row_key, e.run_seq) for e in evs if e.watch_id == watch_id]


# --- 不變式 1:baseline 非特例,id 不在 seen-set 必 added ---


def test_inv1_baseline_all_added(tmp_path):
    s = Store.open(tmp_path / "w.db")
    src = _Src()
    wid = _rolling_watch(s)
    n = _round(s, src, wid, [{"id": "a", "c": "A"}, {"id": "b", "c": "B"}])
    assert n == 2
    kinds = _kinds(s, wid)
    assert {k for k, _, _ in kinds} == {"added"}
    assert all(rs == 1 for _, _, rs in kinds)  # baseline 計費輪 1(非靜默)


# --- 不變式 2:滾出不報 removed、不污染他者 ---


def test_inv2_rollout_no_removed_no_pollution(tmp_path):
    s = Store.open(tmp_path / "w.db")
    src = _Src()
    wid = _rolling_watch(s)
    # 另一個獨立 watch(自己的 source/資料)——驗事件不跨 watch 污染
    src_other = _Src()
    wid_other = _rolling_watch(s)
    _round(s, src, wid, [{"id": "a", "c": "A"}, {"id": "b", "c": "B"}])
    _round(s, src_other, wid_other, [{"id": "z", "c": "Z"}])  # 他者資料
    n = _round(s, src, wid, [{"id": "b", "c": "B"}, {"id": "c", "c": "C"}])  # a 滾出
    assert n == 1  # 只 c added
    kinds = _kinds(s, wid)
    assert not any(k == "removed" for k, _, _ in kinds)  # 全程無 removed
    # b 未被重報(不污染:沒有第二個 b 事件)
    assert sum(1 for k, rk, _ in kinds if k == "added" and rk == '["b"]') == 1
    # cross-watch:wid 的事件不含他者的 z;wid_other 不含 a/b/c
    assert all(rk != '["z"]' for _, rk, _ in kinds)
    assert {rk for _, rk, _ in _kinds(s, wid_other)} == {'["z"]'}


# --- 不變式 3:窗口滑動交互(進+出+重浮現同時正確)---


def test_inv3_three_way_slide(tmp_path):
    s = Store.open(tmp_path / "w.db")
    src = _Src()
    wid = _rolling_watch(s)
    _round(s, src, wid, [{"id": "a", "c": "A"}, {"id": "b", "c": "B"}, {"id": "c", "c": "C"}])
    n2 = _round(s, src, wid, [{"id": "c", "c": "C"}, {"id": "d", "c": "D"}])  # a,b 滾出,d 進
    assert n2 == 1  # d 真的被 added(別讓「靜默漏掉新項」的 bug 溜過)
    assert '["d"]' in {rk for k, rk, _ in _kinds(s, wid) if k == "added"}
    # 同一輪:d 未變 + a 重浮現(內容同)+ e 真新 → 只 e added,a 不假 added
    n = _round(s, src, wid, [
        {"id": "c", "c": "C"}, {"id": "d", "c": "D"},
        {"id": "a", "c": "A"}, {"id": "e", "c": "E"},
    ])
    assert n == 1  # 只 e(a 重浮現不假 added、d 未變)
    last_added = [rk for k, rk, _ in _kinds(s, wid) if k == "added"]
    assert last_added.count('["a"]') == 1  # a 只在初輪 added 過一次
    assert '["e"]' in last_added


# --- 不變式 4:id 跨輪穩定(同邏輯項不因輪次抖動產生 add/remove)---


def test_inv4_id_stable_across_rounds(tmp_path):
    s = Store.open(tmp_path / "w.db")
    src = _Src()
    wid = _rolling_watch(s)
    rows = [{"id": "x", "c": "X"}]
    _round(s, src, wid, rows)
    n2 = _round(s, src, wid, rows)  # 同一篇再現
    n3 = _round(s, src, wid, rows)
    assert n2 == 0 and n3 == 0  # id 穩定 → 不重複 added
    added = [rk for k, rk, _ in _kinds(s, wid) if k == "added"]
    assert added == ['["x"]']  # 整個生命週期只 added 一次


# --- 不變式 5:重現不誤報(滾出後同內容再現 → 0 event)---


def test_inv5_reappearance_no_false_event(tmp_path):
    s = Store.open(tmp_path / "w.db")
    src = _Src()
    wid = _rolling_watch(s)
    _round(s, src, wid, [{"id": "a", "c": "A"}, {"id": "b", "c": "B"}])
    _round(s, src, wid, [{"id": "b", "c": "B"}, {"id": "c", "c": "C"}])  # a 滾出
    n = _round(s, src, wid, [{"id": "c", "c": "C"}, {"id": "a", "c": "A"}])  # a 同內容再現
    assert n == 0  # F2 核心:seen 過 + 內容沒變 → 0 event


# --- 不變式 6:正規化 determinism(同 HTML → 位元級相同 MD)---


def test_inv6_normalize_deterministic():
    html = '<p>Hello <a href="https://x.com">link</a> <b>bold</b></p>'
    md = html_to_markdown(html)
    assert md == html_to_markdown(html)  # 跨呼叫一致(抓非決定性:dict/隨機序)
    # golden:真的轉對(抓「決定性但壞掉」——連結保留為 MD link、粗體轉 **）
    assert "[link](https://x.com)" in md and "**bold**" in md
    assert norm_version() == norm_version()  # 版本戳穩定


# --- 不變式 7:gate 只延遲不遺失(withhold 付費後可補拿)---


def test_inv7_gate_delays_not_loses(tmp_path):
    s = Store.open(tmp_path / "w.db")
    src = _Src()
    svc = Service(s)
    kid = svc.issue_key()["api_key_id"]  # 真實 onboarding 路徑(free_rounds=2)
    wid = _rolling_watch(s, api_key_id=kid)
    _round(s, src, wid, [{"id": "a", "c": "A"}])  # 計費輪 1
    _round(s, src, wid, [{"id": "a", "c": "A"}, {"id": "b", "c": "B"}])  # 輪 2
    _round(s, src, wid, [{"id": "a", "c": "A"}, {"id": "b", "c": "B"}, {"id": "c", "c": "C"}])  # 輪 3 超額
    res = svc.list_changes(None, caller_key_id=kid, allow_digest=True)
    gated = [e for e in res["events"] if e.get("gated")]
    assert {e["row_key"] for e in gated} == {'["c"]'}  # 輪 3 被 gate
    # 付費後補拿,不遺失;再 replay 回空(恰一次)
    s.set_api_key_tier(kid, "paid")
    replayed = svc.replay_watch(wid, caller_key_id=kid)
    assert {e["row_key"] for e in replayed["events"]} == {'["c"]'}
    assert svc.replay_watch(wid, caller_key_id=kid)["events"] == []


# --- 不變式 8:輪次計量正確(只 added 輪計、計恰一次)---


def test_inv8_round_metering_correct(tmp_path):
    # 走 production read path(Service.list_changes 內部呼叫 meter_and_mark),驗持久水位
    s = Store.open(tmp_path / "w.db")
    src = _Src()
    svc = Service(s)
    kid = svc.issue_key()["api_key_id"]
    wid = _rolling_watch(s, api_key_id=kid)
    _round(s, src, wid, [{"id": "a", "c": "A"}])  # added 輪
    _round(s, src, wid, [{"id": "a", "c": "A2"}])  # modified-only(同 id 內容變)→ 不計輪
    _round(s, src, wid, [{"id": "a", "c": "A2"}, {"id": "b", "c": "B"}])  # added 輪
    svc.list_changes(None, caller_key_id=kid, allow_digest=True)  # 經 gate 計量
    assert s.get_watch(wid).delivered_rounds == 2  # 只算 2 個 added 輪
    # idempotent:同游標重拉不重計(持久水位 last_metered_run_seq)
    svc.list_changes(None, caller_key_id=kid, allow_digest=True)
    assert s.get_watch(wid).delivered_rounds == 2


# --- 不變式 9:交付恰一次 + 游標單調 ---


def test_inv9_delivery_once_cursor_monotonic(tmp_path):
    s = Store.open(tmp_path / "w.db")
    src = _Src()
    svc = Service(s)
    kid = svc.issue_key()["api_key_id"]  # free_rounds=2
    wid = _rolling_watch(s, api_key_id=kid)
    _round(s, src, wid, [{"id": "a", "c": "A"}])  # 計費輪 1
    r1 = svc.list_changes(None, caller_key_id=kid, allow_digest=True)
    c1 = r1["cursor"]
    assert {e["row_key"] for e in r1["events"]} == {'["a"]'}
    # 同游標再拉 → 空 + 游標不倒退(恰一次、單調)
    r1b = svc.list_changes(c1, caller_key_id=kid, allow_digest=True)
    assert r1b["events"] == [] and r1b["cursor"] == c1
    # 輪 2(計費輪 2,免費額度內)+ 輪 3(超額被 gate)
    _round(s, src, wid, [{"id": "a", "c": "A"}, {"id": "b", "c": "B"}])
    _round(s, src, wid, [{"id": "a", "c": "A"}, {"id": "b", "c": "B"}, {"id": "c", "c": "C"}])
    r2 = svc.list_changes(c1, caller_key_id=kid, allow_digest=True)
    # b 正常交付、c 被 gate 成 stub——但游標仍含 stub 的 id 照常前進(不變式 9:gate 不卡游標)
    gated = [e for e in r2["events"] if e.get("gated")]
    assert {e["row_key"] for e in gated} == {'["c"]'}
    assert r2["cursor"] > c1  # 跨 gated stub 仍單調遞增
    assert r2["cursor"] == max(e["id"] for e in r2["events"])  # 游標 = 含 stub 的最大 id
    # 追上後再拉 → 空、游標不倒退(gated 事件不重複交付)
    r3 = svc.list_changes(r2["cursor"], caller_key_id=kid, allow_digest=True)
    assert r3["events"] == [] and r3["cursor"] == r2["cursor"]


# --- 不變式 10:跨 session 狀態持久(落 SQLite)---


def test_inv10_state_persists_across_sessions(tmp_path):
    db = tmp_path / "w.db"
    src = _Src()
    # session A:建 watch + 跑輪 1 {a,b}
    s1 = Store.open(db)
    wid = _rolling_watch(s1)
    _round(s1, src, wid, [{"id": "a", "c": "A"}, {"id": "b", "c": "B"}])
    seq_a = s1.get_watch(wid).last_run_seq
    s1.close()
    # session B:重開同一 db,跑輪 2 {b,c}——若 seen-set 沒持久,b 會被當新 added
    s2 = Store.open(db)
    n = _round(s2, src, wid, [{"id": "b", "c": "B"}, {"id": "c", "c": "C"}])
    assert n == 1  # 只 c added → 證 seen-set(含 a,b)跨 session 持久
    added = [rk for k, rk, _ in _kinds(s2, wid) if k == "added"]
    assert added.count('["b"]') == 1  # b 未被重報
    assert s2.get_watch(wid).last_run_seq == seq_a + 1  # run_seq 跨 session 接續
