import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from wechat_export.export_service import QuerySpec, record_matches_spec, write_records, ExportCancelled

class ExtraExportTests(unittest.TestCase):
    def test_type_filter_same_canonical_semantics(self):
        spec = QuerySpec.from_mapping({'scope': {'kind': 'all'}, 'message_types': ['image']})
        self.assertTrue(record_matches_spec({'message_type_normalized': 'image'}, spec))
        self.assertFalse(record_matches_spec({'message_type_normalized': 'text'}, spec))

    def test_html_escaped_offline(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'messages.html'
            count = write_records(p, iter([{'sender_display_name': '<script>bad()</script>', 'text': 'a & b', 'is_self': True}]), 'html')
            text = p.read_text()
            self.assertEqual(count, 1)
            self.assertNotIn('<script>', text)
            self.assertIn('a &amp; b', text)
            self.assertIn('Content-Security-Policy', text)

    def test_empty_cancel_does_not_publish(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'messages.html'
            with self.assertRaises(ExportCancelled):
                write_records(p, iter([]), 'html', should_cancel=lambda: True)
            self.assertFalse(p.exists())
