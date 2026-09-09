"""Read-only RMFH container observations.

Public write-ups describe AES-GCM with a 16-byte key taken from the phone,
IV at header offset 19 (12 bytes), tag in the RMFT trailer. That layout is
treated as a hypothesis until HMAC/GCM tag verification succeeds on a local
sample with a user-authorized key. This module does not guess keys.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path
from typing import Any

RMFH_MAGIC = b"RMFH"
RMFT_MAGIC = b"RMFT"
HEADER_LEN = 128
TRAILER_LEN = 128
HYPOTHESIS_IV_OFFSET = 19
HYPOTHESIS_IV_LEN = 12
HYPOTHESIS_TAG_OFFSET_IN_TRAILER = 10
HYPOTHESIS_TAG_LEN = 16


def classify_relpath(rel: str) -> str:
    parts = Path(rel).parts
    if "ChatPackage" in parts:
        return "chat_package"
    if "Media" in parts:
        return "media"
    if "Index" in parts:
        return "index"
    name = Path(rel).name
    if name in {
        "backup.attr",
        "pkg.attr",
        "pkg_info.dat",
        "detail.dat",
        "backup_time.dat",
        "phoneid.dat",
        "phone_history.dat",
        "tar_index.dat",
        "alt_name.dat",
        "roam_device_info.dat",
    }:
        return "metadata"
    return "other"


def header_view(data: bytes) -> dict[str, Any]:
    size = len(data)
    head = data[:HEADER_LEN] if size >= HEADER_LEN else data
    tail = data[-TRAILER_LEN:] if size >= TRAILER_LEN else b""
    has_rmfh = head.startswith(RMFH_MAGIC)
    has_rmft = len(data) >= HEADER_LEN + TRAILER_LEN and data[-TRAILER_LEN:-TRAILER_LEN + 4] == RMFT_MAGIC
    view: dict[str, Any] = {
        "size": size,
        "has_rmfh": has_rmfh,
        "has_rmft_at_eof_minus_128": has_rmft,
        "sqlite_header": data[:16] == b"SQLite format 3\x00",
        "header_sha256": hashlib.sha256(head).hexdigest() if head else None,
        "type_bytes_hex": head[8:12].hex() if len(head) >= 12 else None,
        "hypothesis_iv_hex_len": None,
        "hypothesis_tag_hex_len": None,
        "ciphertext_len": max(0, size - HEADER_LEN - TRAILER_LEN) if has_rmfh and has_rmft else None,
    }
    if has_rmfh and len(head) >= HYPOTHESIS_IV_OFFSET + HYPOTHESIS_IV_LEN:
        view["hypothesis_iv_hex_len"] = HYPOTHESIS_IV_LEN
        view["hypothesis_iv_sha256"] = hashlib.sha256(
            head[HYPOTHESIS_IV_OFFSET : HYPOTHESIS_IV_OFFSET + HYPOTHESIS_IV_LEN]
        ).hexdigest()
    if has_rmft and len(tail) >= HYPOTHESIS_TAG_OFFSET_IN_TRAILER + HYPOTHESIS_TAG_LEN:
        tag = tail[HYPOTHESIS_TAG_OFFSET_IN_TRAILER : HYPOTHESIS_TAG_OFFSET_IN_TRAILER + HYPOTHESIS_TAG_LEN]
        view["hypothesis_tag_hex_len"] = len(tag)
        view["hypothesis_tag_sha256"] = hashlib.sha256(tag).hexdigest()
    return view


def inspect_tree(root: Path, limit_per_class: int | None = None) -> dict[str, Any]:
    counts = Counter()
    rmfh = Counter()
    rmft = Counter()
    sqlite = Counter()
    ivs: dict[str, set[str]] = {}
    samples: dict[str, list[dict[str, Any]]] = {}
    too_small = 0
    files = 0
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        files += 1
        rel = path.relative_to(root).as_posix()
        kind = classify_relpath(rel)
        counts[kind] += 1
        # Read only head/tail.
        size = path.stat().st_size
        if size <= HEADER_LEN + TRAILER_LEN:
            data = path.read_bytes()
        else:
            with path.open("rb") as fh:
                head = fh.read(HEADER_LEN)
                fh.seek(size - TRAILER_LEN)
                tail = fh.read(TRAILER_LEN)
            data = head + (b"\x00" * (size - HEADER_LEN - TRAILER_LEN)) + tail
        view = header_view(data)
        view["size"] = size
        if view["has_rmfh"]:
            rmfh[kind] += 1
        if view["has_rmft_at_eof_minus_128"]:
            rmft[kind] += 1
        if view["sqlite_header"]:
            sqlite[kind] += 1
        if size < HEADER_LEN + TRAILER_LEN:
            too_small += 1
        iv = view.get("hypothesis_iv_sha256")
        if iv:
            ivs.setdefault(kind, set()).add(iv)
        bucket = samples.setdefault(kind, [])
        if limit_per_class is None or len(bucket) < limit_per_class:
            bucket.append({"path": rel, **{k: v for k, v in view.items() if "hex" not in k or k.endswith("_len")}})
    return {
        "root": str(root),
        "file_count": files,
        "by_class": dict(counts),
        "rmfh_by_class": dict(rmfh),
        "rmft_by_class": dict(rmft),
        "sqlite_header_by_class": dict(sqlite),
        "files_smaller_than_header_plus_trailer": too_small,
        "unique_hypothesis_iv_count_by_class": {k: len(v) for k, v in ivs.items()},
        "hypothesis": {
            "source": "public write-up on WeChat 4.0 RMFH (phone-side AES-GCM); not locally verified",
            "header_len": HEADER_LEN,
            "trailer_len": TRAILER_LEN,
            "iv_offset": HYPOTHESIS_IV_OFFSET,
            "iv_len": HYPOTHESIS_IV_LEN,
            "tag_offset_in_trailer": HYPOTHESIS_TAG_OFFSET_IN_TRAILER,
            "tag_len": HYPOTHESIS_TAG_LEN,
            "key_source_claimed": "phone, not desktop process memory",
            "locally_verified": False,
        },
        "samples": samples,
    }
