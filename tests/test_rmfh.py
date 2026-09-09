from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from wechat_export.protobuf_lite import walk
from wechat_export.rmfh import header_view, inspect_tree


class RmfhTests(unittest.TestCase):
    def test_header_view_magic(self) -> None:
        data = b"RMFH" + b"\x00" * 124 + b"\x11" * 10 + b"RMFT" + b"\x00" * 124
        # trailer must be last 128 bytes starting with RMFT. Build explicitly.
        header = b"RMFH" + (b"\x00" * 124)
        trailer = b"RMFT" + (b"\x00" * 124)
        data = header + b"cipher" + trailer
        view = header_view(data)
        self.assertTrue(view["has_rmfh"])
        self.assertTrue(view["has_rmft_at_eof_minus_128"])
        self.assertFalse(view["sqlite_header"])
        self.assertEqual(view["ciphertext_len"], 6)

    def test_inspect_tree_counts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pkg = root / "abc" / "ChatPackage"
            pkg.mkdir(parents=True)
            header = b"RMFH" + (b"\x00" * 124)
            trailer = b"RMFT" + (b"\x00" * 124)
            (pkg / "1-2").write_bytes(header + b"xx" + trailer)
            (root / "detail.dat").write_bytes(b"\x08\x01")
            report = inspect_tree(root)
            self.assertEqual(report["by_class"]["chat_package"], 1)
            self.assertEqual(report["rmfh_by_class"]["chat_package"], 1)

    def test_protobuf_walk(self) -> None:
        # field 1 varint 2, field 3 length-delimited "ab"
        data = bytes([0x08, 0x02, 0x1A, 0x02, 0x61, 0x62])
        fields = walk(data)
        self.assertEqual(fields[0]["field"], 1)
        self.assertEqual(fields[0]["varint"], 2)
        self.assertEqual(fields[1]["field"], 3)
        self.assertEqual(fields[1]["ascii"], "ab")
