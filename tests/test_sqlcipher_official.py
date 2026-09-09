from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from wechat_export.decrypt_livedb import WalUnmergedError, decrypt_one, decrypt_tree
from wechat_export.sqlcipher4 import (
    PAGE_SIZE,
    PageHmacError,
    TruncatedDatabaseError,
    decrypt_database,
    derive_raw_key,
    verify_all_pages,
)
from wechat_export.sqlcipher_cli import export_plaintext, sqlcipher_version

from tests.official_fixtures import PASSPHRASE, create_closed_multipage, create_open_wal_snapshot, require_sqlcipher


@unittest.skipUnless(require_sqlcipher(), "official sqlcipher CLI is not installed")
class OfficialSqlCipherTests(unittest.TestCase):
    def test_cli_is_sqlcipher_4(self) -> None:
        version = sqlcipher_version()
        self.assertIsNotNone(version)
        self.assertIn("4.", version or "")

    def test_multipage_hmac_and_decrypt_match_official_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "enc.db"
            create_closed_multipage(src)
            data = src.read_bytes()
            self.assertGreaterEqual(len(data), PAGE_SIZE * 2)
            self.assertEqual(len(data) % PAGE_SIZE, 0)
            raw = derive_raw_key(PASSPHRASE.encode(), data[:16])
            pages = verify_all_pages(data, raw)
            self.assertGreaterEqual(pages, 2)
            dst = Path(td) / "plain.db"
            decrypt_database(src, dst, raw)
            con = sqlite3.connect(dst)
            count = con.execute("SELECT count(*) FROM t").fetchone()[0]
            self.assertGreaterEqual(count, 400)
            self.assertEqual(con.execute("SELECT v FROM t WHERE v='committed-main'").fetchone()[0], "committed-main")
            con.close()

    def test_wrong_key_fails_hmac(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "enc.db"
            create_closed_multipage(src)
            data = src.read_bytes()
            raw = derive_raw_key(b"wrong-passphrase", data[:16])
            with self.assertRaises(PageHmacError) as ctx:
                verify_all_pages(data, raw)
            self.assertEqual(ctx.exception.page, 1)

    def test_non_first_page_corruption_fails_that_page(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "enc.db"
            create_closed_multipage(src)
            data = bytearray(src.read_bytes())
            raw = derive_raw_key(PASSPHRASE.encode(), bytes(data[:16]))
            verify_all_pages(bytes(data), raw)
            # Flip a byte in page 2 HMAC (last 64 bytes of page 2).
            data[PAGE_SIZE * 2 - 1] ^= 0xFF
            src.write_bytes(data)
            with self.assertRaises(PageHmacError) as ctx:
                verify_all_pages(bytes(data), raw)
            self.assertEqual(ctx.exception.page, 2)
            with self.assertRaises(PageHmacError):
                decrypt_database(src, Path(td) / "out.db", raw)

    def test_truncated_file_errors_without_padding(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "enc.db"
            create_closed_multipage(src)
            data = src.read_bytes()
            raw = derive_raw_key(PASSPHRASE.encode(), data[:16])
            src.write_bytes(data[:-100])
            with self.assertRaises(TruncatedDatabaseError):
                decrypt_database(src, Path(td) / "out.db", raw)

    def test_uncheckpointed_wal_is_merged_not_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            paths = create_open_wal_snapshot(root)
            snap_db = paths["snap_db"]
            self.assertTrue(paths["snap_wal"].exists())
            self.assertGreater(paths["snap_wal"].stat().st_size, 0)
            self.assertLess(snap_db.stat().st_size, paths["snap_wal"].stat().st_size)

            raw = derive_raw_key(PASSPHRASE.encode(), snap_db.read_bytes()[:16])
            main_only = root / "main-only.db"
            decrypt_database(snap_db, main_only, raw)
            main_con = sqlite3.connect(main_only)
            try:
                tables = [r[0] for r in main_con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
                if "t" in tables:
                    main_values = {r[0] for r in main_con.execute("SELECT v FROM t") if isinstance(r[0], str)}
                    self.assertNotIn("in-wal-uncheckpointed", main_values)
            finally:
                main_con.close()

            merged = root / "merged.db"
            rec = decrypt_one(snap_db, merged, raw)
            self.assertTrue(rec["wal_applied"])
            self.assertEqual(rec["method"], "sqlcipher_export_wal_merged")
            con = sqlite3.connect(merged)
            try:
                self.assertEqual(
                    con.execute("SELECT v FROM t WHERE v='in-wal-uncheckpointed'").fetchone()[0],
                    "in-wal-uncheckpointed",
                )
                self.assertGreaterEqual(con.execute("SELECT count(*) FROM t").fetchone()[0], 400)
            finally:
                con.close()

            tree = decrypt_tree(snap_db.parent, root / "tree-out", {"enc.db": raw})
            self.assertEqual(tree["wal_merged"], 1)
            self.assertEqual(tree["failed"], 0)

    def test_wal_without_cli_is_hard_error(self) -> None:
        from unittest import mock

        with tempfile.TemporaryDirectory() as td:
            paths = create_open_wal_snapshot(Path(td))
            raw = derive_raw_key(PASSPHRASE.encode(), paths["snap_db"].read_bytes()[:16])
            with mock.patch("wechat_export.decrypt_livedb.find_sqlcipher", return_value=None):
                with self.assertRaises(WalUnmergedError):
                    decrypt_one(paths["snap_db"], Path(td) / "nope.db", raw)

    def test_export_plaintext_wrong_key_does_not_yield_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "enc.db"
            create_closed_multipage(src)
            dest = Path(td) / "out.db"
            with self.assertRaises(Exception):
                export_plaintext(src, dest, passphrase=b"not-the-key")
            self.assertFalse(dest.exists() and dest.stat().st_size > 0 and dest.read_bytes()[:16] == b"SQLite format 3\x00")
