from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from wechat_export.config import ConfigError, default_config_payload, load_config
from wechat_export.archive_server import viewer_dir


class ConfigPrivacyTests(unittest.TestCase):
    def test_defaults_do_not_embed_an_operator_account(self) -> None:
        payload = default_config_payload()
        self.assertEqual(payload["account"], "")
        self.assertEqual(payload["live_account"], "")
        self.assertEqual(payload["target_names"], [])
        self.assertTrue(payload["xwechat_root"].startswith("~/Library/"))

    def test_missing_account_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.json"
            path.write_text(json.dumps({"data_root": td, "account": "", "live_account": ""}), encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_config(path)

    def test_viewer_assets_ship_with_the_package(self) -> None:
        static = viewer_dir()
        self.assertTrue((static / "index.html").is_file())
        self.assertTrue((static / "app.js").is_file())
        self.assertTrue((static / "setup.js").is_file())
        self.assertTrue((static / "styles.css").is_file())
        for name in ("app.js", "setup.js"):
            js = (static / name).read_text(encoding="utf-8")
            self.assertNotIn("innerHTML", js)

    def test_relative_paths_resolve_from_config_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            private = root / "private"
            private.mkdir()
            path = private / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "data_root": "..",
                        "project_root": "../..",
                        "account": "demo_account",
                        "live_account": "demo_live",
                        "xwechat_root": str(root / "xwechat"),
                    }
                ),
                encoding="utf-8",
            )
            cfg = load_config(path)
            self.assertEqual(cfg.data_root, root.resolve())
