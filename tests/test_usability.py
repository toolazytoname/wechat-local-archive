"""Safety and functional regressions for the task-first UI (synthetic data only)."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from tests import test_insights_http
from wechat_export.insights.providers import GrokCliProvider, grok_failure_code
from wechat_export.insights.store import InsightsError


class UsabilityHttpTests(unittest.TestCase):
    def setUp(self):
        self.case=test_insights_http.InsightsHttpTests()
        self.case.setUp()
    def tearDown(self):
        self.case.tearDown()

    def test_connection_check_requires_explicit_confirmation_and_uses_only_fixed_sample(self):
        calls=[]
        class Provider:
            kind='remote'
            def analyze(self,payload):
                calls.append(payload)
                return {'observations':[]}
        with patch('wechat_export.insights_routes.resolve_provider',return_value=Provider()):
            for value in (None,False,'true'):
                status,_=self.case._post('/api/insights/engines/test',{'engine':'byok','confirm_test':value})
                self.assertNotEqual(status,200)
            self.assertEqual(calls,[])
            status,result=self.case._post('/api/insights/engines/test',{'engine':'byok','confirm_test':True,'records':[{'text':'MUST NOT SEND'}]})
        self.assertEqual(status,200);self.assertTrue(result['ok'])
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0]['records'][0]['record_uid'],'connection_test')
        self.assertNotIn('MUST NOT SEND',json.dumps(calls))
        _,runs=self.case._get('/api/profiles/runs')
        self.assertEqual(runs['runs'],[])

    def test_connection_error_is_actionable_without_echoing_private_details(self):
        class Provider:
            kind='remote'
            def analyze(self,payload):raise InsightsError('PRIVATE RESPONSE MUST NOT LEAK','remote_auth')
        with patch('wechat_export.insights_routes.resolve_provider',return_value=Provider()):
            _,result=self.case._post('/api/insights/engines/test',{'engine':'byok','confirm_test':True})
        self.assertFalse(result['ok']);self.assertEqual(result['code'],'remote_auth')
        self.assertNotIn('PRIVATE',json.dumps(result));self.assertIn('设置',result['message'])

    def test_saved_friend_report_list_includes_scope_for_reopen(self):
        self.case._post('/api/insights/context',{'accept_consistent_self':True})
        scope={'conversation_id':'wxid_alice','friend_sender_ids':['wxid_alice']}
        status,_=self.case._post('/api/profiles/runs',{'kind':'friend','engine':'local_explicit','scope':scope})
        self.assertEqual(status,200)
        _,runs=self.case._get('/api/profiles/runs?kind=friend')
        self.assertEqual(runs['runs'][0]['scope'],scope)

    def test_task_public_scope_is_explicit_for_reconnecting_only_the_selected_friend(self):
        from wechat_export.jobs import Job
        from wechat_export.insights.tasks import public_task
        scope={'conversation_id':'synthetic_friend','friend_sender_ids':['synthetic_friend']}
        job=Job('synthetic','profile_analysis','running','now','now',payload={
            'profile_kind':'friend','scope':scope,'source_revision':'synthetic-revision','remote':True})
        result=public_task(job)
        self.assertEqual(result['scope'],scope)
        self.assertEqual(result['source_revision'],'synthetic-revision')
        self.assertNotIn('archive_root',result)


class GrokUsabilityTests(unittest.TestCase):
    def test_current_camelcase_structured_output_without_text(self):
        with tempfile.TemporaryDirectory() as td:
            binary=Path(td)/'grok';binary.write_text('#!/bin/sh\n');binary.chmod(0o700)
            result={'observations':[{'statement':'synthetic'}]}
            provider=GrokCliProvider({'command':str(binary)},consent_granted=True,
                run_cmd=lambda *a,**k:subprocess.CompletedProcess([],0,json.dumps({'structuredOutput':result}),''))
            self.assertEqual(provider.analyze({'records':[{'text':'synthetic'}]})['observations'],result['observations'])

    def test_auth_failure_not_retried_and_raw_output_not_exposed(self):
        calls=[]
        with tempfile.TemporaryDirectory() as td:
            binary=Path(td)/'grok';binary.write_text('#!/bin/sh\n');binary.chmod(0o700)
            def fail(*a,**k):
                calls.append(1)
                return subprocess.CompletedProcess([],1,'PRIVATE PAYLOAD','Unauthorized')
            p=GrokCliProvider({'command':str(binary)},consent_granted=True,run_cmd=fail)
            with self.assertRaises(InsightsError) as raised:p.analyze({'records':[{'text':'synthetic'}]})
            self.assertEqual(raised.exception.code,'remote_auth');self.assertNotIn('PRIVATE',str(raised.exception))
            self.assertEqual(len(calls),1)

    def test_allowlisted_error_codes(self):
        for text,code in [('quota exceeded','remote_rate_limit'),('timed out','remote_timeout'),('unknown private response','remote_http')]:
            self.assertEqual(grok_failure_code('',text),code)


class ProfileViewStateTests(unittest.TestCase):
    def test_scope_match_and_finished_history_label(self):
        import shutil
        if not shutil.which('node'):
            self.skipTest('node unavailable')
        script = r"""
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const label={textContent:'正在处理'},other={textContent:'untouched'};
const context={window:{},document:{querySelectorAll:()=>[
  {dataset:{taskId:'selected'},querySelector:()=>label},
  {dataset:{taskId:'other'},querySelector:()=>other}
]}};
vm.createContext(context);vm.runInContext(fs.readFileSync('wechat_export/static/profiles.js','utf8'),context);
assert(context.sameProfileScope({},{}));
assert(context.sameProfileScope({conversation_id:'a',friend_sender_ids:['a','b']},{friend_sender_ids:['b','a'],conversation_id:'a'}));
assert(!context.sameProfileScope({conversation_id:'a'},{conversation_id:'b'}));
context.updateTaskHistoryState({job_id:'selected',state:'ready',created_at:'2026-01-01T00:00:00Z'});
assert(label.textContent.includes('已完成'));assert.equal(other.textContent,'untouched');
"""
        subprocess.run(['node','-e',script],check=True,capture_output=True,text=True)


if __name__=='__main__':unittest.main()
