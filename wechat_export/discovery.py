"""Read-only discovery of local xWeChat account directories. No identity guessing."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from wechat_export.config import default_xwechat_root

SKIP_DIR_NAMES = {
    "Backup",
    "backup",
    "all_users",
    "WMPF",
    "wmpf",
    "crash",
    "Crash",
}


def account_id_for(dir_name: str) -> str:
    digest = hashlib.sha256(dir_name.encode("utf-8")).hexdigest()[:12]
    return f"acc_{digest}"


def _dir_size_bytes(path: Path, limit_files: int = 4000) -> int:
    total = 0
    seen = 0
    for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
        dirnames[:] = [name for name in dirnames if name not in {".git"}]
        for name in filenames:
            file_path = Path(dirpath) / name
            try:
                total += file_path.stat().st_size
            except OSError:
                continue
            seen += 1
            if seen >= limit_files:
                return total
    return total


def discover_accounts(xwechat_root: Path | None = None) -> dict[str, Any]:
    root = xwechat_root or default_xwechat_root()
    report: dict[str, Any] = {
        "xwechat_root_exists": root.exists(),
        "xwechat_root_readable": False,
        "status": "ok",
        "accounts": [],
        "notes": [
            "Directory names are not treated as a person's identity.",
            "Confirm the account yourself; this tool will not guess from truncated names.",
        ],
    }
    if not root.exists():
        report["status"] = "xwechat_root_missing"
        return report
    try:
        children = list(root.iterdir())
    except PermissionError:
        report["status"] = "permission_denied"
        return report
    except OSError:
        report["status"] = "unreadable"
        return report
    report["xwechat_root_readable"] = True
    accounts: list[dict[str, Any]] = []
    for child in sorted(children, key=lambda p: p.name):
        if child.is_symlink() or not child.is_dir() or child.name in SKIP_DIR_NAMES or child.name.startswith("."):
            continue
        db_storage = child / "db_storage"
        msg = child / "msg"
        if not db_storage.is_dir() and not msg.is_dir():
            continue
        db_count = 0
        if db_storage.is_dir():
            try:
                db_count = sum(1 for p in db_storage.rglob("*.db") if p.is_file())
            except OSError:
                db_count = 0
        mtime = None
        try:
            mtime = int(child.stat().st_mtime)
        except OSError:
            pass
        accounts.append(
            {
                "account_id": account_id_for(child.name),
                "dir_name": child.name,
                "has_db_storage": db_storage.is_dir(),
                "has_msg": msg.is_dir(),
                "db_file_count": db_count,
                "approx_size_bytes": _dir_size_bytes(child),
                "mtime_epoch": mtime,
            }
        )
    report["accounts"] = accounts
    if not accounts:
        report["status"] = "no_accounts"
    elif len(accounts) == 1:
        report["status"] = "single_account"
    else:
        report["status"] = "multiple_accounts"
    return report


def resolve_account_dir(account_id: str, xwechat_root: Path | None = None) -> Path:
    report = discover_accounts(xwechat_root)
    for item in report["accounts"]:
        if item["account_id"] == account_id:
            root = xwechat_root or default_xwechat_root()
            return (root / item["dir_name"]).resolve()
    raise FileNotFoundError("unknown account_id")
