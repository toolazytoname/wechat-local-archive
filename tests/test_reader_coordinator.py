"""State-machine contracts using explicit fake boundaries; no real WeChat runs.

These tests do NOT prove HMAC compatibility. Real synthetic codec/KDF suites test
those primitives separately; the callbacks below simulate their contracts.
"""
import copy
import fcntl
import json
import shutil
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from tests.test_build_fingerprint import fake_bundle, fake_environment
from wechat_export.authorization import CONSENT_KEYS, make_live_grant, LIVE_GRANT_PHRASE
from wechat_export.build_fingerprint import SCHEMA
from wechat_export.jobs import JobStore
from wechat_export.live_reader import ReaderError
from wechat_export.runtime import resolve_runtime
from wechat_export.workflow import start_read_job, advance, WorkflowError

CONFIRM = {'confirm_live_step':True,'confirm_library_exception':True}


class ReaderCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve()
        self.runtime=resolve_runtime(self.root/'runtime')
        self.store=JobStore(self.runtime.jobs_root)
        self.bundle=fake_bundle(self.root/'Synthetic.app')
        self.env=fake_environment(self.bundle)
        self.account=self.root/'synthetic-account'
        (self.account/'db_storage/contact').mkdir(parents=True)
        (self.account/'db_storage/message').mkdir()
        for p in ('contact/contact.db','message/message_0.db'):
            (self.account/'db_storage'/p).write_bytes(b'not an encrypted fixture; coordinator only')
        self.job=start_read_job(self.store,account_id='synthetic',adapter_id='candidate',synthetic=False)
        self.job.payload.update({key:True for key in CONSENT_KEYS})
        self.job.payload['live_grant']=make_live_grant(self.job.job_id,LIVE_GRANT_PHRASE)
        self.store.save(self.job)
        self.calls=[]
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.stack.enter_context(patch('wechat_export.environment.collect_environment',side_effect=lambda:copy.deepcopy(self.env)))
        self.preflight=self.stack.enter_context(patch('wechat_export.adapters.macos_xwechat.preflight',return_value={'ok':True}))
        self.stack.enter_context(patch('wechat_export.discovery.resolve_account_dir',return_value=self.account))
        self.snapshot=self.stack.enter_context(patch('wechat_export.livedb_snapshot.snapshot_strict',side_effect=self.fake_snapshot))
        self.prepare=self.stack.enter_context(patch('wechat_export.live_reader.prepare_copy',side_effect=self.fake_prepare))
        self.capture=self.stack.enter_context(patch('wechat_export.adapters.macos_xwechat.execute_key_capture',side_effect=self.fake_capture))
        self.continue_=self.stack.enter_context(patch('wechat_export.workflow._continue_from_materials',side_effect=self.fake_continue))

    def fake_snapshot(self,src,dst,cancelled):
        self.calls.append('snapshot');shutil.copytree(src,dst)
        return {'consistency':'idle_hash_verified','hot_copies':0,'db_count':2}

    def fake_prepare(self,*,app,account,work,cancelled):
        self.calls.append('prepare')
        self.assertEqual(app,Path('/Applications/WeChat.app'))  # Assert argv scope, never access it.
        (work/'reader-prepared.json').write_text(json.dumps({'fingerprint_schema':SCHEMA,
            'original_bundle_sha256':self.env['wechat_fingerprint']['sha256']}))
        return work/'WeChat-debug.app'

    def fake_capture(self,*,job,app,copy,snapshot,output,cancelled):
        self.calls.append('capture')
        self.assertEqual(job.state,'acquiring_key')
        self.assertTrue(job.payload['capture_attempted'])
        output.write_bytes(b'SYNTHETIC-COORDINATOR-ONLY-KEY!!!'[:32].ljust(32,b'!'));output.chmod(0o600)
        return {'hmac_verified':True,'databases_verified':2,'breakpoint_hit':True}

    def fake_continue(self,store,job,body):
        self.calls.append('continue')
        job.state='awaiting_sample_check'
        job.payload['export_source_id']='export:synthetic-coordinator-result'
        store.save(job);return job

    def step(self,command,body=None):
        self.job=advance(self.store,self.job,command,CONFIRM if body is None else body,runtime=self.runtime)
        return self.job

    def diagnostic(self):
        return json.loads((self.runtime.jobs_root/(self.job.job_id+'-diagnostic.json')).read_text())

    def test_prepare_wait_capture_sample_and_no_repeat(self):
        self.assertEqual(self.step('prepare_reader').state,'awaiting_user_action')
        self.assertEqual(self.calls,['snapshot','prepare'])
        self.assertTrue(self.job.payload['live_grant']['consumed'])
        self.assertIn('prepared_binding',self.job.payload)
        self.assertEqual(self.step('acquire_key').state,'awaiting_sample_check')
        self.assertEqual(self.calls,['snapshot','prepare','capture','continue'])
        self.assertTrue(self.job.payload['live_key_acquisition_completed'])
        self.assertFalse(self.job.payload['new_user_first_read'])
        self.assertTrue((self.runtime.data_root/'work'/('snapshot-'+self.job.job_id)/'key-reference.json').exists())
        self.assertEqual(self.step('acquire_key').payload['block_reason'],'reader_not_prepared')
        self.assertEqual(self.capture.call_count,1)

    def test_missing_stage_confirmation_and_expired_grant_do_nothing(self):
        with self.assertRaises(WorkflowError): self.step('prepare_reader',{})
        self.job.payload['live_grant']['expires_at']=0
        result=self.step('prepare_reader')
        self.assertEqual(result.payload['block_reason'],'live_grant_missing')
        self.assertEqual(self.calls,[])

    def test_unsupported_or_unfingerprinted_environment_does_not_snapshot(self):
        self.env['wechat_build']='269631'
        self.assertEqual(self.step('prepare_reader').payload['block_reason'],'unsupported_version')
        self.assertEqual(self.calls,[])
        self.env['wechat_build']='269630';self.env['wechat_fingerprint']['complete']=False
        result=self.step('prepare_reader')
        self.assertEqual(result.payload['block_reason'],'bundle_fingerprint_or_signature_unverified')
        self.assertEqual(self.calls,[])

    def test_snapshot_failure_and_cancel_stop_before_preparing_copy(self):
        self.snapshot.side_effect=RuntimeError('source_files_open')
        self.assertEqual(self.step('prepare_reader').payload['block_reason'],'source_files_open')
        self.prepare.assert_not_called()
        self.assertTrue(self.job.payload['live_grant']['consumed'])

    def test_cancel_after_snapshot_does_not_call_prepare(self):
        def snap(*args):
            result=self.fake_snapshot(*args);self.store.request_cancel(self.job.job_id);return result
        self.snapshot.side_effect=snap
        self.assertEqual(self.step('prepare_reader').state,'cancelled')
        self.prepare.assert_not_called();self.capture.assert_not_called()

    def test_prepare_failure_retains_snapshot_but_never_launches(self):
        self.prepare.side_effect=ReaderError('reader_command_failed')
        result=self.step('prepare_reader')
        self.assertEqual(result.payload['block_reason'],'reader_command_failed')
        self.assertTrue((self.runtime.data_root/'work'/('snapshot-'+self.job.job_id)/'live-db').exists())
        self.assertNotIn('prepared_binding',result.payload)
        self.capture.assert_not_called()

    def test_module_change_with_same_version_blocks_launch(self):
        self.step('prepare_reader')
        self.env['wechat_fingerprint']['sha256']='b'*64
        result=self.step('acquire_key')
        self.assertEqual(result.payload['block_reason'],'environment_changed')
        self.capture.assert_not_called()

    def test_legacy_preparation_without_binding_cannot_launch(self):
        self.step('prepare_reader');self.job.payload.pop('prepared_binding')
        self.assertEqual(self.step('acquire_key').payload['block_reason'],'environment_changed')
        self.capture.assert_not_called()

    def test_failed_capture_does_not_register_key_or_retry(self):
        self.step('prepare_reader')
        self.capture.side_effect=ReaderError('no_authenticated_candidate')
        result=self.step('acquire_key')
        self.assertEqual(result.payload['block_reason'],'no_authenticated_candidate')
        self.assertNotIn('key_file_name',result.payload);self.continue_.assert_not_called()
        self.step('acquire_key');self.assertEqual(self.capture.call_count,1)

    def test_partial_capture_result_is_not_success(self):
        self.step('prepare_reader')
        self.capture.side_effect=None;self.capture.return_value={'hmac_verified':True}
        self.assertEqual(self.step('acquire_key').payload['block_reason'],'capture_verification_incomplete')
        self.continue_.assert_not_called()

    def test_cancel_after_capture_does_not_register_or_normalize(self):
        self.step('prepare_reader')
        def capture(**kwargs):
            result=self.fake_capture(**kwargs);self.store.request_cancel(self.job.job_id);return result
        self.capture.side_effect=capture
        self.assertEqual(self.step('acquire_key').state,'cancelled')
        self.assertNotIn('key_file_name',self.job.payload);self.continue_.assert_not_called()
        # Private candidate material is retained, not automatically purged.
        self.assertTrue(any(self.runtime.private_root.glob('passphrase-*')))

    def test_shared_live_lock_prevents_second_reader(self):
        with (self.runtime.jobs_root/'live-reader.lock').open('a') as lease:
            fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.assertEqual(self.step('prepare_reader').payload['block_reason'],'reader_busy')
        self.assertEqual(self.calls,[])

    def test_unexpected_exception_text_is_never_persisted_or_public(self):
        self.prepare.side_effect=ReaderError('SYNTHETIC SENSITIVE ERROR TEXT')
        result=self.step('prepare_reader')
        self.assertEqual(result.payload['block_reason'],'reader_operation_failed')
        self.assertNotIn('SENSITIVE',json.dumps(self.diagnostic()))
        self.assertNotIn('SENSITIVE',json.dumps(result.to_dict()))

    def test_application_changed_during_prepare_is_not_launchable(self):
        def prepare(**kwargs):
            self.env['wechat_fingerprint']['sha256']='c'*64
            return self.fake_prepare(**kwargs)
        self.prepare.side_effect=prepare
        self.assertEqual(self.step('prepare_reader').payload['block_reason'],'environment_changed_during_prepare')
        self.capture.assert_not_called()
        self.assertNotIn('prepared_binding',self.job.payload)

    def test_valid_result_without_private_key_file_is_not_registered(self):
        self.step('prepare_reader')
        self.capture.side_effect=None
        self.capture.return_value={'hmac_verified':True,'databases_verified':2,'breakpoint_hit':True}
        self.assertEqual(self.step('acquire_key').payload['block_reason'],'captured_key_file_invalid')
        self.assertNotIn('key_file_name',self.job.payload)
        self.continue_.assert_not_called()

    def test_insecure_key_permissions_are_not_registered(self):
        self.step('prepare_reader')
        def capture(**kwargs):
            result=self.fake_capture(**kwargs);kwargs['output'].chmod(0o644);return result
        self.capture.side_effect=capture
        self.assertEqual(self.step('acquire_key').payload['block_reason'],'captured_key_file_invalid')
        self.assertNotIn('key_file_name',self.job.payload)
        self.continue_.assert_not_called()

    def test_interrupted_capture_recovery_revokes_authority(self):
        self.step('prepare_reader')
        self.job.state='acquiring_key';self.job.payload['capture_attempted']=True;self.store.save(self.job)
        self.assertEqual(JobStore(self.runtime.jobs_root).recover_interrupted(),1)
        self.job=self.store.get(self.job.job_id)
        self.assertNotIn('live_grant',self.job.payload)
        self.assertEqual(self.job.state,'blocked')
        self.step('acquire_key')
        self.capture.assert_not_called()
