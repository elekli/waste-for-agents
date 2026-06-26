"""儲存層(SQLite):watches / snapshots / change_events。

游標式 events_since:無變化回空 + 同游標(no-op 秒回)。
所有結構化欄位(query / key_columns / ignore_columns / snapshot rows / event detail)
以 JSON 字串存。

並發:scheduler(event loop 緒)與 FastAPI sync route(threadpool 緒)共用單一連線。
threadsafety==3 只保證 sqlite C 層 serialized,不保證 Python Connection 的 cursor/
交易狀態,故所有公開方法以 RLock 序列化(見 _lock)。
"""

from __future__ import annotations

import json
import sqlite3
import threading
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

# 一個 event 的待寫入表示:(kind, row_key, detail)
EventTuple = tuple[str, str, Row]


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
    source_kind: str = "dataset"
    free_rounds: int = 2
    delivered_rounds: int = 0
    last_run_seq: int = 0
    api_key_id: str | None = None


@dataclass
class ChangeEvent:
    id: int
    watch_id: str
    kind: str  # "added" | "removed" | "modified"
    row_key: str
    detail: Row
    created_at: str
    run_seq: int = 0


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
    last_error    TEXT,
    source_kind   TEXT NOT NULL DEFAULT 'dataset',
    free_rounds   INTEGER NOT NULL DEFAULT 2,
    delivered_rounds INTEGER NOT NULL DEFAULT 0,
    last_run_seq  INTEGER NOT NULL DEFAULT 0,
    api_key_id    TEXT
);
CREATE TABLE IF NOT EXISTS snapshots (
    watch_id   TEXT PRIMARY KEY,
    rows_json  TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    norm_version TEXT
);
CREATE TABLE IF NOT EXISTS change_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    watch_id   TEXT NOT NULL,
    kind       TEXT NOT NULL,
    row_key    TEXT NOT NULL,
    detail_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    run_seq    INTEGER NOT NULL DEFAULT 0,
    withheld   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_change_events_watch ON change_events(watch_id);
