import io
import json
import tempfile
import unittest
from pathlib import Path
from wechat_export.attachment_accounting import AttachmentAccounting, attachment_facts, attachment_summary, MAX_REFS
from wechat_export.media_audit import MediaProbe
from wechat_export.export_run import collect_records, export_records
from tests.test_export_pipeline import _cfg, _build_dbs
from wechat_export.fsutil import sha256_file


class AttachmentAccountingTests(unittest.TestCase):
    def test_payloads_are_not_media_and_paths_not_disclosed(self):
        rec = {'record_uid': 'synthetic', 'text': 'hello', 'type_name': 'text',
               'attachment_refs': [{'encoding': 'base64', 'data': 'c2VjcmV0'},
                                   {'kind': 'file', 'path': '/private/secret', 'url': 'https://invalid.test/private'}]}
        stream = io.StringIO()
        counter = AttachmentAccounting(stream)
        counter.observe(rec)
        result = counter.summary()
        self.assertEqual(result['raw_payload_references'], 1)
        self.assertEqual(result['media_references'], 1)
        self.assertEqual(result['binary_files_exported'], 0)
        for secret in ('c2VjcmV0', '/private/secret', 'https://'):
            self.assertNotIn(secret, stream.getvalue())

    def test_limits_and_forged_summary(self):
        rec = {'text': 'hi', 'attachment_refs': [{'kind': 'file'}] * (MAX_REFS + 3),
               'attachment_summary': {'binary_files_exported': 99, 'availability': 'verified'}}
        self.assertEqual(attachment_facts(rec)['refs_not_inspected'], 3)
        self.assertEqual(attachment_summary(rec)['binary_files_exported'], 0)
        self.assertTrue(attachment_summary(rec)['inspection_limited'])

    def test_probe_does_not_follow_arbitrary_reference(self):
        with tempfile.TemporaryDirectory() as td:
            ref = {'slot': 'ref-0', 'kind': 'image', 'path': '/etc/passwd', 'availability': 'verified'}
            result = MediaProbe(Path(td)).observe({}, ref)
            self.assertEqual(result['availability'], 'unsupported')
            self.assertFalse(result['binary_included'])
            self.assertEqual(MediaProbe(None).observe({}, ref)['availability'], 'not_checked')

    def test_full_export_sidecars_hashes_and_tamper_rejection(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); cfg = _cfg(root)
            records, targets, _ = collect_records(_build_dbs(root), cfg, 'live-db', 'synthetic')
            args = dict(source_kind='live-db', backup2_coverage='unverified', extra_notes=[])
            out = export_records(records, targets, cfg, 'accounting', **args)
            manifest = json.loads((out / 'manifest.json').read_text())
            self.assertEqual(manifest['attachment_accounting']['records_examined'], len(records))
            for name, digest in manifest['generated_files'].items():
                self.assertEqual(sha256_file(out / name), digest, name)
            for path in (out / 'targets').glob('*/manifest.json'):
                target = json.loads(path.read_text())
                self.assertTrue((path.parent / 'coverage.json').exists())
                self.assertFalse(target['attachment_accounting']['attachments_complete'])
            self.assertEqual(export_records(records, targets, cfg, 'accounting', **args), out)
            (out / 'coverage.json').write_text('{}')
            with self.assertRaises(FileExistsError):
                export_records(records, targets, cfg, 'accounting', **args)

class MediaObservationTests(unittest.TestCase):
    def test_header_statuses_are_not_binary_recovery(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve(); candidate = root / 'candidate.bin'
            for kind, content, expected in [('image', b'\xff\xd8\xff' + b'\0'*30, 'local_candidate'),
                                            ('video', b'\xff\xd8\xff' + b'\0'*30, 'preview_only'),
                                            ('voice', b'opaque-ciphertext', 'opaque_candidate')]:
                candidate.write_bytes(content)
                with patch('wechat_export.media_audit.resolve_media_file', return_value={
                        'found': True, 'md5': 'a'*32, 'path': str(candidate)}):
                    result = MediaProbe(root).observe({'text': 'synthetic'}, {'slot': 'primary', 'kind': kind})
                self.assertEqual(result['availability'], expected)
                self.assertFalse(result['content_integrity_verified'])
                self.assertFalse(result['binary_included'])
                self.assertEqual(result['candidate_relative_path'], 'candidate.bin')
            with patch('wechat_export.media_audit.resolve_media_file', return_value={'found': False, 'md5': 'a'*32}):
                result = MediaProbe(root).observe({'text': 'synthetic'}, {'slot': 'primary', 'kind': 'image'})
                self.assertEqual(result['availability'], 'not_found_in_supported_layout')

    def test_symlink_candidate_is_rejected(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve(); (root / 'real').write_bytes(b'synthetic')
            (root / 'link').symlink_to(root / 'real')
            with patch('wechat_export.media_audit.resolve_media_file', return_value={
                    'found': True, 'md5': 'a'*32, 'path': str(root / 'link')}):
                result = MediaProbe(root).observe({'text': 'synthetic'}, {'slot': 'primary', 'kind': 'image'})
                self.assertEqual(result['availability'], 'inspection_failed')
