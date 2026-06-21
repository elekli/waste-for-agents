"""CLI 進入點。`waste-for-agents serve` 起 HTTP 常駐 + 排程器。"""

import argparse


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="waste-for-agents")
    sub = parser.add_subparsers(dest="command")
    p_serve = sub.add_parser("serve", help="起 MCP server(HTTP streamable，常駐排程器)")
    p_serve.add_argument("--db", default="waste.db", help="SQLite 路徑")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8848)
    p_serve.add_argument("--tick", type=float, default=5.0, help="排程器 tick 秒數")

    args = parser.parse_args(argv)

    if args.command == "serve":
        from .server import serve

        serve(db_path=args.db, host=args.host, port=args.port, tick_s=args.tick)
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
