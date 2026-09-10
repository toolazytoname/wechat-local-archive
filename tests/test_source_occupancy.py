"""Actual lsof on synthetic files plus fail-closed inspection contracts."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from wechat_export.livedb_snapshot import assert_files_idle, snapshot_strict


class SourceOccupancyTests(unittest.TestCase):
    def test_errors_and_ambiguous_output_are_not_idle(self):
        for code,out,err in [(2,'',''),(1,'','permission denied'),(0,'',''),(1,'p123\n','')]:
            with self.subTest(code=code,out=out,err=err), \
                 patch('wechat_export.livedb_snapshot.subprocess.run',return_value=subprocess.CompletedProcess([],code,out,err)):
                with self.assertRaisesRegex(RuntimeError,'inspection_failed'): assert_files_idle([Path('/synthetic.db')])
        with patch('wechat_export.livedb_snapshot.subprocess.run',side_effect=subprocess.TimeoutExpired('lsof',20)):
            with self.assertRaisesRegex(RuntimeError,'inspection_failed'): assert_files_idle([Path('/synthetic.db')])

    def test_batched_paths_do_not_limit_inspection_to_wechat_pids(self):
        with patch('wechat_export.livedb_snapshot.subprocess.run',return_value=subprocess.CompletedProcess([],1,'','')) as run:
            assert_files_idle([Path(f'/synthetic/{n}.db') for n in range(257)])
        self.assertEqual(run.call_count,3)
        for call in run.call_args_list:
            args=call.args[0]
            self.assertEqual(args[:5],['lsof','-n','-P','-Fpn','--'])
            self.assertNotIn('-p',args);self.assertLessEqual(len(args),133)

    @unittest.skipUnless(shutil.which('lsof'),'lsof required')
    def test_current_nonwechat_process_holding_source_is_detected(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td).resolve();db=root/'synthetic.db';db.write_bytes(b'SYNTHETIC')
            assert_files_idle([db])
            with db.open('rb'):
                with self.assertRaisesRegex(RuntimeError,'source_files_open'): assert_files_idle([db])
            assert_files_idle([db])

    @unittest.skipUnless(shutil.which('lsof'),'lsof required')
    def test_handle_opened_during_copy_prevents_success_report(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td).resolve();source=root/'source';source.mkdir()
            db=source/'synthetic.db';db.write_bytes(b'SYNTHETIC')
            held=[];copy2=shutil.copy2
            def copy_then_open(src,dst):
                result=copy2(src,dst);held.append(db.open('rb'));return result
            try:
                with patch('wechat_export.livedb_snapshot.wechat_pids',return_value=[]), \
                     patch('wechat_export.livedb_snapshot.shutil.copy2',side_effect=copy_then_open):
                    with self.assertRaisesRegex(RuntimeError,'source_files_open'):
                        snapshot_strict(source,root/'work/live-db')
                self.assertFalse((root/'work/live-snapshot-consistency.json').exists())
            finally:
                for handle in held: handle.close()
