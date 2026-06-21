"""Chunk 5 Service 邏輯測試(不架 HTTP)+ Chunk 6 HTTP 端點接線測試。"""

from fastapi.testclient import TestClient

from waste_for_agents.server import Service, build_app
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


def test_http_health_and_changes(tmp_path) -> None:
    store = Store.open(tmp_path / "w.db")
    wid = store.create_watch("fake", {}, ["id"], [], 3600).id
    e1 = store.append_event(wid, "added", "1", {"row": {"id": "1"}})

    app = build_app(store, tick_s=3600.0)  # tick 大,測試期間 scheduler 不干擾
    with TestClient(app) as client:
        h = client.get("/health")
        assert h.status_code == 200
        assert h.json()["watches"] == 1

        c = client.get("/changes")
        assert c.status_code == 200
        assert [e["id"] for e in c.json()["events"]] == [e1]

        c2 = client.get("/changes", params={"since": e1})
        assert c2.json()["events"] == []
        assert c2.json()["cursor"] == e1
