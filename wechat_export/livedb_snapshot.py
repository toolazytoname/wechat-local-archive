"""Copy live-db files as db+wal+shm units. Never checkpoint the originals."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from wechat_export.fsutil import ensure_dir, sha256_file, utc_now_iso, write_json


def wechat_pids() -> list[int]:
    proc = subprocess.run(["pgrep", "-f", r"(^|/)(WeChat|WeChatHelper)( |$|\.app/)"], capture_output=True, text=True, timeout=10)
    if proc.returncode not in (0, 1):
        raise RuntimeError("process_inspection_failed")
    tokens = proc.stdout.split()
    if (proc.returncode == 0 and not tokens) or any(not p.isdigit() for p in tokens):
        raise RuntimeError("process_inspection_failed")
    if proc.returncode == 1 and tokens:
        raise RuntimeError("process_inspection_failed")
    return [int(p) for p in tokens]


def paths_open_in_wechat(paths: list[Path]) -> list[str]:
    pids = wechat_pids()
    if not pids:
        return []
    wanted = {str(p) for p in paths if p.exists()}
    held: list[str] = []
    for pid in pids:
        try:
            proc = subprocess.run(["lsof", "-p", str(pid), "-Fn"],
                                  capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError("file_occupancy_inspection_failed") from exc
        # A disappeared PID or permission error is not evidence of an idle DB.
        # Stop and let the operator retry instead of labelling an unknown copy idle.
        if proc.returncode != 0 or proc.stderr.strip() or f"p{pid}" not in proc.stdout.splitlines():
            raise RuntimeError("file_occupancy_inspection_failed")
        for line in proc.stdout.splitlines():
            if line.startswith("n") and line[1:] in wanted:
                held.append(line[1:])
    return sorted(set(held))


def assert_files_idle(paths: list[Path]) -> None:
    """Inspect file handles across visible processes, not just WeChat names.

    This is an observation, not a lock against later writers. Signature/exit
    gates and before/after source hashes are still required. Inspection errors
    and unexpected lsof output fail closed; no sudo or permissions are changed.
    """
    for offset in range(0, len(paths), 128):
        batch = paths[offset:offset + 128]
        try:
            proc = subprocess.run(['lsof', '-n', '-P', '-Fpn', '--', *map(str, batch)],
                                  capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError('file_occupancy_inspection_failed') from exc
        if proc.stderr.strip() or proc.returncode not in (0, 1):
            raise RuntimeError('file_occupancy_inspection_failed')
        if proc.returncode == 1 and not proc.stdout.strip():
            continue
        if proc.returncode == 0 and any(line.startswith('p') and line[1:].isdigit() for line in proc.stdout.splitlines()):
            raise RuntimeError('source_files_open')
        raise RuntimeError('file_occupancy_inspection_failed')


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


def snapshot_strict(src_root: Path, dest_root: Path, cancelled=lambda: False) -> dict:
    """Fail closed, source-before/source-after and destination hash equality."""
    if wechat_pids():
        raise RuntimeError('wechat_running')
    if dest_root.exists() or src_root.is_symlink() or not src_root.is_dir():
        raise RuntimeError('invalid_snapshot_paths')
    def inventory(root):
        result = {}
        for p in sorted(root.rglob('*')):
            if cancelled():
                raise RuntimeError('cancelled')
            if p.is_symlink():
                raise RuntimeError('snapshot_symlink')
            if p.is_file() and (p.name.endswith('.db') or p.name.endswith('.db-wal') or p.name.endswith('.db-shm')):
                result[p.relative_to(root).as_posix()] = sha256_file(p)
        return result
    before = inventory(src_root)
    if not before:
        raise RuntimeError('empty_snapshot')
    source_files = [src_root / rel for rel in before]
    assert_files_idle(source_files)
    ensure_dir(dest_root)
    for rel in before:
        if cancelled() or wechat_pids():
            raise RuntimeError('snapshot_interrupted')
        dst = dest_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_root / rel, dst)
    assert_files_idle(source_files)
    if wechat_pids() or before != inventory(src_root) or before != inventory(dest_root):
        raise RuntimeError('snapshot_changed')
    summary = {'consistency': 'idle_hash_verified', 'files': before,
               'db_count': sum(p.endswith('.db') for p in before), 'hot_copies': 0,
               'file_occupancy': 'all_visible_processes_checked_before_and_after'}
    write_json(dest_root.parent / 'live-snapshot-consistency.json', summary)
    return summary
