"""Copy/sign command scope and original/debug-copy identity guards; no app launch."""
import json
import plistlib
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from tests.test_build_fingerprint import fake_bundle
from wechat_export.build_fingerprint import fingerprint_bundle, SCHEMA
from wechat_export.live_reader import prepare_copy, acquire_key, ReaderError


class ReaderBundleBindingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve()
        self.app=fake_bundle(self.root/'Synthetic.app')
        self.account=self.root/'source/xwechat_files/synthetic';(self.account/'config').mkdir(parents=True)
        (self.account/'config/synthetic.txt').write_text('fixture')
        self.work=self.root/'work';self.work.mkdir()
        self.original=fingerprint_bundle(self.app)['sha256']

    def test_prepare_signs_only_copy_and_binds_full_original_and_copy(self):
        signed=[]
        signed_values={}
        def command(args,timeout=60):
            if args[0]=='ditto': shutil.copytree(args[1],args[2],symlinks=True)
            elif '--force' in args:
                signed.append(Path(args[-1]))
                self.assertTrue(Path(args[-1]).is_relative_to(self.work/'WeChat-debug.app'))
                self.assertNotIn('--options',args)
                signed_values[args[-1]]=plistlib.loads(Path(args[args.index('--entitlements')+1]).read_bytes()) if '--entitlements' in args else {}
            return b''
        ent=plistlib.dumps({'synthetic.original.entitlement':True})
        def entitlements(args,**kwargs):
            return subprocess.CompletedProcess([],0,plistlib.dumps(signed_values.get(args[-1],{'synthetic.original.entitlement':True})),b'')
        with patch('wechat_export.live_reader.wechat_pids',return_value=[]), \
             patch('wechat_export.live_reader.original_identity',return_value=self.original), \
             patch('wechat_export.live_reader.run',side_effect=command), \
             patch('wechat_export.live_reader.subprocess.run',side_effect=entitlements):
            copy=prepare_copy(app=self.app,account=self.account,work=self.work,cancelled=lambda:False)
        self.assertTrue(signed)
        self.assertEqual(fingerprint_bundle(self.app)['sha256'],self.original)
        report=json.loads((self.work/'reader-prepared.json').read_text())
        self.assertEqual(report['fingerprint_schema'],SCHEMA)
        self.assertEqual(report['original_bundle_sha256'],self.original)
        self.assertEqual(report['debug_bundle_sha256'],fingerprint_bundle(copy)['sha256'])
        values=plistlib.loads((self.work/'sign-entitlements.plist').read_bytes())
        self.assertTrue(values['synthetic.original.entitlement'])
        self.assertTrue(values['com.apple.security.cs.disable-library-validation'])
        audit=json.loads((self.work/'signing-audit.json').read_text())
        self.assertTrue(audit['entitlements_verified'])
        self.assertEqual({i['path'] for i in audit['targets'] if i['library_exception_target']},{'.','Contents/MacOS/WeChat'})

    def test_changed_debug_module_refuses_before_capture(self):
        copy=self.work/'WeChat-debug.app';shutil.copytree(self.app,copy,symlinks=True)
        report={'fingerprint_schema':SCHEMA,'original_bundle_sha256':self.original,
                'debug_bundle_sha256':fingerprint_bundle(copy)['sha256']}
        (self.work/'reader-prepared.json').write_text(json.dumps(report))
        (copy/'Contents/Frameworks/Example.framework/Versions/A/Example').write_bytes(b'changed nested module')
        with patch('wechat_export.live_reader.wechat_pids',return_value=[]), \
             patch('wechat_export.live_reader.original_identity',return_value=self.original), \
             patch('wechat_export.live_reader.run'), patch('wechat_export.live_reader.capture_kdf') as capture:
            with self.assertRaisesRegex(ReaderError,'debug_copy_changed_since_prepare'):
                acquire_key(app=self.app,copy=copy,snapshot=self.root/'snapshot',output=self.root/'key.raw',cancelled=lambda:False)
        capture.assert_not_called();self.assertFalse((self.root/'key.raw').exists())

    def test_old_main_executable_only_report_is_not_launch_authority(self):
        copy=self.work/'WeChat-debug.app';shutil.copytree(self.app,copy,symlinks=True)
        (self.work/'reader-prepared.json').write_text(json.dumps({'original_executable_sha256':self.original}))
        with patch('wechat_export.live_reader.wechat_pids',return_value=[]), \
             patch('wechat_export.live_reader.original_identity',return_value=self.original), \
             patch('wechat_export.live_reader.capture_kdf') as capture:
            with self.assertRaisesRegex(ReaderError,'original_changed_since_prepare'):
                acquire_key(app=self.app,copy=copy,snapshot=self.root/'snapshot',output=self.root/'key.raw',cancelled=lambda:False)
        capture.assert_not_called()

    def test_entitlement_decode_failure_never_becomes_empty_permissions(self):
        from wechat_export.live_reader import read_entitlements
        for code,blob in [(1,b''),(0,b'not plist'),(0,plistlib.dumps(['not a dictionary']))]:
            with patch('wechat_export.live_reader.subprocess.run',return_value=subprocess.CompletedProcess([],code,blob,b'')):
                with self.assertRaisesRegex(ReaderError,'entitlements_unreadable'):
                    read_entitlements(self.app)
        blob=plistlib.dumps({'synthetic.permission':True},fmt=plistlib.FMT_BINARY)
        with patch('wechat_export.live_reader.subprocess.run',return_value=subprocess.CompletedProcess([],0,blob,b'')):
            self.assertTrue(read_entitlements(self.app)['synthetic.permission'])

    def test_exception_scope_is_not_any_binary_named_wechat(self):
        from wechat_export.live_reader import library_exception_target
        self.assertFalse(library_exception_target(self.app/'Contents/Frameworks/WeChat',self.app))
        helper=self.app/'Contents/MacOS/WeChatHelper.app';(helper/'Contents').mkdir(parents=True)
        plist=helper/'Contents/Info.plist'
        plist.write_bytes(plistlib.dumps({'CFBundleIdentifier':'unrelated.fixture','CFBundleExecutable':'WeChatHelper'}))
        self.assertFalse(library_exception_target(helper,self.app))
        plist.write_bytes(plistlib.dumps({'CFBundleIdentifier':'com.tencent.xinWeChat.WeChatHelper','CFBundleExecutable':'WeChatHelper'}))
        self.assertTrue(library_exception_target(helper,self.app))
        self.assertTrue(library_exception_target(helper/'Contents/MacOS/WeChatHelper',self.app))

    def test_signed_entitlement_mismatch_prevents_prepared_receipt(self):
        def command(args,timeout=60):
            if args[0]=='ditto': shutil.copytree(args[1],args[2],symlinks=True)
            return b''
        # Original is empty. The root/main require the authorized exception,
        # but this mocked signer does not actually add it: post-check must fail.
        with patch('wechat_export.live_reader.wechat_pids',return_value=[]), \
             patch('wechat_export.live_reader.original_identity',return_value=self.original), \
             patch('wechat_export.live_reader.run',side_effect=command), \
             patch('wechat_export.live_reader.read_entitlements',return_value={}):
            with self.assertRaisesRegex(ReaderError,'signed_entitlements_mismatch'):
                prepare_copy(app=self.app,account=self.account,work=self.work,cancelled=lambda:False)
        self.assertFalse((self.work/'reader-prepared.json').exists())
        self.assertFalse((self.work/'signing-audit.json').exists())
