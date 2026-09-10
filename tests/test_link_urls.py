import json
import unittest
from wechat_export.link_urls import safe_web_url
from wechat_export.preview import analysis_record
from wechat_export.message_cards import card_details, card_text

class LinkURLTests(unittest.TestCase):
    def test_allowed_web_destinations_preserve_article_queries(self):
        for url in ['https://example.org/article?a=1&b=2#part', 'http://example.org/', 'https://例子.测试/文章']:
            self.assertEqual(safe_web_url(url), url)
    def test_reject_unsafe_or_ambiguous_destinations(self):
        for url in [None, 4, '', '//example.org', 'javascript:alert(1)', 'data:text/html,hi',
                    'file:///etc/passwd', 'weixin://x', 'https://user:pw@example.org',
                    'https://example.org\\@evil.test', 'https://exa\nmple.org',
                    ' https://example.org', 'https://', 'https://example.org:99999',
                    'https://%65xample.org', 'https://example.org/'+'a'*8192]:
            with self.subTest(url_type=type(url).__name__):self.assertIsNone(safe_web_url(url))
    def test_only_original_webpage_field_exported(self):
        xml='<msg><appmsg><title>Article</title><url>https://example.org/?a=1&amp;b=2</url><cdnattachurl>CDN_SECRET</cdnattachurl><aeskey>KEY_SECRET</aeskey></appmsg></msg>'
        record=analysis_record({'text':xml,'message_type_normalized':'app'})
        self.assertEqual(record['card']['url'],'https://example.org/?a=1&b=2')
        self.assertFalse(record['card']['automatic_remote_load'])
        self.assertNotIn('SECRET',json.dumps(record))
        self.assertIn('https://example.org/?a=1&b=2',card_text(record['card']))
    def test_oversized_url_not_truncated_into_working_link(self):
        xml='<msg><appmsg><url>https://example.org/'+('a'*8200)+'</url></appmsg></msg>'
        self.assertIsNone(card_details(xml)['url'])
    def test_nested_url_rejected(self):
        self.assertIsNone(card_details('<msg><appmsg><url><x>https://example.org/</x></url></appmsg></msg>')['url'])

    def test_both_html_writers_keep_clickable_safe_destination(self):
        import tempfile
        from pathlib import Path
        from wechat_export.export_service import write_records
        from wechat_export.offline_html import write_offline_html, document_row
        record={'text':'<msg><appmsg><title>Article</title><url>https://example.org/?a=1&amp;b=2</url></appmsg></msg>', 'message_type_normalized':'app'}
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            write_records(root/'slice.html',iter([record]),'html')
            write_offline_html(root/'direct.html','Article',iter([record]))
            for p in root.glob('*.html'):
                text=p.read_text()
                self.assertIn('href="https://example.org/?a=1&amp;b=2"',text)
                self.assertIn('rel="noopener noreferrer"',text)
        self.assertNotIn('<a ',document_row('','','',False,link_url='javascript:alert(1)'))

    def test_csv_has_original_url_column(self):
        import csv,tempfile
        from pathlib import Path
        from wechat_export.export_service import write_records
        raw={'text':'<msg><appmsg><title>Article</title><url>https://example.org/a</url></appmsg></msg>', 'message_type_normalized':'app'}
        with tempfile.TemporaryDirectory() as td:
            for i,rec in enumerate([raw,analysis_record(raw)]):
                p=Path(td)/f'{i}.csv'
                write_records(p,iter([rec]),'csv')
                with p.open() as f:row=next(csv.DictReader(f))
                self.assertEqual(row['original_url'],'https://example.org/a')
