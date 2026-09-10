from __future__ import annotations

import unittest

from wechat_export.adapters.macos_xwechat import VERIFIED_BUILDS, evaluate, execute_key_capture, AdapterError
from wechat_export.authorization import (
    LIVE_GRANT_PHRASE,
    consents_complete,
    live_operations_permitted,
    make_live_grant,
)


class AuthorizationTests(unittest.TestCase):
    def test_key_capture_allowed_stays_false(self) -> None:
        env = {
            "platform": "macOS-26-arm64",
            "machine": "arm64",
            "mac_ver": "26.6.2",
            "wechat_present": True,
            "wechat_version": "4.1.13",
            "wechat_build": "269630",
        }
        adapter = evaluate(env)
        self.assertFalse(adapter["key_capture_allowed"])
        self.assertEqual(VERIFIED_BUILDS, ())
        with self.assertRaises(AdapterError) as ctx:
            execute_key_capture()
        self.assertEqual(ctx.exception.code, "live_grant_required")

    def test_consent_dict_incomplete(self) -> None:
        self.assertFalse(consents_complete({"confirm_preservation": True}))
        self.assertTrue(
            consents_complete(
                {
                    "confirm_preservation": True,
                    "confirm_debug_copy": True,
                    "confirm_key_capture": True,
                    "confirm_enter_wechat": True,
                }
            )
        )

    def test_live_requires_grant_and_preflight(self) -> None:
        payload = {
            "confirm_preservation": True,
            "confirm_debug_copy": True,
            "confirm_key_capture": True,
            "confirm_enter_wechat": True,
        }
        adapter = {"candidate": True, "key_capture_allowed": False}
        ok, reason = live_operations_permitted(
            job_id="job-1",
            payload=payload,
            adapter=adapter,
            preflight={"ok": True},
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "live_grant_missing")
        payload["live_grant"] = make_live_grant("job-1", LIVE_GRANT_PHRASE)
        ok, reason = live_operations_permitted(
            job_id="job-1",
            payload=payload,
            adapter=adapter,
            preflight={"ok": False, "reason": "wechat_running"},
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "wechat_running")
        ok, reason = live_operations_permitted(
            job_id="job-1",
            payload=payload,
            adapter=adapter,
            preflight={"ok": True},
        )
        self.assertTrue(ok)
        self.assertIsNone(reason)
        # Global adapter switch remains false even when this job is permitted.
        self.assertFalse(adapter["key_capture_allowed"])
