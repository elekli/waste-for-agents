"""CLI 進入點。

- `waste-for-agents serve` 起 HTTP 常駐 + 排程器(`--unmetered` 關計費 gate);
- `waste-for-agents issue-key` 直接寫本機 DB 發一把 key、印明文(解 onboarding 雞生蛋:
  要 key 才能設 MCP header,但拿 key 又得先連上 server);
- `waste-for-agents teardown` 清 data dir。
"""

import argparse
import os


def _resolve_db(db: str | None) -> str:
    """db 路徑:顯式給則用之,否則單一 data dir(並確保存在)。"""
    if db is not None:
        return db
    from .paths import db_path as default_db_path
    from .paths import ensure_data_dir

    ensure_data_dir()
    return str(default_db_path())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="waste-for-agents")
    sub = parser.add_subparsers(dest="command")
    p_serve = sub.add_parser("serve", help="起 MCP server(HTTP streamable，常駐排程器)")
    p_serve.add_argument(
        "--db", default=None, help="SQLite 路徑(省略 → 單一 data dir,見 WASTE_DATA_DIR)"
    )
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8848)
    p_serve.add_argument("--tick", type=float, default=5.0, help="排程器 tick 秒數")
    p_serve.add_argument(
        "--unmetered",
        action="store_true",
        help="關閉計費 gate(self-host / workflow;亦可設 env WASTE_UNMETERED=1)",
    )

    p_issue = sub.add_parser(
        "issue-key", help="發一把 API key(直接寫本機 DB,印明文 key 到 stdout)"
    )
    p_issue.add_argument(
        "--db", default=None, help="SQLite 路徑(省略 → 單一 data dir)"
    )
    p_issue.add_argument(
        "--tier", default="free", choices=["free", "paid"], help="key tier(預設 free)"
    )

    p_bind = sub.add_parser(
        "bind-subscription",
        help="把 Polar 訂閱綁到一把 api_key(綁定當下按訂閱狀態翻 tier)",
    )
    p_bind.add_argument("subscription_id", help="Polar subscription id(webhook 已記錄)")
    p_bind.add_argument("api_key_id", help="issue-key 發出的 api_key_id")
    p_bind.add_argument(
        "--db", default=None, help="SQLite 路徑(省略 → 單一 data dir)"
    )

    sub.add_parser("teardown", help="刪除整個 data dir(清空落地物;見 WASTE_DATA_DIR)")

    args = parser.parse_args(argv)

    if args.command == "serve":
        from .server import serve

        unmetered = args.unmetered or _env_truthy("WASTE_UNMETERED")
        serve(
            db_path=args.db,
            host=args.host,
            port=args.port,
            tick_s=args.tick,
            unmetered=unmetered,
            polar_webhook_secret=(
                os.environ.get("POLAR_WEBHOOK_SECRET") or ""
            ).strip()
            or None,
        )
        return 0

    if args.command == "bind-subscription":
        from .store import Store

        store = Store.open(_resolve_db(args.db))
        if not store.bind_subscription_key(args.subscription_id, args.api_key_id):
            print(f"找不到訂閱:{args.subscription_id}(webhook 還沒收過這筆?)")
            return 1
        tier = store.get_api_key_tier(args.api_key_id)
        print(f"已綁定 {args.subscription_id} → {args.api_key_id}(tier={tier})")
        return 0

    if args.command == "issue-key":
        from .auth import DEFAULT_FREE_RATE_LIMIT, generate_key, hash_key
        from .store import Store

        store = Store.open(_resolve_db(args.db))
        key = generate_key()
        store.create_api_key(
            hash_key(key), tier=args.tier, rate_limit=DEFAULT_FREE_RATE_LIMIT
        )
        # 只把明文 key 印到 stdout,讓 `KEY=$(... issue-key)` 乾淨擷取。
        print(key)
        return 0

    if args.command == "teardown":
        from .paths import UnsafeTeardownError, data_dir, teardown

        target = data_dir()
        try:
            removed = teardown()
        except UnsafeTeardownError as exc:
            print(f"拒絕:{exc}")
            return 1
        print(f"{'已刪除' if removed else '不存在(無動作)'}:{target}")
        return 0

    parser.print_help()
    return 0


def _env_truthy(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


if __name__ == "__main__":
    raise SystemExit(main())
