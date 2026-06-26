"""store 的 rolling_window 支援:source_kind 欄位、run_seq、snapshot 持久化(list)。

store 一律存 list、get_snapshot 回 list;合併在 diff_rolling/scheduler 邊界做,
store 只原子持久化(見計畫 F2 定案)。
"""

import sqlite3

from waste_for_agents.store import Store


def test_migration_adds_columns_to_old_db(tmp_path):
    # 模擬「舊 schema」db(只有原始欄位),插一列舊資料,再用新 Store 開 → 應補上
    # 新欄位(取 DEFAULT)且舊資料不損(F2 migration 安全性)。
    db = tmp_path / "old.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        "CREATE TABLE watches (id TEXT PRIMARY KEY, source TEXT NOT NULL, "
        "query_json TEXT NOT NULL, key_columns_json TEXT NOT NULL, "
        "ignore_columns_json TEXT NOT NULL, interval_s INTEGER NOT NULL, "
        "created_at TEXT NOT NULL, last_run_at TEXT, last_error TEXT);"
        "CREATE TABLE snapshots (watch_id TEXT PRIMARY KEY, rows_json TEXT NOT NULL, "
        "updated_at TEXT NOT NULL);"
        "CREATE TABLE change_events (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "watch_id TEXT NOT NULL, kind TEXT NOT NULL, row_key TEXT NOT NULL, "
        "detail_json TEXT NOT NULL, created_at TEXT NOT NULL);"
    )
    conn.execute(
        "INSERT INTO watches (id, source, query_json, key_columns_json, "
        "ignore_columns_json, interval_s, created_at) "
        "VALUES ('w1', 'twinkle', '{}', '[\"id\"]', '[]', 300, '2026-01-01')"
    )
    conn.commit()
    conn.close()

    s = Store.open(db)  # 觸發 _migrate
    w = s.get_watch("w1")
    assert w is not None
    assert w.source_kind == "dataset"  # 補上的新欄位取 DEFAULT
    assert w.free_rounds == 2 and w.last_run_seq == 0 and w.api_key_id is None
    assert w.source == "twinkle"  # 舊資料未損


def test_create_watch_source_kind_default(tmp_path):
    s = Store.open(tmp_path / "w.db")
    w = s.create_watch("twinkle", {"q": 1}, ["id"], [], 300)
    assert w.source_kind == "dataset"  # 不破壞既有
    assert w.free_rounds == 2 and w.delivered_rounds == 0
    assert w.api_key_id is None


def test_create_watch_rolling(tmp_path):
    s = Store.open(tmp_path / "w.db")
    w = s.create_watch(
        "rss", {"url": "x"}, ["id"], [], 3600, source_kind="rolling_window"
    )
    assert w.source_kind == "rolling_window"
    # reload 後仍是 rolling_window(持久)
    assert (s.get_watch(w.id)).source_kind == "rolling_window"


def test_rolling_snapshot_persists_as_list(tmp_path):
    s = Store.open(tmp_path / "w.db")
    w = s.create_watch(
        "rss", {"url": "x"}, ["id"], [], 3600, source_kind="rolling_window"
    )
    # scheduler 算好 merged list 後傳入;store 忠實存下傳入的 rows(不合併)。
    s.record_run(w.id, [{"id": "a"}, {"id": "b"}], [], None, run_seq=1)
    snap = s.get_snapshot(w.id)
    assert isinstance(snap, list)
    assert {r["id"] for r in snap} == {"a", "b"}


def test_record_run_writes_run_seq_and_norm_version(tmp_path):
    s = Store.open(tmp_path / "w.db")
    w = s.create_watch(
        "rss", {"url": "x"}, ["id"], [], 3600, source_kind="rolling_window"
    )
    s.record_run(
        w.id,
        [{"id": "a"}],
        [("added", '["a"]', {"id": "a"})],
        None,
        run_seq=7,
        norm_version="md1+fp2",
    )
    events, _ = s.events_since(None)
    assert events and events[-1].run_seq == 7


def test_record_run_defaults_backward_compatible(tmp_path):
    # 舊 4-arg positional caller 仍可用(run_seq/norm_version 有預設)
    s = Store.open(tmp_path / "w.db")
    w = s.create_watch("twinkle", {"q": 1}, ["id"], [], 300)
    s.record_run(w.id, [{"id": "a"}], [], None)  # 無 run_seq/norm_version
    assert s.get_snapshot(w.id) == [{"id": "a"}]
