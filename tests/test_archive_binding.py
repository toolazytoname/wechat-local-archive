import tempfile
import unittest
from pathlib import Path
from tests.test_export_service import _demo_tree
from wechat_export.archive_binding import ArchiveBinding, ArchiveBindingError
from wechat_export.export_service import write_records, QueryError

class ArchiveBindingTests(unittest.TestCase):
    def test_content_identity_not_only_record_ids(self):
        with tempfile.TemporaryDirectory() as td:
            a=_demo_tree(Path(td)/'a')
            binding=ArchiveBinding.capture(a,a/'archive.sqlite')
            copy=binding.snapshot_to(Path(td)/'private')
            self.assertEqual((copy/'all/messages.jsonl').read_bytes(),(a/'all/messages.jsonl').read_bytes())
            self.assertEqual((copy/'archive.sqlite').read_bytes(),(a/'archive.sqlite').read_bytes())

    def test_pending_index_wal_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            a=_demo_tree(Path(td)/'a')
            (a/'archive.sqlite-wal').write_bytes(b'not-checkpointed')
            with self.assertRaises(ArchiveBindingError):
                ArchiveBinding.capture(a,a/'archive.sqlite')

    def test_preview_count_mismatch_does_not_publish(self):
        with tempfile.TemporaryDirectory() as td:
            output=Path(td)/'messages.jsonl'
            with self.assertRaises(QueryError) as ctx:
                write_records(output,iter([{'record_uid':'one'}]),'jsonl',expected_count=2)
            self.assertEqual(ctx.exception.code,'preview_count_mismatch')
            self.assertFalse(output.exists())
            self.assertFalse(output.with_suffix('.jsonl.tmp').exists())

class CoverageBindingTests(unittest.TestCase):
    def test_sidecars_copied_and_mutation_invalidates_binding(self):
        from wechat_export.archive_index import build_index
        for filename in ('coverage.json', 'coverage.md', 'attachment-ledger.jsonl'):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as td:
                root = _demo_tree(Path(td) / 'archive')
                (root / filename).write_text('{}\n')
                build_index(root)
                binding = ArchiveBinding.capture(root, root / 'archive.sqlite')
                copy = binding.snapshot_to(Path(td) / 'private')
                self.assertEqual((copy / filename).read_bytes(), b'{}\n')
                (root / filename).write_text('changed')
                with self.assertRaises(ArchiveBindingError):
                    binding.verify(strong=True)
