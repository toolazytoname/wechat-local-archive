import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from wechat_export.archive_binding import ArchiveBinding
from tests.test_export_service import _demo_tree
from wechat_export.cli import main

class SliceCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = _demo_tree(Path(self.tmp.name))

    def invoke(self, args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(args)
        return code, json.loads(out.getvalue())

    def test_cli_preview_equals_export(self):
        args = ['slice', '--export-dir', str(self.root), '--all', '--readable-only', '--mode', 'analysis']
        status, preview = self.invoke(args + ['--preview'])
        self.assertEqual(status, 0)
        status, exported = self.invoke(args)
        self.assertEqual(status, 0)
        self.assertEqual(preview['count'], exported['count'])
        self.assertEqual(exported['count'], 3)
        self.assertEqual(exported['record_source'], 'canonical')
        self.assertEqual(exported['query']['mode'], 'analysis')

    def test_verify_bad_count_nonzero(self):
        p = self.root / 'manifest.json'
        data = json.loads(p.read_text())
        data['record_count'] = 999
        data['export_status'] = 'partial'
        p.write_text(json.dumps(data))
        status, report = self.invoke(['verify', '--export-dir', str(self.root)])
        self.assertEqual(status, 1)
        self.assertFalse(report['archive_valid'])
        self.assertFalse(report['live_db_text_decode_complete'])

    def test_valid_counts_are_not_completion(self):
        status, report = self.invoke(['verify', '--export-dir', str(self.root)])
        self.assertEqual(status, 0)
        self.assertTrue(report['archive_valid'])
        self.assertFalse(report['live_db_text_decode_complete'])

    def test_duplicate_record_id_fails_even_when_count_matches(self):
        messages = self.root / 'all/messages.jsonl'
        first = messages.read_text().splitlines()[0]
        with messages.open('a') as out:
            out.write(first + '\n')
        p = self.root / 'manifest.json'
        data = json.loads(p.read_text())
        data['record_count'] = 5
        p.write_text(json.dumps(data))
        status, report = self.invoke(['verify', '--export-dir', str(self.root)])
        self.assertEqual(status, 1)
        self.assertEqual(report['duplicate_record_ids'], 1)

    def test_preview_revision_can_be_required_and_stale_revision_rejected(self):
        args = ['slice', '--export-dir', str(self.root), '--all']
        _, preview = self.invoke(args + ['--preview'])
        revision = preview['source_binding']['source_revision']
        _, result = self.invoke(args + ['--source-revision', revision])
        self.assertEqual(result['source_binding']['source_revision'], revision)
        manifest = self.root / 'manifest.json'
        manifest.write_text(manifest.read_text() + '\n')
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(args + ['--source-revision', revision]), 1)

    def test_source_change_after_copy_does_not_affect_export(self):
        original = ArchiveBinding.snapshot_to
        copies = []
        def snapshot_then_change(binding, destination, cancelled=lambda: False):
            result = original(binding, destination, cancelled)
            copies.append(destination)
            (self.root / 'all/messages.jsonl').write_text('')
            return result
        with patch.object(ArchiveBinding, 'snapshot_to', snapshot_then_change):
            _, exported = self.invoke(['slice', '--export-dir', str(self.root), '--all'])
        self.assertEqual(exported['count'], 4)
        self.assertEqual(len(Path(exported['path']).read_text().splitlines()), 4)
        self.assertFalse(copies[0].exists())
        self.assertTrue(Path(exported['path']).exists())

    def test_source_change_before_copy_fails_without_publishing(self):
        original = ArchiveBinding.snapshot_to
        def change_then_snapshot(binding, destination, cancelled=lambda: False):
            (self.root / 'all/messages.jsonl').write_text('')
            return original(binding, destination, cancelled)
        with patch.object(ArchiveBinding, 'snapshot_to', change_then_snapshot), contextlib.redirect_stdout(
                io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(['slice', '--export-dir', str(self.root), '--all']), 1)
        self.assertFalse((self.root / 'slices').exists())
