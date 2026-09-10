from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from wechat_export.materials import list_materials, resolve_materials
from wechat_export.runtime import resolve_runtime


class MaterialsTests(unittest.TestCase):
    def test_rejects_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runtime = resolve_runtime(td)
            with self.assertRaises(ValueError):
                resolve_materials("snapshot:../secret", runtime)
            with self.assertRaises(ValueError):
                resolve_materials("/etc/passwd", runtime)

    def test_lists_registered_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runtime = resolve_runtime(td)
            snap = runtime.data_root / "work" / "20260909T000000-test"
            (snap / "live-db" / "contact").mkdir(parents=True)
            (snap / "live-db" / "contact" / "contact.db").write_bytes(b"x")
            (snap / "snapshot-pointer.json").write_text(
                json.dumps({"live_db": str(snap / "live-db")}),
                encoding="utf-8",
            )
            items = list_materials(runtime)
            ids = [i["source_id"] for i in items]
            self.assertIn("snapshot:20260909T000000-test", ids)
            resolved = resolve_materials("snapshot:20260909T000000-test", runtime)
            self.assertTrue(resolved["live_db"].is_dir())

    def test_no_implicit_neighboring_checkout_scan(self) -> None:
        from unittest.mock import patch
        from wechat_export.materials import ops_work_root
        with patch.dict('os.environ', {}, clear=True), patch.object(Path, 'is_dir', return_value=True):
            self.assertIsNone(ops_work_root())
        with tempfile.TemporaryDirectory() as td:
            with patch.dict('os.environ', {'WECHAT_EXPORT_OPS_ROOT': td}):
                self.assertEqual(ops_work_root(), Path(td) / 'work')
