"""Bounded local attachment reads; never follow a replaced path outside its root."""
from __future__ import annotations

import os
import re
import stat
from contextlib import contextmanager
from pathlib import Path

CHUNK_SIZE = 256 * 1024


class UnsatisfiableRange(ValueError):
    pass


def byte_range(value: str | None, size: int) -> tuple[int, int, bool]:
    """Single bytes range. Unsupported/malformed ranges are ignored (full 200)."""
    if not value or len(value) > 128:
        return 0, size, False
    match = re.fullmatch(r"bytes=([0-9]*)-([0-9]*)", value.strip())
    if not match or not any(match.groups()):
        return 0, size, False
    first, last = match.groups()
    if not first:
        length = int(last)
        if length == 0 or size == 0:
            raise UnsatisfiableRange()
        return max(0, size - length), size, True
    start = int(first)
    end = int(last) + 1 if last else size
    if last and end <= start:  # Invalid, not an unsatisfied valid range.
        return 0, size, False
    if start >= size:
        raise UnsatisfiableRange()
    return start, min(end, size), True


@contextmanager
def open_local_media(root: Path, candidate: Path):
    """Walk from an open root using no-follow dir FDs, including the final file.

    Callers provide resolved absolute paths from the archive resolver. This is
    containment, not an immutable media snapshot or a same-UID adversary defense.
    """
    root = Path(os.path.abspath(root))
    candidate = Path(os.path.abspath(candidate))
    try:
        parts = candidate.relative_to(root).parts
    except ValueError as exc:
        raise FileNotFoundError("media outside archive") from exc
    if not parts or any(p in (".", "..") for p in parts):
        raise FileNotFoundError("invalid media path")
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    fd = None
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise FileNotFoundError("media is not a regular file")
        stream = os.fdopen(fd, "rb")
        fd = None
        with stream:
            yield stream, info.st_size
    finally:
        if fd is not None:
            os.close(fd)
        os.close(directory)


def copy_range(source, target, start: int, end: int) -> None:
    """Copy exactly the selected extent with bounded memory; truncation fails."""
    source.seek(start)
    remaining = end - start
    while remaining:
        chunk = source.read(min(CHUNK_SIZE, remaining))
        if not chunk:
            raise EOFError("media changed during streaming")
        target.write(chunk)
        remaining -= len(chunk)
