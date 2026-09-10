"""Browser cannot choose an arbitrary path; destination is fixed per read job."""
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_export_service import _demo_tree
from wechat_export.archive_server import ArchiveHandler, ArchiveHTTPServer, ServerContext
from wechat_export.http_security import new_session_token
from wechat_export.runtime import resolve_runtime


class OutputHttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name).resolve()
        self.runtime=resolve_runtime(self.root/'runtime')
        self.token=new_session_token()
        self.ctx=ServerContext(bind_port=0,session_token=self.token,runtime=self.runtime)
        self.server=ArchiveHTTPServer(('127.0.0.1',0),ArchiveHandler)
        self.ctx.bind_port=self.server.server_address[1];self.server.context=self.ctx
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(5);self.tmp.cleanup()

    def request(self, path, body=None, auth=True):
        conn=http.client.HTTPConnection('127.0.0.1',self.ctx.bind_port,timeout=5)
        headers={}
        if body is not None:
            headers={'Content-Type':'application/json'}
            if auth:
                headers.update({'Origin':f'http://127.0.0.1:{self.ctx.bind_port}',
                                'Cookie':f'wla_session={self.token}','X-CSRF-Token':self.token})
        conn.request('POST' if body is not None else 'GET',path,
                     body=json.dumps(body) if body is not None else None,headers=headers)
        response=conn.getresponse();result=response.status,json.loads(response.read());conn.close();return result

    def test_auth_and_no_browser_paths(self):
        with patch('wechat_export.archive_server.choose_native_folder') as picker:
            status,_=self.request('/api/setup/choose-output',{},auth=False)
            self.assertEqual(status,403);picker.assert_not_called()
            status,body=self.request('/api/setup/choose-output',{'path':'/tmp'})
            self.assertEqual(status,400);self.assertEqual(body['code'],'native_selection_required')
            picker.assert_not_called()
            status,_=self.request('/api/workflow/start',{'account_id':'synthetic','destination_path':'/tmp'})
            self.assertEqual(status,400)
            status,_=self.request('/api/workflow/start',{'account_id':'synthetic','destination_id':'unknown'})
            self.assertEqual(status,400)

    def test_native_cancel_does_not_register_and_selection_binds_job(self):
        with patch('wechat_export.archive_server.choose_native_folder',return_value=None):
            self.assertEqual(self.request('/api/setup/choose-output',{}),(200,{'cancelled':True}))
        first=self.root/'first';first.mkdir()
        second=self.root/'second';second.mkdir()
        with patch('wechat_export.archive_server.choose_native_folder',return_value=first):
            code,data=self.request('/api/setup/choose-output',{});self.assertEqual(code,200)
        token=data['location']['destination_id']
        code,job=self.request('/api/workflow/start',{'account_id':'synthetic','destination_id':token})
        self.assertEqual(code,200)
        job_id=job['job_id'];self.assertEqual(job['payload_public']['destination_id'],token)
        with patch('wechat_export.archive_server.choose_native_folder',return_value=second):
            self.assertEqual(self.request('/api/setup/choose-output',{})[0],200)
        persisted=self.ctx.job_store.get(job_id)
        self.assertEqual(persisted.payload['destination_id'],token)
        self.assertEqual(Path(persisted.payload['destination_binding']['path']).parent,first)
        # A registered external archive can be opened without accepting its path.
        from wechat_export.output_locations import OutputLocations
        root=OutputLocations(self.runtime).resolve(token)
        archive=_demo_tree(root/'fixture')
        code,opened=self.request('/api/setup/open-archive',{'source_id':f'external:{token}:{archive.name}'})
        self.assertEqual(code,200);self.assertTrue(opened['archive_id'])
        code,listing=self.request('/api/setup/archives')
        self.assertEqual(code,200)
        self.assertIn(f'external:{token}:{archive.name}',[a['source_id'] for a in listing['archives']])
