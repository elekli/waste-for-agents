"""Chunk 2 結構化 diff 測試。重點:忽略欄位反誤報。"""

from waste_for_agents.diff import diff_rows


def test_added() -> None:
    r = diff_rows([{"id": 1}], [{"id": 1}, {"id": 2}], ["id"], [])
    assert r.added == [{"id": 2}]
    assert r.removed == []
    assert r.modified == []
    assert not r.is_empty


def test_removed() -> None:
    r = diff_rows([{"id": 1}, {"id": 2}], [{"id": 1}], ["id"], [])
    assert r.removed == [{"id": 2}]
    assert r.added == []
    assert r.modified == []


def test_modified() -> None:
    r = diff_rows([{"id": 1, "name": "a"}], [{"id": 1, "name": "b"}], ["id"], [])
    assert r.added == []
    assert r.removed == []
    assert len(r.modified) == 1
    m = r.modified[0]
    assert m.key == r.modified[0].key  # 有 key
    assert m.changes == {"name": ["a", "b"]}


def test_no_change_is_empty() -> None:
    rows = [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]
    r = diff_rows(rows, rows, ["id"], [])
    assert r.is_empty


def test_ignore_columns_suppress_false_positive() -> None:
    """只有 timestamp 欄位變 → 不該報變化(核心反誤報)。"""
    old = [{"id": 1, "name": "a", "updated_at": "2026-06-21T00:00:00Z"}]
    new = [{"id": 1, "name": "a", "updated_at": "2026-06-21T09:00:00Z"}]
    r = diff_rows(old, new, ["id"], ["updated_at"])
    assert r.is_empty


def test_ignore_columns_keep_real_change() -> None:
    """忽略 timestamp,但真實欄位變動仍要報,且 detail 不含被忽略欄位。"""
    old = [{"id": 1, "name": "a", "updated_at": "T1"}]
    new = [{"id": 1, "name": "b", "updated_at": "T2"}]
    r = diff_rows(old, new, ["id"], ["updated_at"])
    assert len(r.modified) == 1
    assert r.modified[0].changes == {"name": ["a", "b"]}


def test_field_order_independent() -> None:
    old = [{"id": 1, "a": 1, "b": 2}]
    new = [{"b": 2, "a": 1, "id": 1}]
    assert diff_rows(old, new, ["id"], []).is_empty


def test_json_roundtrip_stable() -> None:
    """row 經 JSON round-trip(snapshot 還原)後與原 row diff 應為空。"""
    import json

    rows = [{"id": 1, "n": 10, "s": "x"}]
    reloaded = json.loads(json.dumps(rows))
    assert diff_rows(rows, reloaded, ["id"], []).is_empty


def test_column_added_to_row_is_modification() -> None:
    old = [{"id": 1, "name": "a"}]
    new = [{"id": 1, "name": "a", "extra": "z"}]
    r = diff_rows(old, new, ["id"], [])
    assert len(r.modified) == 1
    assert r.modified[0].changes == {"extra": [None, "z"]}


def test_empty_to_has_and_has_to_empty() -> None:
    assert diff_rows([], [{"id": 1}], ["id"], []).added == [{"id": 1}]
    assert diff_rows([{"id": 1}], [], ["id"], []).removed == [{"id": 1}]


def test_composite_key() -> None:
    old = [{"y": 2026, "m": 5, "v": 1}]
    new = [{"y": 2026, "m": 5, "v": 2}, {"y": 2026, "m": 6, "v": 9}]
    r = diff_rows(old, new, ["y", "m"], [])
    assert r.modified[0].changes == {"v": [1, 2]}
    assert r.added == [{"y": 2026, "m": 6, "v": 9}]
