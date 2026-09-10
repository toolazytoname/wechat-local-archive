from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from wechat_export.discovery import account_id_for, discover_accounts


class DiscoveryTests(unittest.TestCase):
    def test_missing_root(self) -> None:
        report = discover_accounts(Path("/tmp/wechat-export-missing-xwechat-root"))
        self.assertEqual(report["status"], "xwechat_root_missing")
        self.assertEqual(report["accounts"], [])

    def test_empty_tree(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            report = discover_accounts(root)
            self.assertEqual(report["status"], "no_accounts")

    def test_single_and_multiple_without_guessing_identity(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            one = root / "folder_alpha"
            one.mkdir()
            (one / "db_storage").mkdir()
            (one / "db_storage" / "contact.db").write_bytes(b"x")
            report = discover_accounts(root)
            self.assertEqual(report["status"], "single_account")
            self.assertEqual(len(report["accounts"]), 1)
            self.assertEqual(report["accounts"][0]["dir_name"], "folder_alpha")
            self.assertTrue(report["accounts"][0]["account_id"].startswith("acc_"))
            self.assertNotEqual(report["accounts"][0]["account_id"], "folder_alpha")
            two = root / "folder_beta"
            two.mkdir()
            (two / "msg").mkdir()
            report = discover_accounts(root)
            self.assertEqual(report["status"], "multiple_accounts")
            self.assertEqual(len(report["accounts"]), 2)

    def test_skips_backup_dir_and_does_not_truncate_names(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "Backup").mkdir()
            (root / "Backup" / "db_storage").mkdir()
            long_name = root / "long_account_name_not_truncated"
            long_name.mkdir()
            (long_name / "db_storage").mkdir()
            report = discover_accounts(root)
            names = [a["dir_name"] for a in report["accounts"]]
            self.assertEqual(names, ["long_account_name_not_truncated"])
            self.assertEqual(account_id_for("a"), account_id_for("a"))
            self.assertNotEqual(account_id_for("a"), account_id_for("b"))
