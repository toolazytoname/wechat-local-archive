import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from tests.test_export_service import _demo_tree
from wechat_export.export_service import QuerySpec, count_selection, write_slice, ExportCancelled


class SelectionAccountingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = _demo_tree(Path(self.tmp.name) / 'archive')
        self.conn = sqlite3.connect(self.root / 'archive.sqlite')
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)

    def test_filter_counts_all_formats_and_sources(self):
        for source in ('canonical', 'index'):
            for fmt in ('jsonl', 'csv', 'md', 'html'):
                for readable in (False, True):
                    with self.subTest(source=source, format=fmt, readable=readable):
                        spec = QuerySpec.from_mapping({'scope': {'kind':'all'}, 'format':fmt, 'readable_only':readable})
                        preview = count_selection(self.conn, spec)
                        result = write_slice(self.conn, self.root, spec, source=source)
                        self.assertEqual(result['selection_accounting'], preview)
                        self.assertEqual(preview['candidate_count'], 4)
                        self.assertEqual(preview['selected_count'], 3 if readable else 4)
                        self.assertEqual(preview['excluded_unreadable_count'], 1 if readable else 0)
                        coverage = json.loads((Path(result['path']).parent / 'coverage.json').read_text())
                        self.assertEqual(coverage['selection_accounting'], preview)
                        self.assertIn('因可读内容过滤而排除', (Path(result['path']).parent / 'coverage.md').read_text())

    def test_baseline_keeps_conversation_time_and_type_restrictions(self):
        choices = [
            ({'scope':{'kind':'conversations','conversation_ids':['wxid_alice']}},3,2),
            ({'scope':{'kind':'all'},'until':'2026-01-02T01:02:00Z'},2,2),
            ({'scope':{'kind':'all'},'message_types':['image']},1,0),
            ({'scope':{'kind':'all'},'since':'2030-01-01T00:00:00Z'},0,0),
        ]
        for body,candidate,selected in choices:
            with self.subTest(body=body):
                spec = QuerySpec.from_mapping(dict(body, readable_only=True))
                result = write_slice(self.conn,self.root,spec,source='canonical')
                counts = result['selection_accounting']
                self.assertEqual(counts,count_selection(self.conn,spec))
                self.assertEqual(counts['candidate_count'],candidate)
                self.assertEqual(counts['selected_count'],selected)
                self.assertEqual(counts['excluded_unreadable_count'],candidate-selected)

    def test_zero_selected_records_remain_cancellable_during_scan(self):
        path = self.root/'all/messages.jsonl'
        row = json.loads(path.read_text().splitlines()[-1])
        path.write_text('\n'.join(json.dumps(dict(row,record_uid=str(i))) for i in range(2000))+'\n')
        calls = 0
        def cancel():
            nonlocal calls
            calls += 1
            return calls > 7
        output = Path(self.tmp.name)/'cancelled'
        spec = QuerySpec.from_mapping({'scope':{'kind':'all'},'readable_only':True})
        with self.assertRaises(ExportCancelled):
            write_slice(None,self.root,spec,source='canonical',jobs_root=output,should_cancel=cancel)
        self.assertLess(calls,20)
        self.assertFalse(list(output.rglob('manifest.json')))

    def test_no_scope_matches_still_checks_cancellation(self):
        path = self.root/'all/messages.jsonl'
        row = json.loads(path.read_text().splitlines()[0])
        path.write_text('\n'.join(json.dumps(dict(row,record_uid=str(i))) for i in range(2000))+'\n')
        calls = 0
        def cancel():
            nonlocal calls
            calls += 1
            return calls > 2
        spec = QuerySpec.from_mapping({'scope':{'kind':'all'},'since':'2030-01-01T00:00:00Z'})
        with self.assertRaises(ExportCancelled):
            write_slice(None,self.root,spec,source='canonical',jobs_root=Path(self.tmp.name)/'empty',should_cancel=cancel)
        self.assertEqual(calls,3)
