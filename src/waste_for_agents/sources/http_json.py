"""HttpJsonSource — generic HTTP/JSON adapter:GET 一個 URL,解析 JSON 成 list[dict]。

動機:透過 hub(如 Twinkle)監看「即時」源會撞 hub 的 cache 凍結——hub 為省成本
normalise + cache,對每分鐘更新的源等於凍死(實測 YouBike 經 Twinkle 拿到的是 16 天前
快照,直打上游卻是此刻值)。要監看快速變動的結構化源,得直接打上游。此 adapter 即為此:
薄薄一層 GET + JSON 解析,讓同一個 diff 引擎能跑在真正高頻的源上。

query 語意(dict):
    url(必填):GET 的目標。
    records_path(選填):dot-path 指向 payload 內的陣列;None = top-level 本身即陣列。
        例:{"data": {"items": [...]}} → records_path="data.items"。
    headers(選填):附加 request headers(dict)。

值正規化:每個欄位值 stringify(與 TwinkleSource 全字串對齊),確保 diff 比較穩定、
不因 JSON 數字型別在兩次抓取間漂移(int vs str)而誤報。除 stringify 外不做任何
數字↔字串語意轉換,保留前導零識別碼等原貌。

SSRF:此 adapter 會 GET 任意 query.url,故經 netguard.guarded_get(scheme/內網/
metadata 阻擋 + redirect 逐跳重驗 + 出站 header allowlist),與 rss/discovery 一致。
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from ..netguard import UnsafeUrlError, guarded_get
from .base import Row

_MAX_ERROR_LEN = 1000
_DEFAULT_TIMEOUT = 10.0


class HttpJsonFetchError(RuntimeError):
    """連線、HTTP 狀態碼、或 JSON 解析/形狀失敗。一律具名,不靜默吞。"""


def _truncate(text: str) -> str:
    """錯誤會經 last_error 對外,截長避免無界上游內容外洩。"""
    if len(text) > _MAX_ERROR_LEN:
        return text[:_MAX_ERROR_LEN] + "…(truncated)"
    return text


def _stringify(value: Any) -> str:
    """欄位值正規化成字串。scalar → str();None → '';dict/list → canonical JSON。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _dig(payload: Any, path: str | None) -> Any:
    """沿 dot-path 取出巢狀值;路徑斷在哪即具名拋出。"""
    if not path:
        return payload
    cur = payload
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise HttpJsonFetchError(f"records_path '{path}' 在 payload 找不到段 '{part}'")
        cur = cur[part]
    return cur


def _extract_rows(payload: Any, records_path: str | None) -> list[Row]:
    """payload → list[dict];每筆須為物件,每值 stringify。形狀不符即拋。"""
    records = _dig(payload, records_path)
    if not isinstance(records, list):
        raise HttpJsonFetchError(
            f"預期陣列,得到 {type(records).__name__}(records_path={records_path!r})"
        )
    rows: list[Row] = []
    for i, rec in enumerate(records):
        if not isinstance(rec, dict):
            raise HttpJsonFetchError(f"第 {i} 筆非物件:{type(rec).__name__}")
        rows.append({str(k): _stringify(v) for k, v in rec.items()})
    return rows


class HttpJsonSource:
    """GET 一個 JSON endpoint,解析成 list[dict]。query 語意見模組 docstring。"""

    def __init__(self, timeout: float = _DEFAULT_TIMEOUT) -> None:
        self._timeout = timeout

    async def fetch(self, query: Row) -> list[Row]:
        url = query.get("url")
        if not isinstance(url, str) or not url:
            raise HttpJsonFetchError("query.url 必填且須為非空字串")
        records_path = query.get("records_path")
        if records_path is not None and not isinstance(records_path, str):
            raise HttpJsonFetchError("query.records_path 須為字串或省略")
        headers = query.get("headers")
        try:
            # follow_redirects=False:guarded_get 逐跳重驗 + header allowlist(SSRF)。
            async with httpx.AsyncClient(
                timeout=self._timeout, follow_redirects=False
            ) as client:
                resp = await guarded_get(client, url, headers)
                resp.raise_for_status()
                payload = resp.json()
        except UnsafeUrlError as exc:  # SSRF 閘擋下 → 具名
            raise HttpJsonFetchError(str(exc)) from exc
        except HttpJsonFetchError:
            raise
        except Exception as exc:  # 連線/狀態/解碼層失敗 → 具名 + 截長
            raise HttpJsonFetchError(
                _truncate(f"GET {url} 失敗:{type(exc).__name__}: {exc}")
            ) from exc
        return _extract_rows(payload, records_path)
