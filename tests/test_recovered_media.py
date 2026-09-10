import base64,hashlib,json,sqlite3,tempfile,unittest
from pathlib import Path
from contextlib import closing
from wechat_export.recovered_media import recover_archive,resource_stem,attachment,verified_object,public_attachment
from wechat_export.media_resolve import conversation_hash
from tests.test_export_service import _demo_tree

PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jr1kAAAAASUVORK5CYII=')
PDF=b'%PDF-1.4\nSynthetic attachment only\n%%EOF\n'

def fixture(root):
    archive=_demo_tree(root/'archive');account=root/'account';account.mkdir()
    conv='wxid_alice';stem='a'*32
    image=account/'msg/attach'/conversation_hash(conv)/'2026-01/Img'/f'{stem}_t.dat';image.parent.mkdir(parents=True);image.write_bytes(PNG)
    # Existing unreadable high-resolution container must not hide the preview.
    image.with_name(stem+'_h.dat').write_bytes(b'\x07\x08V2'+b'opaque'*100)
    file=account/'msg/file/2026-01/report.pdf';file.parent.mkdir(parents=True);file.write_bytes(PDF)
    db=root/'hardlink.db'
    with closing(sqlite3.connect(db)) as c, c:
        c.execute('create table dir2id(username text)');c.executemany('insert into dir2id values (?)',[(conversation_hash(conv),),('2026-01',)])
        for kind in ('image','file','video'):c.execute(f'create table {kind}_hardlink_info_v4(md5 text,dir1 integer,dir2 integer,file_name text,file_size integer)')
        c.execute('insert into file_hardlink_info_v4 values (?,?,?,?,?)',(hashlib.md5(PDF).hexdigest(),2,0,'report.pdf',len(PDF)))
    resource=root/'resource.db'
    with closing(sqlite3.connect(resource)) as c, c:
        c.execute('create table ChatName2Id(user_name text)');c.execute('insert into ChatName2Id values (?)',(conv,))
        c.execute('create table MessageResourceInfo(chat_id integer,message_svr_id integer,message_local_type integer,packed_info blob)')
        c.execute('insert into MessageResourceInfo values (?,?,?,?)',(1,123,3,b'\x12\x22\x0a\x20'+stem.encode()))
    rows=[json.loads(line) for line in (archive/'all/messages.jsonl').read_text().splitlines()]
    rows[0].update(message_type_normalized='image',server_message_id=123,text='<msg><img md5="'+'b'*32+'"/></msg>')
    rows[1].update(message_type_normalized='app',text='<msg><appmsg><type>6</type><title>report.pdf</title><md5>'+hashlib.md5(PDF).hexdigest()+'</md5><appattach><fileext>pdf</fileext><totallen>'+str(len(PDF))+'</totallen></appattach></appmsg></msg>')
    (archive/'all/messages.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
    return archive,account,db,resource

class RecoveredMediaTests(unittest.TestCase):
    def test_resource_wire_layout_rejects_unknown_keys(self):
        self.assertEqual(resource_stem(b'\x12\x22\x0a\x20'+b'a'*32),'a'*32)
        for value in [None,b'a'*32,b'\x12\x22\x0a\x20'+b'g'*32,b'prefix'+b'a'*32]:self.assertIsNone(resource_stem(value))
    def test_image_preview_and_verified_file(self):
        with tempfile.TemporaryDirectory() as td:
            args=fixture(Path(td));source=(args[0]/'all/messages.jsonl').read_bytes()
            report=recover_archive(*args)
            self.assertEqual(report['counts']['image_available'],1)
            self.assertEqual(report['counts']['file_available'],1)
            image=attachment(args[0],'1');self.assertEqual(image['representation'],'preview')
            with verified_object(args[0],image) as (f,size):self.assertEqual(f.read(),PNG)
            file=attachment(args[0],'2');self.assertEqual(file['representation'],'original_verified')
            with verified_object(args[0],file) as (f,size):self.assertEqual(f.read(),PDF)
            self.assertNotIn('relative_path',public_attachment(file))
            self.assertEqual((args[0]/'all/messages.jsonl').read_bytes(),source)
            with self.assertRaises(FileExistsError):recover_archive(*args)
            (args[0]/'media'/image['relative_path']).write_bytes(b'x'*image['byte_size'])
            with self.assertRaises(ValueError):
                with verified_object(args[0],image):pass
    def test_cross_chat_resource_and_same_name_wrong_file_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            args=fixture(Path(td))
            with closing(sqlite3.connect(args[3])) as c, c:c.execute("update ChatName2Id set user_name='other-chat'")
            (args[1]/'msg/file/2026-01/report.pdf').write_bytes(b'%PDF-'+b'wrong')
            r=recover_archive(*args)
            self.assertEqual(attachment(args[0],'1')['status'],'missing')
            self.assertEqual(attachment(args[0],'2')['status'],'missing')
            self.assertEqual(r['stored_files'],0)

class RecoveredMediaHttpTests(unittest.TestCase):
    def test_bytes_range_download_and_binding(self):
        import http.client, threading
        from wechat_export.runtime import resolve_runtime
        from wechat_export.archive_server import ServerContext,ArchiveHTTPServer,ArchiveHandler
        from wechat_export.archive_index import ensure_index_current
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);args=fixture(root);recover_archive(*args)
            ensure_index_current(args[0])
            ctx=ServerContext(bind_port=0,session_token='synthetic',runtime=resolve_runtime(root/'runtime'),export_dir=args[0],index_path=args[0]/'archive.sqlite',media_root=args[0]/'media')
            server=ArchiveHTTPServer(('127.0.0.1',0),ArchiveHandler);ctx.bind_port=server.server_address[1];server.context=ctx
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            def request(uid,extra=None,binding=True):
                c=http.client.HTTPConnection('127.0.0.1',ctx.bind_port,timeout=10)
                headers={'X-Archive-ID':ctx.binding.archive_id} if binding else {}
                headers.update(extra or {});c.request('GET','/api/media?uid='+uid,headers=headers)
                r=c.getresponse();v=(r.status,dict(r.getheaders()),r.read());c.close();return v
            try:
                status,h,b=request('1');self.assertEqual((status,b),(200,PNG));self.assertEqual(h['Content-Type'],'image/png')
                status,h,b=request('2');self.assertEqual((status,b),(200,PDF));self.assertIn('attachment;',h['Content-Disposition']);self.assertIn('report.pdf',h['Content-Disposition'])
                status,h,b=request('2',{'Range':'bytes=0-4'});self.assertEqual((status,b),(206,PDF[:5]))
                self.assertEqual(request('2',{'Range':'bytes=999-'} )[0],416)
                self.assertNotEqual(request('1',binding=False)[0],200)
            finally:server.shutdown();server.server_close();thread.join(5)
