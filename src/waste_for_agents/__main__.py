"""CLI 進入點。`waste-for-agents serve` 起 HTTP 常駐 + 排程器;`teardown` 清 data dir。"""

import argparse


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

    sub.add_parser("teardown", help="刪除整個 data dir(清空落地物;見 WASTE_DATA_DIR)")

    args = parser.parse_args(argv)

    if args.command == "serve":
        from .server import serve

        serve(db_path=args.db, host=args.host, port=args.port, tick_s=args.tick)
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


if __name__ == "__main__":
    raise SystemExit(main())
