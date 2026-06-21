"""結構化 diff 引擎(護城河)。

diff_rows(old, new, key_columns, ignore_columns) -> DiffResult。
依 key_columns 把 rows 配對,比較時排除 ignore_columns(timestamp/流水號),
避免上游每跑一次就改 last_updated 造成的誤報。

刻意「不」做數字↔字串強制轉型:政府資料常有前導零的識別碼(郵遞區號、統編),
"01000" 與 "1000" 是不同值,強轉會遮蔽真實變化。要忽略易變欄位請用 ignore_columns。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

Row = dict[str, Any]

_MISSING = object()


@dataclass
class Modification:
    key: str
    changes: dict[str, list[Any]]  # {column: [old, new]}


@dataclass
class DiffResult:
    added: list[Row] = field(default_factory=list)
    removed: list[Row] = field(default_factory=list)
    modified: list[Modification] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.added or self.removed or self.modified)


def row_key(row: Row, key_columns: list[str]) -> str:
    """以 key_columns 的值組出穩定字串 key。"""
    return json.dumps(
        [row.get(k) for k in key_columns], ensure_ascii=False, default=str
    )


def _index(rows: list[Row], key_columns: list[str]) -> dict[str, Row]:
    # 同 key 重複時後者覆蓋(MVP 假設來源 key 唯一)。
    return {row_key(r, key_columns): r for r in rows}


def _compare(old: Row, new: Row, ignore: set[str]) -> dict[str, list[Any]]:
    changes: dict[str, list[Any]] = {}
    for col in (old.keys() | new.keys()) - ignore:
        ov = old.get(col, _MISSING)
        nv = new.get(col, _MISSING)
        if ov != nv:
            changes[col] = [
                None if ov is _MISSING else ov,
                None if nv is _MISSING else nv,
            ]
    return changes


def diff_rows(
    old_rows: list[Row],
    new_rows: list[Row],
    key_columns: list[str],
    ignore_columns: list[str],
) -> DiffResult:
    ignore = set(ignore_columns)
    old_idx = _index(old_rows, key_columns)
    new_idx = _index(new_rows, key_columns)
    result = DiffResult()

    for key, new in new_idx.items():
        if key not in old_idx:
            result.added.append(new)
        else:
            changes = _compare(old_idx[key], new, ignore)
            if changes:
                result.modified.append(Modification(key=key, changes=changes))

    for key, old in old_idx.items():
        if key not in new_idx:
            result.removed.append(old)

    return result
