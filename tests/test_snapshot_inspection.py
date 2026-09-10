"""Inspection failures must never become idle evidence (synthetic only)."""
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from wechat_export.livedb_snapshot import paths_open_in_wechat, wechat_pids, copy_db_trio


class InspectionTests(unittest.TestCase):
    def test_malformed_process_output_rejected(self):
        for rc, text in [(0, ''), (0, '123 garbage'), (1, '123')]:
            with self.subTest(rc=rc, text=text), patch('wechat_export.livedb_snapshot.subprocess.run',
                    return_value=subprocess.CompletedProcess([], rc, text, '')):
                with self.assertRaisesRegex(RuntimeError, 'process_inspection_failed'):
                    wechat_pids()

    def test_valid_process_output(self):
        for rc, text, expected in [(0, '123\n456\n', [123, 456]), (1, '', [])]:
            with patch('wechat_export.livedb_snapshot.subprocess.run',
                       return_value=subprocess.CompletedProcess([], rc, text, '')):
                self.assertEqual(wechat_pids(), expected)

    def test_lsof_failures_do_not_copy_database(self):
        for rc, stdout, stderr in [(1, '', ''), (2, '', 'failed'),
                                   (0, 'p123\n', 'warning'), (0, '', '')]:
            with tempfile.TemporaryDirectory() as td, self.subTest(rc=rc, stdout=stdout, stderr=stderr):
                src, dest = Path(td)/'a.db', Path(td)/'copy/a.db'
                src.write_bytes(b'synthetic')
                with patch('wechat_export.livedb_snapshot.wechat_pids', return_value=[123]), patch(
                    'wechat_export.livedb_snapshot.subprocess.run',
                    return_value=subprocess.CompletedProcess([], rc, stdout, stderr)):
                    with self.assertRaisesRegex(RuntimeError, 'file_occupancy_inspection_failed'):
                        copy_db_trio(src, dest)
                self.assertFalse(dest.exists())

    def test_lsof_bounded_and_exact_paths(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td)/'a.db'; src.write_bytes(b'synthetic')
            with patch('wechat_export.livedb_snapshot.wechat_pids', return_value=[123]), patch(
                'wechat_export.livedb_snapshot.subprocess.run',
                return_value=subprocess.CompletedProcess([], 0, f'p123\nn{src}\n', '')) as run:
                self.assertEqual(paths_open_in_wechat([src]), [str(src)])
                self.assertEqual(run.call_args.kwargs['timeout'], 10)

    def test_lsof_timeout_and_missing_tool_fail_closed(self):
        for error in (FileNotFoundError(), subprocess.TimeoutExpired('lsof', 10)):
            with patch('wechat_export.livedb_snapshot.wechat_pids', return_value=[123]), patch(
                'wechat_export.livedb_snapshot.subprocess.run', side_effect=error):
                with self.assertRaisesRegex(RuntimeError, 'file_occupancy_inspection_failed'):
                    paths_open_in_wechat([])
