from __future__ import annotations

import unittest

from wechat_export.insights.store import InsightsError
from wechat_export.learning.fetcher import fetch_not_enabled, validate_fetch_url


def _fake_resolver(ip: str):
    def resolve(host, port, type=None):
        return [(2, 1, 6, "", (ip, port))]

    return resolve


class FetcherTests(unittest.TestCase):
    def test_blocks_loopback_and_metadata(self) -> None:
        with self.assertRaises(InsightsError):
            validate_fetch_url("http://127.0.0.1/secret")
        with self.assertRaises(InsightsError):
            validate_fetch_url("http://localhost/x")
        with self.assertRaises(InsightsError):
            validate_fetch_url("http://169.254.169.254/latest/meta-data")
        with self.assertRaises(InsightsError):
            validate_fetch_url("http://user:pass@example.com/")
        with self.assertRaises(InsightsError):
            validate_fetch_url("file:///etc/passwd")

    def test_dns_rebinding_to_private_ip(self) -> None:
        with self.assertRaises(InsightsError):
            validate_fetch_url("https://example.invalid/x", resolver=_fake_resolver("10.0.0.8"))

    def test_fetch_disabled_by_default(self) -> None:
        with self.assertRaises(InsightsError) as ctx:
            fetch_not_enabled()
        self.assertEqual(ctx.exception.code, "fetch_disabled")
