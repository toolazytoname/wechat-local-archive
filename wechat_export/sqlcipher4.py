"""SQLCipher 4 page codec using Zetetic public defaults.

These defaults are independently checked against Homebrew `sqlcipher` 4.19.0
in tests/test_sqlcipher_official.py. They are NOT yet verified against a
real WeChat live-db file (that requires a key that passes per-page HMAC).

Observed WeChat live-db facts that are NOT the same claim:
- files are 4096-byte aligned
- files lack a plaintext SQLite header

Do not treat alignment as proof of these parameters.
"""

from __future__ import annotations

import hashlib
import hmac
import sqlite3
import struct
from dataclasses import dataclass
from pathlib import Path

from Crypto.Cipher import AES

PAGE_SIZE = 4096
SALT_SIZE = 16
IV_SIZE = 16
HMAC_SIZE = 64
RESERVE_SIZE = 80  # IV(16) + HMAC(64)
KEY_SIZE = 32
KDF_ITER = 256000
HMAC_KDF_ITER = 2
SQLITE_HEADER = b"SQLite format 3\x00"

# Public SQLCipher 4 defaults (Zetetic). Provenance is the official library,
# not a WeChat sample.
PARAM_PROVENANCE = "zetetic_sqlcipher_4_defaults"
WECHAT_LIVE_DB_PARAMS_VERIFIED = False


class SqlCipherError(RuntimeError):
    pass


class TruncatedDatabaseError(SqlCipherError):
    pass


class PageHmacError(SqlCipherError):
    def __init__(self, message: str, page: int | None = None) -> None:
        super().__init__(message)
        self.page = page


@dataclass(frozen=True)
class SqlCipher4Params:
    page_size: int = PAGE_SIZE
    kdf_iter: int = KDF_ITER
    hmac_kdf_iter: int = HMAC_KDF_ITER
    hmac_algorithm: str = "sha512"
    kdf_algorithm: str = "sha512"
    reserve: int = RESERVE_SIZE
    hmac_pgno_endian: str = "le"
    provenance: str = PARAM_PROVENANCE
    wechat_live_db_verified: bool = WECHAT_LIVE_DB_PARAMS_VERIFIED


SQLCIPHER4_DEFAULTS = SqlCipher4Params()


def derive_raw_key(passphrase: bytes, salt: bytes, params: SqlCipher4Params = SQLCIPHER4_DEFAULTS) -> bytes:
    return hashlib.pbkdf2_hmac(params.kdf_algorithm, passphrase, salt, params.kdf_iter, dklen=KEY_SIZE)


def derive_mac_key(raw_key: bytes, salt: bytes, params: SqlCipher4Params = SQLCIPHER4_DEFAULTS) -> bytes:
    mac_salt = bytes(b ^ 0x3A for b in salt)
    return hashlib.pbkdf2_hmac(params.kdf_algorithm, raw_key, mac_salt, params.hmac_kdf_iter, dklen=KEY_SIZE)


def _pack_pgno(pgno: int, params: SqlCipher4Params) -> bytes:
    if params.hmac_pgno_endian == "le":
        return struct.pack("<I", pgno)
    if params.hmac_pgno_endian == "be":
        return struct.pack(">I", pgno)
    raise SqlCipherError(f"unsupported hmac_pgno_endian: {params.hmac_pgno_endian}")


def page_hmac_data(page: bytes, pgno: int, params: SqlCipher4Params = SQLCIPHER4_DEFAULTS) -> bytes:
    if len(page) != params.page_size:
        raise TruncatedDatabaseError(f"page {pgno} length {len(page)} != {params.page_size}")
    end = params.page_size - params.reserve + IV_SIZE
    if pgno == 1:
        return page[SALT_SIZE:end]
    return page[:end]


def stored_page_hmac(page: bytes, params: SqlCipher4Params = SQLCIPHER4_DEFAULTS) -> bytes:
    return page[params.page_size - HMAC_SIZE : params.page_size]


