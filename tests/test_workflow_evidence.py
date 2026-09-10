"""Synthetic regressions: status must reflect work actually performed."""
import json
import tempfile
import unittest
from unittest.mock import patch

from wechat_export.jobs import JobStore
from wechat_export.runtime import resolve_runtime
from wechat_export.workflow import advance, start_read_job, WorkflowError


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.runtime = resolve_runtime(self.tmp.name)
        self.store = JobStore(self.runtime.jobs_root)
        self.job = start_read_job(self.store, account_id='acc_fixture', adapter_id='test', synthetic=False)
        (self.runtime.data_root / 'work' / 'fixture' / 'live-db').mkdir(parents=True)
        self.patch = patch('wechat_export.materials.ops_work_root', return_value=None)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def proceed(self, **extra):
        return advance(self.store, self.job, 'continue_from_materials',
                       dict(source_id='snapshot:fixture', confirm_preservation=True, confirm_snapshot_account=True, **extra),
                       runtime=self.runtime)

    def fixture(self, count=1, source='live-db'):
        out = self.runtime.exports_root / 'fixture-export'
        (out / 'all').mkdir(parents=True)
        (out / 'manifest.json').write_text(json.dumps(dict(
            source_snapshot_id='fixture', source_kind='live-db',
            backup2_coverage='unverified', record_count=count)))
        (out / 'all' / 'messages.jsonl').write_text(json.dumps(dict(
            source_snapshot_id='fixture', source_kind=source, record_uid='record-1', conversation_id='conversation-1'))+'\n')
        return out

    def test_no_export_never_ready(self):
        job = self.proceed()
        self.assertEqual(job.state, 'blocked')
        self.assertEqual(job.payload['block_reason'], 'validated_export_missing')
        self.assertIsNone(job.payload.get('export_source_id'))

    def test_string_false_does_not_authorize(self):
        job = advance(self.store, self.job, 'continue_from_materials',
                      dict(source_id='snapshot:fixture', confirm_preservation='false'), runtime=self.runtime)
        self.assertEqual(job.state, 'awaiting_consent')

    def test_count_mismatch_blocks(self):
        self.fixture(count=2)
        self.assertEqual(self.proceed().state, 'blocked')

    def test_row_provenance_mismatch_blocks(self):
        self.fixture(source='backup2')
        self.assertEqual(self.proceed().state, 'blocked')

    def test_accept_requires_evidence(self):
        with self.assertRaises(WorkflowError):
            advance(self.store, self.job, 'confirm_sample', {'confirm_sample': True})

    def test_validated_export_waits_for_human(self):
        self.fixture()
        with patch('wechat_export.archive_index.build_index') as index:
            job = self.proceed()
        index.assert_called_once()
        self.assertEqual(job.state, 'awaiting_sample_check')
        self.assertFalse(job.payload['new_user_first_read'])
        job = advance(self.store, job, 'confirm_sample', {'confirm_sample': True})
        self.assertEqual(job.state, 'ready')
