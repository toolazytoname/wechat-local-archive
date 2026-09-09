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
        blob = json.dumps(payload)
        self.assertNotIn("shuitaiyang", blob)
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
        self.assertTrue((static / "styles.css").is_file())
        js = (static / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("innerHTML", js)
