"""Operator config. No personal account ids are hardcoded as defaults."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


class ConfigError(FileNotFoundError):
    pass


DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_ROOT = DEFAULT_PROJECT_ROOT / "data"
DEFAULT_CONFIG_PATH = DEFAULT_DATA_ROOT / "private" / "config.json"


def default_xwechat_root() -> Path:
    return Path.home() / "Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files"


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
    account: str = ""
    live_account: str = ""

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


def resolve_from_config(value: str | Path, config_path: Path) -> Path:
    """Relative paths are interpreted from the config file directory, not cwd."""
    path = _as_path(value)
    if path.is_absolute():
        return path
    return (config_path.parent / path).resolve()


def load_config(path: str | Path | None = None) -> AppConfig:
    config_path = _as_path(path or os.environ.get("WECHAT_EXPORT_CONFIG") or DEFAULT_CONFIG_PATH)
    if not config_path.exists():
        raise ConfigError(
            f"config not found: {config_path}. Copy config.example.json and set account/live_account."
        )
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    data_root = resolve_from_config(raw.get("data_root", ".."), config_path)
    xwechat = _as_path(raw["xwechat_root"]) if raw.get("xwechat_root") else default_xwechat_root()
    account = (raw.get("account") or "").strip()
    backup_set = (raw.get("backup_set") or "").strip()
    live_account = (raw.get("live_account") or "").strip()
    if not raw.get("live_account_root") and not live_account:
        raise ConfigError("config missing live_account (and live_account_root); refusing to guess an account id")
    if not raw.get("account_backup_root") and not account:
        raise ConfigError("config missing account (and account_backup_root); refusing to guess an account id")
    live_account_root = (
        resolve_from_config(raw["live_account_root"], config_path)
        if raw.get("live_account_root")
        else xwechat / live_account
    )
    account_backup_root = (
        resolve_from_config(raw["account_backup_root"], config_path)
        if raw.get("account_backup_root")
        else xwechat / "Backup" / account
    )
    if raw.get("source_backup2"):
        source_backup2 = resolve_from_config(raw["source_backup2"], config_path)
    elif account and backup_set:
        source_backup2 = xwechat / "Backup" / account / backup_set / "files" / "2"
    else:
        source_backup2 = account_backup_root / "files" / "2"
    keys = raw.get("keys_path")
    return AppConfig(
        project_root=resolve_from_config(raw.get("project_root", "../.."), config_path),
        data_root=data_root,
        xwechat_root=xwechat,
        account_backup_root=account_backup_root,
        backup_set=backup_set,
        source_backup2=source_backup2,
        live_account_root=live_account_root,
        live_db_root=resolve_from_config(raw["live_db_root"], config_path)
        if raw.get("live_db_root")
        else live_account_root / "db_storage",
        display_timezone=raw.get("display_timezone", "America/Los_Angeles"),
        keys_path=resolve_from_config(keys, config_path) if keys else None,
        config_path=config_path,
        target_names=tuple(raw.get("target_names") or ()),
        account=account,
        live_account=live_account,
    )


def default_config_payload() -> dict:
    return {
        "project_root": "../..",
        "data_root": "..",
        "xwechat_root": "~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files",
        "account": "",
        "backup_set": "",
        "live_account": "",
        "display_timezone": "America/Los_Angeles",
        "keys_path": None,
        "target_names": [],
    }
