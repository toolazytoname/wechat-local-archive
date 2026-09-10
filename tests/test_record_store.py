import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from tests.test_export_pipeline import _build_dbs,_cfg
from wechat_export.export_run import collect_records,export_records
from wechat_export.record_store import RecordStore

class RecordStoreTests(unittest.TestCase):
    def test_disk_and_memory_canonical_records_equal(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);dec=_build_dbs(root);cfg=_cfg(root)
            expected,targets,_=collect_records(dec,cfg,'live-db','test')
            store=RecordStore(root/'spool.sqlite')
            try:
                actual,got_targets,_=collect_records(dec,cfg,'live-db','test',record_store=store)
                self.assertIs(actual,store)
                self.assertEqual([r.to_dict() for r in actual],[r.to_dict() for r in expected])
                self.assertEqual(got_targets,targets)
                out=export_records(actual,targets,cfg,'stream',source_kind='live-db',backup2_coverage='unverified',extra_notes=[])
                rows=[json.loads(line) for line in (out/'all/messages.jsonl').read_text().splitlines()]
                self.assertEqual(rows,[r.to_dict() for r in expected])
                self.assertEqual(json.loads((out/'manifest.json').read_text())['record_count'],5)
                self.assertTrue(list((out/'conversations').rglob('*.md')))
            finally:store.close()

    def test_cancel_in_normalization_is_checked(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);dec=_build_dbs(root);cfg=_cfg(root)
            calls=[0]
            def check():
                calls[0]+=1
                if calls[0]>2:raise RuntimeError('cancelled')
            store=RecordStore(root/'spool.sqlite',check)
            try:
                with self.assertRaisesRegex(RuntimeError,'cancelled'):
                    collect_records(dec,cfg,'live-db','test',record_store=store)
            finally:store.close()

    def test_disk_self_inference_matches_previous_contract(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);dec=_build_dbs(root);cfg=_cfg(root)
            records,_,_=collect_records(dec,cfg,'live-db','test')
            r=records[0]
            store=RecordStore(root/'spool.sqlite')
            try:
                store.extend([replace(r,record_uid='1',sender_id='self',conversation_id='peer1',conversation_type='private',is_self=None),
                              replace(r,record_uid='2',sender_id='self',conversation_id='peer2',conversation_type='private',is_self=None)])
                self.assertIsNotNone(store.infer_self())
                store.sort()
                self.assertTrue(all(x.is_self for x in store))
            finally:store.close()

    def test_streamed_csv_formula_and_xml_safety(self):
        import csv
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);dec=_build_dbs(root);cfg=_cfg(root)
            base,_,_=collect_records(dec,cfg,'live-db','test')
            store=RecordStore(root/'spool.sqlite')
            try:
                store.extend([replace(base[0],record_uid='formula',text='=SUM(A1:A9)',sender_display_name='+malicious'),
                              replace(base[0],record_uid='media',text='<msg><img aeskey="SYNTHETIC_KEY"/></msg>',message_type_normalized='image')])
                store.sort()
                out=export_records(store,{},cfg,'stream',source_kind='live-db',backup2_coverage='unverified',extra_notes=[])
                with (out/'all/messages.csv').open() as fh:rows=list(csv.DictReader(fh))
                self.assertEqual(next(r for r in rows if r['record_uid']=='formula')['text'],"'=SUM(A1:A9)")
                self.assertNotIn('SYNTHETIC_KEY',(out/'all/messages.csv').read_text())
                self.assertTrue(all('SYNTHETIC_KEY' not in p.read_text() for p in (out/'conversations').rglob('*.md')))
                self.assertIn('SYNTHETIC_KEY',(out/'all/messages.jsonl').read_text())
            finally:store.close()

    def test_disk_selection_never_expands_to_all(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);dec=_build_dbs(root);cfg=_cfg(root)
            store=RecordStore(root/'spool.sqlite')
            try:
                collect_records(dec,cfg,'live-db','test',record_store=store)
                with self.assertRaises(ValueError):store.select_conversation('missing')
                self.assertEqual(len(store),5)
                store.select_conversation('wr_group@chatroom')
                self.assertEqual(len(store),1)
                self.assertTrue(all(r.conversation_id=='wr_group@chatroom' for r in store))
            finally:store.close()
