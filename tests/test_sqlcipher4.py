from __future__ import annotations

import hashlib
import hmac
import struct
import tempfile
import unittest
from pathlib import Path

from Crypto.Cipher import AES

from wechat_export.sqlcipher4 import (
    HMAC_SIZE,
    IV_SIZE,
    PAGE_SIZE,
    RESERVE_SIZE,
    SALT_SIZE,
    SQLITE_HEADER,
    PageHmacError,
    TruncatedDatabaseError,
    decrypt_database,
    decrypt_page,
    derive_mac_key,
    page1_hmac_ok,
    verify_all_pages,
)


def _encrypt_page(raw_key: bytes, salt: bytes, iv: bytes, inner: bytes, pgno: int) -> bytes:
    cipher = AES.new(raw_key, AES.MODE_CBC, iv)
    encrypted = cipher.encrypt(inner)
    page = bytearray(PAGE_SIZE)
    if pgno == 1:
        assert len(inner) == PAGE_SIZE - RESERVE_SIZE - SALT_SIZE
        page[:SALT_SIZE] = salt
        page[SALT_SIZE : PAGE_SIZE - RESERVE_SIZE] = encrypted
    else:
        assert len(inner) == PAGE_SIZE - RESERVE_SIZE
        page[: PAGE_SIZE - RESERVE_SIZE] = encrypted
    page[PAGE_SIZE - RESERVE_SIZE : PAGE_SIZE - RESERVE_SIZE + IV_SIZE] = iv
    mac_key = derive_mac_key(raw_key, salt)
    start = SALT_SIZE if pgno == 1 else 0
    digest = hmac.new(mac_key, bytes(page[start : PAGE_SIZE - RESERVE_SIZE + IV_SIZE]), hashlib.sha512)
    digest.update(struct.pack("<I", pgno))
    page[PAGE_SIZE - HMAC_SIZE :] = digest.digest()
    return bytes(page)


class SqlCipher4Tests(unittest.TestCase):
    def test_page1_roundtrip_and_hmac(self) -> None:
        raw_key = bytes(range(32))
        salt = b"s" * 16
        iv = b"i" * 16
        inner = (b"payload" * 1000)[: PAGE_SIZE - RESERVE_SIZE - SALT_SIZE]
        page = _encrypt_page(raw_key, salt, iv, inner, 1)
        self.assertTrue(page1_hmac_ok(page, raw_key))
        self.assertFalse(page1_hmac_ok(page, b"\x00" * 32))
        out = decrypt_page(page, raw_key, 1)
        self.assertEqual(out[:16], SQLITE_HEADER)
        self.assertEqual(out[16 : PAGE_SIZE - RESERVE_SIZE], inner)

    def test_decrypt_database_file(self) -> None:
        raw_key = hashlib.sha256(b"test-key").digest()
        salt = hashlib.sha256(b"salt").digest()[:16]
        iv = hashlib.sha256(b"iv").digest()[:16]
        inner = bytes((i * 3) % 256 for i in range(PAGE_SIZE - RESERVE_SIZE - SALT_SIZE))
        page = _encrypt_page(raw_key, salt, iv, inner, 1)
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "enc.db"
            dst = Path(td) / "plain.db"
            src.write_bytes(page)
            info = decrypt_database(src, dst, raw_key)
            self.assertEqual(info["pages"], 1)
            self.assertFalse(info["wal_applied"])
            self.assertFalse(info["wechat_live_db_params_verified"])
            data = dst.read_bytes()
            self.assertEqual(data[:16], SQLITE_HEADER)
            self.assertEqual(data[16 : PAGE_SIZE - RESERVE_SIZE], inner)

    def test_refuses_to_pad_short_page(self) -> None:
        raw_key = bytes(range(32))
        with self.assertRaises(TruncatedDatabaseError):
            decrypt_page(b"\x00" * 100, raw_key, 1)

    def test_refuses_unaligned_file(self) -> None:
        raw_key = bytes(range(32))
        salt = b"s" * 16
        iv = b"i" * 16
        inner = bytes(PAGE_SIZE - RESERVE_SIZE - SALT_SIZE)
        page = _encrypt_page(raw_key, salt, iv, inner, 1)
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "enc.db"
            src.write_bytes(page + b"\x00" * 100)
            with self.assertRaises(TruncatedDatabaseError):
                decrypt_database(src, Path(td) / "out.db", raw_key)

    def test_page2_hmac_failure_does_not_continue(self) -> None:
        raw_key = bytes(range(32))
        salt = b"s" * 16
        p1 = _encrypt_page(raw_key, salt, b"1" * 16, bytes(PAGE_SIZE - RESERVE_SIZE - SALT_SIZE), 1)
        p2 = _encrypt_page(raw_key, salt, b"2" * 16, bytes(PAGE_SIZE - RESERVE_SIZE), 2)
        good = p1 + p2
        self.assertEqual(verify_all_pages(good, raw_key), 2)
        corrupt = bytearray(good)
        corrupt[-1] ^= 0xFF
        with self.assertRaises(PageHmacError) as ctx:
            verify_all_pages(bytes(corrupt), raw_key)
        self.assertEqual(ctx.exception.page, 2)