"""

# 對既有 db 補欄位(ALTER TABLE ADD COLUMN;以 PRAGMA table_info 判斷已存在則 no-op)。
# 新欄位皆有 DEFAULT,既有列自動補預設,不破壞舊資料。
_MIGRATIONS: list[tuple[str, str, str]] = [
    ("watches", "source_kind", "TEXT NOT NULL DEFAULT 'dataset'"),
    ("watches", "free_rounds", "INTEGER NOT NULL DEFAULT 2"),
    ("watches", "delivered_rounds", "INTEGER NOT NULL DEFAULT 0"),
    ("watches", "last_run_seq", "INTEGER NOT NULL DEFAULT 0"),
    ("watches", "api_key_id", "TEXT"),
    ("snapshots", "norm_version", "TEXT"),
    ("change_events", "run_seq", "INTEGER NOT NULL DEFAULT 0"),
    ("change_events", "withheld", "INTEGER NOT NULL DEFAULT 0"),
]


class Store:
    """SQLite 持久層。用 Store.open(path) 建立(自動建 schema)。執行緒安全(RLock)。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self._lock = threading.RLock()

    @classmethod
    def open(cls, path: str | Path) -> Store:
        # check_same_thread=False:跨執行緒共用(見模組 docstring);序列化由 RLock 保證。
        conn = sqlite3.connect(str(path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)
        cls._migrate(conn)
        conn.commit()
        return cls(conn)

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """對既有 db 補新欄位(idempotent)。"""
        for table, column, decl in _MIGRATIONS:
            cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    # --- watches ---

    def create_watch(
        self,
        source: str,
        query: Row,
        key_columns: list[str],
        ignore_columns: list[str],
        interval_s: int,
        source_kind: str = "dataset",
        api_key_id: str | None = None,
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
            source_kind=source_kind,
            api_key_id=api_key_id,
        )
        with self._lock:
            self.conn.execute(
                "INSERT INTO watches (id, source, query_json, key_columns_json, "
                "ignore_columns_json, interval_s, created_at, last_run_at, last_error, "
                "source_kind, free_rounds, delivered_rounds, last_run_seq, api_key_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                    watch.source_kind,
                    watch.free_rounds,
                    watch.delivered_rounds,
                    watch.last_run_seq,
                    watch.api_key_id,
                ),
            )
            self.conn.commit()
        return watch

    def get_watch(self, watch_id: str) -> Watch | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM watches WHERE id = ?", (watch_id,)
            ).fetchone()
        return self._row_to_watch(row) if row is not None else None

    def list_watches(self) -> list[Watch]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM watches ORDER BY created_at"
            ).fetchall()
        return [self._row_to_watch(r) for r in rows]

    def delete_watch(self, watch_id: str) -> bool:
        with self._lock:
            cur = self.conn.execute("DELETE FROM watches WHERE id = ?", (watch_id,))
            # 連帶清 snapshot 與 change_events,避免孤兒事件持續被 list_changes 取得
            self.conn.execute("DELETE FROM snapshots WHERE watch_id = ?", (watch_id,))
            self.conn.execute(
                "DELETE FROM change_events WHERE watch_id = ?", (watch_id,)
            )
            self.conn.commit()
            return cur.rowcount > 0

    def mark_run(self, watch_id: str, last_error: str | None) -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE watches SET last_run_at = ?, last_error = ? WHERE id = ?",
                (_now_iso(), last_error, watch_id),
            )
            self.conn.commit()

    def record_run(
        self,
        watch_id: str,
        snapshot_rows: list[Row],
        events: list[EventTuple],
        last_error: str | None,
        run_seq: int = 0,
        norm_version: str | None = None,
    ) -> None:
        """一次成功 run 的原子提交:寫所有 events + 更新 snapshot + mark_run,單一交易。

        關鍵:snapshot 只在 events 同一交易內前進——中途失敗則整批 rollback,下輪重抓
        重 diff 對「舊」baseline,絕不靜默漏報。

        run_seq:本輪計費輪序號(rolling 路徑用)。>0 時推進 watches.last_run_seq;
        =0(預設,dataset 或 F5 re-baseline 空輪)不推進。所有本輪 events 寫此 run_seq。
        norm_version:rolling 的正規化版本戳,存進 snapshot(供 F5 版本不符偵測)。
        """
        now = _now_iso()
        with self._lock:
            try:
                for kind, key, detail in events:
                    self.conn.execute(
                        "INSERT INTO change_events "
                        "(watch_id, kind, row_key, detail_json, created_at, run_seq) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (watch_id, kind, key, json.dumps(detail), now, run_seq),
                    )
                self.conn.execute(
                    "INSERT INTO snapshots (watch_id, rows_json, updated_at, norm_version) "
                    "VALUES (?, ?, ?, ?) ON CONFLICT(watch_id) DO UPDATE SET "
                    "rows_json = excluded.rows_json, updated_at = excluded.updated_at, "
                    "norm_version = excluded.norm_version",
                    (watch_id, json.dumps(snapshot_rows), now, norm_version),
                )
                self.conn.execute(
                    "UPDATE watches SET last_run_at = ?, last_error = ?, "
                    "last_run_seq = CASE WHEN ? > last_run_seq THEN ? ELSE last_run_seq END "
                    "WHERE id = ?",
                    (now, last_error, run_seq, run_seq, watch_id),
                )
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

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
            source_kind=row["source_kind"],
            free_rounds=row["free_rounds"],
            delivered_rounds=row["delivered_rounds"],
            last_run_seq=row["last_run_seq"],
            api_key_id=row["api_key_id"],
        )

    # --- snapshots ---

    def get_snapshot(self, watch_id: str) -> list[Row] | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT rows_json FROM snapshots WHERE watch_id = ?", (watch_id,)
            ).fetchone()
        if row is None:
            return None
        rows: list[Row] = json.loads(row["rows_json"])
        return rows

    def set_snapshot(self, watch_id: str, rows: list[Row]) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO snapshots (watch_id, rows_json, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(watch_id) DO UPDATE SET rows_json = excluded.rows_json, "
                "updated_at = excluded.updated_at",
                (watch_id, json.dumps(rows), _now_iso()),
            )
            self.conn.commit()

    # --- change_events ---

    def append_event(self, watch_id: str, kind: str, row_key: str, detail: Row) -> int:
        with self._lock:
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
        with self._lock:
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
            run_seq=row["run_seq"],
        )
