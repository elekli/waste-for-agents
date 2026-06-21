"""儲存層(SQLite):watches / snapshots / change_events。

游標式 events_since:無變化回空 + 同游標(no-op 秒回)。
所有結構化欄位(query / key_columns / ignore_columns / snapshot rows / event detail)
以 JSON 字串存。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

Row = dict[str, Any]

# 跨執行緒共用單一連線(scheduler + FastAPI route),要求 sqlite serialized 模式。
# 非 serialized 的平台寧可 import 時 fail loud,也不要靜默資料競爭。
assert sqlite3.threadsafety == 3, (
    f"sqlite3.threadsafety={sqlite3.threadsafety} 非 serialized(3);"
    "跨執行緒共用連線不安全"
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Watch:
    id: str
    source: str
    query: Row
    key_columns: list[str]
    ignore_columns: list[str]
    interval_s: int
    created_at: str
    last_run_at: str | None
    last_error: str | None


@dataclass
class ChangeEvent:
    id: int
    watch_id: str
    kind: str  # "added" | "removed" | "modified"
    row_key: str
    detail: Row
    created_at: str


_SCHEMA = """
CREATE TABLE IF NOT EXISTS watches (
    id            TEXT PRIMARY KEY,
    source        TEXT NOT NULL,
    query_json    TEXT NOT NULL,
    key_columns_json    TEXT NOT NULL,
    ignore_columns_json TEXT NOT NULL,
    interval_s    INTEGER NOT NULL,
    created_at    TEXT NOT NULL,
    last_run_at   TEXT,
    last_error    TEXT
);
CREATE TABLE IF NOT EXISTS snapshots (
    watch_id   TEXT PRIMARY KEY,
    rows_json  TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS change_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    watch_id   TEXT NOT NULL,
    kind       TEXT NOT NULL,
    row_key    TEXT NOT NULL,
    detail_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


class Store:
    """SQLite 持久層。用 Store.open(path) 建立(自動建 schema)。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    @classmethod
    def open(cls, path: str | Path) -> Store:
        # check_same_thread=False:scheduler(event loop 執行緒)與 FastAPI sync
        # route(threadpool 執行緒)共用同一連線。安全性依賴 sqlite serialized 模式
        # (見 store 模組層的 threadsafety 斷言)。
        conn = sqlite3.connect(str(path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)
        conn.commit()
        return cls(conn)

    def close(self) -> None:
        self.conn.close()

    # --- watches ---

    def create_watch(
        self,
        source: str,
        query: Row,
        key_columns: list[str],
        ignore_columns: list[str],
        interval_s: int,
    ) -> Watch:
        watch = Watch(
            id=uuid.uuid4().hex,
            source=source,
            query=query,
            key_columns=key_columns,
            ignore_columns=ignore_columns,
            interval_s=interval_s,
            created_at=_now_iso(),
            last_run_at=None,
            last_error=None,
        )
        self.conn.execute(
            "INSERT INTO watches (id, source, query_json, key_columns_json, "
            "ignore_columns_json, interval_s, created_at, last_run_at, last_error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                watch.id,
                watch.source,
                json.dumps(watch.query),
                json.dumps(watch.key_columns),
                json.dumps(watch.ignore_columns),
                watch.interval_s,
                watch.created_at,
                watch.last_run_at,
                watch.last_error,
            ),
        )
        self.conn.commit()
        return watch

    def get_watch(self, watch_id: str) -> Watch | None:
        row = self.conn.execute(
            "SELECT * FROM watches WHERE id = ?", (watch_id,)
        ).fetchone()
        return self._row_to_watch(row) if row is not None else None

    def list_watches(self) -> list[Watch]:
        rows = self.conn.execute("SELECT * FROM watches ORDER BY created_at").fetchall()
        return [self._row_to_watch(r) for r in rows]

    def delete_watch(self, watch_id: str) -> bool:
        cur = self.conn.execute("DELETE FROM watches WHERE id = ?", (watch_id,))
        self.conn.execute("DELETE FROM snapshots WHERE watch_id = ?", (watch_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def mark_run(self, watch_id: str, last_error: str | None) -> None:
        self.conn.execute(
            "UPDATE watches SET last_run_at = ?, last_error = ? WHERE id = ?",
            (_now_iso(), last_error, watch_id),
        )
        self.conn.commit()

    @staticmethod
    def _row_to_watch(row: sqlite3.Row) -> Watch:
        query: Row = json.loads(row["query_json"])
        key_columns: list[str] = json.loads(row["key_columns_json"])
        ignore_columns: list[str] = json.loads(row["ignore_columns_json"])
        return Watch(
            id=row["id"],
            source=row["source"],
            query=query,
            key_columns=key_columns,
            ignore_columns=ignore_columns,
            interval_s=row["interval_s"],
            created_at=row["created_at"],
            last_run_at=row["last_run_at"],
            last_error=row["last_error"],
        )

    # --- snapshots ---

    def get_snapshot(self, watch_id: str) -> list[Row] | None:
        row = self.conn.execute(
            "SELECT rows_json FROM snapshots WHERE watch_id = ?", (watch_id,)
        ).fetchone()
        if row is None:
            return None
        rows: list[Row] = json.loads(row["rows_json"])
        return rows

    def set_snapshot(self, watch_id: str, rows: list[Row]) -> None:
        self.conn.execute(
            "INSERT INTO snapshots (watch_id, rows_json, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(watch_id) DO UPDATE SET rows_json = excluded.rows_json, "
            "updated_at = excluded.updated_at",
            (watch_id, json.dumps(rows), _now_iso()),
        )
        self.conn.commit()

    # --- change_events ---

    def append_event(self, watch_id: str, kind: str, row_key: str, detail: Row) -> int:
        cur = self.conn.execute(
            "INSERT INTO change_events (watch_id, kind, row_key, detail_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (watch_id, kind, row_key, json.dumps(detail), _now_iso()),
        )
        self.conn.commit()
        event_id = cur.lastrowid
        assert event_id is not None  # AUTOINCREMENT 必有值
        return event_id

    def events_since(self, cursor: int | None) -> tuple[list[ChangeEvent], int]:
        """回 (events, new_cursor)。cursor=None 從頭;無新事件回 ([], cursor or 0)。"""
        after = cursor if cursor is not None else 0
        rows = self.conn.execute(
            "SELECT * FROM change_events WHERE id > ? ORDER BY id", (after,)
        ).fetchall()
        events = [self._row_to_event(r) for r in rows]
        new_cursor = events[-1].id if events else after
        return events, new_cursor

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> ChangeEvent:
        detail: Row = json.loads(row["detail_json"])
        return ChangeEvent(
            id=row["id"],
            watch_id=row["watch_id"],
            kind=row["kind"],
            row_key=row["row_key"],
            detail=detail,
            created_at=row["created_at"],
        )
