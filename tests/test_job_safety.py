import tempfile
import unittest
from pathlib import Path
from wechat_export.jobs import JobStore
from wechat_export.runtime import resolve_runtime, resolve_source_id
from wechat_export.authorization import consents_complete, CONSENT_KEYS

class JobSafetyTests(unittest.TestCase):
    def test_stale_worker_cannot_undo_cancel(self):
        with tempfile.TemporaryDirectory() as td:
            store = JobStore(Path(td))
            worker = store.create('export', 'indexing')
            store.request_cancel(worker.job_id)
            worker.state = 'ready'
            store.save(worker)
            self.assertEqual(store.get(worker.job_id).state, 'cancelled')
            self.assertTrue(store.cancelled(worker.job_id))

    def test_string_consent_rejected(self):
        self.assertFalse(consents_complete({key: 'false' for key in CONSENT_KEYS}))

    def test_prefix_sibling_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            rt = resolve_runtime(td)
            sibling = Path(td) / 'exports-secret'
            (sibling / 'all').mkdir(parents=True)
            (sibling / 'all' / 'messages.jsonl').write_text('')
            rt.exports_root.mkdir(exist_ok=True)
            (rt.exports_root / 'escape').symlink_to(sibling, target_is_directory=True)
            with self.assertRaises(ValueError):
                resolve_source_id('export:escape', rt)
