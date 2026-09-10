"""Registered snapshot/decrypt/export materials. Browser cannot pass arbitrary paths."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from wechat_export.runtime import RuntimePaths


def ops_work_root() -> Path | None:
    env = os.environ.get("WECHAT_EXPORT_OPS_ROOT")
    if env:
        return Path(env).expanduser() / "work"
    # Never discover a developer's neighboring checkout or an unrelated account.
    # Legacy operators can explicitly register the old root via the environment.
    return None


def _safe_id(source_id: str) -> str:
    if not source_id.startswith("snapshot:"):
        raise ValueError("invalid source_id")
    name = source_id.split(":", 1)[1]
    if not name or "/" in name or name in {".", ".."}:
        raise ValueError("invalid source_id")
    return name


def list_materials(runtime: RuntimePaths) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    roots = [runtime.data_root / "work"]
    ops = ops_work_root()
    if ops:
        roots.append(ops)
    seen: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            live = child / "live-db"
            pointer = child / "snapshot-pointer.json"
            decrypted = child / "decrypted"
            if pointer.is_file():
                try:
                    payload = json.loads(pointer.read_text(encoding="utf-8"))
                    live_path = Path(payload["live_db"]) if payload.get("live_db") else live
                except (OSError, json.JSONDecodeError, KeyError):
                    live_path = live
            else:
                live_path = live
            if not (live_path / "contact" / "contact.db").is_file() and not live.is_dir() and not decrypted.is_dir():
                continue
            source_id = f"snapshot:{child.name}"
            if source_id in seen:
                continue
            seen.add(source_id)
            items.append(
                {
                    "source_id": source_id,
                    "has_live_db": (live_path / "contact" / "contact.db").is_file() or live.is_dir(),
                    "has_decrypted": decrypted.is_dir() and any(decrypted.rglob("contact.db")),
                    "run_id": child.name,
                }
            )
    return items


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def resolve_materials(source_id: str, runtime: RuntimePaths) -> dict[str, Path]:
    name = _safe_id(source_id)
    ops = ops_work_root()
    roots = [runtime.data_root / "work"]
    if ops:
        roots.append(ops)
    for root in roots:
        if not root.is_dir():
            continue
        child = (root / name).resolve()
        if not _is_under(child, root):
            continue
        if not child.is_dir():
            continue
        live = child / "live-db"
        decrypted = child / "decrypted"
        pointer = child / "snapshot-pointer.json"
        if pointer.is_file():
            payload = json.loads(pointer.read_text(encoding="utf-8"))
            if payload.get("live_db"):
                live_p = Path(payload["live_db"]).resolve()
                if not any(_is_under(live_p, r) for r in roots if r.exists()):
                    raise ValueError("invalid source_id")
                live = live_p
        if live.is_dir() or decrypted.is_dir():
            return {"run_id": name, "root": child, "live_db": live, "decrypted": decrypted}
    raise FileNotFoundError("unknown snapshot")
