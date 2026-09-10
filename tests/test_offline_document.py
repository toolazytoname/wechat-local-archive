import tempfile
import unittest
from pathlib import Path
from wechat_export.offline_html import write_offline_html


class OfflineDocumentTests(unittest.TestCase):
    def test_stream_private_csp_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);seen=[]
            def records():
                for i in range(3):
                    seen.append(i)
                    yield {'text':'Literal <script>not executed</script>','sender_display_name':'<img src=x>','readable':True}
            path=write_offline_html(root/'out.html','<script>title</script>',records())
            self.assertEqual(seen,[0,1,2])
            body=path.read_text()
            self.assertIn('Content-Security-Policy',body)
            self.assertIn('base-uri',body)
            self.assertNotIn('<script>',body)
            self.assertNotIn('<img ',body)
            self.assertEqual(path.stat().st_mode&0o777,0o600)
            self.assertEqual(path.stat().st_nlink,1)
            with self.assertRaises(FileExistsError):write_offline_html(path,'new',[])
            self.assertEqual(path.read_text(),body)

    def test_generator_failure_never_publishes(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            def records():
                yield {'text':'synthetic first'}
                raise RuntimeError('synthetic failure')
            with self.assertRaises(RuntimeError):write_offline_html(root/'out.html','fixture',records())
            self.assertFalse((root/'out.html').exists())
            self.assertFalse(list(root.rglob('document.html')))

    def test_symlink_destination_is_not_followed(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);target=root/'retain.html';target.write_text('keep')
            (root/'link.html').symlink_to(target)
            with self.assertRaises(FileExistsError):write_offline_html(root/'link.html','fixture',[])
            self.assertEqual(target.read_text(),'keep')
