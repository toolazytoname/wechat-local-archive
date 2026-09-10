"""Fake Mach-O bytes are hashed only; none of these fixtures is executable code."""
import os
import plistlib
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from wechat_export.build_fingerprint import fingerprint_bundle, FingerprintError, public_fingerprint, SCHEMA


def fake_bundle(root: Path):
    (root/'Contents/MacOS').mkdir(parents=True)
    (root/'Contents/Frameworks/Example.framework/Versions/A').mkdir(parents=True)
    (root/'Contents/MacOS/WeChat').write_bytes(b'\xcf\xfa\xed\xfeSYNTHETIC MAIN, NEVER EXECUTE')
    (root/'Contents/MacOS/WeChat').chmod(0o700)
    framework=root/'Contents/Frameworks/Example.framework'
    (framework/'Versions/A/Example').write_bytes(b'\xcf\xfa\xed\xfeSYNTHETIC MODULE')
    (framework/'Versions/Current').symlink_to('A',target_is_directory=True)
    (framework/'Example').symlink_to('Versions/Current/Example')
    (root/'Contents/Info.plist').write_bytes(plistlib.dumps({
        'CFBundleExecutable':'WeChat','CFBundleIdentifier':'com.tencent.xinWeChat',
        'CFBundleShortVersionString':'4.1.13','CFBundleVersion':'269630'}))
    return root


def fake_environment(root: Path):
    return {'platform':'Darwin','mac_ver':'synthetic-macos','machine':'arm64','wechat_present':True,
            'wechat_version':'4.1.13','wechat_build':'269630','wechat_codesign':{'verified':True},
            'wechat_fingerprint':fingerprint_bundle(root)}


class BundleFingerprintTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve()
        self.app=fake_bundle(self.root/'Synthetic.app')

    def test_all_nested_modules_and_relocated_copy(self):
        one=fingerprint_bundle(self.app)
        self.assertEqual(one['module_count'],2)
        self.assertTrue(one['complete']);self.assertEqual(one['schema'],SCHEMA)
        copy=self.root/'Relocated.app';shutil.copytree(self.app,copy,symlinks=True)
        self.assertEqual(one['sha256'],fingerprint_bundle(copy)['sha256'])
        module=copy/'Contents/Frameworks/Example.framework/Versions/A/Example'
        module.write_bytes(module.read_bytes()+b'CHANGED')
        self.assertNotEqual(one['sha256'],fingerprint_bundle(copy)['sha256'])
        self.assertNotIn('modules',public_fingerprint(one))

    def test_changed_noncode_resource_and_added_file_affect_identity(self):
        one=fingerprint_bundle(self.app)['sha256']
        (self.app/'Contents/resource.txt').write_text('SYNTHETIC resource')
        two=fingerprint_bundle(self.app)['sha256'];self.assertNotEqual(one,two)
        (self.app/'Contents/resource.txt').write_text('different resource')
        self.assertNotEqual(two,fingerprint_bundle(self.app)['sha256'])

    def test_external_symlink_special_file_and_limits(self):
        outside=self.root/'outside';outside.write_text('must not read')
        link=self.app/'Contents/outside';link.symlink_to(outside)
        with self.assertRaisesRegex(FingerprintError,'external_symlink'): fingerprint_bundle(self.app)
        link.unlink();os.mkfifo(link)
        with self.assertRaisesRegex(FingerprintError,'special_file'): fingerprint_bundle(self.app)
        link.unlink()
        for kwargs in ({'max_files':1},{'max_bytes':1},{'timeout':-1},{'cancelled':lambda:True}):
            with self.subTest(kwargs=list(kwargs)), self.assertRaises(FingerprintError):
                fingerprint_bundle(self.app,**kwargs)

    def test_tree_mutation_during_hashing_rejected(self):
        from wechat_export.build_fingerprint import _open_file_under
        done=[False]
        def changed(root,name):
            fd=_open_file_under(root,name)
            if not done[0]:
                done[0]=True;(self.app/'Contents/added.txt').write_text('added after inventory')
            return fd
        with patch('wechat_export.build_fingerprint._open_file_under',side_effect=changed):
            with self.assertRaisesRegex(FingerprintError,'bundle_changed'): fingerprint_bundle(self.app)

    def test_ancestor_symlink_swap_cannot_read_outside_bundle(self):
        from wechat_export.build_fingerprint import _open_file_under
        outside=self.root/'outside';outside.mkdir();(outside/'WeChat').write_bytes(b'private-not-read')
        def changed(root,name):
            if name=='Contents/MacOS/WeChat':
                folder=self.app/'Contents/MacOS'
                folder.rename(self.app/'Contents/old-MacOS');folder.symlink_to(outside,target_is_directory=True)
            return _open_file_under(root,name)
        with patch('wechat_export.build_fingerprint._open_file_under',side_effect=changed):
            with self.assertRaises(OSError): fingerprint_bundle(self.app)
