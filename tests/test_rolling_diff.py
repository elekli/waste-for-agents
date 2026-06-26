"""rolling_window seen-set diff(F2)。

added 對「累積 seen-set」判而非最後窗口,根治舊文滾出再回來的假 added。
baseline 不再特例:seen 空 → 全 added。
"""

from waste_for_agents.diff import diff_rolling

KEY = ["id"]


def _rows(*items):  # items: (id, title)
    return [{"id": i, "title": t} for i, t in items]


def test_baseline_all_added():
    # seen 空 → 全 added(不變式 1:baseline 非特例)
    res, seen = diff_rolling({}, _rows(("a", "A"), ("b", "B")), KEY, [], False)
    assert {r["id"] for r in res.added} == {"a", "b"}
    assert res.removed == [] and res.modified == []
    assert set(seen) == {'["a"]', '["b"]'}


def test_rollout_not_reported_not_polluting():
    # baseline {a,b,c} → 窗口 {b,c,d}:d added、a 滾出不報 removed、b/c 不動(不變式 2)
    _, seen = diff_rolling({}, _rows(("a", "A"), ("b", "B"), ("c", "C")), KEY, [], False)
    res, seen = diff_rolling(seen, _rows(("b", "B"), ("c", "C"), ("d", "D")), KEY, [], False)
    assert {r["id"] for r in res.added} == {"d"}
    assert res.removed == [] and res.modified == []


def test_reappearance_not_false_added():
    # a 滾出後重浮現、內容沒變 → 0 event(不變式 3/5,F2 的核心 bug)
    _, seen = diff_rolling({}, _rows(("a", "A"), ("b", "B")), KEY, [], False)
    _, seen = diff_rolling(seen, _rows(("b", "B"), ("c", "C")), KEY, [], False)  # a 滾出
    res, seen = diff_rolling(seen, _rows(("c", "C"), ("a", "A")), KEY, [], False)  # a 回來
    assert res.added == [] and res.modified == [] and res.removed == []


def test_three_way_slide_single_round():
    # 不變式 3:同一輪「進 d + 出 b + 重浮現 a」三向同時正確
    _, seen = diff_rolling({}, _rows(("a", "A"), ("b", "B"), ("c", "C")), KEY, [], False)
    _, seen = diff_rolling(seen, _rows(("c", "C"), ("d", "D")), KEY, [], False)  # a,b 滾出,d 進
    res, _ = diff_rolling(seen, _rows(("c", "C"), ("d", "D"), ("a", "A")), KEY, [], False)
    assert {r["id"] for r in res.added} == set()  # a 重浮現不假 added、無新 id
    assert res.removed == [] and res.modified == []
    # 再加真新文 e 同輪確認 added 仍會報
    res2, _ = diff_rolling(seen, _rows(("c", "C"), ("a", "A"), ("e", "E")), KEY, [], False)
    assert {r["id"] for r in res2.added} == {"e"}


def test_reappearance_with_edit_is_modified():
    _, seen = diff_rolling({}, _rows(("a", "A")), KEY, [], False)
    res, seen = diff_rolling(seen, _rows(("a", "A2")), KEY, [], False)
    assert res.added == [] and len(res.modified) == 1
    assert res.modified[0].changes["title"] == ["A", "A2"]


def test_suppress_content_modified_rebaselines_silently():
    # F5:版本戳不符 → 內容變不報 modified,但 seen-set 仍更新成新內容
    _, seen = diff_rolling({}, _rows(("a", "A")), KEY, [], False)
    res, seen = diff_rolling(seen, _rows(("a", "A_reMD")), KEY, [], True)
    assert res.modified == []
    assert seen['["a"]']["title"] == "A_reMD"  # 已 re-baseline
