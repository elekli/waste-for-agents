"""CLI 進入點。`waste-for-agents serve` 起 HTTP 常駐(Chunk 5 實作 serve)。"""

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="waste-for-agents")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="起 MCP server(HTTP streamable，常駐排程器)")

    args = parser.parse_args(argv)

    if args.command == "serve":
        # Chunk 5 接上 server.serve()
        print("serve 尚未實作(Chunk 5)", file=sys.stderr)
        return 1

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
