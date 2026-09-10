"""Synthetic bounded IO, containment, and loopback attachment responses."""
import http.client
import io
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from wechat_export.media_stream import byte_range, copy_range, open_local_media, UnsatisfiableRange, CHUNK_SIZE
from wechat_export.archive_server import ArchiveHandler, ArchiveHTTPServer, ServerContext
from wechat_export.runtime import resolve_runtime
from tests.test_export_service import _demo_tree


class MediaStreamTests(unittest.TestCase):
    def test_ranges(self):
        for header, expected in [(None,(0,10,False)), ('bytes=2-4',(2,5,True)),
                ('bytes=7-',(7,10,True)), ('bytes=-3',(7,10,True)),
                ('bytes=-99',(0,10,True)), ('bytes=2-99',(2,10,True)),
                ('bytes=9-2',(0,10,False)), ('bytes=0-1,3-4',(0,10,False)),
                ('bytes=-',(0,10,False)), ('items=0-1',(0,10,False))]:
            with self.subTest(header=header): self.assertEqual(byte_range(header,10),expected)
        for h in ('bytes=10-', 'bytes=-0'):
            with self.assertRaises(UnsatisfiableRange): byte_range(h,10)
        self.assertEqual(byte_range(None,0),(0,0,False))
        with self.assertRaises(UnsatisfiableRange): byte_range('bytes=0-',0)

    def test_bounded_reads_and_truncation(self):
        class Bounded(io.BytesIO):
            def read(self, n=-1):
                if n < 0 or n > CHUNK_SIZE: raise AssertionError('unbounded read')
                return super().read(n)
        data=b'x'*(CHUNK_SIZE*3+17)
        out=io.BytesIO()
        copy_range(Bounded(data),out,7,len(data)-3)
        self.assertEqual(out.getvalue(), data[7:-3])
        with self.assertRaises(EOFError): copy_range(Bounded(b'a'),io.BytesIO(),0,2)

    def test_no_follow_and_regular_only(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/'media';root.mkdir()
            outside=Path(td)/'other';outside.mkdir();(outside/'secret').write_bytes(b'private')
            (root/'file').write_bytes(b'local')
            with open_local_media(root,root/'file') as (f,n):
                self.assertEqual((f.read(),n),(b'local',5))
            (root/'link').symlink_to(outside/'secret')
            (root/'dir').symlink_to(outside,target_is_directory=True)
            os.mkfifo(root/'pipe')
            for p in (root/'link',root/'dir'/'secret',root/'pipe',outside/'secret'):
                with self.subTest(path=p.name), self.assertRaises(OSError):
                    with open_local_media(root,p): self.fail('unsafe file opened')


class MediaStreamHttpTests(unittest.TestCase):
    def test_responses_do_not_materialize_file(self):
        with tempfile.TemporaryDirectory() as td:
            runtime=resolve_runtime(td)
            archive=_demo_tree(runtime.exports_root/'synthetic')
            media=archive/'media';media.mkdir()
            file=media/'sample';data=b'\xff\xd8\xff'+bytes(range(256))*3000;file.write_bytes(data)
            ctx=ServerContext(bind_port=0,session_token="synthetic-test",runtime=runtime,export_dir=archive,
                              index_path=archive/'archive.sqlite',media_root=media)
            server=ArchiveHTTPServer(('127.0.0.1',0),ArchiveHandler)
            ctx.bind_port=server.server_address[1];server.context=ctx
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            import sqlite3
            with sqlite3.connect(archive/'archive.sqlite') as conn:
                uid=conn.execute('select record_uid from messages limit 1').fetchone()[0]
            def request(range_value=None, if_range=None):
                conn=http.client.HTTPConnection('127.0.0.1',ctx.bind_port,timeout=5)
                headers={'X-Archive-ID':ctx.binding.archive_id}
                if range_value: headers['Range']=range_value
                if if_range: headers['If-Range']=if_range
                conn.request('GET',f'/api/media?uid={uid}',headers=headers)
                r=conn.getresponse();result=(r.status,dict(r.getheaders()),r.read());conn.close();return result
            try:
                with patch('wechat_export.archive_server.resolve_media_file',return_value={'found':True,'path':str(file)}), \
                     patch.object(Path,'read_bytes',side_effect=AssertionError('whole-file read forbidden')):
                    status,h,body=request();self.assertEqual(status,200);self.assertEqual(body,data)
                    status,h,body=request('bytes=3-1026');self.assertEqual(status,206)
                    self.assertEqual(body,data[3:1027]);self.assertEqual(h['Content-Range'],f'bytes 3-1026/{len(data)}')
                    status,h,body=request('bytes=-5');self.assertEqual((status,body),(206,data[-5:]))
                    status,h,body=request(f'bytes={len(data)}-');self.assertEqual((status,body),(416,b''))
                    self.assertEqual(h['Content-Range'],f'bytes */{len(data)}')
                    status,h,body=request('bytes=0-0','"old"');self.assertEqual((status,body),(200,data))
            finally:
                server.shutdown();server.server_close();thread.join(5)
