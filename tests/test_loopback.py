from __future__ import annotations

import unittest

from wechat_export.loopback import BindAddressError, allowed_request_host, validate_bind_host


class LoopbackTests(unittest.TestCase):
    def test_only_127_allowed(self) -> None:
        self.assertEqual(validate_bind_host("127.0.0.1"), "127.0.0.1")
        for bad in ("0.0.0.0", "::", "::1", "localhost", "192.168.1.5", "127.0.0.2"):
            with self.subTest(bad=bad):
                with self.assertRaises(BindAddressError):
                    validate_bind_host(bad)

    def test_host_header(self) -> None:
        self.assertTrue(allowed_request_host("127.0.0.1:8765", 8765))
        self.assertTrue(allowed_request_host("localhost:8765", 8765))
        self.assertFalse(allowed_request_host("example.com:8765", 8765))
        self.assertFalse(allowed_request_host("0.0.0.0:8765", 8765))
        self.assertFalse(allowed_request_host("[::1]:8765", 8765))
        self.assertFalse(allowed_request_host(None, 8765))
