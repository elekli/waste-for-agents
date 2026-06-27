"""單一 data dir 解析 + 安全 teardown。

落地物集中於 data_dir()(預設 ~/.waste-for-agents/,env WASTE_DATA_DIR 覆寫)。
teardown 只刪 data dir 本身,拒刪 home/root/cwd 等非預期路徑(防 misconfig 砍掉 home)。
"""

from pathlib import Path

import pytest

from waste_for_agents.paths import (
    UnsafeTeardownError,
    data_dir,
    db_path,
    ensure_data_dir,
    teardown,
)


def test_data_dir_default(monkeypatch):
    monkeypatch.delenv("WASTE_DATA_DIR", raising=False)
    assert data_dir() == (Path.home() / ".waste-for-agents").resolve()


def test_data_dir_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("WASTE_DATA_DIR", str(tmp_path / "wfa"))
    assert data_dir() == (tmp_path / "wfa").resolve()


def test_db_path_under_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("WASTE_DATA_DIR", str(tmp_path / "wfa"))
    assert db_path() == (tmp_path / "wfa").resolve() / "waste.db"
    assert db_path().parent == data_dir()


def test_ensure_data_dir_creates(monkeypatch, tmp_path):
    monkeypatch.setenv("WASTE_DATA_DIR", str(tmp_path / "wfa"))
    d = ensure_data_dir()
    assert d.is_dir()


def test_teardown_removes_only_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("WASTE_DATA_DIR", str(tmp_path / "wfa"))
    d = ensure_data_dir()
    (d / "waste.db").write_text("x")
    sibling = tmp_path / "keep.txt"
    sibling.write_text("keep")
    assert teardown() is True
    assert not d.exists()  # data dir 整個刪除
    assert sibling.exists()  # 隔壁不動


def test_teardown_noop_when_absent(monkeypatch, tmp_path):
    monkeypatch.setenv("WASTE_DATA_DIR", str(tmp_path / "never_created"))
    assert teardown() is False


def test_teardown_refuses_home(monkeypatch):
    monkeypatch.setenv("WASTE_DATA_DIR", str(Path.home()))
    with pytest.raises(UnsafeTeardownError):
        teardown()


def test_teardown_refuses_root(monkeypatch):
    monkeypatch.setenv("WASTE_DATA_DIR", "/")
    with pytest.raises(UnsafeTeardownError):
        teardown()


def test_data_dir_empty_or_whitespace_falls_back(monkeypatch):
    # 空字串 / 純空白 env 都不應變成 cwd 下的怪路徑,而是退回預設(review:空白繞過閘)
    default = (Path.home() / ".waste-for-agents").resolve()
    monkeypatch.setenv("WASTE_DATA_DIR", "")
    assert data_dir() == default
    monkeypatch.setenv("WASTE_DATA_DIR", "   ")
    assert data_dir() == default
    monkeypatch.setenv("WASTE_DATA_DIR", "\t")
    assert data_dir() == default


def test_teardown_refuses_cwd(monkeypatch):
    monkeypatch.setenv("WASTE_DATA_DIR", ".")
    with pytest.raises(UnsafeTeardownError):
        teardown()


def test_teardown_refuses_home_ancestor(monkeypatch):
    # home 的父層(如 /Users、/home)也要擋
    parent = str(Path.home().resolve().parent)
    monkeypatch.setenv("WASTE_DATA_DIR", parent)
    with pytest.raises(UnsafeTeardownError):
        teardown()


def test_teardown_refuses_toplevel_system_dir(monkeypatch):
    # deny-list 漏掉的系統頂層 dir(/usr、/etc…)靠深度閘擋(review Critical)
    for top in ("/usr", "/etc", "/bin", "/var"):
        monkeypatch.setenv("WASTE_DATA_DIR", top)
        with pytest.raises(UnsafeTeardownError):
            teardown()


def test_teardown_refuses_when_target_is_file(monkeypatch, tmp_path):
    f = tmp_path / "deep" / "notadir"
    f.parent.mkdir(parents=True)
    f.write_text("x")
    monkeypatch.setenv("WASTE_DATA_DIR", str(f))
    with pytest.raises(UnsafeTeardownError):
        teardown()  # 目標是檔案 → 清楚拒絕,不吐 NotADirectoryError
    assert f.exists()  # 檔案未動


def test_serve_default_db_lands_in_data_dir(monkeypatch, tmp_path):
    # serve(db_path=None)→ 落地物進 data dir(uvicorn.run 以 no-op mock,不真起 server)
    import uvicorn

    from waste_for_agents import server

    monkeypatch.setenv("WASTE_DATA_DIR", str(tmp_path / "wfa"))
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)
    server.serve(db_path=None)
    assert db_path().exists()  # Store.open 在 data dir 建了 waste.db


def test_cli_teardown_removes_data_dir(monkeypatch, tmp_path):
    from waste_for_agents.__main__ import main

    monkeypatch.setenv("WASTE_DATA_DIR", str(tmp_path / "wfa"))
    ensure_data_dir()
    (data_dir() / "waste.db").write_text("x")
    assert main(["teardown"]) == 0
    assert not data_dir().exists()


def test_cli_teardown_unsafe_returns_nonzero(monkeypatch):
    from waste_for_agents.__main__ import main

    monkeypatch.setenv("WASTE_DATA_DIR", "/")  # 危險路徑
    assert main(["teardown"]) == 1  # 拒絕、不吐 traceback
