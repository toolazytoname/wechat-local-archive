from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_ROOT = DEFAULT_PROJECT_ROOT / "data"
DEFAULT_CONFIG_PATH = DEFAULT_DATA_ROOT / "private" / "config.json"
DEFAULT_XWECHAT = Path(
    "/Users/lazy/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files"
)
DEFAULT_ACCOUNT = "shuitaiyang747"
DEFAULT_BACKUP_SET = "f75aaf601ceed30bfbd93066b7a40e82"
DEFAULT_LIVE_ACCOUNT = "shuitaiyang747_0403"


@dataclass(frozen=True)
class AppConfig:
    project_root: Path
    data_root: Path
    xwechat_root: Path
    account_backup_root: Path
    backup_set: str
    source_backup2: Path
    live_account_root: Path
    live_db_root: Path
    display_timezone: str
    keys_path: Path | None
    config_path: Path
    target_names: tuple[str, ...] = ()

    @property
    def raw_root(self) -> Path:
        return self.data_root / "raw"

    @property
    def private_root(self) -> Path:
        return self.data_root / "private"

    @property
    def work_root(self) -> Path:
        return self.data_root / "work"

    @property
    def normalized_root(self) -> Path:
        return self.data_root / "normalized"

    @property
    def exports_root(self) -> Path:
        return self.data_root / "exports"

    @property
    def reports_root(self) -> Path:
        return self.data_root / "reports"


def _as_path(value: str | Path) -> Path:
    return Path(value).expanduser()


def load_config(path: str | Path | None = None) -> AppConfig:
    config_path = _as_path(path or os.environ.get("WECHAT_EXPORT_CONFIG") or DEFAULT_CONFIG_PATH)
    if not config_path.exists():
        raise FileNotFoundError(f"config not found: {config_path}")
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    data_root = _as_path(raw.get("data_root", DEFAULT_DATA_ROOT))
    xwechat = _as_path(raw.get("xwechat_root", DEFAULT_XWECHAT))
    account = raw.get("account", DEFAULT_ACCOUNT)
    backup_set = raw.get("backup_set", DEFAULT_BACKUP_SET)
    live_account = raw.get("live_account", DEFAULT_LIVE_ACCOUNT)
    keys = raw.get("keys_path")
    return AppConfig(
        project_root=_as_path(raw.get("project_root", DEFAULT_PROJECT_ROOT)),
        data_root=data_root,
        xwechat_root=xwechat,
        account_backup_root=_as_path(raw.get("account_backup_root", xwechat / "Backup" / account)),
        backup_set=backup_set,
        source_backup2=_as_path(
            raw.get("source_backup2", xwechat / "Backup" / account / backup_set / "files" / "2")
        ),
        live_account_root=_as_path(raw.get("live_account_root", xwechat / live_account)),
        live_db_root=_as_path(raw.get("live_db_root", xwechat / live_account / "db_storage")),
        display_timezone=raw.get("display_timezone", "America/Los_Angeles"),
        keys_path=_as_path(keys) if keys else None,
        config_path=config_path,
        target_names=tuple(raw.get("target_names") or ()),
    )


def default_config_payload() -> dict:
    xwechat = DEFAULT_XWECHAT
    return {
        "project_root": str(DEFAULT_PROJECT_ROOT),
        "data_root": str(DEFAULT_DATA_ROOT),
        "xwechat_root": str(xwechat),
        "account": DEFAULT_ACCOUNT,
        "backup_set": DEFAULT_BACKUP_SET,
        "account_backup_root": str(xwechat / "Backup" / DEFAULT_ACCOUNT),
        "source_backup2": str(xwechat / "Backup" / DEFAULT_ACCOUNT / DEFAULT_BACKUP_SET / "files" / "2"),
        "live_account": DEFAULT_LIVE_ACCOUNT,
        "live_account_root": str(xwechat / DEFAULT_LIVE_ACCOUNT),
        "live_db_root": str(xwechat / DEFAULT_LIVE_ACCOUNT / "db_storage"),
        "display_timezone": "America/Los_Angeles",
        "keys_path": None,
        "target_names": [],
    }
