"""單一 data dir:所有落地物(SQLite db)集中於此,並提供安全 teardown。

預設 `~/.waste-for-agents/`,可由 env `WASTE_DATA_DIR` 覆寫(測試 / 多實例 / 自訂位置)。
teardown 只刪解析出的 data dir 本身,且拒刪 home / root / cwd 與其祖先——防 misconfig
(如 `WASTE_DATA_DIR=~` 或 `=/`)把整個 home 砍掉。
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

_ENV_VAR = "WASTE_DATA_DIR"
_DEFAULT = "~/.waste-for-agents"
_DB_NAME = "waste.db"


class UnsafeTeardownError(RuntimeError):
    """teardown 目標是非預期路徑(home / root / cwd 或其祖先),拒絕刪除。"""


def data_dir() -> Path:
    """解析 data dir 絕對路徑(env 覆寫 → 預設 ~/.waste-for-agents;展開 ~ 並 resolve)。

    env 值先 strip:空字串 / 純空白都退回預設(否則 " " 會被當相對路徑解析成 cwd 下怪 dir,
    繞過 teardown 安全閘)。
    """
    raw = (os.environ.get(_ENV_VAR) or "").strip() or _DEFAULT
    return Path(raw).expanduser().resolve()


def ensure_data_dir() -> Path:
    """建立 data dir(含父層)並回其路徑;已存在則 no-op。"""
    d = data_dir()
    if d.exists() and not d.is_dir():
        raise UnsafeTeardownError(f"data dir 路徑已存在但不是目錄:{d}")
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
    """SQLite db 路徑(data dir 下的 waste.db)。"""
    return data_dir() / _DB_NAME


def _assert_safe_to_delete(d: Path) -> None:
    """catastrophic misconfig 防線:白名單(必在 home 或暫存目錄下)疊黑名單(拒 home/cwd/root/祖先)。

    白名單擋掉所有不在 home/tempdir 下的路徑(`/usr`、`/etc`、`/var`→`/private/var` 等系統
    dir,純深度閘抓不到 symlink 解析後的 `/private/var`)。黑名單再擋「雖在 home 下但不該刪」的
    cwd、home 本身、root 與其祖先。兩層都過才允許 rmtree。
    """
    home = Path.home().resolve()
    cwd = Path.cwd().resolve()
    tmp = Path(tempfile.gettempdir()).resolve()
    # 白名單:d 必須是 home 或 tempdir 的「真子孫」(非根本身)
    if not any(root in d.parents for root in (home, tmp)):
        raise UnsafeTeardownError(f"data dir 須在 home 或暫存目錄下,拒絕刪除:{d}")
    # 黑名單:即使在安全根下,仍拒 cwd / home / root / 祖先
    forbidden = {Path(d.anchor), home, cwd}
    forbidden |= set(home.parents) | set(cwd.parents)
    if d in forbidden:
        raise UnsafeTeardownError(f"拒絕刪除非預期路徑:{d}")


def teardown() -> bool:
    """刪整個 data dir(僅該 dir)。回是否真的刪了東西;不存在回 False。

    先過 `_assert_safe_to_delete` 安全閘——非預期路徑拋 UnsafeTeardownError,不刪。
    目標存在但不是目錄(誤指到檔案)→ 同樣拒絕,不吐 NotADirectoryError。
    """
    d = data_dir()
    _assert_safe_to_delete(d)
    if not d.exists():
        return False
    if not d.is_dir():
        raise UnsafeTeardownError(f"data dir 路徑不是目錄,拒絕刪除:{d}")
    shutil.rmtree(d)
    return True
