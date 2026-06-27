"""單一 data dir:所有落地物(SQLite db)集中於此,並提供安全 teardown。

預設 `~/.waste-for-agents/`,可由 env `WASTE_DATA_DIR` 覆寫(測試 / 多實例 / 自訂位置)。
teardown 只刪解析出的 data dir 本身,且拒刪 home / root / cwd 與其祖先——防 misconfig
(如 `WASTE_DATA_DIR=~` 或 `=/`)把整個 home 砍掉。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

_ENV_VAR = "WASTE_DATA_DIR"
_DEFAULT = "~/.waste-for-agents"
_DB_NAME = "waste.db"


class UnsafeTeardownError(RuntimeError):
    """teardown 目標是非預期路徑(home / root / cwd 或其祖先),拒絕刪除。"""


def data_dir() -> Path:
    """解析 data dir 絕對路徑(env 覆寫 → 預設 ~/.waste-for-agents;展開 ~ 並 resolve)。"""
    raw = os.environ.get(_ENV_VAR) or _DEFAULT
    return Path(raw).expanduser().resolve()


def ensure_data_dir() -> Path:
    """建立 data dir(含父層)並回其路徑;已存在則 no-op。"""
    d = data_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
    """SQLite db 路徑(data dir 下的 waste.db)。"""
    return data_dir() / _DB_NAME


def _assert_safe_to_delete(d: Path) -> None:
    """拒刪 root / home / cwd 及其祖先(catastrophic misconfig 防線)。"""
    home = Path.home().resolve()
    cwd = Path.cwd().resolve()
    unsafe = {Path(d.anchor), home, cwd}
    unsafe |= set(home.parents) | set(cwd.parents)
    if d in unsafe:
        raise UnsafeTeardownError(f"拒絕刪除非預期路徑:{d}")


def teardown() -> bool:
    """刪整個 data dir(僅該 dir)。回是否真的刪了東西;不存在回 False。

    先過 `_assert_safe_to_delete` 安全閘——非預期路徑拋 UnsafeTeardownError,不刪。
    """
    d = data_dir()
    _assert_safe_to_delete(d)
    if not d.exists():
        return False
    shutil.rmtree(d)
    return True
