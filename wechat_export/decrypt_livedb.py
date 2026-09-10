"""Decrypt live-db copies. Non-empty WAL is merged via official SQLCipher CLI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from wechat_export.sqlcipher4 import (
    SQLCIPHER4_DEFAULTS,
    PageHmacError,
    SqlCipherError,
    TruncatedDatabaseError,
    decrypt_database,
    derive_raw_key,
    sqlite_integrity,
    verify_database_pages,
    read_prefix,
)
from wechat_export.sqlcipher_cli import export_plaintext, find_sqlcipher


class WalUnmergedError(SqlCipherError):
    pass


def load_raw_keys(path: Path) -> dict[str, bytes]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    mapping = raw.get("databases", raw)
    out: dict[str, bytes] = {}
    for rel, info in mapping.items():
        if isinstance(rel, str) and rel.startswith("_"):
            continue
        if isinstance(info, str):
            out[rel] = bytes.fromhex(info)
        elif isinstance(info, dict) and info.get("enc_key_hex"):
            out[rel] = bytes.fromhex(info["enc_key_hex"])
        elif isinstance(info, dict) and info.get("enc_key"):
            out[rel] = bytes.fromhex(info["enc_key"])
    return out


def _lookup_key(keys: dict[str, bytes], rel: str, name: str) -> bytes | None:
    return keys.get(rel) or keys.get(name)


def _wal_size(db: Path) -> int:
    wal = Path(str(db) + "-wal")
    return wal.stat().st_size if wal.exists() else 0


def decrypt_one(
    src: Path,
    dst: Path,
    raw_key: bytes | None = None,
    *,
    passphrase: bytes | None = None,
    check=lambda: None,
    scratch_parent: Path | None = None,
) -> dict[str, Any]:
    check()
    wal_size = _wal_size(src)
    salt = read_prefix(src)
    if raw_key is None:
        if passphrase is None:
            raise SqlCipherError("raw_key or passphrase is required")
        if len(salt) < 16:
            raise TruncatedDatabaseError(f"{src.name} too small to contain a salt")
        raw_key = derive_raw_key(passphrase, salt)
    # Always authenticate the main file first when it is a complete page set.
    main_pages = None
    main_hmac = None
    try:
        main_pages = verify_database_pages(src, raw_key, check=check) if wal_size else None
        main_hmac = "ok"
    except TruncatedDatabaseError as exc:
        main_hmac = f"truncated:{exc}"
        if wal_size == 0:
            raise
    except PageHmacError as exc:
        main_hmac = f"hmac_failed_page_{exc.page}"
        raise

    if wal_size > 0:
        if find_sqlcipher() is None:
            raise WalUnmergedError(
                f"{src.name} has WAL {wal_size} bytes; official sqlcipher CLI is required to merge it. "
                "Refusing to decrypt the main file alone."
            )
        check()
        info = export_plaintext(src, dst, raw_key=raw_key, passphrase=None, check=check, scratch_parent=scratch_parent)
        integ = sqlite_integrity(dst)
        return {
            "path": src.name,
            "status": "ok" if integ["integrity_check"] == "ok" else "decrypted_integrity_failed",
            "method": "sqlcipher_export_wal_merged",
            "wal_size": wal_size,
            "wal_applied": True,
            "main_pages_hmac": main_hmac,
            "main_pages": main_pages,
            "table_count": integ["table_count"],
            "integrity": integ["integrity_check"],
            "params_provenance": SQLCIPHER4_DEFAULTS.provenance,
            "wechat_live_db_params_verified": SQLCIPHER4_DEFAULTS.wechat_live_db_verified,
            **{k: info[k] for k in ("wal_present", "cli") if k in info},
        }

    page_info = decrypt_database(src, dst, raw_key, check=check, scratch_parent=scratch_parent)
    integ = sqlite_integrity(dst)
    return {
        "path": src.name,
        "status": "ok" if integ["integrity_check"] == "ok" else "decrypted_integrity_failed",
        "method": "page_codec_main_file",
        "wal_size": 0,
        "wal_applied": False,
        "main_pages_hmac": main_hmac,
        "main_pages": page_info["pages"],
        "table_count": integ["table_count"],
        "integrity": integ["integrity_check"],
        "params_provenance": SQLCIPHER4_DEFAULTS.provenance,
        "wechat_live_db_params_verified": SQLCIPHER4_DEFAULTS.wechat_live_db_verified,
    }


def decrypt_tree(
    src_root: Path,
    dst_root: Path,
    keys: dict[str, bytes],
    *,
    passphrase: bytes | None = None,
) -> dict[str, Any]:
    results = []
    for db in sorted(src_root.rglob("*.db")):
        if db.name.endswith("-wal") or db.name.endswith("-shm"):
            continue
        rel = db.relative_to(src_root).as_posix()
        key = _lookup_key(keys, rel, db.name)
        if key is None and passphrase is None:
            wal = _wal_size(db)
            results.append({"path": rel, "status": "no_key", "wal_size": wal, "wal_applied": False})
            continue
        dest = dst_root / rel
        try:
            rec = decrypt_one(db, dest, key, passphrase=passphrase)
            rec["path"] = rel
            results.append(rec)
        except WalUnmergedError as exc:
            results.append({"path": rel, "status": "wal_unmerged", "error_type": type(exc).__name__, "wal_size": _wal_size(db), "wal_applied": False})
        except PageHmacError as exc:
            results.append({"path": rel, "status": "hmac_failed", "error_type": type(exc).__name__, "page": exc.page, "wal_size": _wal_size(db), "wal_applied": False})
        except TruncatedDatabaseError as exc:
            results.append({"path": rel, "status": "truncated", "error_type": type(exc).__name__, "wal_size": _wal_size(db), "wal_applied": False})
        except SqlCipherError as exc:
            results.append({"path": rel, "status": "decrypt_failed", "error_type": type(exc).__name__, "wal_size": _wal_size(db), "wal_applied": False})
    return {
        "src": str(src_root),
        "dst": str(dst_root),
        "ok": sum(1 for r in results if r["status"] == "ok"),
        "failed": sum(1 for r in results if r["status"] != "ok"),
        "wal_merged": sum(1 for r in results if r.get("wal_applied")),
        "wal_unmerged": sum(1 for r in results if r.get("status") == "wal_unmerged"),
        "params_provenance": SQLCIPHER4_DEFAULTS.provenance,
        "wechat_live_db_params_verified": SQLCIPHER4_DEFAULTS.wechat_live_db_verified,
        "results": results,
    }
