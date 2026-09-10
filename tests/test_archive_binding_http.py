"""Two synthetic accounts intentionally share conversation IDs."""
import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_export_service import _demo_tree
from wechat_export.archive_index import build_index
from wechat_export.archive_server import ArchiveHandler, ArchiveHTTPServer, ServerContext
from wechat_export.http_security import new_session_token
from wechat_export.runtime import resolve_runtime

class ArchiveBindingHttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.runtime = resolve_runtime(self.tmp.name)
        self.a = _demo_tree(self.runtime.exports_root / 'account-A')
        self.b = _demo_tree(self.runtime.exports_root / 'account-B')
        p = self.b / 'all/messages.jsonl'
        rows = [json.loads(line) for line in p.read_text().splitlines()]
        for r in rows: r['text'] = 'SYNTHETIC ACCOUNT B'
        rows.append(dict(rows[0], record_uid='B-extra'))
        p.write_text('\n'.join(json.dumps(r) for r in rows)+'\n')
        m = self.b/'manifest.json'
        v=json.loads(m.read_text());v['record_count']=5;m.write_text(json.dumps(v))
        build_index(self.b)
        self.token = new_session_token()
        self.ctx = ServerContext(bind_port=0, session_token=self.token, runtime=self.runtime,
                                 export_dir=self.a, index_path=self.a/'archive.sqlite')
        self.server = ArchiveHTTPServer(('127.0.0.1',0), ArchiveHandler)
        self.port = self.server.server_address[1]
        self.ctx.bind_port = self.port
        self.server.context = self.ctx
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        _, boot = self.request('GET','/api/bootstrap')
        self.a_id = boot.get('archive_id', 'before-binding-A')

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(5)
        self.tmp.cleanup()

    def request(self, method, path, body=None, archive_id=None):
        conn=http.client.HTTPConnection('127.0.0.1',self.port,timeout=10)
        headers={'Host':f'127.0.0.1:{self.port}'}
        if archive_id is not None: headers['X-Archive-ID']=archive_id
        if method=='POST':
            headers.update({'Origin':f'http://127.0.0.1:{self.port}', 'Content-Type':'application/json',
                            'Cookie':f'wla_session={self.token}', 'X-CSRF-Token':self.token})
        conn.request(method,path,body=json.dumps(body) if body is not None else None,headers=headers)
        response=conn.getresponse();status=response.status;data=json.loads(response.read());conn.close()
        return status,data

    def open_b(self):
        code,result=self.request('POST','/api/setup/open-archive',{'source_id':'export:account-B'})
        self.assertEqual(code,200)
        return result

    def await_job(self, job_id):
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            job=self.ctx.job_store.get(job_id)
            if job.state in {'ready','failed','cancelled'}: return job
            time.sleep(.02)
        self.fail('test export did not terminate')

    def test_old_tab_cannot_export_new_account(self):
        self.open_b()
        code,result=self.request('POST','/api/export',{'scope':{'kind':'all'}},self.a_id)
        self.assertEqual(code,409)
        self.assertEqual(result['code'],'archive_changed')

    def test_missing_source_binding_is_rejected(self):
        code,result=self.request('GET','/api/export/preview?scope=all')
        self.assertEqual(code,409)
        self.assertEqual(result['code'],'archive_binding_required')

    def test_switch_during_count_keeps_captured_source(self):
        from wechat_export.export_service import count_messages as original_count
        switched=[]
        def count_then_switch(conn,spec):
            n=original_count(conn,spec)
            if not switched:
                switched.append(True);self.open_b()
            return n
        with patch('wechat_export.archive_server.count_messages',side_effect=count_then_switch):
            code,result=self.request('POST','/api/export',{'scope':{'kind':'all'}},self.a_id)
        self.assertEqual(code,200)
        job=self.await_job(result['job_id'])
        self.assertEqual(job.state,'ready',job.error)
        rows=[json.loads(line) for line in Path(job.payload['path']).read_text().splitlines()]
        self.assertEqual(len(rows),4)
        self.assertFalse(any(r.get('text')=='SYNTHETIC ACCOUNT B' for r in rows))

    def test_canonical_edit_invalidates_preview(self):
        p=self.a/'all/messages.jsonl'
        with p.open('a') as out: out.write('\n')
        code,result=self.request('GET','/api/export/preview?scope=all',archive_id=self.a_id)
        self.assertEqual(code,409)
        self.assertEqual(result['code'],'archive_source_changed')

    def test_old_tab_reads_and_media_rejected_after_switch(self):
        self.open_b()
        for endpoint in ('/api/meta', '/api/conversations', '/api/messages?conversation_id=wxid_alice', '/api/search?q=hello', '/api/media?uid=1'):
            with self.subTest(endpoint=endpoint):
                code,result=self.request('GET',endpoint,archive_id=self.a_id)
                self.assertEqual(code,409)
                self.assertEqual(result['code'],'archive_changed')

    def test_change_after_private_copy_cannot_change_export(self):
        from wechat_export.archive_binding import ArchiveBinding
        original = ArchiveBinding.snapshot_to
        def copy_then_edit(binding, destination, cancelled=lambda:False):
            result=original(binding,destination,cancelled)
            with (self.a/'all/messages.jsonl').open('a') as fh:
                fh.write(json.dumps({'record_uid':'late','conversation_id':'wxid_alice','text':'LATE'})+'\n')
            return result
        tasks=[]
        with patch.object(self.ctx.job_store,'run_in_thread',side_effect=lambda _id,fn: tasks.append(fn) or True):
            code,response=self.request('POST','/api/export',{'scope':{'kind':'all'}},self.a_id)
        self.assertEqual(code,200)
        with patch.object(ArchiveBinding,'snapshot_to',copy_then_edit):
            tasks[0]()
        job=self.ctx.job_store.get(response['job_id'])
        self.assertEqual(job.state,'ready',job.error)
        rows=[json.loads(line) for line in Path(job.payload['path']).read_text().splitlines()]
        self.assertEqual(len(rows),4)
        manifest=json.loads((Path(job.payload['path']).parent/'manifest.json').read_text())
        self.assertEqual(manifest['source_binding']['archive_id'],self.a_id)

    def test_change_before_worker_copy_fails_without_output(self):
        tasks=[]
        with patch.object(self.ctx.job_store,'run_in_thread',side_effect=lambda _id,fn: tasks.append(fn) or True):
            code,response=self.request('POST','/api/export',{'scope':{'kind':'all'}},self.a_id)
        self.assertEqual(code,200)
        with (self.a/'all/messages.jsonl').open('a') as fh: fh.write('\n')
        tasks[0]()
        job=self.ctx.job_store.get(response['job_id'])
        self.assertEqual(job.state,'failed')
        self.assertEqual(job.error['code'],'archive_source_changed')
        self.assertFalse((self.a/'slices').exists())
        self.assertEqual(list(self.runtime.jobs_root.glob('export-source-*')),[])
        from wechat_export.scratch import NAMESPACE
        self.assertEqual(list((self.runtime.jobs_root/NAMESPACE).glob('scratch-*')),[])

    def test_reopen_rebuilds_changed_source_and_changes_binding(self):
        p=self.a/'all/messages.jsonl'
        rows=[json.loads(line) for line in p.read_text().splitlines()]
        rows.append(dict(rows[0],record_uid='extra'))
        p.write_text('\n'.join(json.dumps(r) for r in rows)+'\n')
        code,opened=self.request('POST','/api/setup/open-archive',{'source_id':'export:account-A'})
        self.assertEqual(code,200)
        self.assertNotEqual(opened['archive_id'],self.a_id)
        code,preview=self.request('GET','/api/export/preview?scope=all',archive_id=opened['archive_id'])
        self.assertEqual(code,200)
        self.assertEqual(preview['count'],5)

    def test_local_timezone_preview_and_export_use_same_instant(self):
        from urllib.parse import urlencode
        query=urlencode({'scope':'all','since':'2026-01-01T17:00', 'until':'2026-01-01T17:02',
                         'display_timezone':'America/Los_Angeles'})
        code,preview=self.request('GET','/api/export/preview?'+query,archive_id=self.a_id)
        self.assertEqual(code,200)
        self.assertEqual(preview['count'],2)
        body={'scope':{'kind':'all'},'since':'2026-01-01T17:00','until':'2026-01-01T17:02',
              'display_timezone':'America/Los_Angeles'}
        code,started=self.request('POST','/api/export',body,self.a_id)
        self.assertEqual(code,200)
        job=self.await_job(started['job_id'])
        self.assertEqual(job.state,'ready',job.error)
        self.assertEqual(job.payload['written'],preview['count'])

    def test_dst_fold_rejected_by_both_http_paths(self):
        from urllib.parse import urlencode
        fields={'since':'2026-11-01T01:30','display_timezone':'America/Los_Angeles'}
        code,result=self.request('GET','/api/export/preview?'+urlencode({'scope':'all',**fields}),archive_id=self.a_id)
        self.assertEqual(code,400)
        self.assertEqual(result['code'],'ambiguous_local_time')
        code,result=self.request('POST','/api/export',{'scope':{'kind':'all'},**fields},self.a_id)
        self.assertEqual(code,400)
        self.assertEqual(result['code'],'ambiguous_local_time')

    def test_readable_preview_reports_exclusion_and_slice_agrees(self):
        status, preview = self.request('GET', '/api/export/preview?scope=all&readable=1', archive_id=self.a_id)
        self.assertEqual(status, 200)
        self.assertEqual(preview['count'], 3)
        counts = preview['selection_accounting']
        self.assertEqual((counts['candidate_count'], counts['selected_count'], counts['excluded_unreadable_count']), (4, 3, 1))
        status, created = self.request('POST', '/api/export', {'scope':{'kind':'all'}, 'readable_only':True}, self.a_id)
        self.assertEqual(status, 200)
        job = self.await_job(created['job_id'])
        self.assertEqual(job.state, 'ready')
        result = json.loads((Path(job.payload['path']).parent/'manifest.json').read_text())
        self.assertEqual(result['selection_accounting'], counts)
