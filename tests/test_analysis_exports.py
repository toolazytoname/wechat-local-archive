"""Canonical/viewer query parity and explicit safe analysis output."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tests.test_export_service import _demo_tree
from wechat_export.archive_index import build_index
from wechat_export.export_service import QueryError, QuerySpec, count_messages, iter_canonical_messages, write_slice
from wechat_export.preview import classify_payload

class AnalysisExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = _demo_tree(Path(self.tmp.name))
        path = self.root / 'all/messages.jsonl'
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        base = dict(rows[0])
        extras = [
            ('envelope', 'text', 'sender:\n<msg><img aeskey="SYNTHETIC_SECRET" cdnmidimgurl="SYNTHETIC_CDN"/></msg>'),
            ('empty', 'text', None),
            ('unknown-text', 'unknown_999', 'still readable'),
            ('literal', 'text', 'Alice:\nplease keep this line'),
            ('cdata', 'app', '<msg><appmsg><title><![CDATA[Tea & <coffee>]]></title><url>SYNTHETIC_CDN</url></appmsg></msg>'),
        ]
        for uid, kind, text in extras:
            rows.append(dict(base, record_uid=uid, message_type_normalized=kind, text=text,
                             raw_b64='SYNTHETIC_RAW', attachment_refs=['SYNTHETIC_CDN']))
        self.rows = rows
        path.write_text('\n'.join(json.dumps(r) for r in rows)+'\n')
        build_index(self.root)
        self.conn = sqlite3.connect(self.root / 'archive.sqlite')
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)

    def test_preview_equals_canonical_for_readable_filter(self):
        spec = QuerySpec.from_mapping({'scope': {'kind': 'all'}, 'readable_only': True})
        actual = list(iter_canonical_messages(self.root, spec))
        self.assertEqual(count_messages(self.conn, spec), len(actual))
        ids = {r['record_uid'] for r in actual}
        self.assertNotIn('envelope', ids)
        self.assertNotIn('empty', ids)
        self.assertIn('unknown-text', ids)

    def test_plain_name_prefix_is_not_removed(self):
        info = classify_payload('Alice:\nplease keep this line', 'text')
        self.assertEqual(info['body'], 'Alice:\nplease keep this line')
        self.assertIsNone(info['sender_prefix'])

    def test_cdata_title_readable_in_card(self):
        info = classify_payload(self.rows[-1]['text'], 'app')
        self.assertEqual(info['title'], 'Tea & <coffee>')

    def test_analysis_jsonl_excludes_structured_secrets_and_raw(self):
        spec = QuerySpec.from_mapping({'scope': {'kind': 'all'}, 'format': 'jsonl', 'mode': 'analysis'})
        out = write_slice(self.conn, self.root, spec, source='canonical')
        text = Path(out['path']).read_text()
        self.assertNotIn('SYNTHETIC_SECRET', text)
        self.assertNotIn('SYNTHETIC_CDN', text)
        self.assertNotIn('SYNTHETIC_RAW', text)
        exported = [json.loads(line) for line in text.splitlines()]
        self.assertEqual(len(exported), len(self.rows))
        self.assertEqual(out['query']['mode'], 'analysis')
        literal = next(r for r in exported if r['record_uid'] == 'literal')
        self.assertEqual(literal['text'], 'Alice:\nplease keep this line')

    def test_raw_jsonl_retains_original_records(self):
        spec = QuerySpec.from_mapping({'scope': {'kind': 'all'}, 'format': 'jsonl', 'mode': 'raw'})
        out = write_slice(self.conn, self.root, spec, source='canonical')
        exported = [json.loads(line) for line in Path(out['path']).read_text().splitlines()]
        self.assertEqual(exported, self.rows)

    def test_canonical_missing_never_substitutes_index(self):
        (self.root / 'all/messages.jsonl').unlink()
        spec = QuerySpec.from_mapping({'scope': {'kind': 'all'}})
        with self.assertRaises(QueryError) as ctx:
            write_slice(self.conn, self.root, spec, source='canonical')
        self.assertEqual(ctx.exception.code, 'canonical_source_missing')
        self.assertFalse((self.root / 'slices').exists())

    def test_invalid_mode_and_boolean_rejected(self):
        for fields in ({'mode': 'unexpected'}, {'readable_only': 'false'}):
            with self.assertRaises(QueryError):
                QuerySpec.from_mapping({'scope': {'kind': 'all'}, **fields})

    def test_old_presentation_index_rebuilt_without_source_change(self):
        from wechat_export.archive_index import ensure_index_current
        before = (self.root / 'all/messages.jsonl').read_bytes()
        self.conn.execute("DELETE FROM meta WHERE key='presentation_version'")
        self.conn.execute("UPDATE messages SET text='incorrectly shortened' WHERE record_uid='literal'")
        self.conn.commit()
        self.conn.close()
        index = ensure_index_current(self.root)
        with sqlite3.connect(index) as conn:
            self.assertEqual(conn.execute("SELECT text FROM messages WHERE record_uid='literal'").fetchone()[0], 'Alice:\nplease keep this line')
        self.assertEqual(before, (self.root / 'all/messages.jsonl').read_bytes())
