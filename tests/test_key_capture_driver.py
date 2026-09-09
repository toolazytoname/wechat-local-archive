from __future__ import annotations

import os
import signal
import subprocess
import time
import unittest
from pathlib import Path

from wechat_export.key_capture import apply_hmac_result, capture_kdf, kill_pids, verify_hits_against_db

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
PROBE = FIXTURES / "kdf_probe"
IDLE = FIXTURES / "kdf_idle"


def _cc(src: Path, dst: Path) -> None:
    subprocess.run(["cc", "-O0", "-g", "-o", str(dst), str(src)], check=True)


class KeyCaptureDriverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _cc(FIXTURES / "kdf_probe.c", PROBE)
        _cc(FIXTURES / "kdf_idle.c", IDLE)

    def test_two_kdf_calls_keep_lengths_and_do_not_coerce_first_to_32(self) -> None:
        status, hits = capture_kdf(PROBE, timeout=20, max_hits=8, extra_args=None)
        self.assertFalse(status.launch_failed)
        self.assertTrue(status.breakpoint_resolved, status.public_dict())
        self.assertTrue(status.breakpoint_hit, status.public_dict())
        self.assertGreaterEqual(status.hit_count, 2, status.public_dict())
        self.assertTrue(status.candidate_captured)
        self.assertIn(8, status.candidate_lengths)
        self.assertIn(32, status.candidate_lengths)
        by_len = {h.length: h.material for h in hits}
        self.assertEqual(by_len[8], b"shortpw!")
        self.assertEqual(by_len[32], b"0123456789abcdef0123456789abcdef")
        self.assertFalse(status.hmac_verified)

    def test_idle_process_is_not_a_hit(self) -> None:
        status, hits = capture_kdf(IDLE, timeout=6, max_hits=3)
        self.assertFalse(status.launch_failed)
        self.assertFalse(status.breakpoint_hit, status.public_dict())
        self.assertEqual(hits, [])
        self.assertFalse(status.candidate_captured)
        self.assertEqual(status.candidate_lengths, [])
        self.assertNotEqual(status.hit_count, None)
        self.assertFalse(status.hmac_verified)

    def test_cleanup_only_uses_session_pids(self) -> None:
        sleeper = subprocess.Popen(["sleep", "30"])
        try:
            status, _hits = capture_kdf(PROBE, timeout=15, max_hits=8)
            self.assertTrue(status.session_pids)
            self.assertNotIn(sleeper.pid, status.session_pids)
            self.assertIsNone(sleeper.poll())
            kill_pids(status.session_pids)
            time.sleep(0.3)
            self.assertIsNone(sleeper.poll())
        finally:
            sleeper.send_signal(signal.SIGTERM)
            try:
                sleeper.wait(timeout=2)
            except subprocess.TimeoutExpired:
                sleeper.kill()

    def test_ui_classifier_detects_session_resume_not_chat_list(self) -> None:
        from wechat_export.diagnose_copy import _classify_ui

        dump = (
            "frontmost=true\n"
            "WINDOW title=微信 role=AXWindow desc=standard window\n"
            "  el role=AXButton name=网络代理设置 value=missing value\n"
            "AXButton:进入微信\n"
        )
        flags = _classify_ui(dump)
        self.assertEqual(flags["stage"], "session_resume")
        self.assertTrue(flags["mentions_enter_wechat"])
        self.assertFalse(flags["mentions_chat_list"])

    def test_source_has_no_name_pkill_of_debugserver(self) -> None:
        src = (ROOT / "wechat_export" / "key_capture.py").read_text(encoding="utf-8")
        self.assertNotIn("pkill -x", src)
        self.assertNotIn('["pkill"', src)
        self.assertNotIn("killall", src)

    def test_hmac_verified_is_a_separate_step(self) -> None:
        from tests.official_fixtures import require_sqlcipher, _run_script

        if require_sqlcipher() is None:
            self.skipTest("sqlcipher CLI not installed")
        status, hits = capture_kdf(PROBE, timeout=20, max_hits=8)
        self.assertTrue(status.candidate_captured, status.public_dict())
        self.assertFalse(status.hmac_verified)
        by_len = {h.length: h.material for h in hits}
        pw = by_len[32].decode("ascii")
        db = FIXTURES / "kdf_hmac_probe.db"
        if db.exists():
            db.unlink()
        _run_script(
            db,
            f"""
PRAGMA key = '{pw}';
PRAGMA cipher_compatibility = 4;
CREATE TABLE t(id INTEGER PRIMARY KEY, v TEXT);
INSERT INTO t(v) VALUES ('probe-hmac');
.exit
""",
        )
        try:
            ok, n = verify_hits_against_db(hits, db)
            self.assertTrue(ok)
            self.assertGreaterEqual(n, 1)
            self.assertFalse(status.hmac_verified)
            apply_hmac_result(status, ok, n, mode="passphrase_pbkdf2")
            self.assertTrue(status.hmac_verified)
            self.assertEqual(status.hmac_verified_count, n)
            wrong = [h for h in hits if h.length == 8]
            ok_short, n_short = verify_hits_against_db(wrong, db)
            self.assertFalse(ok_short)
            self.assertEqual(n_short, 0)
        finally:
            db.unlink(missing_ok=True)
