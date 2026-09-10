from __future__ import annotations

from pathlib import Path
from typing import Any

SQLITE_HEADER = b"SQLite format 3\x00"


def inspect_livedb(root: Path) -> dict[str, Any]:
    dbs = []
    for path in sorted(root.rglob("*.db")):
        if path.is_symlink() or not path.is_file():
            continue
        if path.name.endswith("-wal") or path.name.endswith("-shm"):
            continue
        wal = Path(str(path) + "-wal")
        shm = Path(str(path) + "-shm")
        with path.open("rb") as stream:
            head = stream.read(16)
        rec = {
            "path": path.relative_to(root).as_posix(),
            "size": path.stat().st_size,
            "salt_len": 16 if len(head) >= 16 else len(head),
            "salt_sha256": __import__("hashlib").sha256(head[:16]).hexdigest() if len(head) >= 16 else None,
            "has_sqlite_header": head == SQLITE_HEADER,
            "wal_exists": wal.exists(),
            "wal_size": wal.stat().st_size if wal.exists() else 0,
            "shm_exists": shm.exists(),
            "shm_size": shm.stat().st_size if shm.exists() else 0,
            "page_aligned_4096": path.stat().st_size % 4096 == 0,
            "sqlcipher4_params": "unverified",
        }
        dbs.append(rec)
    return {
        "root": str(root),
        "db_count": len(dbs),
        "sqlite_header_count": sum(1 for d in dbs if d["has_sqlite_header"]),
        "with_wal": sum(1 for d in dbs if d["wal_exists"]),
        "with_shm": sum(1 for d in dbs if d["shm_exists"]),
        "page_aligned_4096_count": sum(1 for d in dbs if d["page_aligned_4096"]),
        "sqlcipher4_params": "unverified",
        "sqlcipher4_params_note": (
            "4096-byte alignment is an observation. SQLCipher 4 defaults "
            "(page_size 4096, KDF 256000, HMAC-SHA512, reserve 80) are Zetetic "
            "library defaults, verified only against official sqlcipher fixtures, "
            "not against a WeChat live-db HMAC."
        ),
        "databases": dbs,
    }
