import json
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from tests import test_insights_http
from wechat_export.insights.store import open_store, InsightsError
from wechat_export.insights.identity import save_identity
from wechat_export.insights.recovery import list_recovery,recover_missing
from wechat_export.learning.importer import _upsert_item,paste_body,get_item
from wechat_export.learning.notes import save_note
from wechat_export.learning.semantic import prepare_learning,generate_learning,validate_learning_result


class ProductCompletionTests(unittest.TestCase):
    def setUp(self):
        self.case=test_insights_http.InsightsHttpTests();self.case.setUp()
        self.case._post('/api/insights/context',{'accept_consistent_self':True})
    def tearDown(self):self.case.tearDown()
    def wait(self,task):
        for _ in range(200):
            st,result=self.case._get('/api/insights/tasks/'+task['job_id'])
            if result['state'] in {'ready','failed','cancelled','blocked'}:return result
            time.sleep(.01)
        self.fail('worker timeout')
    def test_background_local_profile_and_portable_report(self):
        st,task=self.case._post('/api/profiles/runs',{'kind':'self','engine':'local_explicit','background':True})
        self.assertEqual(st,200);done=self.wait(task);self.assertEqual(done['state'],'ready',done)
        st,pack=self.case._post('/api/insights/exports',{'kind':'profile','run_id':done['run_id']})
        self.assertEqual(st,200,pack);root=Path(pack['path'])
        self.assertTrue((root/'开始阅读.html').is_file());report=json.loads((root/'report.json').read_text())
        self.assertEqual(report['source_kind'],'live-db');self.assertEqual(report['backup2_coverage'],'unverified')
        self.assertTrue(report['observations']);self.assertIn('SHA256SUMS',[p.name for p in root.iterdir()])
        st,listing=self.case._get('/api/insights/tasks');self.assertTrue(listing['tasks'])
    def test_task_cancel_during_cloud_prevents_run_publication(self):
        entered=threading.Event();release=threading.Event()
        class Fake:
            kind='remote';engine_id='synthetic';base_url='https://example.invalid';model='synthetic'
            def analyze(self,payload):
                entered.set();release.wait(5)
                r=payload['records'][0]
                return {'observations':[{'statement':r['text'],'evidence_ids':[r['record_uid']]}]}
        with patch('wechat_export.insights_routes.resolve_provider',return_value=Fake()):
            st,ticket=self.case._post('/api/profiles/consent',{'kind':'self','engine':'byok','approve_remote':True})
            st,task=self.case._post('/api/profiles/runs',{'kind':'self','engine':'byok','approve_remote':True,'consent_ticket':ticket['ticket_id'],'background':True})
            self.assertEqual(st,200,task)
            try:
                self.assertTrue(entered.wait(3))
                st,cancelled=self.case._post('/api/insights/tasks/'+task['job_id']+'/cancel',{})
                self.assertEqual(cancelled['state'],'cancelled')
            finally:release.set()
            for _ in range(100):
                if not self.case.httpd.context.job_store.busy(task['job_id']):break
                time.sleep(.02)
            st,runs=self.case._get('/api/profiles/runs');self.assertFalse(runs['runs'])
    def test_remote_learning_approval_draft_and_replay_rejection(self):
        self.case._post('/api/learning/imports',{'conversation_id':'room@chatroom'})
        _,items=self.case._get('/api/learning/items');item=next(i for i in items['items'] if i['kind']=='link')
        self.case._post(f"/api/learning/items/{item['item_id']}/content",{'text':'学习需要反馈。\n\n复习可以检验理解。'})
        calls=[]
        class Fake:
            kind='remote';engine_id='synthetic';base_url='https://example.invalid';model='synthetic'
            def summarize(self,payload):
                calls.append(payload);p=payload['paragraphs'][0]
                return {'claims':[{'text':'反馈有助于学习。','paragraph_ids':[p['paragraph_id']],'quote':p['text']}],
                        'questions':[{'question':'学习需要什么？','answer':'反馈。','paragraph_ids':[p['paragraph_id']],'quote':p['text']}]}
        path=f"/api/learning/items/{item['item_id']}"
        with patch('wechat_export.insights_routes.resolve_provider',return_value=Fake()):
            st,bad=self.case._post(path+'/summary-consent',{'engine':'byok','approve_remote':'false'})
            self.assertNotEqual(st,200);self.assertFalse(calls)
            st,ticket=self.case._post(path+'/summary-consent',{'engine':'byok','approve_remote':True});self.assertEqual(st,200,ticket)
            args={'mode':'remote','engine':'byok','approve_remote':True,'consent_ticket':ticket['ticket_id']}
            st,task=self.case._post(path+'/summaries',args);self.assertEqual(st,200,task)
            done=self.wait(task);self.assertEqual(done['state'],'ready',done)
            st,_=self.case._post(path+'/summaries',args);self.assertNotEqual(st,200)
        _,detail=self.case._get(path);self.assertEqual(detail['summaries'][0]['status'],'draft');self.assertEqual(len(detail['review_cards']),1);self.assertEqual(len(calls),1)
    def test_forged_learning_citations_rejected(self):
        cs,qs=validate_learning_result({'claims':[{'text':'fabricated','paragraph_ids':['p1'],'quote':'invented'}]},[{'paragraph_id':'p1','text':'actual'}])
        self.assertEqual(cs,[])


class RecoveryMergeTests(unittest.TestCase):
    def test_explicit_recovery_preserves_current_notes_and_rejects_wrong_account(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td);root=d/'archive';data=d/'data'
            old=open_store(data,'old');save_identity(old,{'self_sender_ids':['me'],'verification_state':'consistent'})
            item,_=_upsert_item(old,kind='link',title='Synthetic',canonical_key='synthetic',content_state='title_only')
            paste_body(old,item,'BODY');note=save_note(old,item,'OLD');old.close()
            current=open_store(data,'new',archive_root=root);save_identity(current,{'self_sender_ids':['me'],'verification_state':'consistent'})
            candidate=list_recovery(current,data)[0]
            with self.assertRaises(InsightsError):recover_missing(current,data,candidate['candidate_id'],candidate['fingerprint'],['other'])
            result=recover_missing(current,data,candidate['candidate_id'],candidate['fingerprint'],['me'])
            self.assertEqual(result['added']['notes'],1)
            save_note(current,item,'NEW',note_id=note['note_id'],revision=1)
            recover_missing(current,data,candidate['candidate_id'],candidate['fingerprint'],['me'])
            self.assertEqual(get_item(current,item)['notes'][0]['user_text'],'NEW');current.close()
