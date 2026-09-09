from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from wechat_export.config import AppConfig
from wechat_export.environment import collect_environment
from wechat_export.fsutil import (
    chmod_file,
    ensure_dir,
    file_record,
    iter_regular_files,
    sha256_file,
    utc_now_iso,
    write_json,
    write_jsonl,
)


class SnapshotError(RuntimeError):
    pass


def make_snapshot_id(now: datetime | None = None, tz_name: str = "America/Los_Angeles") -> str:
    now = now or datetime.now(ZoneInfo(tz_name))
    offset = now.strftime("%z")
    return now.strftime("%Y%m%dT%H%M%S") + offset


def tree_stats(root: Path) -> dict[str, Any]:
    root = root.resolve()
    files = 0
    bytes_ = 0
    newest_mtime_ns = 0
    oldest_mtime_ns: int | None = None
    newest_path = None
    oldest_path = None
    for path in iter_regular_files(root):
        st = path.lstat()
        files += 1
        if path.is_symlink():
            continue
        bytes_ += st.st_size
        if oldest_mtime_ns is None or st.st_mtime_ns < oldest_mtime_ns:
            oldest_mtime_ns = st.st_mtime_ns
            oldest_path = path.relative_to(root).as_posix()
        if st.st_mtime_ns >= newest_mtime_ns:
            newest_mtime_ns = st.st_mtime_ns
            newest_path = path.relative_to(root).as_posix()
    return {
        "file_count": files,
        "byte_count": bytes_,
        "oldest_mtime_ns": oldest_mtime_ns,
        "newest_mtime_ns": newest_mtime_ns,
        "oldest_path": oldest_path,
        "newest_path": newest_path,
    }


def build_manifest(root: Path, progress_path: Path | None = None) -> list[dict[str, Any]]:
    root = root.resolve()
    rows: list[dict[str, Any]] = []
    for path in iter_regular_files(root):
        digest = None if path.is_symlink() else sha256_file(path)
        rows.append(file_record(root, path, digest))
        if progress_path and len(rows) % 200 == 0:
            progress_path.write_text(f"hashed {len(rows)} files last={path.name}\n", encoding="utf-8")
    if progress_path:
        progress_path.write_text(f"hashed {len(rows)} files done\n", encoding="utf-8")
    return rows


def compare_manifests(src_rows: list[dict[str, Any]], dst_rows: list[dict[str, Any]]) -> dict[str, Any]:
    src_map = {r["path"]: r for r in src_rows}
    dst_map = {r["path"]: r for r in dst_rows}
    missing_in_dest = sorted(set(src_map) - set(dst_map))
    extra_in_dest = sorted(set(dst_map) - set(src_map))
    hash_mismatch = []
    size_mismatch = []
    for path in sorted(set(src_map) & set(dst_map)):
        s, d = src_map[path], dst_map[path]
        if s.get("is_symlink") or d.get("is_symlink"):
            if s.get("symlink_target") != d.get("symlink_target"):
                hash_mismatch.append(path)
            continue
        if s["size"] != d["size"]:
            size_mismatch.append(path)
        if s["sha256"] != d["sha256"]:
            hash_mismatch.append(path)
    ok = not missing_in_dest and not extra_in_dest and not hash_mismatch and not size_mismatch
    return {
        "ok": ok,
        "missing_in_dest": missing_in_dest,
        "extra_in_dest": extra_in_dest,
        "hash_mismatch": hash_mismatch,
        "size_mismatch": size_mismatch,
        "compared_files": len(set(src_map) & set(dst_map)),
    }


def ditto_copy(src: Path, dst: Path) -> None:
    if dst.exists():
        raise SnapshotError(f"refusing to copy into existing path: {dst}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(["ditto", str(src), str(dst)], capture_output=True, text=True)
    if result.returncode != 0:
        raise SnapshotError(f"ditto failed: {result.stderr.strip() or result.stdout.strip()}")


def snapshot_backup_account(cfg: AppConfig, snapshot_id: str | None = None) -> dict[str, Any]:
    src = cfg.account_backup_root
    if not src.is_dir():
        raise SnapshotError(f"account backup root missing: {src}")
    snapshot_id = snapshot_id or make_snapshot_id(tz_name=cfg.display_timezone)
    snap_root = cfg.raw_root / snapshot_id
    dest = snap_root / "backup-account"
    if snap_root.exists():
        raise SnapshotError(f"snapshot already exists: {snap_root}")

    ensure_dir(cfg.data_root)
    ensure_dir(cfg.raw_root)
    ensure_dir(cfg.private_root)
    ensure_dir(cfg.work_root)
    ensure_dir(cfg.normalized_root)
    ensure_dir(cfg.exports_root)
    ensure_dir(cfg.reports_root)
    ensure_dir(snap_root)

    started = utc_now_iso()
    stats_before = tree_stats(src)
    env = collect_environment(
        {
            "snapshot_id": snapshot_id,
            "source_root": str(src),
            "dest_root": str(dest),
            "source_stats_before": stats_before,
        }
    )
    write_json(snap_root / "environment.json", env)

    progress = snap_root / "progress.txt"
    progress.write_text("hashing source\n", encoding="utf-8")
    src_rows = build_manifest(src, progress_path=progress)
    write_jsonl(snap_root / "source-manifest.jsonl", iter(src_rows))

    progress.write_text("ditto copy\n", encoding="utf-8")
    ditto_copy(src, dest)
    os.chmod(dest, 0o700)

    progress.write_text("hashing dest\n", encoding="utf-8")
    dst_rows = build_manifest(dest, progress_path=progress)
    write_jsonl(snap_root / "dest-manifest.jsonl", iter(dst_rows))

    stats_after = tree_stats(src)
    dest_stats = tree_stats(dest)
    comparison = compare_manifests(src_rows, dst_rows)
    source_changed = (
        stats_before["byte_count"] != stats_after["byte_count"]
        or stats_before["file_count"] != stats_after["file_count"]
        or stats_before["newest_mtime_ns"] != stats_after["newest_mtime_ns"]
    )
    status = "ok" if comparison["ok"] and not source_changed else "failed"
    verification = {
        "snapshot_id": snapshot_id,
        "started_at_utc": started,
        "finished_at_utc": utc_now_iso(),
        "source_root": str(src),
        "dest_root": str(dest),
        "tool": "ditto",
        "source_stats_before": stats_before,
        "source_stats_after": stats_after,
        "dest_stats": dest_stats,
        "source_changed_during_copy": source_changed,
        "comparison": comparison,
        "status": status,
        "raw_preservation_complete": status == "ok",
    }
    write_json(snap_root / "copy-verification.json", verification)
    report_dir = ensure_dir(cfg.reports_root / snapshot_id)
    shutil.copy2(snap_root / "copy-verification.json", report_dir / "copy-verification.json")
    shutil.copy2(snap_root / "environment.json", report_dir / "environment.json")
    chmod_file(report_dir / "copy-verification.json")
    chmod_file(report_dir / "environment.json")
    if status != "ok":
        raise SnapshotError(
            "snapshot verification failed; source may have changed or copy is incomplete. "
            f"see {snap_root / 'copy-verification.json'}"
        )
    return verification
