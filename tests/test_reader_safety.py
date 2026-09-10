import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from wechat_export.jobs import JobStore
from wechat_export.livedb_snapshot import snapshot_strict, wechat_pids
from wechat_export.workflow import advance, start_read_job, WorkflowError
from wechat_export.runtime import resolve_runtime, demo_export_dir
from wechat_export.adapters.macos_xwechat import execute_key_capture, AdapterError

class ReaderSafetyTests(unittest.TestCase):
    def test_no_grant_no_capture(self):
        with self.assertRaises(AdapterError):
            execute_key_capture()

    def test_no_stage_confirmation_no_commands(self):
        with tempfile.TemporaryDirectory() as td:
            rt = resolve_runtime(td)
            store = JobStore(rt.jobs_root)
            job = start_read_job(store, account_id='acc_test', adapter_id='test', synthetic=False)
            with patch('wechat_export.live_reader.subprocess.run') as run:
                with self.assertRaises(WorkflowError):
                    advance(store, job, 'prepare_reader', {}, runtime=rt)
                run.assert_not_called()

    def test_strict_snapshot_hashes_all_parts(self):
        with tempfile.TemporaryDirectory() as td, patch('wechat_export.livedb_snapshot.wechat_pids', return_value=[]):
            root = Path(td)
            src = root / 'source'
            src.mkdir()
            for suffix in ('', '-wal', '-shm'):
                (src / ('test.db' + suffix)).write_bytes(b'fixture'+suffix.encode())
            report = snapshot_strict(src, root / 'copy')
            self.assertEqual(report['consistency'], 'idle_hash_verified')
            self.assertEqual(len(report['files']), 3)

    def test_running_process_refuses_snapshot(self):
        with tempfile.TemporaryDirectory() as td, patch('wechat_export.livedb_snapshot.wechat_pids', return_value=[123]):
            with self.assertRaises(RuntimeError):
                snapshot_strict(Path(td), Path(td)/'out')

    def test_inspection_error_is_not_idle(self):
        with patch('wechat_export.livedb_snapshot.subprocess.run') as run:
            run.return_value.returncode = 2
            with self.assertRaises(RuntimeError):
                wechat_pids()

    def test_lease_blocks_duplicate_and_recovery(self):
        with tempfile.TemporaryDirectory() as td:
            store = JobStore(Path(td))
            job = store.create('read', 'decrypting')
            release = threading.Event()
            self.assertTrue(store.run_in_thread(job.job_id, lambda: release.wait(5)))
            self.assertFalse(store.run_in_thread(job.job_id, lambda: None))
            self.assertEqual(store.recover_interrupted(), 0)
            release.set()
            store._threads[job.job_id].join(5)
            self.assertEqual(store.recover_interrupted(), 1)
            self.assertEqual(store.get(job.job_id).error['code'], 'interrupted')

    def test_installed_demo_writable_copy(self):
        with tempfile.TemporaryDirectory() as td, patch('wechat_export.runtime.source_checkout_root', return_value=None), patch.dict('os.environ', {'WECHAT_EXPORT_DATA_ROOT': td}):
            demo = demo_export_dir()
            self.assertTrue(demo.is_relative_to(Path(td).resolve()))
            self.assertTrue((demo/'all/messages.jsonl').is_file())

    def test_switch_account_revokes_previous_grant(self):
        from wechat_export.authorization import make_live_grant, LIVE_GRANT_PHRASE
        with tempfile.TemporaryDirectory() as td:
            store = JobStore(Path(td))
            job = start_read_job(store, account_id='acc_one', adapter_id='test', synthetic=False)
            job.payload['live_grant'] = make_live_grant(job.job_id, LIVE_GRANT_PHRASE)
            job.payload['confirm_preservation'] = True
            job = advance(store, job, 'select_account', {'account_id': 'acc_two'})
            self.assertNotIn('live_grant', job.payload)
            self.assertFalse(job.payload['confirm_preservation'])
