"""端到端:MCP tool 真的從 Bearer header 強制認證(背景 uvicorn + 真 MCP client)。

驗證 spike 證實的 ctx→header 路徑在完整 build_app + FastMCP streamable-http 下成立:
無 key 被拒、issue_key 自助發 key、帶 key 後 create/list 正常且 scope 到自己。
本地、無外網(用未註冊 source 'fake' → create 不 fetch)。
"""

from __future__ import annotations

import json
import socket
import threading
import time
from typing import Any

import httpx
import pytest
import uvicorn

from waste_for_agents.server import build_app
from waste_for_agents.store import Store


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _start_server(app: Any, port: int) -> uvicorn.Server:
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    # 等 /health 起來(取代盲 sleep)
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health", timeout=0.5).status_code == 200:
                return server
        except Exception:
            time.sleep(0.1)
    raise RuntimeError("server 未在時限內就緒")


def _tool_payload(result: Any) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(result.content[0].text)
    return data


@pytest.mark.asyncio
async def test_mcp_tool_enforces_bearer_auth(tmp_path):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    store = Store.open(tmp_path / "e2e.db")
    port = _free_port()
    server = _start_server(build_app(store, tick_s=3600.0), port)
    url = f"http://127.0.0.1:{port}/mcp/"
    args = {"source": "fake", "query": {}, "key_columns": ["id"], "ignore_columns": []}
    try:
        # 1) 無 key:create_watch 被拒,issue_key 可自助發 key
        async with streamablehttp_client(url) as (r, w, _):
            async with ClientSession(r, w) as session:
                await session.initialize()
                rejected = _tool_payload(await session.call_tool("create_watch", args))
                assert "unauthorized" in rejected.get("error", "")
                issued = _tool_payload(await session.call_tool("issue_key", {}))
                key = issued["api_key"]
                assert key.startswith("wfa_")

        # 2) 帶 Bearer key:create + list 正常,且只見自己的 watch
        headers = {"Authorization": f"Bearer {key}"}
        async with streamablehttp_client(url, headers=headers) as (r, w, _):
            async with ClientSession(r, w) as session:
                await session.initialize()
                created = _tool_payload(await session.call_tool("create_watch", args))
                wid = created["watch_id"]
                listed = _tool_payload(await session.call_tool("list_watches", {}))
                assert [x["id"] for x in listed["watches"]] == [wid]
    finally:
        server.should_exit = True
