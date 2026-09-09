"""APFS clonefile tree copy with EINTR retries. Does not follow symlinks."""

from __future__ import annotations

import ctypes
import ctypes.util
import errno
import os
import shutil
import stat
from pathlib import Path

_libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
_clonefile = _libc.clonefile
_clonefile.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
_clonefile.restype = ctypes.c_int
CLONE_NOFOLLOW = 0x0001


def clonefile(src: Path, dst: Path, flags: int = CLONE_NOFOLLOW) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(8):
        rc = _clonefile(os.fsencode(src), os.fsencode(dst), flags)
        if rc == 0:
            shutil.copystat(src, dst, follow_symlinks=False)
            return
        err = ctypes.get_errno()
        if err == errno.EINTR:
            continue
        if err == errno.EEXIST:
            dst.unlink()
            continue
        raise OSError(err, os.strerror(err), str(src))
    raise OSError(errno.EINTR, "clonefile interrupted repeatedly", str(src))


def copy_one(src: Path, dst: Path) -> str:
    st = src.lstat()
    if stat.S_ISLNK(st.st_mode):
        if dst.exists() or dst.is_symlink():
            dst.unlink()
        os.symlink(os.readlink(src), dst)
        return "symlink"
    if stat.S_ISDIR(st.st_mode):
        dst.mkdir(parents=True, exist_ok=True)
        shutil.copystat(src, dst, follow_symlinks=False)
        return "dir"
    if dst.exists() and dst.is_file() and dst.stat().st_size == st.st_size:
        return "skip_same_size"
    if dst.exists():
        dst.unlink()
    try:
        clonefile(src, dst)
        return "clone"
    except OSError:
        shutil.copy2(src, dst, follow_symlinks=False)
        return "copy"


def clone_tree(src: Path, dst: Path, progress: Path | None = None) -> dict:
    src = src.resolve()
    dst.mkdir(parents=True, exist_ok=True)
    counts = {"clone": 0, "copy": 0, "skip_same_size": 0, "symlink": 0, "dir": 0, "error": 0}
    errors: list[str] = []
    n = 0
    for dirpath, dirnames, filenames in os.walk(src, followlinks=False):
        dirnames.sort()
        filenames.sort()
        rel_dir = os.path.relpath(dirpath, src)
        dest_dir = dst if rel_dir == "." else dst / rel_dir
        dest_dir.mkdir(parents=True, exist_ok=True)
        for name in filenames:
            n += 1
            s = Path(dirpath) / name
            d = dest_dir / name
            try:
                kind = copy_one(s, d)
                counts[kind] = counts.get(kind, 0) + 1
            except OSError as exc:
                counts["error"] += 1
                if len(errors) < 50:
                    errors.append(f"{s.relative_to(src)}: {exc.errno} {exc.strerror}")
            if progress and n % 500 == 0:
                progress.write_text(f"files={n} {counts}\n", encoding="utf-8")
    if progress:
        progress.write_text(f"files={n} {counts} done\n", encoding="utf-8")
    return {"files_seen": n, "counts": counts, "errors": errors}