def compute_page_hmac(page: bytes, mac_key: bytes, pgno: int, params: SqlCipher4Params = SQLCIPHER4_DEFAULTS) -> bytes:
    digest = hmac.new(mac_key, page_hmac_data(page, pgno, params), hashlib.sha512)
    digest.update(_pack_pgno(pgno, params))
    return digest.digest()


def page_hmac_ok(page: bytes, raw_key: bytes, pgno: int, salt: bytes, params: SqlCipher4Params = SQLCIPHER4_DEFAULTS) -> bool:
    mac_key = derive_mac_key(raw_key, salt, params)
    return hmac.compare_digest(compute_page_hmac(page, mac_key, pgno, params), stored_page_hmac(page, params))


def page1_hmac_ok(page: bytes, raw_key: bytes, params: SqlCipher4Params = SQLCIPHER4_DEFAULTS) -> bool:
    if len(page) != params.page_size:
        return False
    return page_hmac_ok(page, raw_key, 1, page[:SALT_SIZE], params)


def verify_all_pages(data: bytes, raw_key: bytes, params: SqlCipher4Params = SQLCIPHER4_DEFAULTS) -> int:
    if len(data) < params.page_size:
        raise TruncatedDatabaseError(f"file shorter than one page ({len(data)} < {params.page_size})")
    if len(data) % params.page_size != 0:
        raise TruncatedDatabaseError(
            f"file size {len(data)} is not a multiple of page_size {params.page_size}; refusing to pad"
        )
    salt = data[:SALT_SIZE]
    pages = len(data) // params.page_size
    for pgno in range(1, pages + 1):
        start = (pgno - 1) * params.page_size
        page = data[start : start + params.page_size]
        if not page_hmac_ok(page, raw_key, pgno, salt, params):
            raise PageHmacError(f"HMAC failed on page {pgno}/{pages}", page=pgno)
    return pages


def decrypt_page(page: bytes, raw_key: bytes, pgno: int, params: SqlCipher4Params = SQLCIPHER4_DEFAULTS) -> bytes:
    if len(page) != params.page_size:
        raise TruncatedDatabaseError(f"page {pgno} length {len(page)} != {params.page_size}; refusing to pad")
    iv = page[params.page_size - params.reserve : params.page_size - params.reserve + IV_SIZE]
    cipher = AES.new(raw_key, AES.MODE_CBC, iv)
    if pgno == 1:
        encrypted = page[SALT_SIZE : params.page_size - params.reserve]
        decrypted = cipher.decrypt(encrypted)
        return SQLITE_HEADER + decrypted + (b"\x00" * params.reserve)
    encrypted = page[: params.page_size - params.reserve]
    decrypted = cipher.decrypt(encrypted)
    return decrypted + (b"\x00" * params.reserve)


def decrypt_database(src: Path, dst: Path, raw_key: bytes, params: SqlCipher4Params = SQLCIPHER4_DEFAULTS) -> dict:
    """Decrypt the main database file only. Does not apply WAL.

    Callers with a non-empty -wal must use sqlcipher_cli.export_plaintext.
    """
    data = src.read_bytes()
    if data[:16] == SQLITE_HEADER:
        raise SqlCipherError(f"refusing to decrypt plaintext sqlite: {src}")
    pages = verify_all_pages(data, raw_key, params)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    with tmp.open("wb") as out:
        for pgno in range(1, pages + 1):
            start = (pgno - 1) * params.page_size
            out.write(decrypt_page(data[start : start + params.page_size], raw_key, pgno, params))
    tmp.replace(dst)
    header = dst.read_bytes()[:16]
    if header != SQLITE_HEADER:
        raise SqlCipherError(f"decrypted header is not sqlite: {dst}")
    return {
        "pages": pages,
        "bytes_in": len(data),
        "path": str(dst),
        "wal_applied": False,
        "params_provenance": params.provenance,
        "wechat_live_db_params_verified": params.wechat_live_db_verified,
    }


def sqlite_integrity(path: Path) -> dict:
    uri = f"file:{path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        conn.execute("PRAGMA query_only=ON")
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY 1")]
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        except sqlite3.DatabaseError as exc:
            integrity = f"error:{exc}"
        return {"tables": tables, "table_count": len(tables), "integrity_check": integrity}
    finally:
        conn.close()
