"""Synthetic native selection, persisted IDs, and identity-bound destinations."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wechat_export.output_locations import OutputLocations, choose_native_folder, SUBDIRECTORY
from wechat_export.runtime import resolve_runtime, resolve_source_id, list_local_archives
from wechat_export.http_security import HttpGuardError
from wechat_export.storage_plan import estimate_storage


class OutputLocationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.runtime = resolve_runtime(self.root / 'runtime')
        self.locations = OutputLocations(self.runtime)
        self.selected = self.root / 'chosen'; self.selected.mkdir()

    def test_persisted_registration_and_external_archive(self):
        item = self.locations.register_native_selection(self.selected)
        token = item['destination_id']
        self.assertEqual(len(token), 32)
        self.assertEqual(self.locations.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.locations.register_native_selection(self.selected)['destination_id'], token)
        root = OutputLocations(self.runtime).resolve(token)
        self.assertEqual(root.stat().st_mode & 0o777, 0o700)
        archive = root / 'read-synthetic'; (archive / 'all').mkdir(parents=True)
        (archive / 'all/messages.jsonl').write_text('')
        sid = self.locations.source_id(token, archive.name)
        self.assertEqual(resolve_source_id(sid, self.runtime), archive)
        with patch('wechat_export.runtime.demo_export_dir', return_value=None):
            self.assertIn(sid, [r['source_id'] for r in list_local_archives(self.runtime)])
        for sid in (f'external:{token}:..', 'external:unknown:read-synthetic', f'external:{token}:a/b'):
            with self.assertRaises((ValueError, FileNotFoundError)):
                resolve_source_id(sid, self.runtime)

    def test_replaced_or_missing_destination_does_not_redirect(self):
        item = self.locations.register_native_selection(self.selected)
        root = self.locations.resolve(item['destination_id'])
        root.rename(root.with_name('detached'))
        self.assertFalse(self.locations.list()[1]['available'])
        self.assertFalse(root.exists())  # A missing volume is never recreated.
        root.mkdir(mode=0o700)
        with self.assertRaises(ValueError): self.locations.resolve(item['destination_id'])
        self.assertFalse(self.locations.list()[1]['available'])

    def test_reject_symlink_private_tree_and_unsafe_existing_folder(self):
        alias = self.root / 'alias'; alias.symlink_to(self.selected, target_is_directory=True)
        with self.assertRaises(ValueError): self.locations.register_native_selection(alias)
        with self.assertRaises(ValueError): self.locations.register_native_selection(self.runtime.private_root)
        target = self.selected / SUBDIRECTORY; target.mkdir(mode=0o755)
        with self.assertRaises(ValueError): self.locations.register_native_selection(self.selected)
        self.assertEqual(target.stat().st_mode & 0o777, 0o755)
        target.rmdir(); target.symlink_to(self.runtime.private_root, target_is_directory=True)
        with self.assertRaises(ValueError): self.locations.register_native_selection(self.selected)

    def test_registry_symlink_is_not_followed(self):
        original = self.root / 'not-registry'; original.write_text('protected')
        self.locations.path.symlink_to(original)
        with self.assertRaises(OSError): self.locations.list()
        self.assertEqual(original.read_text(), 'protected')

    def test_native_cancel_and_fixed_script(self):
        from subprocess import CompletedProcess, TimeoutExpired
        with patch('wechat_export.output_locations.platform.system', return_value='Darwin'), \
             patch('wechat_export.output_locations.subprocess.run', return_value=CompletedProcess([], 0, '\n', '')) as run:
            self.assertIsNone(choose_native_folder())
            args = run.call_args.args[0]
            self.assertEqual(args[:2], ['/usr/bin/osascript', '-e'])
            self.assertIn('choose folder', args[2])
        with patch('wechat_export.output_locations.platform.system', return_value='Darwin'), \
             patch('wechat_export.output_locations.subprocess.run', side_effect=TimeoutExpired('osascript', 180)):
            with self.assertRaises(HttpGuardError) as error: choose_native_folder()
            self.assertEqual(error.exception.code, 'picker_timeout')
        with patch('wechat_export.output_locations.platform.system', return_value='Linux'):
            with self.assertRaises(HttpGuardError): choose_native_folder()

    def test_capacity_counts_both_budgets_on_shared_volume(self):
        from collections import namedtuple
        usage = namedtuple('usage', 'total used free')
        source = self.root / 'db_storage'; source.mkdir(); (source / 'synthetic.db').write_bytes(b'x' * 100)
        with patch('wechat_export.storage_plan.shutil.disk_usage', return_value=usage(10**12,0,1536*1024**2)):
            plan = estimate_storage(source=source,work_root=self.runtime.data_root,output_root=self.selected)
        self.assertEqual(plan['source_bytes'], 100)
        self.assertTrue(plan['same_filesystem'])
        self.assertFalse(plan['estimate_satisfied'])
        self.assertTrue(plan['estimate_only'])
        (source / 'link').symlink_to(self.root / 'outside')
        with self.assertRaises(ValueError): estimate_storage(source=source,work_root=self.runtime.data_root,output_root=self.selected)

    def test_selected_folder_inside_git_checkout_ignores_archive_contents(self):
        import subprocess
        subprocess.run(['git','init','-q',str(self.selected)],check=True)
        item=self.locations.register_native_selection(self.selected)
        root=self.locations.resolve(item['destination_id'])
        (root/'synthetic-message.txt').write_text('SYNTHETIC ONLY')
        result=subprocess.run(['git','-C',str(self.selected),'status','--porcelain','--untracked-files=all'],capture_output=True,text=True,check=True)
        self.assertEqual(result.stdout,'')
        self.assertEqual((root/'.gitignore').read_text(),'*\n')
        from wechat_export.privacy_audit import private_path
        self.assertTrue(private_path('chosen/WeChat Local Archives/archive/manifest.json'))

    def test_offline_job_blocks_before_environment_or_reader_operations(self):
        from wechat_export.jobs import JobStore
        from wechat_export.workflow import start_read_job, advance
        item=self.locations.register_native_selection(self.selected)
        token=item['destination_id'];binding=self.locations.binding(token)
        store=JobStore(self.runtime.jobs_root)
        job=start_read_job(store,account_id='synthetic',adapter_id='candidate',synthetic=False,
                           destination_id=token,destination_binding=binding)
        root=self.locations.resolve(token);root.rename(root.with_name('offline'))
        with patch('wechat_export.environment.collect_environment') as environment, \
             patch('wechat_export.live_reader.prepare_copy') as prepare:
            result=advance(store,job,'prepare_reader',{'confirm_live_step':True,'confirm_library_exception':True},runtime=self.runtime)
        self.assertEqual(result.payload['block_reason'],'destination_unavailable')
        environment.assert_not_called();prepare.assert_not_called()

    @unittest.skipUnless(__import__('sys').platform == 'darwin', 'macOS AppleScript compiler required')
    def test_native_picker_script_compiles_without_executing_dialog(self):
        import subprocess
        from wechat_export.output_locations import CHOOSER_SCRIPT
        output=self.root/'picker.scpt'
        result=subprocess.run(['/usr/bin/osacompile','-o',str(output),'-e',CHOOSER_SCRIPT],capture_output=True,timeout=20)
        self.assertEqual(result.returncode,0,'fixed native picker script must compile')
        self.assertTrue(output.is_file())
