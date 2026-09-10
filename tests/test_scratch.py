"""Actual process leases and synthetic disposable data only. Never inspect live WeChat."""
import contextlib
import io
import json
import os
import select
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from wechat_export.scratch import NAMESPACE, RETENTION_SECONDS, ScratchError, ScratchSpace, sweep


class ScratchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def abandoned(self):
        # A fixture for a released owner lease. A separate test uses real SIGKILL.
        space = ScratchSpace(self.root, 'http-export-source').__enter__()
        (space.payload/'synthetic.jsonl').write_text('fictional payload')
        os.close(space.lease_fd); os.close(space.root_fd)
        return space.path

    def age(self):
        return time.time() + RETENTION_SECONDS + 5

    def test_context_cleanup_success_and_failure(self):
        for fail in (False, True):
            with self.subTest(fail=fail):
                try:
                    with ScratchSpace(self.root, 'cli-export-source') as space:
                        path = space.path
                        (space.payload/'data.txt').write_text('fictional')
                        self.assertEqual(path.stat().st_mode & 0o777, 0o700)
                        self.assertEqual((path/'receipt.json').stat().st_mode & 0o777, 0o600)
                        if fail: raise ValueError('synthetic')
                except ValueError:
                    pass
                self.assertFalse(path.exists())

    def test_live_lease_beats_age(self):
        with ScratchSpace(self.root, 'http-export-source') as space:
            report = sweep(self.root, apply=True, now=self.age())
            self.assertEqual(report['busy'], 1)
            self.assertEqual(report['removed'], 0)
            self.assertTrue(space.path.exists())

    def test_dry_run_and_age_before_delete(self):
        path = self.abandoned()
        self.assertEqual(sweep(self.root, apply=True)['recent'], 1)
        report = sweep(self.root, now=self.age())
        self.assertEqual(report['eligible'], 1)
        self.assertEqual(report['removed'], 0)
        self.assertTrue(path.exists())
        self.assertEqual(sweep(self.root, apply=True, now=self.age())['removed'], 1)
        self.assertFalse(path.exists())

    def test_legacy_and_unmarked_entries_untouched(self):
        owned = self.abandoned()
        legacy = self.root/'export-source-old'; legacy.mkdir()
        (legacy/'archive.db').write_text('keep')
        unknown = owned.parent/('scratch-'+'a'*32); unknown.mkdir(mode=0o700)
        (unknown/'keep').write_text('unknown owner')
        report = sweep(self.root, apply=True, now=self.age())
        self.assertEqual(report['removed'], 1)
        self.assertTrue((legacy/'archive.db').exists())
        self.assertTrue((unknown/'keep').exists())
        self.assertEqual(report['unknown'], 1)

    def test_symlinks_never_followed(self):
        outside = self.root/'outside'; outside.mkdir()
        marker = outside/'keep'; marker.write_text('do not delete')
        path = self.abandoned()
        (path/'payload/pointer').symlink_to(outside, target_is_directory=True)
        alias = path.parent/('scratch-'+'b'*32); alias.symlink_to(outside, target_is_directory=True)
        report = sweep(self.root, apply=True, now=self.age())
        self.assertEqual(report['removed'], 1)
        self.assertTrue(marker.exists())
        self.assertTrue(alias.is_symlink())

    def test_forged_identity_and_future_timestamp_not_deleted(self):
        for field, value in [('identity', [0, 0]), ('created_at', time.time()+10**9), ('purpose', 'original-backup')]:
            with self.subTest(field=field):
                path = self.abandoned(); receipt = path/'receipt.json'
                raw = json.loads(receipt.read_text()); raw[field] = value
                receipt.write_text(json.dumps(raw))
                self.assertGreaterEqual(sweep(self.root, apply=True, now=self.age())['unknown'], 1)
                self.assertTrue(path.exists())

    def test_unregistered_or_symlink_root_rejected(self):
        namespace = self.root/NAMESPACE
        namespace.mkdir(mode=0o700)
        self.assertEqual(sweep(self.root, apply=True)['errors'], 1)
        with self.assertRaises(FileNotFoundError):
            with ScratchSpace(self.root, 'cli-export-source'): pass
        namespace.rmdir()
        elsewhere = self.root/'elsewhere'; elsewhere.mkdir()
        namespace.symlink_to(elsewhere, target_is_directory=True)
        self.assertEqual(sweep(self.root, apply=True)['errors'], 1)
        with self.assertRaises(OSError):
            with ScratchSpace(self.root, 'cli-export-source'): pass
        self.assertTrue(elsewhere.exists())

    def test_unprivate_receipt_not_deleted(self):
        path = self.abandoned(); (path/'receipt.json').chmod(0o644)
        self.assertEqual(sweep(self.root, apply=True, now=self.age())['unknown'], 1)
        self.assertTrue(path.exists())

    def test_surviving_child_keeps_lease_after_owner_fd_closes(self):
        space = ScratchSpace(self.root, 'sqlcipher-export').__enter__()
        proc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], pass_fds=space.inherit_fds)
        os.close(space.lease_fd); os.close(space.root_fd)
        try:
            self.assertIsNone(proc.poll())
            self.assertEqual(sweep(self.root, apply=True, now=self.age())['busy'], 1)
        finally:
            proc.terminate(); proc.wait(timeout=10)
        self.assertEqual(sweep(self.root, apply=True, now=self.age())['removed'], 1)

    def test_real_process_crash_is_recoverable(self):
        code = """
import sys,time
from pathlib import Path
from wechat_export.scratch import ScratchSpace
with ScratchSpace(Path(sys.argv[1]), 'http-export-source') as space:
    (space.payload/'data.txt').write_text('fictional crash fixture')
    print(space.path.name, flush=True)
    time.sleep(30)
"""
        proc = subprocess.Popen([sys.executable, '-c', code, str(self.root)], stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True)
        try:
            ready, _, _ = select.select([proc.stdout], [], [], 10)
            self.assertTrue(ready, 'synthetic child did not become ready')
            name = proc.stdout.readline().strip()
            self.assertTrue(name.startswith('scratch-'))
            path = self.root/NAMESPACE/name
            self.assertTrue(path.exists())
            self.assertEqual(sweep(self.root, apply=True, now=self.age())['busy'], 1)
            proc.kill(); proc.wait(timeout=10)
            self.assertEqual(sweep(self.root, apply=True, now=self.age())['removed'], 1)
            self.assertFalse(path.exists())
        finally:
            if proc.poll() is None: proc.kill(); proc.wait(timeout=10)
            proc.stdout.close()

    def test_cli_dry_run_by_default_and_explicit_apply(self):
        from wechat_export.cli import main
        path = self.abandoned()
        receipt = path/'receipt.json'; raw = json.loads(receipt.read_text())
        raw['created_at'] -= RETENTION_SECONDS + 5; receipt.write_text(json.dumps(raw))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(['cleanup-scratch','--parent',str(self.root)]), 0)
        self.assertTrue(json.loads(out.getvalue())['dry_run'])
        self.assertTrue(path.exists())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['cleanup-scratch','--parent',str(self.root),'--apply']), 0)
        self.assertFalse(path.exists())

    def test_no_unsafe_recursive_delete_fallback(self):
        with patch('wechat_export.scratch.shutil.rmtree.avoids_symlink_attacks', False):
            with self.assertRaises(ScratchError):
                with ScratchSpace(self.root, 'page-decrypt'): pass

    def test_unreclaimed_scratch_is_never_exported_as_an_extra_message_database(self):
        import shutil
        from tests.test_export_pipeline import _build_dbs, _cfg
        from wechat_export.export_run import collect_records
        from wechat_export.source_ledger import snapshot_inventory
        plain = _build_dbs(self.root)
        with ScratchSpace(plain/'message', 'page-decrypt') as space:
            shutil.copy2(plain/'message/message_0.db', space.payload/'plain.db')
            with self.assertRaisesRegex(ValueError, 'unreclaimed_scratch'):
                collect_records(plain, _cfg(self.root), 'live-db', 'synthetic')
            with self.assertRaisesRegex(ValueError, 'scratch_is_not_a_snapshot_source'):
                snapshot_inventory(plain)

    def test_failed_cleanup_does_not_delete_a_replaced_directory(self):
        path = self.abandoned()
        original = path.parent/'saved-owned'
        path.rename(original)
        path.mkdir(mode=0o700)
        (path/'keep').write_text('replacement')
        report = sweep(self.root, apply=True, now=self.age())
        self.assertEqual(report['removed'], 0)
        self.assertTrue((path/'keep').exists())
        self.assertTrue((original/'payload/synthetic.jsonl').exists())

    def test_fifo_receipt_never_blocks_a_sweep(self):
        path = self.abandoned()
        (path/'receipt.json').unlink()
        os.mkfifo(path/'receipt.json', mode=0o600)
        started = time.monotonic()
        self.assertEqual(sweep(self.root, apply=True, now=self.age())['unknown'], 1)
        self.assertLess(time.monotonic()-started, 2)
        self.assertTrue(path.exists())

    def test_concurrent_first_use_waits_for_complete_registration(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor
        import wechat_export.scratch as module
        original = module._write_json
        barrier = threading.Barrier(2)
        def delayed(path, value):
            if path.name == 'namespace.json': time.sleep(0.05)
            original(path, value)
        def worker():
            barrier.wait(timeout=5)
            with ScratchSpace(self.root, 'cli-export-source') as space:
                self.assertTrue((space.path/'receipt.json').is_file())
                return space.name
        with patch('wechat_export.scratch._write_json', side_effect=delayed), ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(worker) for _ in range(2)]
            names = [f.result(timeout=10) for f in futures]
        self.assertEqual(len(set(names)), 2)
        self.assertEqual(list((self.root/NAMESPACE).glob('scratch-*')), [])

    def test_server_startup_recovers_shared_work_scratch_not_persistent_work(self):
        from wechat_export.runtime import resolve_runtime
        from wechat_export.archive_server import ServerContext
        rt = resolve_runtime(self.root)
        work = rt.data_root/'work'; work.mkdir()
        keep = work/'persistent-run'; keep.mkdir()
        (keep/'retained.db').write_text('fictional retained retry material')
        space = ScratchSpace(work, 'page-decrypt').__enter__()
        (space.payload/'plain.db').write_text('fictional incomplete disposable data')
        receipt = space.path/'receipt.json'; raw = json.loads(receipt.read_text())
        raw['created_at'] -= RETENTION_SECONDS + 10; receipt.write_text(json.dumps(raw))
        os.close(space.lease_fd); os.close(space.root_fd)
        context = ServerContext(bind_port=8765, session_token='synthetic', runtime=rt)
        self.assertEqual(context.scratch_recovery['work']['removed'], 1)
        self.assertTrue((keep/'retained.db').exists())
        self.assertFalse(space.path.exists())

    def test_interrupted_cleanup_retains_receipt_and_can_resume(self):
        path = self.abandoned()
        (path/'payload/extra').write_text('fictional extra')
        def partial_remove(name, *, dir_fd):
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY, dir_fd=dir_fd)
            try: os.unlink('extra', dir_fd=fd)
            finally: os.close(fd)
            raise OSError('synthetic deletion interruption')
        # Preserve the safety capability while injecting an I/O interruption.
        with patch('wechat_export.scratch.shutil.rmtree', side_effect=partial_remove) as remover:
            remover.avoids_symlink_attacks = True
            report = sweep(self.root, apply=True, now=self.age())
        self.assertEqual(report['errors'], 1)
        self.assertEqual(report['removed'], 0)
        self.assertTrue((path/'receipt.json').exists())
        self.assertTrue((path/'lease').exists())
        self.assertTrue((path/'payload/synthetic.jsonl').exists())
        self.assertEqual(sweep(self.root, apply=True, now=self.age())['removed'], 1)
        self.assertFalse(path.exists())

    def test_cleanup_failure_does_not_replace_original_failure(self):
        with patch('wechat_export.scratch._remove', side_effect=OSError('synthetic cleanup failure')):
            with self.assertRaisesRegex(ValueError, 'original failure'):
                with ScratchSpace(self.root, 'cli-export-source') as space:
                    (space.payload/'data').write_text('fictional')
                    raise ValueError('original failure')
        self.assertTrue((space.path/'receipt.json').exists())
        self.assertEqual(sweep(self.root, apply=True, now=self.age())['removed'], 1)

    def test_excessively_nested_receipt_is_retained_without_crashing_collector(self):
        path = self.abandoned()
        (path/'receipt.json').write_text('['*1500+'0'+']'*1500)
        report = sweep(self.root, apply=True, now=self.age())
        self.assertEqual(report['unknown'], 1)
        self.assertTrue((path/'payload/synthetic.jsonl').exists())

    def test_official_sqlcipher_keeps_inherited_lease_until_exit(self):
        from wechat_export.sqlcipher_cli import find_sqlcipher
        binary = find_sqlcipher()
        if binary is None: self.skipTest('official SQLCipher CLI required')
        space = ScratchSpace(self.root, 'sqlcipher-export').__enter__()
        proc = subprocess.Popen([str(binary), ':memory:'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True, pass_fds=space.inherit_fds)
        os.close(space.lease_fd); os.close(space.root_fd)
        try:
            proc.stdin.write('SELECT 1;\n'); proc.stdin.flush()
            self.assertTrue(select.select([proc.stdout], [], [], 10)[0])
            self.assertEqual(proc.stdout.readline().strip(), '1')
            self.assertIsNone(proc.poll())
            self.assertEqual(sweep(self.root, apply=True, now=self.age())['busy'], 1)
            proc.communicate('.exit\n', timeout=10)
        finally:
            if proc.poll() is None: proc.kill(); proc.communicate(timeout=10)
            if proc.stdin and not proc.stdin.closed: proc.stdin.close()
            if proc.stdout and not proc.stdout.closed: proc.stdout.close()
        self.assertEqual(sweep(self.root, apply=True, now=self.age())['removed'], 1)

    def test_parent_alias_cannot_collect_another_namespaces_payload(self):
        path = self.abandoned()
        with tempfile.TemporaryDirectory() as td:
            alias = Path(td)/'alias'
            alias.symlink_to(self.root, target_is_directory=True)
            self.assertEqual(sweep(alias, apply=True, now=self.age())['errors'], 1)
            self.assertTrue((path/'payload/synthetic.jsonl').exists())
            with self.assertRaises(ScratchError):
                with ScratchSpace(alias, 'http-export-source'): pass
