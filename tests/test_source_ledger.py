import json
from contextlib import closing
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from tests import test_read_pipeline as fixtures
from wechat_export.source_ledger import inspect_database_role
from wechat_export.read_pipeline import PipelineError

class SchemaRoleTests(unittest.TestCase):
    def test_name_does_not_prove_fts(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'message_fts.db'
            with closing(sqlite3.connect(path)) as c:
                c.execute('CREATE TABLE Msg_records(local_id INTEGER,create_time INTEGER,message_content TEXT)')
                c.execute("INSERT INTO Msg_records VALUES(1,2,'synthetic')")
                c.commit()
            role=inspect_database_role(path)
            self.assertEqual(role['role'],'message_source')
            self.assertEqual(role['input_rows'],1)

    def test_only_virtual_and_shadow_tables_are_derived(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'arbitrary.db'
            with closing(sqlite3.connect(path)) as c:c.execute('CREATE VIRTUAL TABLE search USING fts5(body)')
            self.assertEqual(inspect_database_role(path)['role'],'derived_fts_only')
            with closing(sqlite3.connect(path)) as c:c.execute('CREATE TABLE unknown_payload(body TEXT)')
            self.assertNotEqual(inspect_database_role(path)['role'],'derived_fts_only')

class LedgerPipelineTests(unittest.TestCase):
    setUp=fixtures.ReadPipelineTests.setUp
    execute=fixtures.ReadPipelineTests.execute

    def test_success_ledger_counts_and_hashes(self):
        out=self.execute()
        manifest=json.loads((out/'manifest.json').read_text())
        ledger=json.loads((out/'source-ledger.json').read_text())
        self.assertEqual(manifest['input_message_rows'],5)
        self.assertEqual(ledger['output_records'],5)
        self.assertEqual(ledger['snapshot_inventory']['sha256'],manifest['source_snapshot_sha256'])
        self.assertTrue(manifest['records_complete'])
        self.assertFalse(manifest['coverage_verified'])
        self.assertFalse(manifest['attachments_complete'])
        self.assertTrue(all(r['status']=='ok' for r in ledger['databases']))
        rows=[json.loads(line) for line in (out/'all/messages.jsonl').read_text().splitlines()]
        self.assertTrue(all(r['schema_version']=='wechat-canonical/1' for r in rows))

    def test_corrupt_fts_name_blocks_instead_of_silent_skip(self):
        (self.snapshot/'message/message_fts.db').write_bytes(b'not an authenticated database')
        with self.assertRaises(PipelineError):self.execute()
        self.assertFalse((self.cfg.exports_root/'test-run').exists())
        ledger=json.loads((self.cfg.work_root/'test-run/source-ledger.json').read_text())
        self.assertEqual(ledger['state'],'failed')
        self.assertTrue(any(r['status']=='failed' for r in ledger['databases']))

    def test_real_message_source_with_fts_in_name_not_omitted(self):
        original=self.snapshot/'message/message_0.db'
        original.rename(self.snapshot/'message/message_fts_0.db')
        out=self.execute()
        self.assertEqual(json.loads((out/'manifest.json').read_text())['record_count'],5)

    def test_source_mutation_during_processing_never_publishes(self):
        def mutate(state,*_):
            if state=='normalizing':
                with (self.snapshot/'contact/contact.db').open('ab') as f:f.write(b'changed')
        with self.assertRaises(PipelineError):self.execute(progress=mutate)
        self.assertFalse((self.cfg.exports_root/'test-run').exists())
        ledger=json.loads((self.cfg.work_root/'test-run/source-ledger.json').read_text())
        self.assertEqual(ledger['state'],'failed')
        self.assertFalse(ledger['records_complete'])

    def test_newly_discovered_message_source_is_exported_with_relative_identity(self):
        extra=self.snapshot/'other'
        extra.mkdir()
        shutil.copyfile(self.snapshot/'message/message_0.db',extra/'unrecognized_name.db')
        out=self.execute()
        rows=[json.loads(line) for line in (out/'all/messages.jsonl').read_text().splitlines()]
        self.assertEqual(len(rows),10)
        self.assertEqual(len({r['record_uid'] for r in rows}),10)
        self.assertIn('other/unrecognized_name.db',{r['source_relative_path'] for r in rows})

class UnknownSchemaAccountingTests(unittest.TestCase):
    def test_contentless_fts_impostor_is_not_excluded(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'synthetic.db'
            with closing(sqlite3.connect(path)) as c:
                c.execute("CREATE VIRTUAL TABLE search USING fts5(body,content='')")
                c.execute('CREATE TABLE search_content(id INTEGER, real_message TEXT)')
                c.execute("INSERT INTO search_content VALUES(1,'synthetic')")
                c.commit()
            result = inspect_database_role(path)
            self.assertNotEqual(result['role'], 'derived_fts_only')
            self.assertEqual(result['unclassified_rows'], 1)
            self.assertFalse(result['schema_coverage_complete'])

    def test_unknown_tables_views_and_triggers_are_gaps(self):
        from wechat_export.source_ledger import database_accounting
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'synthetic.db'
            with closing(sqlite3.connect(path)) as c:
                c.executescript('''CREATE TABLE Msg_x(local_id, create_time, message_content);
                CREATE TABLE extra(body); INSERT INTO extra VALUES('synthetic');
                CREATE VIEW extra_view AS SELECT * FROM extra;
                CREATE TRIGGER extra_trigger AFTER INSERT ON extra BEGIN SELECT 1; END;''')
            role = inspect_database_role(path)
            result = database_accounting([{'path': 'synthetic.db', 'status': 'ok', 'schema_evidence': role}], authenticated=True)
            self.assertEqual(role['role'], 'message_source')
            self.assertEqual(result['unclassified_rows'], 1)
            self.assertEqual(result['unclassified_schema_object_count'], 2)
            self.assertFalse(result['schema_coverage_complete'])
            self.assertNotIn('sql', role['unclassified_schema_objects'][0])
