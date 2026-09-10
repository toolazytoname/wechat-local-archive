"""Workflow integration uses encrypted fixture DBs; never launches WeChat."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from tests import test_read_pipeline as fixtures
from wechat_export.jobs import JobStore
from wechat_export.runtime import resolve_runtime
from wechat_export.workflow import start_read_job, advance

class GuidedIntegrationTests(unittest.TestCase):
    setUp = fixtures.ReadPipelineTests.setUp
    # Reuse only fixture setup, not the parent assertions as separate evidence.
    def test_snapshot_to_sample_gate_via_real_workflow(self):
        import shutil
        rt = resolve_runtime(self.root / 'runtime')
        snap = rt.data_root / 'work' / 'fixture'
        shutil.copytree(self.snapshot, snap / 'live-db')
        (snap / 'account-binding.json').write_text(json.dumps({'account_id': 'acc_fixture'}))
        shutil.copyfile(self.key, rt.private_root / 'passphrase.raw')
        (rt.private_root / 'passphrase.raw').chmod(0o600)
        store = JobStore(rt.jobs_root)
        job = start_read_job(store, account_id='acc_fixture', adapter_id='test', synthetic=False)
        with patch('wechat_export.materials.ops_work_root', return_value=None), patch('wechat_export.discovery.resolve_account_dir', return_value=self.root / 'fixture_account'):
            job = advance(store, job, 'continue_from_materials', {
                'source_id': 'snapshot:fixture', 'confirm_preservation': True,
            }, runtime=rt)
        self.assertEqual(job.state, 'awaiting_sample_check', job.error)
        out = rt.exports_root / job.payload['export_source_id'].split(':', 1)[1]
        self.assertTrue((out / 'archive.sqlite').is_file())
        self.assertEqual(json.loads((out/'manifest.json').read_text())['record_count'], 5)
        job = advance(store, job, 'confirm_sample', {'confirm_sample': True}, runtime=rt)
        self.assertEqual(job.state, 'ready')
