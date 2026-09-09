"""Copy live-db files as db+wal+shm units. Never checkpoint the originals."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from wechat_export.fsutil import ensure_dir, sha256_file, utc_now_iso, write_json


def wechat_pids() -> list[int]:
    proc = subprocess.run(["pgrep", "-x", "WeChat"], capture_output=True, text=True)
    return [int(p) for p in proc.stdout.split() if p.strip().isdigit()]


def paths_open_in_wechat(paths: list[Path]) -> list[str]:
    pids = wechat_pids()
    if not pids:
        return []
    wanted = {str(p) for p in paths if p.exists()}
    held: list[str] = []
    for pid in pids:
        proc = subprocess.run(["lsof", "-p", str(pid), "-Fn"], capture_output=True, text=True)
        for line in proc.stdout.splitlines():
            if line.startswith("n") and line[1:] in wanted:
                held.append(line[1:])
    return sorted(set(held))


def trio_for(db: Path) -> dict[str, Path]:
    return {
        "db": db,
        "wal": Path(str(db) + "-wal"),
        "shm": Path(str(db) + "-shm"),
    }


def copy_db_trio(src_db: Path, dest_db: Path) -> dict[str, Any]:
    if dest_db.exists():
        raise FileExistsError(dest_db)
    dest_db.parent.mkdir(parents=True, exist_ok=True)
    trio = trio_for(src_db)
    present = {kind: path for kind, path in trio.items() if path.exists()}
    held = paths_open_in_wechat(list(present.values()))
    # Copy WAL first, then main, then WAL again so a writer is less likely to
    # produce a main file that points past a stale WAL. This is still not the
    # SQLite backup API; if WeChat has the files open the copy is hot.
    if "wal" in present:
        shutil.copy2(present["wal"], Path(str(dest_db) + "-wal"))
    shutil.copy2(present["db"], dest_db)
    if "wal" in present:
        shutil.copy2(present["wal"], Path(str(dest_db) + "-wal"))
    if "shm" in present:
        shutil.copy2(present["shm"], Path(str(dest_db) + "-shm"))
    dest_trio = trio_for(dest_db)
    rec: dict[str, Any] = {
        "src": str(src_db),
        "dst": str(dest_db),
        "held_by_wechat": held,
        "consistency": "hot_copy_unverified" if held else "process_idle_copy",
        "parts": {},
    }
    for kind, path in dest_trio.items():
        if not path.exists():
            rec["parts"][kind] = {"present": False, "size": 0}
            continue
        rec["parts"][kind] = {
            "present": True,
            "size": path.stat().st_size,
            "sha256": sha256_file(path) if kind != "shm" else None,
        }
    return rec


def snapshot_livedb_tree(src_root: Path, dest_root: Path) -> dict[str, Any]:
    if dest_root.exists():
        raise FileExistsError(f"refusing to copy into existing path: {dest_root}")
    ensure_dir(dest_root.parent)
    dbs = sorted(
        p
        for p in src_root.rglob("*.db")
        if p.is_file() and not p.name.endswith("-wal") and not p.name.endswith("-shm")
    )
    results = []
    for src in dbs:
        rel = src.relative_to(src_root)
        dest = dest_root / rel
        results.append({"path": rel.as_posix(), **copy_db_trio(src, dest)})
    payload = {
        "collected_at_utc": utc_now_iso(),
        "src_root": str(src_root),
        "dst_root": str(dest_root),
        "wechat_pids": wechat_pids(),
        "db_count": len(results),
        "hot_copies": sum(1 for r in results if r["consistency"] == "hot_copy_unverified"),
        "idle_copies": sum(1 for r in results if r["consistency"] == "process_idle_copy"),
        "databases": results,
    }
    write_json(dest_root.parent / "live-snapshot-consistency.json", payload)
    os.chmod(dest_root.parent / "live-snapshot-consistency.json", 0o600)
    return payload
