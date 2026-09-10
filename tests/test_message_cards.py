import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from tests.test_export_service import _demo_tree
from wechat_export.preview import analysis_record,record_presentation
from wechat_export.archive_index import build_index
from wechat_export.message_cards import card_details,MAX_CARD_ITEMS

class MessageCardTests(unittest.TestCase):
    def raw(self,xml):return {'text':xml,'message_type_normalized':'app','record_uid':'fixture','conversation_id':'peer'}

    def test_file_not_generic_link_and_no_secret(self):
        xml='<msg><appmsg><title>report.pdf</title><appattach><fileext>pdf</fileext><totallen>123</totallen><aeskey>SECRET</aeskey><cdnattachurl>REMOTE</cdnattachurl></appattach></appmsg></msg>'
        record=analysis_record(self.raw(xml))
        self.assertEqual(record['media_kind'],'file')
        self.assertEqual(record['card']['size_bytes'],123)
        self.assertNotIn('SECRET',json.dumps(record));self.assertNotIn('REMOTE',json.dumps(record))

    def test_quote_safe_text(self):
        xml='<msg><appmsg><title>My reply</title><refermsg><displayname>Alice</displayname><content><![CDATA[quoted & plain]]></content></refermsg></appmsg></msg>'
        card=analysis_record(self.raw(xml))['card']
        self.assertEqual(card['kind'],'quote')
        self.assertEqual(card['quoted_text'],'quoted & plain')
        self.assertEqual(card['author'],'Alice')

    def test_quote_structured_payload_not_leaked(self):
        xml='<msg><appmsg><title>reply</title><refermsg><content><![CDATA[<msg aeskey="SECRET"/>]]></content></refermsg></appmsg></msg>'
        self.assertNotIn('SECRET',json.dumps(analysis_record(self.raw(xml))))

    def test_forwarded_cdata_bounded_items(self):
        nested='<recordinfo><datalist>'+''.join(f'<dataitem><sourcename>User</sourcename><datadesc>line {i}</datadesc></dataitem>' for i in range(12))+'</datalist></recordinfo>'
        xml='<msg><appmsg><title>Thread</title><recorditem><![CDATA['+nested+']]></recorditem></appmsg></msg>'
        card=analysis_record(self.raw(xml))['card']
        self.assertEqual(card['kind'],'forwarded');self.assertEqual(card['item_count'],12)
        self.assertEqual(len(card['items']),MAX_CARD_ITEMS);self.assertTrue(card['truncated'])

    def test_dtd_and_oversized_fail_to_placeholder(self):
        for xml in ('<!DOCTYPE x [<!ENTITY secret SYSTEM "file:///etc/passwd">]><msg><appmsg>&secret;</appmsg></msg>', '<msg>'+('x'*256001)+'</msg>'):
            self.assertEqual(card_details(xml)['parse_status'],'unparsed')

    def test_index_holds_allowlisted_card_not_raw_xml(self):
        with tempfile.TemporaryDirectory() as td:
            root=_demo_tree(Path(td))
            path=root/'all/messages.jsonl'
            rows=[json.loads(line) for line in path.read_text().splitlines()]
            rows[0].update(text='<msg><appmsg><title>reply</title><refermsg><displayname>Alice</displayname><content>prior</content></refermsg></appmsg></msg>',message_type_normalized='app')
            path.write_text('\n'.join(json.dumps(r) for r in rows)+'\n')
            build_index(root)
            conn=sqlite3.connect(root/'archive.sqlite')
            try:
                row=conn.execute("SELECT media_kind,card_json,text FROM messages WHERE record_uid='1'").fetchone()
                self.assertEqual(row[0],'quote');self.assertEqual(json.loads(row[1])['quoted_text'],'prior')
                self.assertIsNone(row[2])
            finally:conn.close()

    def test_offline_exports_keep_quote_details_escaped(self):
        from wechat_export.export_service import write_records
        from wechat_export.offline_html import write_offline_html
        xml='<msg><appmsg><title>reply</title><refermsg><displayname><![CDATA[<b>Alice</b>]]></displayname><content>prior &amp; safe</content></refermsg></appmsg></msg>'
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            write_records(root/'messages.html',iter([self.raw(xml)]),'html')
            text=(root/'messages.html').read_text()
            self.assertIn('prior &amp; safe',text)
            self.assertNotIn('<b>Alice</b>',text)
            write_offline_html(root/'offline.html','fixture',[self.raw(xml)])
            self.assertIn('prior &amp; safe',(root/'offline.html').read_text())

    def test_nested_structured_quote_never_flattens_secret(self):
        xml='<msg><appmsg><title>reply</title><refermsg><content><msg><aeskey>SECRET</aeskey></msg></content></refermsg></appmsg></msg>'
        self.assertNotIn('SECRET',json.dumps(analysis_record(self.raw(xml))))
