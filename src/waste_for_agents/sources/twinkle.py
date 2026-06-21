"""TwinkleSource — 當 MCP client 連 Twinkle Hub,呼叫 query_rows。

Twinkle query_rows 回傳 column-oriented:{columns:[...], rows:[[...],...], ...},
所有值為字串。_extract_rows 把它 zip 成 list[dict]。

失敗一律具名(TwinkleFetchError),不靜默吞。token 走環境變數,絕不 hardcode。
網路層薄;解析層 _extract_rows 是純函式,可單測。
"""

from __future__ import annotations

import json
import os
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import (  # type: ignore[attr-defined]  # create_mcp_http_client 執行期存在,僅未列入 __all__
    create_mcp_http_client,
    streamable_http_client,
)

from .base import Row

TWINKLE_URL = "https://api.twinkleai.tw/mcp/"


class TwinkleFetchError(RuntimeError):
    """連線、auth、工具回應或解析失敗。"""


def _extract_rows(payload: Any) -> list[Row]:
    """column-oriented payload -> list[dict]。形狀不符即拋 TwinkleFetchError。"""
    if not isinstance(payload, dict) or "columns" not in payload or "rows" not in payload:
        raise TwinkleFetchError(
            f"query_rows 回傳形狀非預期(缺 columns/rows):{type(payload).__name__}"
        )
    columns = payload["columns"]
    rows = payload["rows"]
    return [dict(zip(columns, row, strict=False)) for row in rows]


def _result_payload(result: Any) -> Any:
    """從 CallToolResult 取出結構化 payload:優先 structuredContent,否則解析 text。"""
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return structured
    for block in getattr(result, "content", []):
        text = getattr(block, "text", None)
        if text is not None:
            return json.loads(text)
    raise TwinkleFetchError("query_rows 結果無可解析內容")


class TwinkleSource:
    """以 MCP client 連 Twinkle Hub 的 source adapter。query 即 query_rows 的參數 dict。"""

    def __init__(self, token: str | None = None, url: str = TWINKLE_URL) -> None:
        self._token = token or os.environ.get("TWINKLE_TOKEN")
        self._url = url

    async def fetch(self, query: Row) -> list[Row]:
        if not self._token:
            raise TwinkleFetchError("TWINKLE_TOKEN 未設定")
        headers = {"Authorization": f"Bearer {self._token}"}
        try:
            # mcp 1.28:streamable_http_client 不再收 headers,改傳預配的 http_client
            async with create_mcp_http_client(headers=headers) as http_client:
                async with streamable_http_client(
                    self._url, http_client=http_client
                ) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        result = await session.call_tool("query_rows", query)
        except TwinkleFetchError:
            raise
        except Exception as exc:  # 連線/協定層失敗 → 具名
            raise TwinkleFetchError(f"query_rows 呼叫失敗:{exc}") from exc

        if getattr(result, "isError", False):
            raise TwinkleFetchError(f"query_rows 回報錯誤:{getattr(result, 'content', None)}")
        return _extract_rows(_result_payload(result))
