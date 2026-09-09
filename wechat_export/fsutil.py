from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Any


def ensure_dir(path: Path, mode: int = 0o700) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, mode)
    return path


def chmod_file(path: Path, mode: int = 0o600) -> None:
    os.chmod(path, mode)


def write_json(path: Path, payload: Any, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    chmod_file(path, mode)


def write_jsonl(path: Path, rows: Iterator[dict[str, Any]], mode: int = 0o600) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    count = 0
    with tmp.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            count += 1
    os.replace(tmp, path)
    chmod_file(path, mode)
    return count


def sha256_file(path: Path, bufsize: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(bufsize)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def iter_regular_files(root: Path) -> Iterator[Path]:
    root = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        filenames.sort()
        for name in filenames:
            path = Path(dirpath) / name
            try:
                st = path.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(st.st_mode):
                yield path
                continue
            if stat.S_ISREG(st.st_mode):
                yield path


def file_record(root: Path, path: Path, digest: str | None = None) -> dict[str, Any]:
    st = path.lstat()
    rel = path.relative_to(root).as_posix()
    is_link = stat.S_ISLNK(st.st_mode)
    rec: dict[str, Any] = {
        "path": rel,
        "size": 0 if is_link else st.st_size,
        "mode": st.st_mode,
        "mtime_ns": st.st_mtime_ns,
        "dev": st.st_dev,
        "ino": st.st_ino,
        "nlink": st.st_nlink,
        "is_symlink": is_link,
        "sha256": None if is_link else digest,
    }
    if is_link:
        rec["symlink_target"] = os.readlink(path)
    return rec


def utc_now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="microseconds")
