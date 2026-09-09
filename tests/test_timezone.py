from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo


def calendar_day(ts_iso: str, tz_name: str) -> str:
    return datetime.fromisoformat(ts_iso).astimezone(ZoneInfo(tz_name)).strftime("%Y-%m-%d")


class TimezoneTests(unittest.TestCase):
    def test_utc_message_groups_on_local_calendar_day(self) -> None:
        ts = "2026-01-02T01:00:00+00:00"
        self.assertEqual(calendar_day(ts, "UTC"), "2026-01-02")
        self.assertEqual(calendar_day(ts, "America/Los_Angeles"), "2026-01-01")
