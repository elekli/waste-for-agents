"""Chunk 5 Service 邏輯測試(不架 HTTP)。"""

from waste_for_agents.server import Service
from waste_for_agents.store import Store


def _svc(tmp_path) -> Service:
    return Service(Store.open(tmp_path / "w.db"))


def test_create_and_list_watches(tmp_path) -> None:
    svc = _svc(tmp_path)
    out = svc.create_watch("twinkle", {"dataset_id": "ly-bills"}, ["議案編號"], ["updated_at"], 120)
    assert out["watch_id"]

    watches = svc.list_watches()["watches"]
    assert len(watches) == 1
    w = watches[0]
    assert w["id"] == out["watch_id"]
    assert w["source"] == "twinkle"
    assert w["interval_s"] == 120
    assert w["last_error"] is None


def test_list_changes_cursor_and_noop(tmp_path) -> None:
    svc = _svc(tmp_path)
    wid = svc.create_watch("fake", {}, ["id"], [], 60)["watch_id"]
    e1 = svc.store.append_event(wid, "added", "1", {"row": {"id": "1"}})
    e2 = svc.store.append_event(wid, "modified", "2", {"changes": {"n": ["a", "b"]}})

    out = svc.list_changes(None)
    assert [e["id"] for e in out["events"]] == [e1, e2]
    assert out["events"][1]["kind"] == "modified"
    assert out["events"][1]["detail"] == {"changes": {"n": ["a", "b"]}}
    assert out["cursor"] == e2

    # no-op:已追上,空 + 同游標
    out2 = svc.list_changes(out["cursor"])
    assert out2["events"] == []
    assert out2["cursor"] == e2


def test_delete_watch(tmp_path) -> None:
    svc = _svc(tmp_path)
    wid = svc.create_watch("fake", {}, ["id"], [], 60)["watch_id"]
    assert svc.delete_watch(wid) == {"deleted": True}
    assert svc.delete_watch(wid) == {"deleted": False}
    assert svc.list_watches()["watches"] == []
