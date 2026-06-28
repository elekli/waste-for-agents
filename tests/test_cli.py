"""CLI 進入點:issue-key 直接寫本機 DB 發 key(解 onboarding 雞生蛋)。"""

from waste_for_agents.__main__ import _env_truthy, main
from waste_for_agents.auth import AuthError, RateLimiter, authenticate
from waste_for_agents.store import Store


def test_issue_key_cli_prints_working_key(tmp_path, capsys):
    db = str(tmp_path / "cli.db")
    rc = main(["issue-key", "--db", db])
    assert rc == 0

    key = capsys.readouterr().out.strip()
    assert key.startswith("wfa_")  # 只印明文 key(便於 KEY=$(...) 擷取)

    # 同一個 DB 裡這把 key 能通過認證,tier=free
    store = Store.open(db)
    rec = authenticate(store, RateLimiter(), key)
    assert rec.tier == "free"


def test_issue_key_cli_paid_tier(tmp_path, capsys):
    db = str(tmp_path / "cli2.db")
    assert main(["issue-key", "--db", db, "--tier", "paid"]) == 0
    key = capsys.readouterr().out.strip()
    rec = authenticate(Store.open(db), RateLimiter(), key)
    assert rec.tier == "paid"


def test_issue_key_only_stores_hash_not_plaintext(tmp_path, capsys):
    # 安全:DB 不該存明文 key(只存 hash)
    db = str(tmp_path / "cli3.db")
    main(["issue-key", "--db", db])
    key = capsys.readouterr().out.strip()

    with open(db, "rb") as f:
        blob = f.read()
    assert key.encode() not in blob


def test_bad_key_rejected(tmp_path):
    db = str(tmp_path / "cli4.db")
    main(["issue-key", "--db", db])
    store = Store.open(db)
    try:
        authenticate(store, RateLimiter(), "wfa_not-a-real-key")
        raise AssertionError("應拒絕無效 key")
    except AuthError:
        pass


def test_env_truthy(monkeypatch):
    monkeypatch.delenv("WASTE_TEST_FLAG", raising=False)
    assert _env_truthy("WASTE_TEST_FLAG") is False  # 未設
    for v in ("1", "true", "YES", "on"):
        monkeypatch.setenv("WASTE_TEST_FLAG", v)
        assert _env_truthy("WASTE_TEST_FLAG") is True
    for v in ("0", "false", "", "no"):
        monkeypatch.setenv("WASTE_TEST_FLAG", v)
        assert _env_truthy("WASTE_TEST_FLAG") is False
