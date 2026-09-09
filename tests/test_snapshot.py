from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from wechat_export.snapshot import build_manifest, compare_manifests, ditto_copy, tree_stats


class SnapshotTests(unittest.TestCase):
    def test_manifest_and_ditto_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            dst = Path(td) / "dst"
            (src / "a").mkdir(parents=True)
            (src / "a" / "one.txt").write_text("hello\n", encoding="utf-8")
            (src / "two.bin").write_bytes(b"\x00\x01\x02" * 100)
            stats = tree_stats(src)
            self.assertEqual(stats["file_count"], 2)
            self.assertEqual(stats["byte_count"], 6 + 300)
            ditto_copy(src, dst)
            src_rows = build_manifest(src)
            dst_rows = build_manifest(dst)
            cmp = compare_manifests(src_rows, dst_rows)
            self.assertTrue(cmp["ok"], cmp)
            self.assertEqual(len(src_rows), 2)

    def test_detect_hash_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            dst = Path(td) / "dst"
            src.mkdir()
            dst.mkdir()
            (src / "f").write_text("a", encoding="utf-8")
            (dst / "f").write_text("b", encoding="utf-8")
            cmp = compare_manifests(build_manifest(src), build_manifest(dst))
            self.assertFalse(cmp["ok"])
            self.assertEqual(cmp["hash_mismatch"], ["f"])

    def test_refuses_existing_dest(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            dst = Path(td) / "dst"
            src.mkdir()
            dst.mkdir()
            (src / "f").write_text("a", encoding="utf-8")
            with self.assertRaises(Exception):
                ditto_copy(src, dst)
