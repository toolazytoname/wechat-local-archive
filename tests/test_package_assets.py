from __future__ import annotations

import unittest
from pathlib import Path

from wechat_export.archive_server import viewer_dir


class PackageAssetTests(unittest.TestCase):
    def test_product_scripts_ship_with_package_data(self) -> None:
        static = viewer_dir()
        for name in ("index.html", "styles.css", "app.js", "setup.js", "profiles.js", "learning.js", "shell.js"):
            self.assertTrue((static / name).is_file(), name)
        shell = (static / "shell.js").read_text(encoding="utf-8")
        self.assertIn('querySelectorAll("[data-page]")', shell)
        profiles = (static / "profiles.js").read_text(encoding="utf-8")
        self.assertIn("consent_ticket", profiles)
        self.assertIn("/api/profiles/consent", profiles)
