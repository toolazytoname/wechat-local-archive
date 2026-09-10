from __future__ import annotations

import unittest

from wechat_export.adapters.macos_xwechat import VERIFIED_BUILDS, evaluate
from wechat_export.compatibility import evaluate_environment


class CompatibilityTests(unittest.TestCase):
    def test_registry_has_no_verified_builds(self) -> None:
        self.assertEqual(VERIFIED_BUILDS, ())
        env = {
            "platform": "macOS-26.6.2-arm64-arm-64bit",
            "machine": "arm64",
            "mac_ver": "26.6.2",
            "wechat_present": True,
            "wechat_version": "4.1.13",
            "wechat_build": "269630",
        }
        adapter = evaluate(env)
        self.assertTrue(adapter["candidate"])
        self.assertFalse(adapter["build_verified"])
        self.assertFalse(adapter["key_capture_allowed"])
        self.assertTrue(adapter["live_operations_eligible"])
        report = evaluate_environment(env)
        self.assertTrue(report["stages"]["environment_detected"])
        self.assertTrue(report["stages"]["adapter_candidate"])
        self.assertFalse(report["stages"]["key_acquisition_verified"])
        self.assertFalse(report["stages"]["codec_verified"])
        self.assertFalse(report["stages"]["export_verified"])
        self.assertFalse(report["supported_for_guided_read"])
        self.assertTrue(report["archive_import_allowed"])
        self.assertEqual(report["read_blocked_reason"], "no_verified_build")

    def test_unknown_version_is_not_a_candidate(self) -> None:
        env = {
            "platform": "macOS",
            "machine": "arm64",
            "wechat_present": True,
            "wechat_version": "3.8.0",
            "wechat_build": "100",
        }
        report = evaluate_environment(env)
        self.assertFalse(report["stages"]["adapter_candidate"])
        self.assertTrue(report["archive_import_allowed"])

    def test_missing_wechat_still_allows_import(self) -> None:
        env = {"platform": "macOS", "machine": "arm64", "wechat_present": False}
        report = evaluate_environment(env)
        self.assertEqual(report["read_blocked_reason"], "wechat_not_installed")
        self.assertTrue(report["archive_import_allowed"])
