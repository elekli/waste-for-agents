"""Chunk 1 儲存層測試。"""

from waste_for_agents.store import Store


def _store(tmp_path) -> Store:
    return Store.open(tmp_path / "w.db")


# --- Task 1.1: schema ---


def test_init_creates_tables(tmp_path) -> None:
    store = _store(tmp_path)
    names = {
        row[0]
        for row in store.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {"watches", "snapshots", "change_events"} <= names


# --- Task 1.2: watch CRUD ---


def test_watch_crud_roundtrip(tmp_path) -> None:
    store = _store(tmp_path)
    w = store.create_watch(
        source="twinkle",
        query={"dataset_id": "pcc-tender", "where": "x", "limit": 10},
        key_columns=["id"],
        ignore_columns=["updated_at"],
        interval_s=60,
    )
    assert w.id
    assert w.created_at
    assert w.last_run_at is None
    assert w.last_error is None

    got = store.get_watch(w.id)
    assert got is not None
    assert got.source == "twinkle"
    assert got.query == {"dataset_id": "pcc-tender", "where": "x", "limit": 10}
    assert got.key_columns == ["id"]
    assert got.ignore_columns == ["updated_at"]
    assert got.interval_s == 60

    assert [x.id for x in store.list_watches()] == [w.id]

    assert store.delete_watch(w.id) is True
    assert store.get_watch(w.id) is None
    assert store.delete_watch(w.id) is False


def test_get_missing_watch_returns_none(tmp_path) -> None:
    assert _store(tmp_path).get_watch("nope") is None


# --- Task 1.2: snapshots ---


def test_snapshot_roundtrip(tmp_path) -> None:
    store = _store(tmp_path)
    w = store.create_watch("twinkle", {}, ["id"], [], 60)
    assert store.get_snapshot(w.id) is None

    rows = [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]
    store.set_snapshot(w.id, rows)
    assert store.get_snapshot(w.id) == rows

    store.set_snapshot(w.id, [{"id": 1, "name": "z"}])
    assert store.get_snapshot(w.id) == [{"id": 1, "name": "z"}]


# --- Task 1.2: change_events + cursor ---


def test_events_since_cursor(tmp_path) -> None:
    store = _store(tmp_path)
    w = store.create_watch("twinkle", {}, ["id"], [], 60)

    # 無事件:空 + 游標歸零
    events, cursor = store.events_since(None)
    assert events == []
    assert cursor == 0

    e1 = store.append_event(w.id, "added", "1", {"row": {"id": 1}})
    e2 = store.append_event(w.id, "modified", "2", {"changed": {"name": ["a", "b"]}})
    e3 = store.append_event(w.id, "removed", "3", {"row": {"id": 3}})
    assert e1 < e2 < e3

    events, cursor = store.events_since(None)
    assert [e.id for e in events] == [e1, e2, e3]
    assert [e.kind for e in events] == ["added", "modified", "removed"]
    assert cursor == e3
    assert events[1].detail == {"changed": {"name": ["a", "b"]}}

    # 從游標 e1:只拿到 e2, e3
    events, cursor = store.events_since(e1)
    assert [e.id for e in events] == [e2, e3]
    assert cursor == e3

    # 已追上:空 + 同游標(no-op 秒回)
    events, cursor = store.events_since(e3)
    assert events == []
    assert cursor == e3


def test_delete_watch_cascades_events(tmp_path) -> None:
    """刪 watch 連帶刪 change_events,避免孤兒事件持續被 list_changes 取得。"""
    store = _store(tmp_path)
    w = store.create_watch("twinkle", {}, ["id"], [], 60)
    store.append_event(w.id, "added", "1", {"row": {"id": 1}})
    store.append_event(w.id, "added", "2", {"row": {"id": 2}})
    assert len(store.events_since(None)[0]) == 2

    store.delete_watch(w.id)
    assert store.events_since(None)[0] == []
    assert store.get_snapshot(w.id) is None


def test_record_run_atomic_writes(tmp_path) -> None:
    """record_run 一次寫:events + snapshot 前進 + mark_run。"""
    store = _store(tmp_path)
    w = store.create_watch("fake", {}, ["id"], [], 60)
    rows = [{"id": "1", "n": "a"}]
    events = [("added", "[\"1\"]", {"row": rows[0]})]

    store.record_run(w.id, rows, events, None)

    assert store.get_snapshot(w.id) == rows
    got_events, cursor = store.events_since(None)
    assert [e.kind for e in got_events] == ["added"]
    assert cursor == got_events[0].id
    got = store.get_watch(w.id)
    assert got is not None and got.last_run_at is not None and got.last_error is None


def test_mark_run_sets_timestamp_and_error(tmp_path) -> None:
    store = _store(tmp_path)
    w = store.create_watch("twinkle", {}, ["id"], [], 60)

    store.mark_run(w.id, last_error="boom")
    got = store.get_watch(w.id)
    assert got is not None
    assert got.last_run_at is not None
    assert got.last_error == "boom"

    store.mark_run(w.id, last_error=None)
    got = store.get_watch(w.id)
    assert got is not None
    assert got.last_error is None
