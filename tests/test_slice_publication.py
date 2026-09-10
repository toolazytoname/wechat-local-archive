"""A killed slice writer leaves only leased private staging, never partial final data."""
import json
import select
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from tests.test_export_service import _demo_tree
from wechat_export.export_service import QuerySpec, write_slice, ExportCancelled, QueryError
from wechat_export.scratch import NAMESPACE, RETENTION_SECONDS, sweep


class SlicePublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = _demo_tree(self.root/'source')
        self.dest = self.root/'output'
        self.spec = QuerySpec.from_mapping({'scope': {'kind': 'all'}})

    def test_data_and_manifest_publish_together_private_and_not_collected(self):
        import os
        rename = os.rename
        def inspect_then_publish(stage, final):
            self.assertEqual({p.name for p in stage.iterdir()}, {'messages.jsonl', 'manifest.json', 'coverage.json', 'coverage.md', 'attachment-ledger.jsonl'})
            self.assertEqual(list(final.iterdir()), [])
            rename(stage, final)
        with patch('wechat_export.export_service.os.rename', side_effect=inspect_then_publish):
            result = write_slice(None, self.source, self.spec, jobs_root=self.dest, source='canonical')
        output = Path(result['path'])
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        self.assertEqual(output.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(result['count'], 4)
        self.assertEqual(json.loads((output.parent/'manifest.json').read_text())['path'], str(output))
        self.assertEqual(sweep(self.dest, apply=True, now=time.time()+RETENTION_SECONDS+10)['removed'], 0)
        self.assertTrue(output.exists())

    def test_cancel_and_count_failure_remove_staging_and_reservation(self):
        for options, error in [({'should_cancel': lambda: True}, ExportCancelled),
                               ({'expected_count': 99}, QueryError)]:
            with self.subTest(options=list(options)), self.assertRaises(error):
                write_slice(None, self.source, self.spec, jobs_root=self.dest, source='canonical', **options)
            self.assertFalse(list(self.dest.rglob('messages.jsonl')))
            self.assertEqual([p for p in self.dest.iterdir() if p.name not in {NAMESPACE, NAMESPACE+'.init.lock'}], [])
            self.assertEqual(list((self.dest/NAMESPACE).glob('scratch-*')), [])

    def test_occupied_reservation_is_preserved(self):
        from wechat_export.export_service import write_records
        def interfere(*args, **kwargs):
            count = write_records(*args, **kwargs)
            reserved = next(p for p in self.dest.iterdir() if p.name not in {NAMESPACE, NAMESPACE+'.init.lock'})
            (reserved/'keep').write_text('other writer')
            return count
        with patch('wechat_export.export_service.write_records', side_effect=interfere):
            with self.assertRaisesRegex(QueryError, 'destination changed'):
                write_slice(None, self.source, self.spec, jobs_root=self.dest, source='canonical')
        self.assertEqual(len(list(self.dest.glob('*/keep'))), 1)
        self.assertFalse(list(self.dest.rglob('messages.jsonl')))

    def test_actual_writer_crash_never_publishes_partial_data(self):
        code = """
import sys,time
from pathlib import Path
import wechat_export.export_service as service
root,output=map(Path,sys.argv[1:])
original=service.write_records
def pause(*args,**kwargs):
    count=original(*args,**kwargs)
    print('staged',flush=True)
    time.sleep(30)
    return count
service.write_records=pause
service.write_slice(None,root,service.QuerySpec.from_mapping({'scope':{'kind':'all'}}),jobs_root=output,source='canonical')
"""
        proc = subprocess.Popen([sys.executable, '-c', code, str(self.source), str(self.dest)],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        try:
            self.assertTrue(select.select([proc.stdout], [], [], 10)[0])
            self.assertEqual(proc.stdout.readline().strip(), 'staged')
            self.assertFalse(list(self.dest.glob('*/messages.jsonl')))
            self.assertEqual(len(list((self.dest/NAMESPACE).glob('scratch-*/payload/export/messages.jsonl'))), 1)
            proc.kill(); proc.wait(timeout=10)
            self.assertEqual(sweep(self.dest, apply=True, now=time.time()+RETENTION_SECONDS+10)['removed'], 1)
            self.assertFalse(list(self.dest.rglob('messages.jsonl')))
            self.assertTrue((self.source/'all/messages.jsonl').exists())
            # Only an empty UUID reservation may remain; never auto-delete exports.
            self.assertTrue(all(not list(p.iterdir()) for p in self.dest.iterdir() if p.name not in {NAMESPACE, NAMESPACE+'.init.lock'}))
        finally:
            if proc.poll() is None: proc.kill(); proc.wait(timeout=10)
            proc.stdout.close()

    def test_index_crash_preserves_previous_index_and_rebuild_recovers(self):
        import hashlib
        import sqlite3
        from wechat_export.archive_index import ensure_index_current
        index = self.source/'archive.sqlite'
        previous = hashlib.sha256(index.read_bytes()).hexdigest()
        rows = [json.loads(line) for line in (self.source/'all/messages.jsonl').read_text().splitlines()]
        with (self.source/'all/messages.jsonl').open('a') as out:
            out.write(json.dumps(dict(rows[0], record_uid='synthetic-extra'))+'\n')
        code = """
import sys,time
from pathlib import Path
import wechat_export.archive_index as index
original=index.os.replace
def pause(src,dst):
    if Path(dst).name=='archive.sqlite':
        print('index-staged',flush=True)
        time.sleep(30)
    return original(src,dst)
index.os.replace=pause
index.build_index(Path(sys.argv[1]))
"""
        proc = subprocess.Popen([sys.executable, '-c', code, str(self.source)], stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True)
        try:
            self.assertTrue(select.select([proc.stdout], [], [], 10)[0])
            self.assertEqual(proc.stdout.readline().strip(), 'index-staged')
            self.assertEqual(hashlib.sha256(index.read_bytes()).hexdigest(), previous)
            self.assertEqual(len(list((self.source/NAMESPACE).glob('scratch-*/payload/index.sqlite'))), 1)
            proc.kill(); proc.wait(timeout=10)
            self.assertEqual(sweep(self.source, apply=True, now=time.time()+RETENTION_SECONDS+10)['removed'], 1)
            self.assertEqual(hashlib.sha256(index.read_bytes()).hexdigest(), previous)
            ensure_index_current(self.source)
            conn = sqlite3.connect(index)
            try: self.assertEqual(conn.execute('SELECT count(*) FROM messages').fetchone()[0], 5)
            finally: conn.close()
        finally:
            if proc.poll() is None: proc.kill(); proc.wait(timeout=10)
            proc.stdout.close()

    def test_symlink_output_parent_rejected_before_reservation(self):
        outside = self.root/'outside'; outside.mkdir()
        self.dest.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(QueryError, 'symlink'):
            write_slice(None, self.source, self.spec, jobs_root=self.dest, source='canonical')
        self.assertEqual(list(outside.iterdir()), [])
